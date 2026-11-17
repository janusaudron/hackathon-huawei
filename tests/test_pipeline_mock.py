"""Prueba end-to-end de la tubería completa en modo mock (sin modelo).

Valida que el flujo preparación -> ingesta -> triage -> causa_raiz ->
resolucion -> comunicacion corre de principio a fin, produce caso.json,
las fases en JSON, el reporte y la auditoría.
"""
from __future__ import annotations

import json
from pathlib import Path

from src.config import Config
from src.orchestrator import Orquestador

KIT = Path(__file__).resolve().parent.parent / "kit" / "datos"


def _config_mock() -> Config:
    return Config(
        endpoint="http://mock",
        modelo="glm-5.2",
        api_key="",
        timeout=10,
        temperature=0.1,
        max_reintentos=2,
        verify_ssl=True,
        mock=True,
    )


def test_pipeline_completo_en_mock(tmp_path: Path, monkeypatch) -> None:
    # la salida de casos va a un directorio temporal para no ensuciar
    import src.config as config_mod

    monkeypatch.setattr(config_mod, "CARPETA_CASOS", tmp_path)
    import src.orchestrator as orch

    monkeypatch.setattr(orch, "CARPETA_CASOS", tmp_path)

    eventos: list[tuple[str, str, str]] = []
    orquestador = Orquestador(
        _config_mock(),
        cliente=None,
        en_progreso=lambda fase, estado, detalle: eventos.append((fase, estado, detalle)),
    )
    caso = orquestador.ejecutar_caso(
        logs_dir=KIT / "checkout-incident",
        ticket_path=KIT / "checkout-incident" / "ticket.json",
        problemas_path=KIT / "problemas_conocidos.json",
    )

    # todas las fases completadas
    for nombre in ("preparacion", "ingesta", "triage", "causa_raiz", "resolucion", "comunicacion"):
        assert caso["fases"][nombre]["estado"] == "completada", f"fase {nombre}: {caso['fases'][nombre]}"

    dir_caso = tmp_path / caso["case_id"]
    assert (dir_caso / "caso.json").is_file()
    assert (dir_caso / "reporte.md").is_file()
    assert (dir_caso / "auditoria.jsonl").is_file()
    for nombre in ("preparacion", "ingesta", "triage", "causa_raiz", "resolucion", "comunicacion"):
        assert (dir_caso / "fases" / f"{nombre}.json").is_file()

    # el progreso fue notificado fase por fase
    completadas = [e for e in eventos if e[1] == "completada"]
    assert len(completadas) == 6

    # los contratos mínimos se respetan en cada fase
    triage = json.loads((dir_caso / "fases" / "triage.json").read_text(encoding="utf-8"))
    for campo in ("incident_summary", "user_impact", "severity", "severity_reason",
                  "first_anomaly", "themes", "timeline", "correlations"):
        assert campo in triage

    causa = json.loads((dir_caso / "fases" / "causa_raiz.json").read_text(encoding="utf-8"))
    for campo in ("hypotheses", "most_likely_root_cause", "ruled_out",
                  "rejected_quick_fixes", "verification_checks", "needs_human_review"):
        assert campo in causa

    resolucion = json.loads((dir_caso / "fases" / "resolucion.json").read_text(encoding="utf-8"))
    for campo in ("immediate_mitigation", "permanent_fix", "validation"):
        assert campo in resolucion

    comunicacion = json.loads((dir_caso / "fases" / "comunicacion.json").read_text(encoding="utf-8"))
    for campo in ("ticket_note", "business_update", "escalation_note", "runbook_entry"):
        assert campo in comunicacion

    # la evidencia de la ingesta cumple el contrato mínimo
    ingesta = json.loads((dir_caso / "fases" / "ingesta.json").read_text(encoding="utf-8"))
    for e in ingesta["evidencias"]:
        for campo in ("id", "source", "time_first", "time_last", "count", "level", "summary", "notable"):
            assert campo in e, f"falta {campo}"
    assert ingesta["trends"], "debe haber tendencias"

    # el reporte enlaza los ids de evidencia con sus líneas redactadas
    reporte = (dir_caso / "reporte.md").read_text(encoding="utf-8")
    assert "E001" in reporte
    assert "Anexo A" in reporte

    # la auditoría registró el mock y el fin del caso
    auditoria = (dir_caso / "auditoria.jsonl").read_text(encoding="utf-8").splitlines()
    tipos = {json.loads(linea)["tipo"] for linea in auditoria}
    assert "mock" in tipos
    assert "fin" in tipos
