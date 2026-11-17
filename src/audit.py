"""Auditoría del sistema: cada prompt, cada respuesta, tiempos y tokens.

Escribe un registro JSONL por caso: una línea por evento. Los eventos de
llamada al modelo guardan el prompt completo, la respuesta cruda, la
duración, los tokens y los errores de validación que haya habido.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class Auditoria:
    """Registro de auditoría JSONL de un caso."""

    def __init__(self, ruta: Path):
        self.ruta = Path(ruta)
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        self._archivo = self.ruta.open("a", encoding="utf-8")

    # ------------------------------------------------------------------
    def evento(self, tipo: str, fase: str = "", detalle: Any = None) -> None:
        """Registra un evento genérico (inicio de fase, error, aviso...)."""
        self._escribir(
            {
                "ts": _ahora(),
                "tipo": tipo,
                "fase": fase,
                "detalle": detalle,
            }
        )

    def llamada_modelo(
        self,
        fase: str,
        agente: str,
        intento: int,
        messages: list[dict[str, Any]],
        respuesta: Any,
        duracion_s: float,
        usage: dict[str, Any] | None = None,
        error: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Registra una llamada al modelo con prompt y respuesta completos."""
        entrada = {
            "ts": _ahora(),
            "tipo": "llamada_modelo",
            "fase": fase,
            "agente": agente,
            "intento": intento,
            "duracion_s": round(duracion_s, 2),
            "tokens_entrada": (usage or {}).get("prompt_tokens"),
            "tokens_salida": (usage or {}).get("completion_tokens"),
            "tokens_total": (usage or {}).get("total_tokens"),
            "prompt": messages,
            "respuesta": respuesta,
        }
        if error:
            entrada["error"] = error
        if extra:
            entrada.update(extra)
        self._escribir(entrada)

    def llamada_tool(
        self,
        fase: str,
        agente: str,
        nombre: str,
        argumentos: dict[str, Any],
        resultado: Any,
        error: str | None = None,
    ) -> None:
        """Registra la ejecución de una tool: qué se llamó, con qué argumentos y qué devolvió."""
        self._escribir(
            {
                "ts": _ahora(),
                "tipo": "llamada_tool",
                "fase": fase,
                "agente": agente,
                "tool": nombre,
                "argumentos": argumentos,
                "resultado": resultado,
                "error": error,
            }
        )

    def cerrar(self) -> None:
        try:
            self._archivo.close()
        except OSError:
            pass

    # ------------------------------------------------------------------
    def _escribir(self, registro: dict[str, Any]) -> None:
        try:
            self._archivo.write(
                json.dumps(registro, ensure_ascii=False, default=str) + "\n"
            )
            self._archivo.flush()
        except OSError:
            # La auditoría nunca debe tumbar el flujo.
            pass


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Cronometro:
    """Cronómetro simple para medir fases."""

    def __init__(self):
        self._inicio = time.monotonic()

    def segundos(self) -> float:
        return time.monotonic() - self._inicio
