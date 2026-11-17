"""Reporte final del caso: markdown legible con todas las fases.

Incluye los ids de evidencia enlazados a sus líneas de log (redactadas),
las tendencias y un resumen de auditoría por fase.
"""
from __future__ import annotations

from typing import Any


def generar_reporte(caso: dict[str, Any]) -> str:
    """Construye el reporte markdown completo de un caso."""
    lineas: list[str] = []
    fases = caso.get("fases", {})

    _encabezado(lineas, caso)
    _resumen_ejecutivo(lineas, fases)
    _triage(lineas, fases)
    _causa_raiz(lineas, fases)
    _resolucion(lineas, fases)
    _comunicaciones(lineas, fases)
    _metricas_preparacion(lineas, fases)
    _anexo_evidencia(lineas, fases)
    _anexo_tendencias(lineas, fases)
    _anexo_auditoria(lineas, fases)
    return "\n".join(lineas)


# ----------------------------------------------------------------------
def _encabezado(lineas: list[str], caso: dict[str, Any]) -> None:
    lineas.append(f"# Reporte de incidente: {caso.get('ticket_id', caso.get('case_id', 'caso'))}")
    lineas.append("")
    lineas.append(f"- **Caso:** {caso.get('case_id')}")
    lineas.append(f"- **Generado:** {caso.get('generado_en')}")
    lineas.append(f"- **Modo:** {'simulado (mock)' if caso.get('modo') == 'mock' else 'real'}")
    if caso.get("modelo"):
        lineas.append(f"- **Modelo:** {caso.get('modelo')} (Huawei Cloud MaaS)")
    ticket = caso.get("ticket", {})
    fases = caso.get("fases", {})
    lineas.append(f"- **Aplicación:** {ticket.get('application', 'n/d')}")
    lineas.append(f"- **Reportado:** {ticket.get('reported_at', 'n/d')}")
    lineas.append("")
    estados = " · ".join(
        f"{nombre}: {fases.get(nombre, {}).get('estado', 'pendiente')}"
        for nombre in ("preparacion", "ingesta", "triage", "causa_raiz", "resolucion", "comunicacion")
    )
    lineas.append(f"**Fases:** {estados}")
    lineas.append("")


def _salida(fases: dict[str, Any], nombre: str) -> dict[str, Any] | None:
    fase = fases.get(nombre, {})
    if fase.get("estado") == "completada" and isinstance(fase.get("salida"), dict):
        return fase["salida"]
    return None


def _seccion_error(lineas: list[str], fases: dict[str, Any], nombre: str) -> None:
    fase = fases.get(nombre, {})
    if fase.get("estado") == "error":
        lineas.append(f"> **Error en la fase {nombre}:** {fase.get('error')}")
        lineas.append("")


def _resumen_ejecutivo(lineas: list[str], fases: dict[str, Any]) -> None:
    lineas.append("## 1. Resumen ejecutivo")
    lineas.append("")
    triage = _salida(fases, "triage")
    causa = _salida(fases, "causa_raiz")
    if triage:
        lineas.append(f"- **Severidad:** {triage.get('severity')} — {triage.get('severity_reason')}")
        lineas.append(f"- **Qué falla:** {triage.get('incident_summary')}")
        lineas.append(f"- **Impacto:** {triage.get('user_impact')}")
    if causa:
        lineas.append(f"- **Causa más probable:** {causa.get('most_likely_root_cause')}")
        if causa.get("needs_human_review"):
            lineas.append("- **Requiere revisión humana:** sí (confianza baja o evidencia insuficiente)")
    if not triage and not causa:
        lineas.append("_Sin resultados: las fases de análisis no se completaron._")
    lineas.append("")


def _triage(lineas: list[str], fases: dict[str, Any]) -> None:
    lineas.append("## 2. Triage y correlación")
    lineas.append("")
    _seccion_error(lineas, fases, "triage")
    triage = _salida(fases, "triage")
    if not triage:
        return
    fa = triage.get("first_anomaly", {})
    lineas.append(f"**Primera anomalía:** {fa.get('time', '')} — {fa.get('description', '')} (evidencia {fa.get('evidence_id', '')})")
    lineas.append("")
    lineas.append("### Temas")
    lineas.append("")
    lineas.append("| Tema | Clasificación | Evidencia |")
    lineas.append("|---|---|---|")
    for tema in triage.get("themes", []):
        ids = ", ".join(tema.get("evidence_ids", []))
        lineas.append(f"| {tema.get('title', '')} | {tema.get('classification', '')} | {ids} |")
    lineas.append("")
    lineas.append("### Línea de tiempo")
    lineas.append("")
    lineas.append("| Hora | Fuente | Evento | Evidencia |")
    lineas.append("|---|---|---|---|")
    for punto in triage.get("timeline", []):
        lineas.append(
            f"| {punto.get('time', '')} | {punto.get('source', '')} | {punto.get('event', '')} | {punto.get('evidence_id', '')} |"
        )
    lineas.append("")
    lineas.append("### Correlaciones causa → efecto")
    lineas.append("")
    for cor in triage.get("correlations", []):
        lineas.append(f"- **{cor.get('cause_id')} → {cor.get('effect_id')}:** {cor.get('explanation')}")
    lineas.append("")


