"""Pruebas del pipeline de preparación de evidencia (Fase 1)."""
from __future__ import annotations

import re
from pathlib import Path

from src.evidence.prepare import preparar_evidencia

KIT = Path(__file__).resolve().parent.parent / "kit" / "datos" / "checkout-incident"

RE_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
RE_IP = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
RE_JWT = re.compile(r"eyJ[A-Za-z0-9_\-]{10,}")


def _preparado():
    return preparar_evidencia(KIT)


def test_metricas_de_preparacion() -> None:
    r = _preparado()
    m = r.metricas
    assert m["lineas_originales"] == 1780
    assert m["cantidad_evidencias"] > 10, "debería colapsar el ruido en pocas evidencias"
    assert m["reduccion_pct"] > 80, "la reducción de tamaño debe ser sustancial"
    assert m["datos_protegidos_total"] > 1000, "el kit trae >1000 datos sensibles"
    assert m["datos_protegidos_por_tipo"]["email"] > 100
    assert m["datos_protegidos_por_tipo"]["ip"] > 1000


def test_contrato_minimo_por_evidencia() -> None:
    r = _preparado()
    ids = [e["id"] for e in r.evidencias]
    assert len(ids) == len(set(ids)), "los ids de evidencia deben ser únicos"
    assert all(re.fullmatch(r"E\d{3,}", i) for i in ids)
    for e in r.evidencias:
        for campo in ("id", "source", "time_first", "time_last", "count", "level"):
            assert e[campo] is not None, f"falta {campo} en {e['id']}"
        assert e["count"] >= 1
        assert e["level"] in {"INFO", "WARN", "ERROR", "DEBUG", "TRACE", "UNKNOWN"}


def test_tendencias_presentes() -> None:
    r = _preparado()
    assert r.trends, "debería detectar tendencias en las métricas"
    for t in r.trends:
        for campo in ("metric", "source", "evidence_id", "first", "last", "max", "direction"):
            assert t[campo] is not None, f"falta {campo} en la tendencia"
        assert t["direction"] in {"creciente", "decreciente", "estable"}
        assert t["first"] <= t["max"]


def test_ningun_dato_sensible_en_lo_que_va_al_modelo() -> None:
    """La carga que se envía al modelo no contiene datos sensibles."""
    r = _preparado()
    carga = r.evidencia_compacta() + r.tendencias_para_modelo()
    for item in carga:
        texto = str(item)
        assert not RE_EMAIL.search(texto), f"email en la carga al modelo: {texto!r}"
        assert not RE_IP.search(texto), f"IP en la carga al modelo: {texto!r}"
        assert not RE_JWT.search(texto), f"token en la carga al modelo: {texto!r}"


def test_los_codigos_ora_sobreviven_a_la_plantilla() -> None:
    """Los códigos ORA-xxxxx importan para correlacionar: no se normalizan."""
    r = _preparado()
    plantillas = " ".join(e["template"] for e in r.evidencias)
    assert re.search(r"ORA-\d{5}", plantillas), "los códigos ORA deben conservarse"


def test_funciona_con_otros_logs(tmp_path: Path) -> None:
    """El sistema funciona con logs de otro formato sin cambiar código."""
    (tmp_path / "app.log").write_text(
        "2026-05-01 10:00:00 INFO servicio-x: usuario=a@b.com conectado desde 8.8.8.8\n"
        "2026-05-01 10:00:05 ERROR servicio-x: fallo inesperado\n"
        "2026-05-01 10:00:06 ERROR servicio-x: fallo inesperado\n",
        encoding="utf-8",
    )
    (tmp_path / "ticket.json").write_text("{}", encoding="utf-8")
    r = preparar_evidencia(tmp_path)
    assert r.metricas["lineas_originales"] == 3
    assert r.metricas["cantidad_evidencias"] == 2  # 1 info + 1 error agrupado
    assert r.metricas["datos_protegidos_por_tipo"]["email"] == 1
    assert r.metricas["datos_protegidos_por_tipo"]["ip"] == 1
