"""Pruebas del bucle de tools del agente de causa raíz.

Simula las respuestas del endpoint para validar, sin clave:
1. el bucle de function calling nativo (tool_calls -> resultado -> contrato)
2. el fallback por prompt cuando el endpoint no soporta ``tools``
"""
from __future__ import annotations

import json
from pathlib import Path

from src.agents.causa_raiz import AgenteCausaRaiz
from src.audit import Auditoria
from src.config import Config
from src.maas_client import ErrorToolsNoSoportadas

KIT = Path(__file__).resolve().parent.parent / "kit" / "datos"


def _config() -> Config:
    return Config(
        endpoint="http://mock", modelo="glm-5.2", api_key="x",
        timeout=10, temperature=0.1, max_reintentos=2,
        verify_ssl=True, mock=False,
    )


def _respuesta_texto(texto: str) -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": texto}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}}


def _respuesta_tool(nombre: str, argumentos: dict) -> dict:
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {"name": nombre, "arguments": json.dumps(argumentos)},
                        }
                    ],
                }
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def _contrato_final() -> dict:
    return {
        "hypotheses": [
            {"rank": 1, "title": "h1", "confidence": "High", "causal_chain": "c1",
             "supporting_evidence": ["E001"], "contradicting_evidence": [], "known_issue": None},
            {"rank": 2, "title": "h2", "confidence": "Low", "causal_chain": "c2",
             "supporting_evidence": ["E002"], "contradicting_evidence": [], "known_issue": None},
        ],
        "most_likely_root_cause": "causa probable de prueba",
        "ruled_out": [{"item": "x", "reason": "r", "evidence_id": "E001"}],
        "rejected_quick_fixes": [{"action": "a", "why_wrong": "w"}],
        "verification_checks": [
            {"check": "c", "command": "SELECT 1 FROM DUAL", "confirms_if": "s", "refutes_if": "n"}
        ],
        "needs_human_review": False,
    }


def _contexto() -> dict:
    evidencias = [
        {"id": "E001", "source": "app.log", "level": "ERROR", "count": 2,
         "time_first": "2026-01-01 10:00:00", "time_last": "2026-01-01 10:05:00",
         "template": "ORA-00054 resource busy", "exception": None, "caused_by": None,
         "samples": ["ORA-00054 resource busy"], "lineas_log": [1, 2]},
        {"id": "E002", "source": "db.log", "level": "WARN", "count": 1,
         "time_first": "2026-01-01 10:01:00", "time_last": "2026-01-01 10:01:00",
         "template": "slow query", "exception": None, "caused_by": None,
         "samples": ["slow query"], "lineas_log": [3, 3]},
    ]
    return {
        "ticket": {"ticket_id": "T-1", "recent_changes": ["release 1.2.3"]},
        "triage": {"severity": "High"},
        "evidencias": evidencias,
        "trends": [],
        "ids_evidencia": {"E001", "E002"},
        "ruta_problemas": KIT / "problemas_conocidos.json",
    }


# ----------------------------------------------------------------------
class ClienteConTools:
    """Simula un endpoint que soporta function calling."""

    def __init__(self):
        self.config = _config()
        self.llamadas = 0
        self.tools_enviadas = False

    def chat(self, messages, tools=None, temperature=None):
        self.llamadas += 1
        if tools:
            self.tools_enviadas = True
        if self.llamadas == 1:
            return _respuesta_tool("buscar_problemas_conocidos", {"texto": "ORA-12170"})
        return _respuesta_texto(json.dumps(_contrato_final(), ensure_ascii=False))

    def texto_respuesta(self, respuesta):
        return respuesta["choices"][0]["message"]["content"] or ""

    def usage(self, respuesta):
        return respuesta.get("usage", {})


class ClienteSinTools:
    """Simula un endpoint que rechaza el parámetro tools."""

    def __init__(self):
        self.config = _config()
        self.llamadas = 0

    def chat(self, messages, tools=None, temperature=None):
        self.llamadas += 1
        if tools:
            raise ErrorToolsNoSoportadas("tools no soportado")
        if self.llamadas == 2:
            # primera llamada útil tras el fallback: pide una tool por prompt
            return _respuesta_texto(
                json.dumps({"tool_call": {"name": "consultar_cambios_recientes", "arguments": {}}})
            )
        return _respuesta_texto(json.dumps(_contrato_final(), ensure_ascii=False))

    def texto_respuesta(self, respuesta):
        return respuesta["choices"][0]["message"]["content"] or ""

    def usage(self, respuesta):
        return respuesta.get("usage", {})


# ----------------------------------------------------------------------
def test_bucle_function_calling_nativo(tmp_path: Path) -> None:
    cliente = ClienteConTools()
    auditoria = Auditoria(tmp_path / "aud.jsonl")
    salida = AgenteCausaRaiz().ejecutar(_contexto(), cliente, auditoria)

    assert salida["most_likely_root_cause"] == "causa probable de prueba"
    assert cliente.llamadas == 2, "debe llamar una vez por la tool y otra por el contrato"
    assert cliente.tools_enviadas, "los esquemas de tools deben enviarse"

    # la tool quedó registrada en la auditoría con argumentos y resultado
    lineas = (tmp_path / "aud.jsonl").read_text(encoding="utf-8").splitlines()
    eventos = [json.loads(l) for l in lineas]
    tools = [e for e in eventos if e["tipo"] == "llamada_tool"]
    assert len(tools) == 1
    assert tools[0]["tool"] == "buscar_problemas_conocidos"
    assert tools[0]["argumentos"] == {"texto": "ORA-12170"}
    # la base de problemas conocidos del kit responde con el KI que matchea
    ids_ki = [r.get("id") for r in tools[0]["resultado"]["resultados"]]
    assert "KI-002" in ids_ki


def test_fallback_pseudo_tools(tmp_path: Path) -> None:
    cliente = ClienteSinTools()
    auditoria = Auditoria(tmp_path / "aud.jsonl")
    salida = AgenteCausaRaiz().ejecutar(_contexto(), cliente, auditoria)

    assert salida["most_likely_root_cause"] == "causa probable de prueba"
    lineas = (tmp_path / "aud.jsonl").read_text(encoding="utf-8").splitlines()
    eventos = [json.loads(l) for l in lineas]
    tools = [e for e in eventos if e["tipo"] == "llamada_tool"]
    assert len(tools) == 1
    assert tools[0]["tool"] == "consultar_cambios_recientes"
    assert tools[0]["resultado"]["cambios"] == ["release 1.2.3"]