def _causa_raiz(lineas: list[str], fases: dict[str, Any]) -> None:
    lineas.append("## 3. Análisis de causa raíz")
    lineas.append("")
    _seccion_error(lineas, fases, "causa_raiz")
    causa = _salida(fases, "causa_raiz")
    if not causa:
        return
    lineas.append("### Hipótesis (ordenadas por verosimilitud)")
    lineas.append("")
    for h in causa.get("hypotheses", []):
        lineas.append(f"**{h.get('rank')}. {h.get('title')}** — confianza: {h.get('confidence')}")
        lineas.append("")
        lineas.append(f"Cadena causal: {h.get('causal_chain')}")
        lineas.append("")
        a_favor = ", ".join(h.get("supporting_evidence", [])) or "—"
        en_contra = ", ".join(h.get("contradicting_evidence", [])) or "—"
        ki = h.get("known_issue")
        lineas.append(f"- A favor: {a_favor}")
        lineas.append(f"- En contra: {en_contra}")
        if ki:
            lineas.append(f"- Problema conocido aplicable: {ki}")
        lineas.append("")
    lineas.append(f"**Causa más probable:** {causa.get('most_likely_root_cause')}")
    lineas.append("")
    descartados = causa.get("ruled_out", [])
    if descartados:
        lineas.append("### Parece alarmante pero NO es la causa")
        lineas.append("")
        for r in descartados:
            lineas.append(f"- **{r.get('item')}** — {r.get('reason')} (evidencia {r.get('evidence_id')})")
        lineas.append("")
    lineas.append("### Soluciones rápidas rechazadas")
    lineas.append("")
    for q in causa.get("rejected_quick_fixes", []):
        lineas.append(f"- **{q.get('action')}** — por qué está mal: {q.get('why_wrong')}")
    lineas.append("")
    lineas.append("### Comprobaciones de verificación (solo lectura)")
    lineas.append("")
    lineas.append("| Comprobación | Comando | Confirma si | Refuta si |")
    lineas.append("|---|---|---|---|")
    for c in causa.get("verification_checks", []):
        lineas.append(
            f"| {c.get('check', '')} | `{c.get('command', '')}` | {c.get('confirms_if', '')} | {c.get('refutes_if', '')} |"
        )
    lineas.append("")


def _resolucion(lineas: list[str], fases: dict[str, Any]) -> None:
    lineas.append("## 4. Plan de resolución")
    lineas.append("")
    _seccion_error(lineas, fases, "resolucion")
    res = _salida(fases, "resolucion")
    if not res:
        return
    lineas.append("### Mitigación inmediata (de la opción más segura a la más invasiva)")
    lineas.append("")
    lineas.append("| # | Paso | Responsable | Riesgo | Rollback | ¿Solicitud de cambio? |")
    lineas.append("|---|---|---|---|---|---|")
    for paso in res.get("immediate_mitigation", []):
        sc = "sí" if paso.get("requires_change_request") else "no"
        lineas.append(
            f"| {paso.get('order')} | {paso.get('step')} | {paso.get('owner')} | "
            f"{paso.get('risk')} | {paso.get('rollback')} | {sc} |"
        )
    lineas.append("")
    lineas.append("### Solución definitiva")
    lineas.append("")
    for fix in res.get("permanent_fix", []):
        lineas.append(f"- **{fix.get('action')}** (responsable: {fix.get('owner')}) — evita: {fix.get('prevents')}")
    lineas.append("")
    lineas.append("### Criterios de validación de la recuperación")
    lineas.append("")
    for v in res.get("validation", []):
        lineas.append(f"- {v.get('criterion')} → objetivo: {v.get('target')}")
    lineas.append("")


def _comunicaciones(lineas: list[str], fases: dict[str, Any]) -> None:
    lineas.append("## 5. Comunicaciones")
    lineas.append("")
    _seccion_error(lineas, fases, "comunicacion")
    com = _salida(fases, "comunicacion")
    if not com:
        return
    lineas.append("### Nota técnica para el ticket")
    lineas.append("")
    lineas.append(com.get("ticket_note", ""))
    lineas.append("")
    bu = com.get("business_update", {})
    lineas.append("### Comunicado al negocio")
    lineas.append("")
    lineas.append(bu.get("text", ""))
    lineas.append("")
    lineas.append(f"**Próxima actualización:** {bu.get('next_update_at', '')}")
    lineas.append("")
    esc = com.get("escalation_note", {})
    lineas.append(f"### Escalamiento a {esc.get('to', 'equipo especializado')}")
    lineas.append("")
    lineas.append(esc.get("text", ""))
    lineas.append("")
    lineas.append("Lo que se les pide:")
    for r in esc.get("requests", []):
        lineas.append(f"- {r}")
    lineas.append("")
    rb = com.get("runbook_entry", {})
    lineas.append("### Entrada de runbook")
    lineas.append("")
    lineas.append(f"**{rb.get('title', '')}**")
    lineas.append("")
    lineas.append(f"- **Síntomas:** {rb.get('symptoms', '')}")
    lineas.append(f"- **Diagnóstico:** {rb.get('diagnosis', '')}")
    lineas.append(f"- **Resolución:** {rb.get('resolution', '')}")
    lineas.append(f"- **Prevención:** {rb.get('prevention', '')}")
    lineas.append("")


