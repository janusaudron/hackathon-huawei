"""Agente de Resolución (Fase 4): plan de acción seguro.

Recibe el triage y la causa raíz; produce la mitigación ordenada de la
opción más segura a la más invasiva, el fix definitivo y los criterios de
validación. VALIDA que ningún paso proponga un quick fix rechazado en la
fase anterior (regla 1 del reto).
"""
from __future__ import annotations

import re
from typing import Any

from src.agents.base import AgenteBase, json_compacto

PALABRAS_VACIAS = {
    "de", "la", "el", "los", "las", "un", "una", "y", "o", "en", "por",
    "para", "con", "sin", "que", "se", "del", "al", "the", "of", "and",
    "to", "a", "conexin",
}


def _tokens(texto: str) -> set[str]:
    return {
        t
        for t in re.findall(r"[a-záéíóúñ_]{4,}", texto.lower())
        if t not in PALABRAS_VACIAS
    }


class AgenteResolucion(AgenteBase):
    nombre = "resolucion"
    fase = "resolucion"

    def valores_contexto(self, contexto: dict[str, Any]) -> dict[str, str]:
        return {
            "TICKET_JSON": json_compacto(contexto["ticket"]),
            "TRIAGE_JSON": json_compacto(contexto["triage"]),
            "CAUSA_RAIZ_JSON": json_compacto(contexto["causa_raiz"]),
            "QUICK_FIXES_JSON": json_compacto(contexto["quick_fixes_rechazados"]),
        }

    # ------------------------------------------------------------------
    def validar_salida(self, data: Any, contexto: dict[str, Any]) -> list[str]:
        errores: list[str] = []
        if not isinstance(data, dict):
            return [f"la salida debe ser un objeto JSON, recibido: {type(data).__name__}"]

        # immediate_mitigation ---------------------------------------------
        pasos = data.get("immediate_mitigation")
        if not isinstance(pasos, list) or not pasos:
            errores.append("immediate_mitigation: debe ser una lista con al menos un paso")
            pasos = pasos if isinstance(pasos, list) else []
        ordenes: list[int] = []
        for i, paso in enumerate(pasos):
            donde = f"immediate_mitigation[{i}]"
            if not isinstance(paso, dict):
                errores.append(f"{donde}: debe ser un objeto")
                continue
            for campo in ("step", "owner", "risk", "rollback"):
                if not isinstance(paso.get(campo), str) or not paso.get(campo, "").strip():
                    errores.append(f"{donde}.{campo}: debe ser un texto no vacío")
            order = paso.get("order")
            if not isinstance(order, int) or order < 1:
                errores.append(f"{donde}.order: debe ser un entero >= 1")
            else:
                ordenes.append(order)
            if not isinstance(paso.get("requires_change_request"), bool):
                errores.append(f"{donde}.requires_change_request: debe ser true o false")
        if ordenes and sorted(ordenes) != list(range(1, len(ordenes) + 1)):
            errores.append("immediate_mitigation: los order deben ser 1, 2, ... sin repetir")

        # permanent_fix -------------------------------------------------------
        fixes = data.get("permanent_fix")
        if not isinstance(fixes, list) or not fixes:
            errores.append("permanent_fix: debe ser una lista con al menos una acción")
        else:
            for i, fix in enumerate(fixes):
                donde = f"permanent_fix[{i}]"
                if not isinstance(fix, dict):
                    errores.append(f"{donde}: debe ser un objeto")
                    continue
                for campo in ("action", "owner", "prevents"):
                    if not isinstance(fix.get(campo), str) or not fix.get(campo, "").strip():
                        errores.append(f"{donde}.{campo}: debe ser un texto no vacío")

        # validation ------------------------------------------------------------
        criterios = data.get("validation")
        if not isinstance(criterios, list) or not criterios:
            errores.append("validation: debe ser una lista con al menos un criterio")
        else:
            for i, criterio in enumerate(criterios):
                donde = f"validation[{i}]"
                if not isinstance(criterio, dict):
                    errores.append(f"{donde}: debe ser un objeto")
                    continue
                for campo in ("criterion", "target"):
                    if not isinstance(criterio.get(campo), str) or not criterio.get(campo, "").strip():
                        errores.append(f"{donde}.{campo}: debe ser un texto no vacío")

        # PROHIBIDO: quick fixes rechazados en la fase anterior ---------------
        for paso in pasos:
            if not isinstance(paso, dict):
                continue
            texto_paso = f"{paso.get('step', '')} {paso.get('rollback', '')}"
            tokens_paso = _tokens(texto_paso)
            if not tokens_paso:
                continue
            for rechazado in contexto.get("quick_fixes_rechazados", []):
                accion = rechazado.get("action", "") if isinstance(rechazado, dict) else str(rechazado)
                tokens_rechazo = _tokens(accion)
                if not tokens_rechazo:
                    continue
                interseccion = tokens_paso & tokens_rechazo
                if interseccion and len(interseccion) / len(tokens_rechazo) >= 0.6:
                    errores.append(
                        f"PROHIBIDO: el paso {paso.get('order')} parece proponer un quick fix "
                        f"rechazado en la fase anterior: '{accion}'. Reformula el plan sin esa acción."
                    )
        return errores

    # ------------------------------------------------------------------
    def salida_mock(self, contexto: dict[str, Any]) -> dict[str, Any]:
        return {
            "immediate_mitigation": [
                {
                    "order": 1,
                    "step": "paso simulado en modo mock",
                    "owner": "AMS",
                    "risk": "riesgo simulado",
                    "rollback": "rollback simulado",
                    "requires_change_request": True,
                }
            ],
            "permanent_fix": [
                {
                    "action": "acción definitiva simulada (modo mock)",
                    "owner": "Desarrollo",
                    "prevents": "prevención simulada",
                }
            ],
            "validation": [
                {"criterion": "criterio simulado (modo mock)", "target": "objetivo simulado"}
            ],
        }
