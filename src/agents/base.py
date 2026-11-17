"""Clase base de los agentes.

Cada agente:

- Carga su prompt desde ``prompts/<nombre>.md`` (la bitácora es la fuente
  de verdad de los prompts). El archivo tiene dos secciones separadas por
  ``---SYSTEM---`` y ``---USUARIO---``; la sección de usuario admite
  placeholders ``%%NOMBRE%%``.
- Ejecuta un bucle: construir mensajes -> llamar a MaaS -> limpiar JSON ->
  validar contrato -> reintentar con retroalimentación si hay errores.
- Registra en auditoría cada intento (prompt, respuesta, duración, tokens).
- En modo mock (sin clave) devuelve una salida válida genérica para poder
  probar la tubería completa.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from src.audit import Auditoria
from src.config import CARPETA_PROMPTS
from src.maas_client import ClienteMaaS
from src.validation import JSONInvalido, limpiar_json


class ErrorAgente(Exception):
    """El agente no pudo producir una salida válida tras los reintentos."""


class AgenteBase:
    """Base común de todos los agentes del sistema."""

    #: nombre corto (usado en prompts/<nombre>.md y en auditoría)
    nombre: str = "agente"
    #: nombre de la fase a la que pertenece
    fase: str = ""

    # ------------------------------------------------------------------
    # Carga de prompts
    # ------------------------------------------------------------------
    def cargar_prompt(self) -> tuple[str, str]:
        """Devuelve (system, usuario) desde prompts/<nombre>.md."""
        ruta = Path(CARPETA_PROMPTS) / f"{self.nombre}.md"
        if not ruta.exists():
            raise FileNotFoundError(f"falta el archivo de prompt: {ruta}")
        texto = ruta.read_text(encoding="utf-8")
        if "---SYSTEM---" not in texto or "---USUARIO---" not in texto:
            raise ValueError(
                f"{ruta} debe tener secciones ---SYSTEM--- y ---USUARIO---"
            )
        system, usuario = texto.split("---USUARIO---", 1)
        system = system.split("---SYSTEM---", 1)[1]
        return system.strip(), usuario.strip()

    @staticmethod
    def rellenar(plantilla: str, valores: dict[str, str]) -> str:
        """Sustituye placeholders %%NOMBRE%% en la plantilla de usuario."""
        resultado = plantilla
        for clave, valor in valores.items():
            resultado = resultado.replace(f"%%{clave}%%", valor)
        return resultado

    # ------------------------------------------------------------------
    # Interfaz que implementa cada agente
    # ------------------------------------------------------------------
    def construir_mensajes(self, contexto: dict[str, Any]) -> list[dict[str, Any]]:
        """Construye los mensajes (system + user) para el modelo."""
        system, usuario = self.cargar_prompt()
        contenido = self.rellenar(usuario, self.valores_contexto(contexto))
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": contenido},
        ]

    def valores_contexto(self, contexto: dict[str, Any]) -> dict[str, str]:
        """Placeholders %%...%% disponibles en la sección de usuario."""
        return {}

    def validar_salida(self, data: Any, contexto: dict[str, Any]) -> list[str]:
        """Devuelve la lista de errores de contrato (vacía = válida)."""
        return []

    def salida_mock(self, contexto: dict[str, Any]) -> Any:
        """Salida válida genérica cuando no hay clave de MaaS."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Bucle de ejecución
    # ------------------------------------------------------------------
    def ejecutar(
        self,
        contexto: dict[str, Any],
        cliente: ClienteMaaS | None,
        auditoria: Auditoria,
    ) -> Any:
        """Ejecuta el agente con reintentos ante JSON inválido o contrato roto."""
        # Modo mock: sin modelo, salida genérica (para probar la tubería).
        if cliente is None:
            auditoria.evento("mock", self.fase, f"{self.nombre}: salida simulada")
            return self.salida_mock(contexto)

        errores_previos: list[str] = []
        ultimo_error: str = ""

        for intento in range(1, self._max_reintentos(cliente) + 1):
            messages = self.construir_mensajes(contexto)
            if errores_previos:
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Tu respuesta anterior fue rechazada por estas razones:\n- "
                            + "\n- ".join(errores_previos)
                            + "\nCorrige esos problemas y devuelve de nuevo SOLO el JSON "
                            "válido que cumple el contrato, sin texto adicional."
                        ),
                    }
                )

            inicio = time.monotonic()
            try:
                respuesta = cliente.chat(messages)
            except Exception as exc:  # red / HTTP: se registra y se reintenta
                auditoria.llamada_modelo(
                    fase=self.fase,
                    agente=self.nombre,
                    intento=intento,
                    messages=messages,
                    respuesta=None,
                    duracion_s=time.monotonic() - inicio,
                    error=f"error de comunicación: {exc}",
                )
                ultimo_error = f"error de comunicación: {exc}"
                errores_previos = [ultimo_error]
                continue

            duracion = time.monotonic() - inicio
            texto = cliente.texto_respuesta(respuesta)
            auditoria.llamada_modelo(
                fase=self.fase,
                agente=self.nombre,
                intento=intento,
                messages=messages,
                respuesta=texto,
                duracion_s=duracion,
                usage=cliente.usage(respuesta),
            )

            try:
                data = limpiar_json(texto)
            except JSONInvalido as exc:
                errores_previos = [
                    f"la respuesta no es JSON válido ({exc}); "
                    "devuelve SOLO el JSON, sin markdown ni texto extra"
                ]
                ultimo_error = errores_previos[0]
                continue

            errores = self.validar_salida(data, contexto)
            if not errores:
                return data

            errores_previos = errores
            ultimo_error = "; ".join(errores[:5])

        raise ErrorAgente(
            f"{self.nombre}: sin salida válida tras {self._max_reintentos(cliente)} intentos. "
            f"Último error: {ultimo_error}"
        )

    @staticmethod
    def _max_reintentos(cliente: ClienteMaaS) -> int:
        return max(1, cliente.config.max_reintentos)


def json_compacto(obj: Any) -> str:
    """Serializa a JSON compacto para incrustar en prompts."""
    return json.dumps(obj, ensure_ascii=False, indent=1)
