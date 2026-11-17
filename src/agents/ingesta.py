"""Agente de Ingesta (Fase 1): clasifica la evidencia preparada.

La preparación determinista (parseo, redacción, agrupación, tendencias)
corre en código; este agente añade la capa semántica: componente,
categoría, notabilidad y resumen de cada evidencia, y la descripción de
cada tendencia.

Trabaja por LOTES para que la salida del modelo no se trunque: cada lote
es una llamada independiente con su propio ciclo de validación.
"""
from __future__ import annotations

from typing import Any

from src.agents.base import AgenteBase, json_compacto
from src.audit import Auditoria
from src.maas_client import ClienteMaaS

TAMANO_LOTE = 20

CATEGORIAS = {
    "trafico-normal",
    "health-check",
    "despliegue",
    "migracion",
    "cambio-configuracion",
    "rendimiento",
    "error-aplicacion",
    "error-infraestructura",
    "alerta-conocida",
    "ruido",
    "dato-sensible",
    "otro",
}


class AgenteIngesta(AgenteBase):
    nombre = "ingesta"
    fase = "ingesta"

    # ------------------------------------------------------------------
    def ejecutar(
        self,
        contexto: dict[str, Any],
        cliente: ClienteMaaS | None,
        auditoria: Auditoria,
    ) -> dict[str, Any]:
        """Clasifica toda la evidencia, por lotes, y devuelve el merge."""
        evidencias = contexto["evidencias"]
        trends = contexto["trends"]

        clasificaciones: dict[str, dict[str, Any]] = {}
        descripciones_trends: dict[str, dict[str, Any]] = {}

        if cliente is None:
            auditoria.evento("mock", self.fase, "ingesta: salida simulada")
            mock = self.salida_mock(contexto)
            clasificaciones = {i["id"]: i for i in mock["evidencias"]}
            descripciones_trends = {t["metric"]: t for t in mock["trends"]}
        else:
            for lote in self._lotes(evidencias, trends):
                sub = {**contexto, "lote": lote}
                salida = super().ejecutar(sub, cliente, auditoria)
                for item in salida.get("evidencias", []):
                    if isinstance(item, dict) and "id" in item:
                        clasificaciones[item["id"]] = item
                for item in salida.get("trends", []):
                    if isinstance(item, dict) and "metric" in item:
                        descripciones_trends[item["metric"]] = item

        # Merge: lo que falte se completa con valores por defecto y aviso.
        faltantes = [e["id"] for e in evidencias if e["id"] not in clasificaciones]
        if faltantes:
            auditoria.evento(
                "aviso",
                self.fase,
                {
                    "mensaje": "evidencias sin clasificar tras los reintentos; se usan valores por defecto",
                    "ids": faltantes,
                },
            )

        finales = []
        for e in evidencias:
            c = clasificaciones.get(e["id"], {})
            finales.append(
                {
                    # contrato mínimo
                    "id": e["id"],
                    "source": e["source"],
                    "time_first": e["time_first"],
                    "time_last": e["time_last"],
                    "count": e["count"],
                    "level": e["level"],
                    "summary": c.get("summary") or e["template"],
                    "notable": bool(c.get("notable", False)),
                    # extras útiles para los demás agentes
                    "component": c.get("component") or "desconocido",
                    "category": c.get("category") or "otro",
                    "template": e["template"],
                    "exception": e.get("exception"),
                    "caused_by": e.get("caused_by"),
                    "sample": e["samples"][0] if e.get("samples") else "",
                    "samples": e.get("samples", []),
                    "lineas_log": [e["lineas_originales"][0], e["lineas_originales"][-1]],
                }
            )

        trends_finales = []
        for t in trends:
            d = descripciones_trends.get(t["metric"], {})
            trends_finales.append(
                {
                    "metric": t["metric"],
                    "source": t["source"],
                    "evidence_id": t["evidence_id"],
                    "first": t["first"],
                    "last": t["last"],
                    "max": t["max"],
                    "direction": t["direction"],
                    "points": t["points"],
                    "description": d.get("description") or "",
                    "significant": bool(d.get("significant", False)),
                }
            )

        return {"evidencias": finales, "trends": trends_finales}

    # ------------------------------------------------------------------
    def _lotes(
        self, evidencias: list[dict[str, Any]], trends: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Divide la evidencia en lotes; las tendencias van en cada lote."""
        lotes = []
        for i in range(0, len(evidencias), TAMANO_LOTE):
            lote_e = evidencias[i : i + TAMANO_LOTE]
            compacto = [
                {
                    "id": e["id"],
                    "source": e["source"],
                    "level": e["level"],
                    "count": e["count"],
                    "time_first": e["time_first"],
                    "time_last": e["time_last"],
                    "template": e["template"],
                    "exception": e.get("exception"),
                    "caused_by": e.get("caused_by"),
                    "sample": e["samples"][0] if e.get("samples") else "",
                }
                for e in lote_e
            ]
            trends_compacto = [
                {k: v for k, v in t.items() if k != "points"} | {"points": t["points"][:6]}
                for t in trends
            ]
            lotes.append({"evidencias": compacto, "trends": trends_compacto})
        return lotes

    # ------------------------------------------------------------------
    def valores_contexto(self, contexto: dict[str, Any]) -> dict[str, str]:
        lote = contexto["lote"]
        return {
            "EVIDENCIAS_JSON": json_compacto(lote["evidencias"]),
            "TENDENCIAS_JSON": json_compacto(lote["trends"]),
        }

    def validar_salida(self, data: Any, contexto: dict[str, Any]) -> list[str]:
        errores: list[str] = []
        if not isinstance(data, dict):
            return [f"la salida debe ser un objeto JSON, recibido: {type(data).__name__}"]
        lote = contexto["lote"]
        ids_validos = {e["id"] for e in lote["evidencias"]}
        metricas_validas = {t["metric"] for t in lote["trends"]}

        evs = data.get("evidencias")
        if not isinstance(evs, list) or not evs:
            return ["falta 'evidencias' o está vacía"]
        for i, item in enumerate(evs):
            donde = f"evidencias[{i}]"
            if not isinstance(item, dict):
                errores.append(f"{donde}: debe ser un objeto")
                continue
            id_ = item.get("id")
            if id_ not in ids_validos:
                errores.append(f"{donde}.id: id de evidencia inexistente en el lote: {id_!r}")
            for campo in ("component", "summary"):
                valor = item.get(campo)
                if not isinstance(valor, str) or not valor.strip():
                    errores.append(f"{donde}.{campo}: debe ser un texto no vacío")
            if item.get("category") not in CATEGORIAS:
                errores.append(
                    f"{donde}.category: debe ser una de {sorted(CATEGORIAS)} "
                    f"(recibido: {item.get('category')!r})"
                )
            if not isinstance(item.get("notable"), bool):
                errores.append(f"{donde}.notable: debe ser true o false")

        trs = data.get("trends")
        if not isinstance(trs, list):
            errores.append("trends: debe ser una lista")
            trs = []
        elif lote["trends"] and not trs:
            errores.append("trends: debes describir todas las tendencias entregadas")
        for i, item in enumerate(trs):
                donde = f"trends[{i}]"
                if not isinstance(item, dict):
                    errores.append(f"{donde}: debe ser un objeto")
                    continue
                if item.get("metric") not in metricas_validas:
                    errores.append(f"{donde}.metric: métrica inexistente: {item.get('metric')!r}")
                if not isinstance(item.get("description"), str) or not item.get("description", "").strip():
                    errores.append(f"{donde}.description: debe ser un texto no vacío")
                if not isinstance(item.get("significant"), bool):
                    errores.append(f"{donde}.significant: debe ser true o false")
        return errores

    def salida_mock(self, contexto: dict[str, Any]) -> dict[str, Any]:
        return {
            "evidencias": [
                {
                    "id": e["id"],
                    "component": "componente (simulado)",
                    "category": "otro",
                    "notable": False,
                    "summary": "clasificación simulada en modo mock",
                }
                for e in contexto["evidencias"]
            ],
            "trends": [
                {
                    "metric": t["metric"],
                    "description": "tendencia simulada en modo mock",
                    "significant": False,
                }
                for t in contexto["trends"]
            ],
        }
