"""Pruebas de limpieza de JSON y validadores de contratos."""
from __future__ import annotations

import json

import pytest

from src.validation import JSONInvalido, limpiar_json
from src.agents.triage import AgenteTriage
from src.agents.causa_raiz import AgenteCausaRaiz
from src.agents.resolucion import AgenteResolucion
from src.agents.comunicacion import AgenteComunicacion


# ----------------------------------------------------------------------
# limpiar_json
# ----------------------------------------------------------------------
def test_json_limpio() -> None:
    assert limpiar_json('{"a": 1}') == {"a": 1}


def test_json_con_fences() -> None:
    texto = "```json\n{\"a\": 1}\n```"
    assert limpiar_json(texto) == {"a": 1}


def test_json_con_texto_extra() -> None:
    texto = "Aquí está el análisis solicitado:\n\n{\"a\": [1, 2]}\n\nEspero que sirva."
    assert limpiar_json(texto) == {"a": [1, 2]}


def test_json_anidado_en_texto() -> None:
    texto = 'resultado: {"a": {"b": "con } raro"}, "c": 2} fin'
    assert limpiar_json(texto) == {"a": {"b": "con } raro"}, "c": 2}


def test_json_invalido_lanza() -> None:
    with pytest.raises(JSONInvalido):
        limpiar_json("no hay nada aquí")
    with pytest.raises(JSONInvalido):
        limpiar_json("")


# ----------------------------------------------------------------------
# Agente de triage: validación del contrato
# ----------------------------------------------------------------------
def _ctx_triage():
    return {"ids_evidencia": {"E001", "E002", "E003"}}


def test_triage_valido() -> None:
    salida = {
        "incident_summary": "resumen",
        "user_impact": "impacto",
        "severity": "High",
        "severity_reason": "regla del ticket",
        "first_anomaly": {"time": "14:00", "evidence_id": "E001", "description": "anomalía"},
        "themes": [{"title": "tema", "classification": "trigger", "evidence_ids": ["E001"]}],
        "timeline": [{"time": "14:00", "source": "app", "evidence_id": "E001", "event": "evento"}],
        "correlations": [{"cause_id": "E001", "effect_id": "E002", "explanation": "porque"}],
    }
    assert AgenteTriage().validar_salida(salida, _ctx_triage()) == []


def test_triage_rechaza_ids_inventados() -> None:
    salida = {
        "incident_summary": "resumen",
        "user_impact": "impacto",
        "severity": "High",
        "severity_reason": "razón",
        "first_anomaly": {"time": "14:00", "evidence_id": "E999", "description": "anomalía"},
        "themes": [{"title": "tema", "classification": "trigger", "evidence_ids": ["E999"]}],
        "timeline": [],
        "correlations": [],
    }
    errores = AgenteTriage().validar_salida(salida, _ctx_triage())
    assert any("E999" in e for e in errores)


def test_triage_rechaza_severidad_fuera_de_enum() -> None:
    salida = {
        "incident_summary": "resumen",
        "user_impact": "impacto",
        "severity": "Ultra",
        "severity_reason": "razón",
        "first_anomaly": {"time": "14:00", "evidence_id": "E001", "description": "anomalía"},
        "themes": [],
        "timeline": [],
        "correlations": [],
    }
    errores = AgenteTriage().validar_salida(salida, _ctx_triage())
    assert any("severity" in e for e in errores)


# ----------------------------------------------------------------------
# Agente de causa raíz: comprobaciones de solo lectura
# ----------------------------------------------------------------------
def _causa_valida() -> dict:
    return {
        "hypotheses": [
            {
                "rank": 1,
                "title": "hipótesis 1",
                "confidence": "High",
                "causal_chain": "cadena",
                "supporting_evidence": ["E001"],
                "contradicting_evidence": [],
                "known_issue": None,
            },
            {
                "rank": 2,
                "title": "hipótesis 2",
                "confidence": "Low",
                "causal_chain": "cadena",
                "supporting_evidence": ["E002"],
                "contradicting_evidence": ["E003"],
                "known_issue": "KI-001",
            },
        ],
        "most_likely_root_cause": "causa",
        "ruled_out": [{"item": "cosa", "reason": "razón", "evidence_id": "E001"}],
        "rejected_quick_fixes": [{"action": "acción", "why_wrong": "porque"}],
        "verification_checks": [
            {
                "check": "verificar",
                "command": "SELECT * FROM v$session",
                "confirms_if": "sí",
                "refutes_if": "no",
            }
        ],
        "needs_human_review": False,
    }


