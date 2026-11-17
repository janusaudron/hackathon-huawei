"""Parsers de logs: reconocen fecha/hora, nivel y mensaje de cada fuente.

Cada parser devuelve una lista de :class:`EntradaLog` con:

- ``tiempo``: "YYYY-MM-DD HH:MM:SS" (normalizado entre fuentes)
- ``nivel``: INFO | WARN | ERROR | DEBUG | TRACE | UNKNOWN
- ``mensaje``: texto redactado (sin datos sensibles)
- ``stack``: líneas de stack trace unidas a su entrada (si aplica)

Los formatos reconocidos son los estándar de cada tecnología:

- Spring Boot (``2026-10-20 13:40:00.534  INFO 4121 --- [hilo] Logger : msg``)
- Monitor de Oracle (``2026-10-20 13:40:00 INFO  [SHOPDB] msg``)
- Nginx access combined (``IP - - [20/Oct/2026:13:40:00 +0000] "GET ..." 200 ...``)
- Nginx error (``2026/10/20 13:54:41 [error] 811#811: ...``)

Cualquier línea que no matchee ninguno se conserva como entrada genérica
(nivel UNKNOWN), de modo que el sistema funciona con otros logs sin
cambiar código. Las líneas de stack trace se unen a la entrada anterior.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from src.evidence.redaction import (
    ConteoRedaccion,
    RegistroInyecciones,
    neutralizar_instrucciones,
    redactar,
)

# ----------------------------------------------------------------------
# Modelos
# ----------------------------------------------------------------------
@dataclass
class EntradaLog:
    """Una línea de log (o una entrada con su stack trace) ya protegida."""

    fuente: str          # nombre del archivo
    tiempo: str          # "YYYY-MM-DD HH:MM:SS"
    nivel: str
    mensaje: str         # redactado y con inyecciones neutralizadas
    stack: list[str] = field(default_factory=list)
    linea_original: int = 0

    @property
    def excepcion(self) -> str | None:
        """Tipo de excepción principal del stack, si existe."""
        for linea in self.stack:
            m = re.match(r"^([\w.$]+(?:Exception|Error))", linea)
            if m:
                return m.group(1)
        m = re.match(r"^([\w.$]+(?:Exception|Error))\s*:", self.mensaje)
        if m:
            return m.group(1)
        return None

    @property
    def causa(self) -> str | None:
        """La línea 'Caused by: ...' del stack, si existe."""
        for linea in self.stack:
            if linea.startswith("Caused by:"):
                return linea
        return None


# ----------------------------------------------------------------------
# Expresiones regulares de cada formato
# ----------------------------------------------------------------------
RE_SPRING = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?)\s+"
    r"(?P<nivel>TRACE|DEBUG|INFO|WARN|ERROR)\s+"
    r"\d+\s+---\s+\[[^\]]*\]\s+[\w.$]+\s*:\s*(?P<msg>.*)$"
)
RE_ORACLE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+"
    r"(?P<nivel>TRACE|DEBUG|INFO|WARN|ERROR)\s+\[[^\]]+\]\s*(?P<msg>.*)$"
)
RE_NGINX_ACCESS = re.compile(
    r"^\S+ \S+ \S+ \[(?P<ts>[^\]]+)\] "
    r'"(?P<metodo>[A-Z]+) (?P<ruta>[^ "]+)[^"]*" '
    r"(?P<status>\d{3}) (?P<bytes>\d+) "
    r'"[^"]*" "[^"]*"(?P<extra>.*)$'
)
RE_NGINX_ERROR = re.compile(
    r"^(?P<ts>\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2}) "
    r"\[(?P<nivel>\w+)\] [^:]+: (?P<msg>.*)$"
)

FMT_ORACLE_TS = "%Y-%m-%d %H:%M:%S"
FMT_SPRING_TS = "%Y-%m-%d %H:%M:%S.%f"
FMT_NGINX_ACCESS_TS = "%d/%b/%Y:%H:%M:%S %z"
FMT_NGINX_ERROR_TS = "%Y/%m/%d %H:%M:%S"


def _normalizar_tiempo(texto: str, formatos: list[str]) -> str:
    """Parsea un timestamp con cualquiera de los formatos dados."""
    for fmt in formatos:
        try:
            dt = datetime.strptime(texto.strip(), fmt)
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    raise ValueError(f"timestamp no reconocido: {texto!r}")


def _nivel_desde_status(status: int) -> str:
    if status >= 500:
        return "ERROR"
    if status >= 400:
        return "WARN"
    return "INFO"


# ----------------------------------------------------------------------
# Parseo de un archivo
# ----------------------------------------------------------------------
def parsear_archivo(
    ruta: Path,
    conteo: ConteoRedaccion,
    registro_inyecciones: RegistroInyecciones,
) -> tuple[list[EntradaLog], int]:
    """Lee un archivo de log y devuelve (entradas protegidas, líneas físicas).

    Las líneas físicas incluyen los stack traces (que quedan unidos a su
    entrada); la métrica de "líneas originales" del reto cuenta líneas
    físicas del archivo, no entradas.

    El orden del pipeline por línea es SIEMPRE:
    1. redactar datos sensibles
    2. neutralizar instrucciones inyectadas
    3. parsear formato y construir la entrada
    """
    entradas: list[EntradaLog] = []
    fuente = ruta.name
    texto = ruta.read_text(encoding="utf-8", errors="replace")
    lineas_fisicas = 0

    for numero, linea in enumerate(texto.splitlines(), start=1):
        if not linea.strip():
            continue
        lineas_fisicas += 1
        # 1) protección de datos sensibles (determinista, antes del modelo)
        protegida = redactar(linea, conteo)
        # 2) neutralización de inyecciones
        protegida = neutralizar_instrucciones(protegida, registro_inyecciones)
        # 3) parseo
        entrada = _parsear_linea(protegida, fuente, numero, entradas)
        if entrada is not None:
            entradas.append(entrada)

    return entradas, lineas_fisicas


def _parsear_linea(
    linea: str, fuente: str, numero: int, entradas: list[EntradaLog]
) -> EntradaLog | None:
    """Convierte una línea (ya protegida) en entrada, o la une al stack previo."""
    # --- Spring Boot -------------------------------------------------
    m = RE_SPRING.match(linea)
    if m:
        return EntradaLog(
            fuente=fuente,
            tiempo=_normalizar_tiempo(m.group("ts"), [FMT_SPRING_TS, FMT_ORACLE_TS]),
            nivel=m.group("nivel"),
            mensaje=m.group("msg").strip(),
            linea_original=numero,
        )

    # --- Monitor de Oracle -------------------------------------------
    m = RE_ORACLE.match(linea)
    if m:
        return EntradaLog(
            fuente=fuente,
            tiempo=m.group("ts"),
            nivel=m.group("nivel"),
            mensaje=m.group("msg").strip(),
            linea_original=numero,
        )

    # --- Nginx access -------------------------------------------------
    m = RE_NGINX_ACCESS.match(linea)
    if m:
        tiempo = _normalizar_tiempo(m.group("ts"), [FMT_NGINX_ACCESS_TS])
        status = int(m.group("status"))
        extra = m.group("extra").strip()
        mensaje = (
            f'"{m.group("metodo")} {m.group("ruta")} HTTP/1.1" '
            f"status={status} bytes={m.group('bytes')}"
            + (f" {extra}" if extra else "")
        )
        return EntradaLog(
            fuente=fuente,
            tiempo=tiempo,
            nivel=_nivel_desde_status(status),
            mensaje=mensaje,
            linea_original=numero,
        )

    # --- Nginx error ---------------------------------------------------
    m = RE_NGINX_ERROR.match(linea)
    if m:
        nivel = m.group("nivel").upper()
        if nivel not in {"ERROR", "WARN", "DEBUG", "INFO"}:
            nivel = "ERROR" if nivel in {"CRIT", "ALERT", "EMERG"} else "INFO"
        return EntradaLog(
            fuente=fuente,
            tiempo=_normalizar_tiempo(m.group("ts"), [FMT_NGINX_ERROR_TS]),
            nivel=nivel,
            mensaje=m.group("msg").strip(),
            linea_original=numero,
        )

    # --- Stack trace: unir a la entrada anterior ------------------------
    if entradas and _parece_stack(linea):
        entradas[-1].stack.append(linea.rstrip())
        return None

    # --- Formato desconocido: entrada genérica --------------------------
    return EntradaLog(
        fuente=fuente,
        tiempo="",
        nivel="UNKNOWN",
        mensaje=linea.strip(),
        linea_original=numero,
    )


def _parece_stack(linea: str) -> bool:
    """Heurística: líneas típicas de stack trace de Java."""
    l = linea.lstrip()
    if l.startswith("at ") or l.startswith("Caused by:") or l.startswith("... "):
        return True
    if re.match(r"^[\w.$]+(?:Exception|Error)\b", l):
        return True
    return False
