"""Agente de Causa Raíz (Fase 3): por qué ocurrió el incidente.

Es el único agente con TOOLS (function calling). Bucle:

1. Envía ticket + triage + evidencia al modelo con los esquemas de tools.
2. Si el modelo pide tools, las ejecuta (solo lectura) y devuelve el
   resultado a la conversación.
3. Repite hasta obtener el contrato final validado.

Robustez: si el endpoint no soporta ``tools``, degrada a un protocolo por
prompt (el modelo pide tools con un JSON convenido) sin cambiar el flujo.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

from src.agents.base import AgenteBase, ErrorAgente, json_compacto
from src.audit import Auditoria
from src.maas_client import ClienteMaaS, ErrorToolsNoSoportadas
from src.tools.registry import RegistroTools
from src.validation import JSONInvalido, es_id_evidencia, limpiar_json

CONFIANZAS = {"High", "Medium", "Low"}
#: comandos que NO pueden aparecer en las comprobaciones (no son solo lectura)
RE_NO_SOLO_LECTURA = re.compile(
    r"\b(UPDATE|DELETE|INSERT|DROP|ALTER|TRUNCATE|MERGE|GRANT|REVOKE|SHUTDOWN"
    r"|RESTART|KILL|CREATE\s|MODIFY|SET\s)\b",
    re.IGNORECASE,
)

PROTOCOLO_PSEUDO_TOOLS = (
    "El endpoint no soporta function calling. Usa este protocolo alternativo: "
    "para ejecutar una tool responde SOLO con este JSON: "
    '{"tool_call": {"name": "nombre_de_la_tool", "arguments": {...}}}. '
    "Tools disponibles: buscar_problemas_conocidos(texto), "
    "ver_detalle_evidencia(id), consultar_cambios_recientes(). "
    "Cuando tengas suficiente información, responde el contrato final en JSON."
)


class AgenteCausaRaiz(AgenteBase):
    nombre = "causa_raiz"
    fase = "causa_raiz"

    #: máximo de rondas de tools antes de exigir el contrato final
    MAX_RONDAS_TOOLS = 6

    # ------------------------------------------------------------------
    def valores_contexto(self, contexto: dict[str, Any]) -> dict[str, str]:
        return {
            "TICKET_JSON": json_compacto(contexto["ticket"]),
            "TRIAGE_JSON": json_compacto(contexto["triage"]),
            "EVIDENCIAS_JSON": json_compacto(contexto["evidencias"]),
            "TENDENCIAS_JSON": json_compacto(contexto["trends"]),
        }

    # ------------------------------------------------------------------
    def ejecutar(
        self,
        contexto: dict[str, Any],
        cliente: ClienteMaaS | None,
        auditoria: Auditoria,
    ) -> Any:
        if cliente is None:
            auditoria.evento("mock", self.fase, "causa_raiz: salida simulada")
            return self.salida_mock(contexto)

        tools = RegistroTools(
            evidencias=contexto["evidencias"],
            ruta_problemas=contexto["ruta_problemas"],
            ticket=contexto["ticket"],
        )
        contexto = {**contexto, "tools": tools}

        messages = self.construir_mensajes(contexto)
        modo_tools = True
        rondas_tools = 0
        max_llamadas = self.MAX_RONDAS_TOOLS + max(1, cliente.config.max_reintentos)
        llamadas = 0
        ultimo_error = ""

        while llamadas < max_llamadas:
            llamadas += 1
            inicio = time.monotonic()
            try:
                respuesta = cliente.chat(
                    messages, tools=tools.esquemas if modo_tools else None
                )
            except ErrorToolsNoSoportadas:
                modo_tools = False
                messages.append({"role": "user", "content": PROTOCOLO_PSEUDO_TOOLS})
                continue
            except Exception as exc:
                auditoria.llamada_modelo(
                    fase=self.fase,
                    agente=self.nombre,
                    intento=llamadas,
                    messages=messages,
                    respuesta=None,
                    duracion_s=time.monotonic() - inicio,
                    error=f"error de comunicación: {exc}",
                )
                ultimo_error = f"error de comunicación: {exc}"
                messages.append(
                    {
                        "role": "user",
                        "content": f"La llamada falló ({exc}). Intenta de nuevo y responde el contrato final en JSON.",
                    }
                )
                continue

            duracion = time.monotonic() - inicio
            mensaje = respuesta.get("choices", [{}])[0].get("message", {})
            texto = mensaje.get("content") or ""
            auditoria.llamada_modelo(
                fase=self.fase,
                agente=self.nombre,
                intento=llamadas,
                messages=messages,
                respuesta=texto or json.dumps(mensaje, ensure_ascii=False),
                duracion_s=duracion,
                usage=cliente.usage(respuesta),
            )

            # --- function calling nativo --------------------------------
            tool_calls = mensaje.get("tool_calls") or []
            if modo_tools and tool_calls:
                rondas_tools += 1
                if rondas_tools > self.MAX_RONDAS_TOOLS:
                    messages.append(
                        {
                            "role": "user",
                            "content": "Ya usaste suficientes tools. Responde ahora el contrato final en JSON.",
                        }
                    )
                    continue
                messages.append(mensaje)
                for tc in tool_calls:
                    resultado = self._ejecutar_tool(tc, tools, auditoria)
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.get("id", ""),
                            "content": json.dumps(resultado, ensure_ascii=False),
                        }
                    )
                continue

            # --- respuesta final ------------------------------------------
            try:
                data = limpiar_json(texto)
            except JSONInvalido as exc:
                ultimo_error = f"respuesta no es JSON válido: {exc}"
                messages.append(
                    {
                        "role": "user",
                        "content": "Tu respuesta anterior no es JSON válido. Devuelve SOLO el JSON del contrato, sin markdown ni texto extra.",
                    }
                )
                continue

            # --- protocolo pseudo-tools ------------------------------------
            if isinstance(data, dict) and "tool_call" in data and not modo_tools:
                llamada = data["tool_call"]
                nombre = llamada.get("name", "")
                argumentos = llamada.get("arguments") or {}
                if isinstance(argumentos, str):
                    try:
                        argumentos = json.loads(argumentos)
                    except ValueError:
                        argumentos = {}
                resultado = self._auditar_y_ejecutar(nombre, argumentos, tools, auditoria)
                messages.append(
                    {
                        "role": "user",
                        "content": "<resultado-tool>\n"
                        + json.dumps(resultado, ensure_ascii=False)
                        + "\n</resultado-tool>",
                    }
                )
                continue

            errores = self.validar_salida(data, contexto)
            if not errores:
                return data
            ultimo_error = "; ".join(errores[:5])
            messages.append(
                {
                    "role": "user",
                    "content": "Tu respuesta fue rechazada por estas razones:\n- "
                    + "\n- ".join(errores)
                    + "\nCorrige esos problemas y devuelve de nuevo SOLO el JSON válido.",
                }
            )

        raise ErrorAgente(
            f"causa_raiz: sin salida válida tras {llamadas} llamadas. Último error: {ultimo_error}"
        )

    # ------------------------------------------------------------------
    def _ejecutar_tool(
        self, tc: dict[str, Any], tools: RegistroTools, auditoria: Auditoria
    ) -> dict[str, Any]:
        nombre = (tc.get("function") or {}).get("name", "")
        argumentos_brutos = (tc.get("function") or {}).get("arguments", "{}")
        try:
            argumentos = json.loads(argumentos_brutos) if isinstance(argumentos_brutos, str) else argumentos_brutos
        except ValueError:
            argumentos = {}
        return self._auditar_y_ejecutar(nombre, argumentos, tools, auditoria)

    def _auditar_y_ejecutar(
        self,
        nombre: str,
        argumentos: dict[str, Any],
        tools: RegistroTools,
        auditoria: Auditoria,
    ) -> dict[str, Any]:
        if not isinstance(argumentos, dict):
            argumentos = {}
        if not tools.existe(nombre):
            resultado = {"error": f"tool desconocida: {nombre!r}"}
        else:
            resultado = tools.ejecutar(nombre, argumentos)
        auditoria.llamada_tool(
            fase=self.fase, agente=self.nombre, nombre=nombre, argumentos=argumentos, resultado=resultado
        )
        return resultado

    # ------------------------------------------------------------------
    def validar_salida(self, data: Any, contexto: dict[str, Any]) -> list[str]:
        errores: list[str] = []
        if not isinstance(data, dict):
            return [f"la salida debe ser un objeto JSON, recibido: {type(data).__name__}"]
        validos = contexto["ids_evidencia"]

        # hypotheses -----------------------------------------------------
        hipotesis = data.get("hypotheses")
        if not isinstance(hipotesis, list) or not (2 <= len(hipotesis) <= 3):
            errores.append("hypotheses: debe ser una lista de 2 a 3 hipótesis")
            hipotesis = hipotesis if isinstance(hipotesis, list) else []
        ranks: list[int] = []
        for i, h in enumerate(hipotesis):
            donde = f"hypotheses[{i}]"
            if not isinstance(h, dict):
                errores.append(f"{donde}: debe ser un objeto")
                continue
            rank = h.get("rank")
            if not isinstance(rank, int) or rank < 1:
                errores.append(f"{donde}.rank: debe ser un entero >= 1")
            else:
                ranks.append(rank)
            for campo in ("title", "causal_chain"):
                if not isinstance(h.get(campo), str) or not h.get(campo, "").strip():
                    errores.append(f"{donde}.{campo}: debe ser un texto no vacío")
            if h.get("confidence") not in CONFIANZAS:
                errores.append(f"{donde}.confidence: debe ser High, Medium o Low")
            for campo in ("supporting_evidence", "contradicting_evidence"):
                ids = h.get(campo)
                if not isinstance(ids, list):
                    errores.append(f"{donde}.{campo}: debe ser una lista de ids")
                else:
                    for id_ in ids:
                        if not es_id_evidencia(id_) or id_ not in validos:
                            errores.append(f"{donde}.{campo}: id inexistente: {id_!r}")
            ki = h.get("known_issue")
            if ki is not None and not (isinstance(ki, str) and re.fullmatch(r"KI-\d{3}", ki)):
                errores.append(f"{donde}.known_issue: debe ser un id KI-xxx o null")
        if ranks and sorted(ranks) != list(range(1, len(ranks) + 1)):
            errores.append("hypotheses: los rank deben ser 1, 2, ... sin repetir")

        # most_likely_root_cause -------------------------------------------
        if not isinstance(data.get("most_likely_root_cause"), str) or not data.get(
            "most_likely_root_cause", ""
        ).strip():
            errores.append("most_likely_root_cause: debe ser un texto no vacío")

        # ruled_out ---------------------------------------------------------
        descartados = data.get("ruled_out")
        if not isinstance(descartados, list):
            errores.append("ruled_out: debe ser una lista")
        else:
            for i, r in enumerate(descartados):
                donde = f"ruled_out[{i}]"
                if not isinstance(r, dict):
                    errores.append(f"{donde}: debe ser un objeto")
                    continue
                for campo in ("item", "reason"):
                    if not isinstance(r.get(campo), str) or not r.get(campo, "").strip():
                        errores.append(f"{donde}.{campo}: debe ser un texto no vacío")
                id_ = r.get("evidence_id")
                if not es_id_evidencia(id_) or id_ not in validos:
                    errores.append(f"{donde}.evidence_id: id inexistente: {id_!r}")

        # rejected_quick_fixes --------------------------------------------------
        quick = data.get("rejected_quick_fixes")
        if not isinstance(quick, list) or not quick:
            errores.append("rejected_quick_fixes: debe ser una lista con al menos una entrada")
        else:
            for i, q in enumerate(quick):
                donde = f"rejected_quick_fixes[{i}]"
                if not isinstance(q, dict):
                    errores.append(f"{donde}: debe ser un objeto")
                    continue
                for campo in ("action", "why_wrong"):
                    if not isinstance(q.get(campo), str) or not q.get(campo, "").strip():
                        errores.append(f"{donde}.{campo}: debe ser un texto no vacío")

        # verification_checks -----------------------------------------------------
        checks = data.get("verification_checks")
        if not isinstance(checks, list) or not checks:
            errores.append("verification_checks: debe ser una lista con al menos una comprobación")
        else:
            for i, c in enumerate(checks):
                donde = f"verification_checks[{i}]"
                if not isinstance(c, dict):
                    errores.append(f"{donde}: debe ser un objeto")
                    continue
                for campo in ("check", "command", "confirms_if", "refutes_if"):
                    if not isinstance(c.get(campo), str) or not c.get(campo, "").strip():
                        errores.append(f"{donde}.{campo}: debe ser un texto no vacío")
                comando = c.get("command", "")
                if RE_NO_SOLO_LECTURA.search(comando):
                    errores.append(
                        f"{donde}.command: el comando no es de solo lectura "
                        f"(contiene operaciones de escritura): {comando[:80]!r}"
                    )

        # needs_human_review ---------------------------------------------------------
        if not isinstance(data.get("needs_human_review"), bool):
            errores.append("needs_human_review: debe ser true o false")

        return errores

    # ------------------------------------------------------------------
    def salida_mock(self, contexto: dict[str, Any]) -> dict[str, Any]:
        ids = sorted(contexto["ids_evidencia"])
        primero = ids[0] if ids else ""
        return {
            "hypotheses": [
                {
                    "rank": 1,
                    "title": "hipótesis simulada 1 (modo mock)",
                    "confidence": "Low",
                    "causal_chain": "cadena causal simulada en modo mock",
                    "supporting_evidence": ids[:2],
                    "contradicting_evidence": [],
                    "known_issue": None,
                },
                {
                    "rank": 2,
                    "title": "hipótesis simulada 2 (modo mock)",
                    "confidence": "Low",
                    "causal_chain": "cadena causal simulada en modo mock",
                    "supporting_evidence": ids[:1],
                    "contradicting_evidence": [],
                    "known_issue": None,
                },
            ],
            "most_likely_root_cause": "Salida simulada en modo mock: se necesita el modelo para el análisis.",
            "ruled_out": [
                {
                    "item": "elemento simulado (modo mock)",
                    "reason": "salida simulada",
                    "evidence_id": primero,
                }
            ],
            "rejected_quick_fixes": [
                {"action": "acción simulada (modo mock)", "why_wrong": "salida simulada"}
            ],
            "verification_checks": [
                {
                    "check": "comprobación simulada (modo mock)",
                    "command": "-- comando de solo lectura simulado",
                    "confirms_if": "salida simulada",
                    "refutes_if": "salida simulada",
                }
            ],
            "needs_human_review": True,
        }
