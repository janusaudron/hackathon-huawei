"""Limpieza y validación de las respuestas JSON del modelo.

El modelo puede devolver JSON con fences de markdown, texto extra o
comentarios. Aquí se limpia y se extrae el primer bloque JSON balanceado.
Además, utilidades de validación de contratos compartidas por los agentes.
"""
from __future__ import annotations

import json
import re
from typing import Any


class JSONInvalido(ValueError):
    """La respuesta del modelo no contiene JSON utilizable."""


def limpiar_json(texto: str | None) -> Any:
    """Extrae y parsea el primer objeto o array JSON de una respuesta.

    Estrategia (de menor a mayor agresividad):
    1. Parsear el texto tal cual.
    2. Quitar fences de markdown (```json ... ```).
    3. Buscar el primer bloque ``{...}`` o ``[...]]`` balanceado y parsearlo.
    """
    if not texto or not texto.strip():
        raise JSONInvalido("respuesta vacía")

    s = texto.strip()

    # 1) directo
    try:
        return json.loads(s)
    except ValueError:
        pass

    # 2) quitar fences
    s2 = re.sub(r"^```[A-Za-z0-9_-]*\s*", "", s)
    s2 = re.sub(r"\s*```\s*$", "", s2).strip()
    try:
        return json.loads(s2)
    except ValueError:
        pass

    # 3) primer bloque balanceado
    for i, car in enumerate(s2):
        if car not in "{[":
            continue
        cierra = "}" if car == "{" else "]"
        candidato = _bloque_balanceado(s2, i, car, cierra)
        if candidato is None:
            continue
        try:
            return json.loads(candidato)
        except ValueError:
            continue

    raise JSONInvalido(f"no se encontró JSON válido en: {texto[:200]!r}")


def _bloque_balanceado(s: str, inicio: int, abre: str, cierra: str) -> str | None:
    """Devuelve el substring que va de ``inicio`` hasta su cierre balanceado."""
    profundidad = 0
    en_cadena = False
    escape = False
    for j in range(inicio, len(s)):
        c = s[j]
        if en_cadena:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                en_cadena = False
            continue
        if c == '"':
            en_cadena = True
        elif c == abre:
            profundidad += 1
        elif c == cierra:
            profundidad -= 1
            if profundidad == 0:
                return s[inicio : j + 1]
    return None


# ----------------------------------------------------------------------
# Utilidades de validación de contratos
# ----------------------------------------------------------------------
ID_EVIDENCIA = re.compile(r"^E\d{3,}$")


def es_id_evidencia(valor: Any) -> bool:
    return isinstance(valor, str) and bool(ID_EVIDENCIA.fullmatch(valor))


def validar_ids_evidencia(
    ids: Any, validos: set[str], donde: str, errores: list[str]
) -> None:
    """Agrega a ``errores`` los ids de evidencia que no existen."""
    if not isinstance(ids, list) or not ids:
        errores.append(f"{donde}: debe ser una lista no vacía de ids de evidencia")
        return
    for id_ in ids:
        if not es_id_evidencia(id_) or id_ not in validos:
            errores.append(f"{donde}: id de evidencia inexistente: {id_!r}")


def requerir_cadena(obj: Any, campo: str, donde: str, errores: list[str]) -> None:
    valor = obj.get(campo) if isinstance(obj, dict) else None
    if not isinstance(valor, str) or not valor.strip():
        errores.append(f"{donde}.{campo}: debe ser un texto no vacío")


def requerir_lista(obj: Any, campo: str, donde: str, errores: list[str]) -> list[Any]:
    valor = obj.get(campo) if isinstance(obj, dict) else None
    if not isinstance(valor, list):
        errores.append(f"{donde}.{campo}: debe ser una lista")
        return []
    return valor


def requerir_enum(
    obj: Any, campo: str, opciones: set[str], donde: str, errores: list[str]
) -> str | None:
    valor = obj.get(campo) if isinstance(obj, dict) else None
    if valor not in opciones:
        errores.append(
            f"{donde}.{campo}: debe ser uno de {sorted(opciones)} (recibido: {valor!r})"
        )
        return None
    return valor