def _metricas_preparacion(lineas: list[str], fases: dict[str, Any]) -> None:
    prep = _salida(fases, "preparacion")
    if not prep:
        return
    m = prep.get("metricas", {})
    lineas.append("## 6. Métricas de la preparación de evidencia")
    lineas.append("")
    lineas.append(f"- Líneas originales: **{m.get('lineas_originales', 0)}**")
    lineas.append(f"- Evidencias generadas: **{m.get('cantidad_evidencias', 0)}**")
    lineas.append(f"- Tendencias detectadas: **{m.get('cantidad_tendencias', 0)}**")
    protegidos = m.get("datos_protegidos_por_tipo", {})
    lineas.append(
        "- Datos sensibles protegidos: "
        + ", ".join(f"{v} {k}" for k, v in protegidos.items())
        + f" (total {m.get('datos_protegidos_total', 0)})"
    )
    iny = m.get("inyecciones_neutralizadas", {})
    if isinstance(iny, dict) and iny.get("cantidad"):
        lineas.append(f"- Instrucciones inyectadas neutralizadas: **{iny.get('cantidad')}**")
    lineas.append(
        f"- Reducción de tamaño enviado al modelo: **{m.get('reduccion_pct', 0)}%** "
        f"({m.get('bytes_originales', 0)} bytes → {m.get('bytes_al_modelo', 0)} bytes)"
    )
    lineas.append("")


def _anexo_evidencia(lineas: list[str], fases: dict[str, Any]) -> None:
    ing = _salida(fases, "ingesta")
    if not ing:
        return
    lineas.append("## Anexo A. Evidencia (ids enlazados a sus líneas de log)")
    lineas.append("")
    lineas.append("Las líneas están redactadas: ningún dato sensible llega al modelo ni al reporte.")
    lineas.append("")
    lineas.append("| Id | Fuente | Nivel | Veces | Primera | Última | Resumen |")
    lineas.append("|---|---|---|---|---|---|---|")
    for e in ing.get("evidencias", []):
        notable = " ★" if e.get("notable") else ""
        lineas.append(
            f"| {e.get('id')}{notable} | {e.get('source')} | {e.get('level')} | {e.get('count')} | "
            f"{e.get('time_first')} | {e.get('time_last')} | {e.get('summary', '')} |"
        )
    lineas.append("")
    lineas.append("### Detalle de las evidencias notables")
    lineas.append("")
    for e in ing.get("evidencias", []):
        if not e.get("notable"):
            continue
        lineas.append(f"**{e.get('id')} — {e.get('summary', '')}**")
        lineas.append("")
        lineas.append(f"- Componente: {e.get('component')} · Categoría: {e.get('category')} · Nivel: {e.get('level')} · Ocurrencias: {e.get('count')}")
        lineas_log = e.get("lineas_log", [])
        if len(lineas_log) == 2:
            lineas.append(f"- Líneas del log original: {lineas_log[0]} a {lineas_log[1]}")
        lineas.append("- Plantilla:")
        lineas.append("")
        lineas.append("```")
        lineas.append(e.get("template", ""))
        lineas.append("```")
        lineas.append("")
        for muestra in e.get("samples", [])[:2]:
            lineas.append("```")
            lineas.append(str(muestra))
            lineas.append("```")
            lineas.append("")


def _anexo_tendencias(lineas: list[str], fases: dict[str, Any]) -> None:
    ing = _salida(fases, "ingesta")
    if not ing:
        return
    trends = ing.get("trends", [])
    if not trends:
        return
    lineas.append("## Anexo B. Tendencias")
    lineas.append("")
    lineas.append("| Métrica | Fuente | Primero | Último | Máximo | Dirección | Evidencia |")
    lineas.append("|---|---|---|---|---|---|---|")
    for t in trends:
        lineas.append(
            f"| {t.get('metric')} | {t.get('source')} | {t.get('first')} | {t.get('last')} | "
            f"{t.get('max')} | {t.get('direction')} | {t.get('evidence_id')} |"
        )
    lineas.append("")


def _anexo_auditoria(lineas: list[str], fases: dict[str, Any]) -> None:
    lineas.append("## Anexo C. Resumen de auditoría por fase")
    lineas.append("")
    lineas.append("| Fase | Estado | Duración (s) |")
    lineas.append("|---|---|---|")
    for nombre in ("preparacion", "ingesta", "triage", "causa_raiz", "resolucion", "comunicacion"):
        fase = fases.get(nombre, {})
        lineas.append(f"| {nombre} | {fase.get('estado', 'pendiente')} | {fase.get('duracion_s', '—')} |")
    lineas.append("")
    lineas.append("_El detalle completo (cada prompt, cada respuesta, tokens y tools ejecutadas) está en `auditoria.jsonl`._")
    lineas.append("")
