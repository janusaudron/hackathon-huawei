"""Configuración del sistema por variables de entorno.

La clave de MaaS vive SOLO en variables de entorno (archivo .env, fuera del
repositorio). Este módulo es el único lugar que la lee.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

#: Raíz del proyecto (ams-incident-analyst/)
RAIZ_PROYECTO = Path(__file__).resolve().parent.parent

#: Carpeta con los prompts (bitácora) de los agentes
CARPETA_PROMPTS = RAIZ_PROYECTO / "prompts"

#: Carpeta de salida de casos procesados
CARPETA_CASOS = RAIZ_PROYECTO / "casos"

# Carga el .env si existe (no falla si no está).
load_dotenv(RAIZ_PROYECTO / ".env")


@dataclass(frozen=True)
class Config:
    """Configuración efectiva del sistema."""

    endpoint: str
    modelo: str
    api_key: str
    timeout: int
    temperature: float
    max_reintentos: int
    verify_ssl: bool
    mock: bool

    @property
    def listo(self) -> bool:
        """True si hay clave configurada para llamadas reales."""
        return bool(self.api_key)


def cargar_config() -> Config:
    """Lee la configuración desde variables de entorno con valores por defecto."""
    return Config(
        endpoint=os.getenv(
            "MAAS_ENDPOINT",
            "https://api-ap-southeast-1.modelarts-maas.com/v2/chat/completions",
        ),
        modelo=os.getenv("MAAS_MODEL", "glm-5.2"),
        api_key=os.getenv("MAAS_API_KEY", ""),
        timeout=int(os.getenv("MAAS_TIMEOUT", "300")),
        temperature=float(os.getenv("MAAS_TEMPERATURE", "0.1")),
        max_reintentos=int(os.getenv("MAAS_MAX_REINTENTOS", "3")),
        verify_ssl=os.getenv("MAAS_VERIFY_SSL", "true").strip().lower() == "true",
        mock=os.getenv("MAAS_MOCK", "0").strip() == "1",
    )
