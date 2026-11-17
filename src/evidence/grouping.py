"""Reducción del ruido: agrupación de mensajes repetidos y tendencias.

De cada entrada se calcula una **plantilla**: el mensaje con los números
normalizados a ``<N>`` (conservando los códigos que importan, como los
status HTTP y los códigos ORA-). Las entradas con la misma plantilla,
nivel y firma de excepción forman un **grupo de evidencia** con:

- ``count``: cuántas veces apareció
- ``time_first`` / ``time_last``: primera y última hora
- ``sample``: una línea de ejemplo (redactada)

Las métricas numéricas (duraciones, sesiones, esperas...) se extraen ANTES
de normalizar y forman las **tendencias**: series temporales por métrica.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from src.evidence.parsers import EntradaLog

# ----------------------------------------------------------------------
# Normalización a plantilla
# ----------------------------------------------------------------------
# Códigos que se conservan tal cual (aportan semántica):
RE_STATUS = re.compile(r"(?<=status=)\d+")
RE_ORA = re.compile(r"\bORA-\d+\b")
RE_HTTP = re.compile(r"\bHTTP/\d(?:\.\d)?\b")


def plantilla_de(mensaje: str) -> str:
    """Normaliza un mensaje redactado a su plantilla genérica."""
    protegidos: list[str] = []

    def _proteger(m: re.Match) -> str:
        # Placeholder SIN dígitos: sobrevive a la normalización de números.
        protegidos.append(m.group(0))
        return f"\x00{_marca(len(protegidos) - 1)}\x00"

    msg = mensaje
    # 1) proteger códigos con semántica (status HTTP, ORA-xxxxx, HTTP/x.y)
    msg = RE_STATUS.sub(_proteger, msg)
    msg = RE_ORA.sub(_proteger, msg)
    msg = RE_HTTP.sub(_proteger, msg)
    # 2) ids en rutas: /api/products/2824 -> /api/products/<ID>
    #    (solo si el segmento anterior empieza por letra: no toca active=20/20)
    msg = re.sub(r"([A-Za-z][A-Za-z0-9_.\-]*/)(\d+)", r"\1<ID>", msg)
    # 3) ids con prefijo: ORD-884001 -> ORD-<ID>, CHG-55821 -> CHG-<ID>
    msg = re.sub(r"([A-Za-z]{2,}-)\d+", r"\1<ID>", msg)
    # 4) el resto de números -> <N>
    msg = re.sub(r"\d+(?:\.\d+)?", "<N>", msg)
    # 5) restaurar protegidos
    for i, original in enumerate(protegidos):
        msg = msg.replace(f"\x00{_marca(i)}\x00", original)
    # colapsar espacios
    msg = re.sub(r"\s+", " ", msg).strip()
    return msg


def _marca(indice: int) -> str:
    """Marca única sin dígitos para el i-ésimo protegido (A, B, ..., Z, AA...)."""
    letras = ""
    n = indice
    while True:
        letras = chr(65 + n % 26) + letras
        n = n // 26 - 1
        if n < 0:
            return letras


# ----------------------------------------------------------------------
# Extracción de métricas (sobre el mensaje redactado, antes de normalizar)
# ----------------------------------------------------------------------
METRICAS: list[tuple[str, re.Pattern]] = [
    ("duracion_peticion_ms", re.compile(r"\bcompleted in (\d+)ms")),
    ("duracion_consulta_ms", re.compile(r"\btook (\d+)ms")),
    ("sql_segundos", re.compile(r"\belapsed=([\d.]+)s")),
    ("pool_conexiones_activas", re.compile(r"\bactive=(\d+)/")),
    ("pool_en_espera", re.compile(r"\bwaiting=(\d+)")),
    ("sesiones_activas", re.compile(r"\bactive_sessions=(\d+)")),
    ("cpu_pct", re.compile(r"\bcpu=(\d+)")),
    ("espera_media_ms", re.compile(r"\bavg_wait_ms=(\d+)")),
    ("tiempo_respuesta_proxy_s", re.compile(r"\brt=([\d.]+)")),
    ("filas_leidas", re.compile(r"\brows_scanned=(\d+)")),
]


def extraer_metricas(mensaje: str) -> list[tuple[str, float]]:
    """Devuelve [(metrica, valor), ...] presentes en un mensaje redactado."""
    hallazgos: list[tuple[str, float]] = []
    for nombre, patron in METRICAS:
        m = patron.search(mensaje)
        if m:
            try:
                hallazgos.append((nombre, float(m.group(1))))
            except ValueError:
                pass
    return hallazgos


# ----------------------------------------------------------------------
# Agrupación
# ----------------------------------------------------------------------
def agrupar(entradas: list[EntradaLog]) -> list[dict[str, Any]]:
    """Agrupa entradas por (fuente, nivel, plantilla, excepción, causa).

    Devuelve grupos ordenados por primera aparición, con ids E001, E002...
    Cada grupo incluye las series de métricas de sus entradas.
    """
    grupos: dict[tuple, dict[str, Any]] = {}
    orden: list[tuple] = []

    for entrada in entradas:
        plantilla = plantilla_de(entrada.mensaje)
        clave = (
            entrada.fuente,
            entrada.nivel,
            plantilla,
            entrada.excepcion or "",
            entrada.causa or "",
        )
        if clave not in grupos:
            grupos[clave] = {
                "fuente": entrada.fuente,
                "nivel": entrada.nivel,
                "plantilla": plantilla,
                "excepcion": entrada.excepcion,
                "causa": entrada.causa,
                "count": 0,
                "tiempos": [],
                "samples": [],
                "metricas": defaultdict(list),  # metrica -> [(tiempo, valor)]
                "lineas_originales": [],
            }
            orden.append(clave)
        g = grupos[clave]
        g["count"] += 1
        if entrada.tiempo:
            g["tiempos"].append(entrada.tiempo)
        if len(g["samples"]) < 3 or len(g["samples"]) == g["count"] - 1:
            # guarda las 3 primeras y la última línea
            if len(g["samples"]) < 3:
                g["samples"].append(_sample_de(entrada))
        g["lineas_originales"].append(entrada.linea_original)
        for metrica, valor in extraer_metricas(entrada.mensaje):
            g["metricas"][metrica].append((entrada.tiempo, valor))

    resultado: list[dict[str, Any]] = []
    for i, clave in enumerate(orden, start=1):
        g = grupos[clave]
        tiempos = sorted(g["tiempos"])
        resultado.append(
            {
                "id": f"E{i:03d}",
                "source": g["fuente"],
                "level": g["nivel"],
                "template": g["plantilla"],
                "exception": g["excepcion"],
                "caused_by": g["causa"],
                "count": g["count"],
                "time_first": tiempos[0] if tiempos else "",
                "time_last": tiempos[-1] if tiempos else "",
                "samples": g["samples"],
                "lineas_originales": g["lineas_originales"],
                "series_metricas": {
                    k: sorted(v) for k, v in g["metricas"].items()
                },
            }
        )
    return resultado


def _sample_de(entrada: EntradaLog) -> str:
    """Línea de ejemplo legible (redactada), con stack resumido."""
    partes = [entrada.mensaje]
    if entrada.excepcion:
        partes.append(entrada.excepcion)
    if entrada.causa:
        partes.append(entrada.causa)
    muestra = " | ".join(p for p in partes if p)
    return muestra[:400]


# ----------------------------------------------------------------------
# Tendencias
# ----------------------------------------------------------------------
def construir_tendencias(grupos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convierte las series de métricas de los grupos en tendencias.

    Una tendencia describe UNA métrica de UNA fuente a lo largo del
    tiempo: primer valor, último, máximo y dirección. Solo se conservan
    las que cambian (o cuyo rango es notable), para no llenar de ruido.
    """
    tendencias: list[dict[str, Any]] = []
    for grupo in grupos:
        for metrica, serie in grupo["series_metricas"].items():
            if len(serie) < 3:
                continue
            valores = [v for _, v in serie]
            primero, ultimo = valores[0], valores[-1]
            maximo, minimo = max(valores), min(valores)
            if primero == 0:
                direccion = "creciente" if ultimo > 0 else "estable"
            elif ultimo > primero * 1.10:
                direccion = "creciente"
            elif ultimo < primero * 0.90:
                direccion = "decreciente"
            else:
                direccion = "estable"
            rango = maximo / minimo if minimo else float("inf")
            if direccion == "estable" and rango < 2:
                continue  # métrica plana: no aporta
            puntos = _muestrear(serie, maximo=8)
            tendencias.append(
                {
                    "metric": metrica,
                    "source": grupo["source"],
                    "evidence_id": grupo["id"],
                    "first": primero,
                    "last": ultimo,
                    "max": maximo,
                    "direction": direccion,
                    "points": puntos,
                }
            )
    # las crecientes/decrecientes primero, luego por rango
    tendencias.sort(
        key=lambda t: (t["direction"] == "estable", -(t["max"] / (t["first"] or 1)))
    )
    return tendencias[:15]


def _muestrear(serie: list[tuple[str, float]], maximo: int) -> list[list[object]]:
    """Reduce una serie a ``maximo`` puntos [tiempo, valor]."""
    if len(serie) <= maximo:
        return [[t, v] for t, v in serie]
    paso = (len(serie) - 1) / (maximo - 1)
    indices = {round(i * paso) for i in range(maximo)}
    return [[serie[i][0], serie[i][1]] for i in sorted(indices)]
