"""Pipeline de preparación de evidencia (Fase 1, parte determinista).

Lee los logs de un directorio, los protege (redacción + anti-inyección),
los agrupa y produce:

- ``evidencias``: grupos con id, fuente, horas, cantidad, nivel, plantilla
- ``trends``: métricas que cambian con el tiempo
- ``metricas``: líneas originales, evidencias, datos protegidos por tipo
  y reducción de tamaño de lo que se envía al modelo

La clasificación semántica (componente, categoría, notable, resumen) la
hace después el Agente de Ingesta; aquí solo se prepara la evidencia.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.evidence.grouping import agrupar, construir_tendencias
from src.evidence.parsers import parsear_archivo
from src.evidence.redaction import ConteoRedaccion, RegistroInyecciones


@dataclass
class ResultadoPreparacion:
    """Evidencia preparada y métricas del proceso."""

    evidencias: list[dict[str, Any]] = field(default_factory=list)
    trends: list[dict[str, Any]] = field(default_factory=list)
    metricas: dict[str, Any] = field(default_factory=dict)

    def evidencia_compacta(self) -> list[dict[str, Any]]:
        """Vista de la evidencia para enviar al modelo (sin campos internos)."""
        compacta = []
        for e in self.evidencias:
            compacta.append(
                {
                    "id": e["id"],
                    "source": e["source"],
                    "level": e["level"],
                    "count": e["count"],
                    "time_first": e["time_first"],
                    "time_last": e["time_last"],
                    "template": e["template"],
                    "exception": e["exception"],
                    "caused_by": e["caused_by"],
                    "sample": e["samples"][0] if e["samples"] else "",
                }
            )
        return compacta

    def tendencias_para_modelo(self) -> list[dict[str, Any]]:
        """Tendencias sin los puntos completos (ya van first/last/max)."""
        return [
            {k: v for k, v in t.items() if k != "points"}
            | {"points": t["points"][:6]}
            for t in self.trends
        ]


def preparar_evidencia(logs_dir: Path) -> ResultadoPreparacion:
    """Procesa todos los .log de un directorio y devuelve la evidencia."""
    logs_dir = Path(logs_dir)
    archivos = sorted(logs_dir.glob("*.log"))
    if not archivos:
        raise FileNotFoundError(f"no hay archivos .log en {logs_dir}")

    conteo = ConteoRedaccion()
    registro_inyecciones = RegistroInyecciones()
    entradas = []
    lineas_por_fuente: dict[str, int] = {}
    bytes_originales = 0

    for archivo in archivos:
        entradas_fuente, lineas_fisicas = parsear_archivo(archivo, conteo, registro_inyecciones)
        entradas.extend(entradas_fuente)
        lineas_por_fuente[archivo.name] = lineas_fisicas
        bytes_originales += archivo.stat().st_size

    grupos = agrupar(entradas)
    tendencias = construir_tendencias(grupos)

    # Tamaño de lo que se enviaría al modelo (evidencia compacta + trends)
    resultado = ResultadoPreparacion(evidencias=grupos, trends=tendencias)
    carga_modelo = json.dumps(
        {"evidencias": resultado.evidencia_compacta(), "trends": resultado.tendencias_para_modelo()},
        ensure_ascii=False,
    )
    bytes_al_modelo = len(carga_modelo.encode("utf-8"))

    resultado.metricas = {
        "lineas_originales": sum(lineas_por_fuente.values()),
        "lineas_por_fuente": lineas_por_fuente,
        "cantidad_evidencias": len(grupos),
        "cantidad_tendencias": len(tendencias),
        "datos_protegidos_por_tipo": conteo.como_dict(),
        "datos_protegidos_total": conteo.total,
        "inyecciones_neutralizadas": registro_inyecciones.como_dict(),
        "bytes_originales": bytes_originales,
        "bytes_al_modelo": bytes_al_modelo,
        "reduccion_pct": round(
            100 * (1 - bytes_al_modelo / bytes_originales) if bytes_originales else 0, 1
        ),
    }
    return resultado
