"""Agente de Triage (Fase 2): qué falla, a quién, desde cuándo y gravedad.

Recibe el ticket y la evidencia clasificada; produce el contrato de triage
con primera anomalía, temas (trigger/symptom/noise/expected), línea de
tiempo unificada y correlaciones causa -> efecto.
"""
from __future__ import annotations

from typing import Any

from src.agents.base import AgenteBase, json_compacto
from src.validation import (
    es_id_evidencia,
    requerir_cadena,
    requerir_enum,
    requerir_lista,
    validar_ids_evidencia,
)

SEVERIDADES = {"Low", "Medium", "High", "Critical"}
CLASIFICACIONES = {"trigger", "symptom", "noise", "expected"}


class AgenteTriage(AgenteBase):
    nombre = "triage"
    fase = "triage"

    def valores_contexto(self, contexto: dict[str, Any]) -> dict[str, str]:
        return {
            "TICKET_JSON": json_compacto(contexto["ticket"]),
            "EVIDENCIAS_JSON": json_compacto(contexto["evidencias"]),
            "TENDENCIAS_JSON": json_compacto(contexto["trends"]),
        }

    # ------------------------------------------------------------------
    def validar_salida(self, data: Any, contexto: dict[str, Any]) -> list[str]:
        errores: list[str] = []
        if not isinstance(data, dict):
            return [f"la salida debe ser un objeto JSON, recibido: {type(data).__name__}"]
        validos = contexto["ids_evidencia"]

        requerir_cadena(data, "incident_summary", "triage", errores)
        requerir_cadena(data, "user_impact", "triage", errores)
        requerir_enum(data, "severity", SEVERIDADES, "triage", errores)
        requerir_cadena(data, "severity_reason", "triage", errores)

        # first_anomaly -------------------------------------------------
        fa = data.get("first_anomaly")
        if not isinstance(fa, dict):
            errores.append("first_anomaly: debe ser un objeto {time, evidence_id, description}")
        else:
            requerir_cadena(fa, "time", "first_anomaly", errores)
            requerir_cadena(fa, "description", "first_anomaly", errores)
            id_fa = fa.get("evidence_id")
            if not es_id_evidencia(id_fa) or id_fa not in validos:
                errores.append(f"first_anomaly.evidence_id: id inexistente: {id_fa!r}")

        # themes ---------------------------------------------------------
        temas = requerir_lista(data, "themes", "triage", errores)
        for i, tema in enumerate(temas):
            donde = f"themes[{i}]"
            if not isinstance(tema, dict):
                errores.append(f"{donde}: debe ser un objeto")
                continue
            requerir_cadena(tema, "title", donde, errores)
            requerir_enum(tema, "classification", CLASIFICACIONES, donde, errores)
            validar_ids_evidencia(tema.get("evidence_ids"), validos, f"{donde}.evidence_ids", errores)

        # timeline --------------------------------------------------------
        linea = requerir_lista(data, "timeline", "triage", errores)
        if len(linea) > 15:
            errores.append(f"timeline: máximo 15 entradas (recibidas {len(linea)})")
        for i, punto in enumerate(linea):
            donde = f"timeline[{i}]"
            if not isinstance(punto, dict):
                errores.append(f"{donde}: debe ser un objeto")
                continue
            requerir_cadena(punto, "time", donde, errores)
            requerir_cadena(punto, "source", donde, errores)
            requerir_cadena(punto, "event", donde, errores)
            id_p = punto.get("evidence_id")
            if not es_id_evidencia(id_p) or id_p not in validos:
                errores.append(f"{donde}.evidence_id: id inexistente: {id_p!r}")

        # correlations -----------------------------------------------------
        correl = requerir_lista(data, "correlations", "triage", errores)
        for i, par in enumerate(correl):
            donde = f"correlations[{i}]"
            if not isinstance(par, dict):
                errores.append(f"{donde}: debe ser un objeto")
                continue
            for campo in ("cause_id", "effect_id"):
                id_ = par.get(campo)
                if not es_id_evidencia(id_) or id_ not in validos:
                    errores.append(f"{donde}.{campo}: id inexistente: {id_!r}")
            requerir_cadena(par, "explanation", donde, errores)

        return errores

    # ------------------------------------------------------------------
    def salida_mock(self, contexto: dict[str, Any]) -> dict[str, Any]:
        ids = sorted(contexto["ids_evidencia"])
        primero = ids[0] if ids else ""
        segundo = ids[1] if len(ids) > 1 else primero
        evidencia = contexto["evidencias"][0] if contexto["evidencias"] else {}
        return {
            "incident_summary": "Salida simulada en modo mock: resumen no disponible sin modelo.",
            "user_impact": "Salida simulada en modo mock.",
            "severity": "Medium",
            "severity_reason": "Modo mock: severidad por defecto.",
            "first_anomaly": {
                "time": evidencia.get("time_first", ""),
                "evidence_id": primero,
                "description": "Salida simulada en modo mock.",
            },
            "themes": [
                {
                    "title": "tema simulado",
                    "classification": "symptom",
                    "evidence_ids": ids[:5],
                }
            ],
            "timeline": [
                {
                    "time": evidencia.get("time_first", ""),
                    "source": evidencia.get("source", ""),
                    "evidence_id": primero,
                    "event": "evento simulado (modo mock)",
                }
            ],
            "correlations": [
                {
                    "cause_id": primero,
                    "effect_id": segundo,
                    "explanation": "correlación simulada en modo mock",
                }
            ],
        }
