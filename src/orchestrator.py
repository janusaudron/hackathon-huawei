"""Orquestador del sistema (Fase 5).

Ejecuta el flujo completo de un caso:

    preparacion (código) -> ingesta -> triage -> causa_raiz -> resolucion -> comunicacion

Decisiones de diseño (justificadas en ARQUITECTURA.md):

- El orquestador es CÓDIGO, no un agente LLM: el flujo es determinista,
  auditable y cada fase recibe solo lo que necesita.
- Fases secuenciales: cada una depende de la anterior. Dentro de la
  ingesta, los lotes son llamadas independientes.
- Si una fase falla, el flujo no se cae: se registra el error con
  explicación, las fases dependientes se marcan como omitidas y el
  reporte final se genera igualmente con lo que haya.
- El estado del caso se guarda en disco tras cada fase (reanudable e
  inspeccionable).
"""
from __future__ import annotations

import json
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from src.audit import Auditoria, Cronometro
from src.config import CARPETA_CASOS, Config
from src.evidence.prepare import preparar_evidencia
from src.maas_client import ClienteMaaS
from src.report import generar_reporte

FASES = ["preparacion", "ingesta", "triage", "causa_raiz", "resolucion", "comunicacion"]


class Orquestador:
    """Ejecuta el análisis completo de un incidente."""

    def __init__(
        self,
        config: Config,
        cliente: ClienteMaaS | None,
        en_progreso: Callable[[str, str, str], None] | None = None,
    ):
        self.config = config
        self.cliente = cliente
        self.en_progreso = en_progreso or (lambda fase, estado, detalle: None)

    # ------------------------------------------------------------------
    def ejecutar_caso(
        self,
        logs_dir: Path,
        ticket_path: Path,
        problemas_path: Path | None,
    ) -> dict[str, Any]:
        """Analiza un incidente completo y devuelve el caso."""
        ticket = json.loads(Path(ticket_path).read_text(encoding="utf-8"))
        ticket_id = ticket.get("ticket_id", "caso")
        case_id = f"{ticket_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        dir_caso = Path(CARPETA_CASOS) / case_id
        dir_fases = dir_caso / "fases"
        dir_fases.mkdir(parents=True, exist_ok=True)

        auditoria = Auditoria(dir_caso / "auditoria.jsonl")
        caso: dict[str, Any] = {
            "case_id": case_id,
            "ticket_id": ticket_id,
            "generado_en": datetime.now().isoformat(timespec="seconds"),
            "modo": "mock" if self.cliente is None else "real",
            "modelo": self.config.modelo if self.cliente else None,
            "entradas": {
                "logs_dir": str(Path(logs_dir).resolve()),
                "ticket": str(Path(ticket_path).resolve()),
                "problemas_conocidos": str(Path(problemas_path).resolve()) if problemas_path else None,
            },
            "ticket": ticket,
            "fases": {},
        }
        self._guardar_caso(caso, dir_caso)
        auditoria.evento("inicio", "", {"case_id": case_id, "modo": caso["modo"]})

        # ------------------------------------------------------------
        # Fase: preparación (determinista, sin modelo)
        # ------------------------------------------------------------
        registro_prep = self._fase(caso, "preparacion", dir_caso, auditoria)
        try:
            resultado = preparar_evidencia(Path(logs_dir))
            registro_prep["salida"] = {
                "evidencias": resultado.evidencias,
                "trends": resultado.trends,
                "metricas": resultado.metricas,
            }
            registro_prep["estado"] = "completada"
        except Exception as exc:
            registro_prep["estado"] = "error"
            registro_prep["error"] = f"{exc}"
            auditoria.evento("error", "preparacion", str(exc))
        self._cerrar_fase(caso, "preparacion", registro_prep, dir_caso, dir_fases, auditoria)
        if registro_prep["estado"] != "completada":
            return self._finalizar(caso, dir_caso, auditoria)
        resultado_prep = registro_prep["salida"]
        evidencias_completas = resultado_prep["evidencias"]
        trends = resultado_prep["trends"]
        ids_evidencia = {e["id"] for e in evidencias_completas}
        evidencia_modelo = self._evidencia_para_modelo(evidencias_completas)

        contexto_base: dict[str, Any] = {
            "ticket": ticket,
            "evidencias": evidencias_completas,
            "trends": trends,
            "ids_evidencia": ids_evidencia,
            "ruta_problemas": problemas_path,
        }

        # ------------------------------------------------------------
        # Fase: ingesta (agente, por lotes)
        # ------------------------------------------------------------
        from src.agents.ingesta import AgenteIngesta

        fase = self._fase(caso, "ingesta", dir_caso, auditoria)
        if fase is None:
            return self._finalizar(caso, dir_caso, auditoria)
        try:
            salida = AgenteIngesta().ejecutar(contexto_base, self.cliente, auditoria)
            evidencias_completas = salida["evidencias"]
            trends = salida["trends"]
            contexto_base["evidencias"] = evidencias_completas
            contexto_base["trends"] = trends
            evidencia_modelo = self._evidencia_para_modelo(evidencias_completas)
            fase["salida"] = {
                "evidencias": evidencias_completas,
                "trends": trends,
            }
            fase["estado"] = "completada"
        except Exception as exc:
            fase["estado"] = "error"
            fase["error"] = f"{exc}"
            auditoria.evento("error", "ingesta", str(exc))
        self._cerrar_fase(caso, "ingesta", fase, dir_caso, dir_fases, auditoria)
        if fase["estado"] != "completada":
            return self._finalizar(caso, dir_caso, auditoria)

        # A partir de aquí los agentes usan la vista compacta.
        contexto_agentes = {
            **contexto_base,
            "evidencias": evidencia_modelo,
        }

        # ------------------------------------------------------------
        # Fase: triage
        # ------------------------------------------------------------
        triage = self._agente_simple(caso, "triage", contexto_agentes, dir_caso, dir_fases, auditoria)
        if triage is None:
            return self._finalizar(caso, dir_caso, auditoria)

        # ------------------------------------------------------------
        # Fase: causa raíz (con tools)
        # ------------------------------------------------------------
        contexto_causa = {**contexto_agentes, "triage": triage}
        causa_raiz = self._agente_simple(
            caso, "causa_raiz", contexto_causa, dir_caso, dir_fases, auditoria
        )
        if causa_raiz is None:
            return self._finalizar(caso, dir_caso, auditoria)

        # ------------------------------------------------------------
        # Fase: resolución (con quick fixes prohibidos)
        # ------------------------------------------------------------
        quick_fixes = causa_raiz.get("rejected_quick_fixes", [])
        contexto_resolucion = {
            **contexto_agentes,
            "triage": triage,
            "causa_raiz": causa_raiz,
            "quick_fixes_rechazados": quick_fixes,
        }
        resolucion = self._agente_simple(
            caso, "resolucion", contexto_resolucion, dir_caso, dir_fases, auditoria
        )
        if resolucion is None:
            return self._finalizar(caso, dir_caso, auditoria)

        # ------------------------------------------------------------
        # Fase: comunicación
        # ------------------------------------------------------------
        contexto_comunicacion = {
            **contexto_agentes,
            "triage": triage,
            "causa_raiz": causa_raiz,
            "resolucion": resolucion,
        }
        self._agente_simple(
            caso, "comunicacion", contexto_comunicacion, dir_caso, dir_fases, auditoria
        )

        return self._finalizar(caso, dir_caso, auditoria)

    # ------------------------------------------------------------------
    # Auxiliares de fases
    # ------------------------------------------------------------------
    def _fase(
        self, caso: dict[str, Any], nombre: str, dir_caso: Path, auditoria: Auditoria
    ) -> dict[str, Any] | None:
        """Inicializa el registro de una fase y notifica el progreso."""
        registro = {"estado": "en_curso", "duracion_s": None, "salida": None, "error": None}
        caso["fases"][nombre] = registro
        registro["_cronometro"] = Cronometro()
        registro["_dir"] = dir_caso
        self.en_progreso(nombre, "en_curso", "")
        return registro

    def _cerrar_fase(
        self,
        caso: dict[str, Any],
        nombre: str,
        registro: dict[str, Any],
        dir_caso: Path,
        dir_fases: Path,
        auditoria: Auditoria,
    ) -> None:
        registro["duracion_s"] = round(registro["_cronometro"].segundos(), 2)
        registro.pop("_cronometro", None)
        registro.pop("_dir", None)
        if registro["estado"] == "completada" and registro.get("salida") is not None:
            (dir_fases / f"{nombre}.json").write_text(
                json.dumps(registro["salida"], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        self._guardar_caso(caso, dir_caso)
        self.en_progreso(nombre, registro["estado"], registro.get("error") or "")
        auditoria.evento("fin_fase", nombre, {"estado": registro["estado"], "duracion_s": registro["duracion_s"]})

    def _agente_simple(
        self,
        caso: dict[str, Any],
        nombre_agente: str,
        contexto: dict[str, Any],
        dir_caso: Path,
        dir_fases: Path,
        auditoria: Auditoria,
    ) -> dict[str, Any] | None:
        """Ejecuta un agente de una sola llamada; devuelve su salida o None."""
        from src.agents.causa_raiz import AgenteCausaRaiz
        from src.agents.comunicacion import AgenteComunicacion
        from src.agents.resolucion import AgenteResolucion
        from src.agents.triage import AgenteTriage

        agentes = {
            "triage": AgenteTriage,
            "causa_raiz": AgenteCausaRaiz,
            "resolucion": AgenteResolucion,
            "comunicacion": AgenteComunicacion,
        }
        registro = self._fase(caso, nombre_agente, dir_caso, auditoria)
        if registro is None:
            return None
        agente = agentes[nombre_agente]()
        try:
            salida = agente.ejecutar(contexto, self.cliente, auditoria)
            registro["salida"] = salida
            registro["estado"] = "completada"
        except Exception as exc:
            registro["estado"] = "error"
            registro["error"] = f"{exc}"
            auditoria.evento("error", nombre_agente, str(exc))
            self._cerrar_fase(caso, nombre_agente, registro, dir_caso, dir_fases, auditoria)
            return None
        self._cerrar_fase(caso, nombre_agente, registro, dir_caso, dir_fases, auditoria)
        return salida

    # ------------------------------------------------------------------
    def _finalizar(
        self, caso: dict[str, Any], dir_caso: Path, auditoria: Auditoria
    ) -> dict[str, Any]:
        """Marca fases pendientes como omitidas y genera el reporte."""
        for nombre in FASES:
            if nombre not in caso["fases"]:
                caso["fases"][nombre] = {
                    "estado": "omitida",
                    "duracion_s": None,
                    "salida": None,
                    "error": "omitida porque una fase anterior falló",
                }
                self.en_progreso(nombre, "omitida", "")

        try:
            reporte = generar_reporte(caso)
            (dir_caso / "reporte.md").write_text(reporte, encoding="utf-8")
            caso["reporte"] = str((dir_caso / "reporte.md").resolve())
        except Exception as exc:
            auditoria.evento("error", "reporte", f"no se pudo generar el reporte: {exc}\n{traceback.format_exc()}")
        self._guardar_caso(caso, dir_caso)
        auditoria.evento("fin", "", {"case_id": caso["case_id"]})
        auditoria.cerrar()
        return caso

    # ------------------------------------------------------------------
    @staticmethod
    def _evidencia_para_modelo(evidencias: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Vista compacta de la evidencia para los prompts de los agentes."""
        campos = [
            "id", "source", "level", "count", "time_first", "time_last",
            "component", "category", "notable", "summary", "template",
            "exception", "caused_by", "sample",
        ]
        return [{c: e.get(c) for c in campos} for e in evidencias]

    @staticmethod
    def _guardar_caso(caso: dict[str, Any], dir_caso: Path) -> None:
        (dir_caso / "caso.json").write_text(
            json.dumps(caso, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
