"""Agente de Comunicación (Fase 4): cuatro textos, cuatro audiencias.

Produce la nota técnica del ticket, el comunicado al negocio (validado
automáticamente contra jerga técnica), la nota de escalamiento y la
entrada de runbook.
"""
from __future__ import annotations

import re
from typing import Any

from src.agents.base import AgenteBase, json_compacto
from src.validation import requerir_cadena, requerir_lista

#: patrones PROHIBIDOS en el comunicado al negocio
RE_JERGA = [
    (re.compile(r"\bORA-\d+\b"), "códigos de error"),
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"), "direcciones IP"),
    (re.compile(r"\b[A-Z][A-Z0-9_]{2,}\.[A-Z][A-Z0-9_]{2,}\b"), "nombres de tablas"),
    (re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"), "correos"),
    (re.compile(r"\b(?:\d[ -]?){12,18}\d\b"), "números de tarjeta"),
    (re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE|JOIN|FROM|WHERE|DDL|DML|SQL)\b"), "jerga SQL"),
    (re.compile(r"\b(Hikari|JDBC|Flyway|stack trace|upstream|timeout|pool)\b", re.I), "jerga técnica"),
    (re.compile(r"\bE\d{3,}\b"), "ids internos de evidencia"),
    (re.compile(r"\b(KI-|CHG-|INC-)\d+\b"), "ids internos de tickets"),
]
RE_HORA = re.compile(r"^\d{2}:\d{2}$")


class AgenteComunicacion(AgenteBase):
    nombre = "comunicacion"
    fase = "comunicacion"

    def valores_contexto(self, contexto: dict[str, Any]) -> dict[str, str]:
        return {
            "TICKET_JSON": json_compacto(contexto["ticket"]),
            "TRIAGE_JSON": json_compacto(contexto["triage"]),
            "CAUSA_RAIZ_JSON": json_compacto(contexto["causa_raiz"]),
            "RESOLUCION_JSON": json_compacto(contexto["resolucion"]),
        }

    # ------------------------------------------------------------------
    def validar_salida(self, data: Any, contexto: dict[str, Any]) -> list[str]:
        errores: list[str] = []
        if not isinstance(data, dict):
            return [f"la salida debe ser un objeto JSON, recibido: {type(data).__name__}"]

        requerir_cadena(data, "ticket_note", "comunicacion", errores)

        # business_update: sin jerga técnica --------------------------------
        bu = data.get("business_update")
        if not isinstance(bu, dict):
            errores.append("business_update: debe ser un objeto {text, next_update_at}")
        else:
            requerir_cadena(bu, "text", "business_update", errores)
            hora = bu.get("next_update_at")
            if not isinstance(hora, str) or not RE_HORA.match(hora.strip()):
                errores.append("business_update.next_update_at: debe ser una hora HH:MM")
            texto = bu.get("text", "") or ""
            for patron, prohibido in RE_JERGA:
                hallazgo = patron.search(texto)
                if hallazgo:
                    errores.append(
                        f"business_update.text contiene {prohibido} ({hallazgo.group(0)!r}): "
                        "el comunicado al negocio no puede llevar jerga técnica ni datos internos. "
                        "Reescríbelo en lenguaje llano."
                    )

        # escalation_note ------------------------------------------------------
        esc = data.get("escalation_note")
        if not isinstance(esc, dict):
            errores.append("escalation_note: debe ser un objeto {to, text, requests}")
        else:
            requerir_cadena(esc, "to", "escalation_note", errores)
            requerir_cadena(esc, "text", "escalation_note", errores)
            solicitudes = requerir_lista(esc, "requests", "escalation_note", errores)
            if solicitudes and not all(isinstance(s, str) and s.strip() for s in solicitudes):
                errores.append("escalation_note.requests: deben ser textos no vacíos")
            if not solicitudes:
                errores.append("escalation_note.requests: debe tener al menos una solicitud concreta")

        # runbook_entry ---------------------------------------------------------
        rb = data.get("runbook_entry")
        if not isinstance(rb, dict):
            errores.append("runbook_entry: debe ser un objeto {title, symptoms, diagnosis, resolution, prevention}")
        else:
            for campo in ("title", "symptoms", "diagnosis", "resolution", "prevention"):
                requerir_cadena(rb, campo, "runbook_entry", errores)

        return errores

    # ------------------------------------------------------------------
    def salida_mock(self, contexto: dict[str, Any]) -> dict[str, Any]:
        return {
            "ticket_note": "Nota técnica simulada en modo mock.",
            "business_update": {
                "text": "Comunicado simulado en modo mock.",
                "next_update_at": "15:00",
            },
            "escalation_note": {
                "to": "Equipo simulado",
                "text": "Escalamiento simulado en modo mock.",
                "requests": ["solicitud simulada"],
            },
            "runbook_entry": {
                "title": "Runbook simulado (modo mock)",
                "symptoms": "síntomas simulados",
                "diagnosis": "diagnóstico simulado",
                "resolution": "resolución simulada",
                "prevention": "prevención simulada",
            },
        }
