"""Interfaz web del analista de incidentes (Fase 5).

API:

- GET  /                         -> página única
- POST /api/analizar             -> lanza el análisis en segundo plano
- GET  /api/casos                -> casos disponibles
- GET  /api/casos/{id}/estado    -> progreso por fases
- GET  /api/casos/{id}/reporte   -> reporte final (markdown)
- GET  /api/casos/{id}/caso      -> caso completo (JSON)
- GET  /api/casos/{id}/fases/{f} -> salida JSON de una fase
"""
from __future__ import annotations

import re
import sys
import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from src.config import CARPETA_CASOS, cargar_config  # noqa: E402
from src.maas_client import ClienteMaaS  # noqa: E402
from src.orchestrator import Orquestador  # noqa: E402

app = FastAPI(title="El analista de incidentes que nunca duerme")

#: registro en memoria de los casos lanzados desde la web
REGISTRO: dict[str, dict[str, Any]] = {}
LOCK = threading.Lock()

FASES = ["preparacion", "ingesta", "triage", "causa_raiz", "resolucion", "comunicacion"]


class PeticionAnalisis(BaseModel):
    logs_dir: str
    ticket: str
    problemas: str | None = None


# ----------------------------------------------------------------------
@app.get("/")
def index() -> FileResponse:
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.post("/api/analizar")
def analizar(peticion: PeticionAnalisis) -> dict[str, Any]:
    logs_dir = Path(peticion.logs_dir)
    ticket = Path(peticion.ticket)
    if not logs_dir.is_dir():
        raise HTTPException(400, f"no existe el directorio de logs: {logs_dir}")
    if not ticket.is_file():
        raise HTTPException(400, f"no existe el ticket: {ticket}")
    problemas = Path(peticion.problemas) if peticion.problemas else None

    config = cargar_config()
    cliente = ClienteMaaS(config) if (config.listo and not config.mock) else None

    with LOCK:
        import time

        case_id = f"web_{int(time.time())}"
        REGISTRO[case_id] = {
            "estado": {"fases": {f: "pendiente" for f in FASES}, "actual": None, "finalizado": False},
            "caso": None,
            "error": None,
        }

    def progreso(fase: str, estado: str, detalle: str) -> None:
        with LOCK:
            REGISTRO[case_id]["estado"]["fases"][fase] = estado
            REGISTRO[case_id]["estado"]["actual"] = fase

    def correr() -> None:
        try:
            orquestador = Orquestador(config, cliente, en_progreso=progreso)
            caso = orquestador.ejecutar_caso(
                logs_dir=logs_dir, ticket_path=ticket, problemas_path=problemas
            )
            with LOCK:
                REGISTRO[case_id]["caso"] = caso
                REGISTRO[case_id]["case_id_real"] = caso.get("case_id")
                REGISTRO[case_id]["estado"]["finalizado"] = True
        except Exception as exc:  # el flujo no se cae sin explicación
            with LOCK:
                REGISTRO[case_id]["error"] = f"{exc}"
                REGISTRO[case_id]["estado"]["finalizado"] = True

    hilo = threading.Thread(target=correr, daemon=True)
    with LOCK:
        REGISTRO[case_id]["hilo"] = hilo
    hilo.start()
    return {"case_id": case_id, "modo": "mock" if cliente is None else "real"}


@app.get("/api/casos")
def listar_casos() -> dict[str, Any]:
    casos = []
    for entrada in sorted(Path(CARPETA_CASOS).glob("*"), reverse=True):
        if (entrada / "caso.json").is_file():
            casos.append({"case_id": entrada.name, "ruta": str(entrada)})
    return {"casos": casos}


@app.get("/api/casos/{case_id}/estado")
def estado(case_id: str) -> dict[str, Any]:
    with LOCK:
        registro = REGISTRO.get(case_id)
        if registro:
            return {
                "case_id": case_id,
                "case_id_real": registro.get("case_id_real"),
                "en_vivo": True,
                **registro["estado"],
                "error": registro["error"],
            }
    # si no está en memoria, quizá esté en disco (proceso anterior)
    dir_caso = Path(CARPETA_CASOS) / case_id
    if not dir_caso.is_dir():
        raise HTTPException(404, f"caso no encontrado: {case_id}")
    caso = _leer_json(dir_caso / "caso.json")
    fases = {nombre: f.get("estado", "pendiente") for nombre, f in caso.get("fases", {}).items()}
    return {"case_id": case_id, "en_vivo": False, "fases": fases, "finalizado": True, "error": None}


@app.get("/api/casos/{case_id}/reporte", response_class=PlainTextResponse)
def reporte(case_id: str) -> PlainTextResponse:
    ruta = Path(CARPETA_CASOS) / case_id / "reporte.md"
    if not ruta.is_file():
        raise HTTPException(404, "el reporte aún no está listo")
    return PlainTextResponse(ruta.read_text(encoding="utf-8"), media_type="text/markdown")


@app.get("/api/casos/{case_id}/caso")
def caso(case_id: str) -> JSONResponse:
    ruta = Path(CARPETA_CASOS) / case_id / "caso.json"
    if not ruta.is_file():
        raise HTTPException(404, "el caso aún no está listo")
    return JSONResponse(_leer_json(ruta))


@app.get("/api/casos/{case_id}/fases/{fase}")
def fase(case_id: str, fase: str) -> JSONResponse:
    ruta = Path(CARPETA_CASOS) / case_id / "fases" / f"{fase}.json"
    if not ruta.is_file():
        raise HTTPException(404, f"fase no disponible: {fase}")
    return JSONResponse(_leer_json(ruta))


@app.get("/casos/{case_id}/{archivo}")
def archivo_caso(case_id: str, archivo: str) -> FileResponse:
    """Sirve archivos generados de un caso (reporte, auditoría, JSONs)."""
    if not re.fullmatch(r"[\w.\-]+", archivo) or archivo.startswith("."):
        raise HTTPException(400, "nombre de archivo inválido")
    ruta = Path(CARPETA_CASOS) / case_id / archivo
    if not ruta.is_file():
        raise HTTPException(404, f"archivo no disponible: {archivo}")
    return FileResponse(ruta)


def _leer_json(ruta: Path) -> dict[str, Any]:
    import json

    return json.loads(ruta.read_text(encoding="utf-8"))
