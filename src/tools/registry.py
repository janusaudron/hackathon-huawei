"""Tools de solo lectura para los agentes (function calling).

Mínimo exigido por el reto: una tool que consulte la base de problemas
conocidos. Además: ver el detalle completo de una evidencia y consultar
los cambios recientes del ticket.

Ninguna tool modifica nada: solo leen el kit de datos del caso.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


class RegistroTools:
    """Registro de tools disponibles para los agentes, con esquemas OpenAI."""

    def __init__(
        self,
        evidencias: list[dict[str, Any]],
        ruta_problemas: Path,
        ticket: dict[str, Any],
    ):
        self.evidencias = {e["id"]: e for e in evidencias}
        self.problemas = self._cargar_problemas(ruta_problemas)
        self.ticket = ticket
        self.esquemas = [
            {
                "type": "function",
                "function": {
                    "name": "buscar_problemas_conocidos",
                    "description": (
                        "Busca en la base de problemas conocidos del equipo (KI) "
                        "las entradas cuyo patrón coincide con el texto dado. "
                        "Devuelve id, título y la guía del equipo para cada coincidencia."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "texto": {
                                "type": "string",
                                "description": (
                                    "Texto o patrón a buscar, por ejemplo un mensaje "
                                    "de error tal cual aparece en la evidencia"
                                ),
                            }
                        },
                        "required": ["texto"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "ver_detalle_evidencia",
                    "description": (
                        "Devuelve el detalle completo de una evidencia por su id: "
                        "líneas de ejemplo redactadas, plantilla, excepción, causa "
                        "y rango de tiempos."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "id": {
                                "type": "string",
                                "description": "Identificador de la evidencia, por ejemplo E012",
                            }
                        },
                        "required": ["id"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "consultar_cambios_recientes",
                    "description": (
                        "Devuelve los cambios recientes registrados en el ticket "
                        "del incidente (releases, configuraciones, etc.)."
                    ),
                    "parameters": {"type": "object", "properties": {}},
                },
            },
        ]

    # ------------------------------------------------------------------
    @staticmethod
    def _cargar_problemas(ruta: Path) -> list[dict[str, Any]]:
        if not ruta or not Path(ruta).exists():
            return []
        return json.loads(Path(ruta).read_text(encoding="utf-8"))

    def existe(self, nombre: str) -> bool:
        return nombre in {"buscar_problemas_conocidos", "ver_detalle_evidencia", "consultar_cambios_recientes"}

    # ------------------------------------------------------------------
    def ejecutar(self, nombre: str, argumentos: dict[str, Any]) -> dict[str, Any]:
        """Ejecuta una tool por nombre. Solo lectura: nunca modifica nada."""
        if nombre == "buscar_problemas_conocidos":
            return self.buscar_problemas_conocidos(str(argumentos.get("texto", "")))
        if nombre == "ver_detalle_evidencia":
            return self.ver_detalle_evidencia(str(argumentos.get("id", "")))
        if nombre == "consultar_cambios_recientes":
            return self.consultar_cambios_recientes()
        return {"error": f"tool desconocida: {nombre}"}

    # ------------------------------------------------------------------
    def buscar_problemas_conocidos(self, texto: str) -> dict[str, Any]:
        """Busca KIs cuyo patrón (regex) aparece en el texto dado."""
        resultados = []
        for ki in self.problemas:
            patron = ki.get("pattern", "")
            coincide = False
            if patron:
                try:
                    coincide = bool(re.search(patron, texto, re.IGNORECASE))
                except re.error:
                    coincide = patron.lower() in texto.lower()
            if coincide:
                resultados.append(
                    {
                        "id": ki.get("id"),
                        "title": ki.get("title"),
                        "pattern": patron,
                        "guidance": ki.get("guidance"),
                    }
                )
        if not resultados:
            return {
                "resultados": [],
                "mensaje": "ningún problema conocido coincide con el texto dado",
            }
        return {"resultados": resultados}

    def ver_detalle_evidencia(self, id_: str) -> dict[str, Any]:
        e = self.evidencias.get(id_)
        if not e:
            return {"error": f"no existe la evidencia {id_!r}"}
        return {
            "id": e["id"],
            "source": e["source"],
            "level": e["level"],
            "count": e["count"],
            "time_first": e["time_first"],
            "time_last": e["time_last"],
            "template": e.get("template", ""),
            "exception": e.get("exception"),
            "caused_by": e.get("caused_by"),
            "samples": e.get("samples", []),
            "lineas_log": e.get("lineas_log", []),
        }

    def consultar_cambios_recientes(self) -> dict[str, Any]:
        return {"cambios": self.ticket.get("recent_changes", [])}