def test_causa_raiz_valida() -> None:
    errores = AgenteCausaRaiz().validar_salida(_causa_valida(), {"ids_evidencia": {"E001", "E002", "E003"}})
    assert errores == []


def test_causa_raiz_rechaza_comandos_de_escritura() -> None:
    data = _causa_valida()
    data["verification_checks"][0]["command"] = "ALTER SYSTEM KILL SESSION '1,2'"
    errores = AgenteCausaRaiz().validar_salida(data, {"ids_evidencia": {"E001", "E002", "E003"}})
    assert any("solo lectura" in e for e in errores)


def test_causa_raiz_exige_2_o_3_hipotesis() -> None:
    data = _causa_valida()
    data["hypotheses"] = data["hypotheses"][:1]
    errores = AgenteCausaRaiz().validar_salida(data, {"ids_evidencia": {"E001", "E002", "E003"}})
    assert any("2 a 3" in e for e in errores)


# ----------------------------------------------------------------------
# Agente de resolución: prohibición de quick fixes rechazados
# ----------------------------------------------------------------------
def _resolucion(paso: str) -> dict:
    return {
        "immediate_mitigation": [
            {
                "order": 1,
                "step": paso,
                "owner": "AMS",
                "risk": "riesgo",
                "rollback": "rollback",
                "requires_change_request": True,
            }
        ],
        "permanent_fix": [{"action": "acción", "owner": "Dev", "prevents": "previene"}],
        "validation": [{"criterion": "criterio", "target": "objetivo"}],
    }


def test_resolucion_rechaza_quick_fix_prohibido() -> None:
    contexto = {
        "quick_fixes_rechazados": [
            {"action": "aumentar el pool de conexiones y reiniciar la aplicación", "why_wrong": "trata el síntoma"}
        ]
    }
    errores = AgenteResolucion().validar_salida(
        _resolucion("aumentar el pool de conexiones y reiniciar la aplicación"), contexto
    )
    assert any("PROHIBIDO" in e for e in errores)


def test_resolucion_acepta_pasos_normales() -> None:
    contexto = {
        "quick_fixes_rechazados": [
            {"action": "aumentar el pool de conexiones y reiniciar la aplicación", "why_wrong": "trata el síntoma"}
        ]
    }
    errores = AgenteResolucion().validar_salida(
        _resolucion("crear el índice que falta con la opción ONLINE"), contexto
    )
    assert errores == []


# ----------------------------------------------------------------------
# Agente de comunicación: el comunicado al negocio no lleva jerga
# ----------------------------------------------------------------------
def _comunicacion(texto_negocio: str) -> dict:
    return {
        "ticket_note": "nota técnica",
        "business_update": {"text": texto_negocio, "next_update_at": "15:00"},
        "escalation_note": {"to": "DBA", "text": "texto", "requests": ["revisar X"]},
        "runbook_entry": {
            "title": "título",
            "symptoms": "síntomas",
            "diagnosis": "diagnóstico",
            "resolution": "resolución",
            "prevention": "prevención",
        },
    }


def test_comunicado_sin_jerga_pasa() -> None:
    assert AgenteComunicacion().validar_salida(
        _comunicacion("El proceso de compra presenta demoras; el equipo técnico ya está trabajando en una solución."),
        {},
    ) == []


def test_comunicado_con_jerga_se_rechaza() -> None:
    for jerga in (
        "El error ORA-00054 bloquea la tabla SHOP.ORDER_ITEMS",
        "El servidor 10.0.2.11 no responde",
        "El pool HikariCP se agotó tras un timeout",
        "Escribimos a juan@tienda.com sobre el incidente E017",
    ):
        errores = AgenteComunicacion().validar_salida(_comunicacion(jerga), {})
        assert errores, f"debería rechazar: {jerga}"
