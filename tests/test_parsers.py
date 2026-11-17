"""Pruebas de los parsers de logs."""
from __future__ import annotations

from pathlib import Path

from src.evidence.parsers import parsear_archivo
from src.evidence.redaction import ConteoRedaccion, RegistroInyecciones

KIT = Path(__file__).resolve().parent.parent / "kit" / "datos" / "checkout-incident"

#: líneas físicas por archivo (contadas a mano del kit)
LINEAS_ESPERADAS = {
    "checkout-service.log": 636,
    "oracle-db-monitor.log": 93,
    "nginx-access.log": 1024,
    "nginx-error.log": 27,
}


def _parsear(nombre: str):
    conteo = ConteoRedaccion()
    registro = RegistroInyecciones()
    entradas, lineas_fisicas = parsear_archivo(KIT / nombre, conteo, registro)
    return entradas, lineas_fisicas, conteo, registro


def test_checkout_service_lineas_y_stack() -> None:
    entradas, lineas_fisicas, conteo, _ = _parsear("checkout-service.log")
    assert lineas_fisicas == LINEAS_ESPERADAS["checkout-service.log"]
    # los stack traces quedan unidos a su entrada
    con_stack = [e for e in entradas if e.stack]
    assert con_stack, "debería haber stack traces en el log de la aplicación"
    for e in con_stack:
        assert e.excepcion, "todo stack debe tener excepción reconocida"
        assert e.causa and e.causa.startswith("Caused by:")
    # la redacción protegió correos
    assert conteo.email > 0


def test_checkout_service_tiempos_y_niveles() -> None:
    entradas, _, _, _ = _parsear("checkout-service.log")
    assert entradas[0].tiempo == "2026-10-20 13:40:00"
    niveles = {e.nivel for e in entradas}
    assert {"INFO", "WARN", "ERROR", "DEBUG"} <= niveles


def test_oracle_monitor() -> None:
    entradas, _, _, _ = _parsear("oracle-db-monitor.log")
    assert len(entradas) == LINEAS_ESPERADAS["oracle-db-monitor.log"]
    assert entradas[0].tiempo == "2026-10-20 13:40:00"
    assert entradas[0].nivel == "INFO"


def test_nginx_access() -> None:
    entradas, _, _, _ = _parsear("nginx-access.log")
    assert len(entradas) == LINEAS_ESPERADAS["nginx-access.log"]
    assert entradas[0].tiempo == "2026-10-20 13:40:00"
    # el mensaje canónico no incluye la IP del cliente
    assert "203.0.113" not in entradas[0].mensaje
    assert 'status=' in entradas[0].mensaje
    # hay errores 5xx clasificados como ERROR
    assert any(e.nivel == "ERROR" for e in entradas)


def test_nginx_error() -> None:
    entradas, _, _, _ = _parsear("nginx-error.log")
    assert len(entradas) == LINEAS_ESPERADAS["nginx-error.log"]
    assert entradas[0].tiempo == "2026-10-20 13:54:41"
    assert entradas[0].nivel == "ERROR"


def test_todas_las_fuentes_protegidas() -> None:
    """Ninguna entrada parseada contiene datos sensibles sin redactar."""
    import re

    re_email = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
    re_ip = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
    for nombre in LINEAS_ESPERADAS:
        entradas, _, _, _ = _parsear(nombre)
        for e in entradas:
            texto = e.mensaje + " " + " ".join(e.stack)
            assert not re_email.search(texto), f"{nombre}: email sin redactar: {texto!r}"
            assert not re_ip.search(texto), f"{nombre}: IP sin redactar: {texto!r}"
