"""Cliente reutilizable de Huawei Cloud MaaS (API compatible con OpenAI).

Es el único módulo que habla con el endpoint. Recibe mensajes y devuelve la
respuesta del modelo. Características:

- Clave siempre desde variables de entorno, nunca en el código.
- Timeout amplio (una fase puede tardar 1-3 minutos).
- Reintentos con espera ante 429 / 5xx / timeouts de red.
- Soporte de ``tools`` (function calling) con detección de endpoints que no
  lo aceptan, para que el agente pueda degradar a un protocolo por prompt.
"""
from __future__ import annotations

import json
import time
from typing import Any, Callable, Optional

import requests

from src.config import Config


class ErrorMaaS(Exception):
    """Error al comunicarse con MaaS."""


class ErrorToolsNoSoportadas(ErrorMaaS):
    """El endpoint rechazó el parámetro ``tools``."""


def normalizar_endpoint(endpoint: str) -> str:
    """Acepta tanto la URL completa como la base estilo OpenAI.

    - ``https://host/v2/chat/completions``  -> igual (URL completa)
    - ``http://host:4000/v1``               -> ``http://host:4000/v1/chat/completions``
    """
    e = (endpoint or "").strip().rstrip("/")
    if not e:
        raise ErrorMaaS("MAAS_ENDPOINT no está configurado")
    if e.endswith("/chat/completions"):
        return e
    return e + "/chat/completions"


class ClienteMaaS:
    """Cliente mínimo y robusto para el endpoint de chat completions de MaaS."""

    def __init__(self, config: Config):
        self.config = config
        self.endpoint = normalizar_endpoint(config.endpoint)
        self.sesion = requests.Session()
        #: Callback opcional (fase, agente, evento) para notificar progreso.
        self.on_evento: Optional[Callable[[str, str, str], None]] = None

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------
    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        temperature: Optional[float] = None,
    ) -> dict[str, Any]:
        """Envía una conversación y devuelve la respuesta JSON completa.

        ``messages`` usa el formato OpenAI (system/user/assistant/tool).
        Si ``tools`` se pasa y el endpoint no lo soporta, lanza
        :class:`ErrorToolsNoSoportadas` para que el agente degradue.
        """
        if not self.config.listo:
            raise ErrorMaaS(
                "Falta MAAS_API_KEY. Configúrala en el archivo .env "
                "(copia .env.example). Nunca la escribas en el código."
            )

        payload: dict[str, Any] = {
            "model": self.config.modelo,
            "messages": messages,
            "temperature": self.config.temperature if temperature is None else temperature,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        ultimo_error: Exception | None = None
        for intento in range(3):  # 1 intento + 2 reintentos
            if intento:
                espera = 5 * intento
                self._notificar(f"reintento {intento} tras error ({ultimo_error}); espera {espera}s")
                time.sleep(espera)
            try:
                respuesta = self._post(payload)
                if tools:
                    self._verificar_rechazo_de_tools(respuesta)
                return respuesta
            except ErrorToolsNoSoportadas:
                raise  # no se reintenta: es determinista
            except ErrorMaaS as exc:
                ultimo_error = exc
        raise ErrorMaaS(f"MaaS no respondió correctamente: {ultimo_error}")

    def texto_respuesta(self, respuesta: dict[str, Any]) -> str:
        """Extrae el texto de la primera elección de la respuesta."""
        try:
            contenido = respuesta["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ErrorMaaS(f"Respuesta sin contenido de texto: {exc}") from exc
        if contenido is None:
            # Puede ocurrir cuando el modelo solo hizo tool calls.
            return ""
        return str(contenido)

    def usage(self, respuesta: dict[str, Any]) -> dict[str, Any]:
        """Devuelve el uso de tokens reportado por el endpoint (si viene)."""
        return respuesta.get("usage") or {}

    # ------------------------------------------------------------------
    # Interno
    # ------------------------------------------------------------------
    def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.api_key}",
        }
        try:
            resp = self.sesion.post(
                self.endpoint,
                headers=headers,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                timeout=self.config.timeout,
                verify=self.config.verify_ssl,
            )
        except requests.Timeout as exc:
            raise ErrorMaaS(f"timeout tras {self.config.timeout}s") from exc
        except requests.RequestException as exc:
            raise ErrorMaaS(f"error de red: {exc}") from exc

        if resp.status_code in (429, 500, 502, 503, 504):
            raise ErrorMaaS(f"HTTP {resp.status_code}: {resp.text[:300]}")
        if resp.status_code in (400, 401, 403, 404, 422):
            raise ErrorMaaS(f"HTTP {resp.status_code}: {resp.text[:500]}")
        if not resp.ok:
            raise ErrorMaaS(f"HTTP {resp.status_code}: {resp.text[:300]}")
        try:
            return resp.json()
        except ValueError as exc:
            raise ErrorMaaS(f"respuesta no es JSON: {resp.text[:300]}") from exc

    def _verificar_rechazo_de_tools(self, respuesta: dict[str, Any]) -> None:
        """Detecta respuestas de error serializadas por algunos gateways.

        Algunos endpoints devuelven 200 con un cuerpo de error cuando no
        soportan ``tools``; otros devuelven 400 (que _post ya reporta).
        Aquí cubrimos el caso 200 con cuerpo de error.
        """
        if "choices" in respuesta:
            return
        cuerpo = json.dumps(respuesta, ensure_ascii=False)
        if "tool" in cuerpo.lower():
            raise ErrorToolsNoSoportadas(cuerpo[:300])

    def _notificar(self, mensaje: str) -> None:
        if self.on_evento:
            self.on_evento("cliente", "ClienteMaaS", mensaje)


def crear_cliente(config: Config) -> ClienteMaaS:
    """Fábrica simple del cliente."""
    return ClienteMaaS(config)
