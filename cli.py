"""CLI del analista de incidentes AMS.

Comandos:

    python cli.py probar-maas                       # Fase 0: prueba de conexión
    python cli.py preparar --logs-dir <dir>        # Fase 1: evidencia (sin modelo)
    python cli.py analizar --logs-dir <dir> --ticket <ticket.json>
    python cli.py web                               # interfaz web

Ejemplo con el kit:

    python cli.py analizar --logs-dir kit/datos/checkout-incident --ticket kit/datos/checkout-incident/ticket.json --problemas kit/datos/problemas_conocidos.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Asegura que el proyecto esté importable desde cualquier cwd.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.config import CARPETA_CASOS, cargar_config  # noqa: E402
from src.maas_client import ClienteMaaS  # noqa: E402

KIT = Path(__file__).resolve().parent / "kit" / "datos"


def _configurar_logging_simple() -> None:
    import urllib3

    urllib3.disable_warnings()


# ----------------------------------------------------------------------
def cmd_probar_maas(args: argparse.Namespace) -> int:
    """Fase 0: envía un mensaje a GLM y muestra la respuesta."""
    config = cargar_config()
    print(f"Endpoint : {config.endpoint}")
    print(f"Modelo   : {config.modelo}")
    print(f"Timeout  : {config.timeout}s · Temperatura: {config.temperature}")
    print()
    if config.mock:
        print("MAAS_MOCK=1: modo simulado, no se llama al modelo.")
        return 0
    if not config.listo:
        print("FALTA LA CLAVE: copia .env.example a .env y pon tu MAAS_API_KEY.")
        return 2

    cliente = ClienteMaaS(config)
    print("Enviando mensaje de prueba a MaaS (puede tardar unos segundos)...")
    respuesta = cliente.chat(
        [
            {
                "role": "system",
                "content": "Eres un asistente de prueba de conexión. Responde en español.",
            },
            {
                "role": "user",
                "content": "Responde con una sola frase confirmando que la conexión funciona.",
            },
        ]
    )
    print()
    print("Respuesta:")
    print(cliente.texto_respuesta(respuesta))
    uso = cliente.usage(respuesta)
    if uso:
        print()
        print(f"Tokens: {uso}")
    return 0


# ----------------------------------------------------------------------
def cmd_preparar(args: argparse.Namespace) -> int:
    """Fase 1 (determinista): prepara la evidencia y muestra las métricas."""
    from src.evidence.prepare import preparar_evidencia

    resultado = preparar_evidencia(Path(args.logs_dir))
    m = resultado.metricas
    print(f"Líneas originales      : {m['lineas_originales']}")
    print(f"Evidencias generadas   : {m['cantidad_evidencias']}")
    print(f"Tendencias detectadas  : {m['cantidad_tendencias']}")
    print(f"Datos sensibles protegidos: {m['datos_protegidos_por_tipo']}")
    print(f"Inyecciones neutralizadas: {m['inyecciones_neutralizadas']['cantidad']}")
    print(f"Reducción de tamaño    : {m['reduccion_pct']}% ({m['bytes_originales']} -> {m['bytes_al_modelo']} bytes)")
    print()
    for e in resultado.evidencias:
        marca = "*" if e["count"] > 1 else " "
        print(f"{marca} {e['id']} [{e['level']:7}] x{e['count']:4} {e['time_first']} -> {e['time_last']} | {e['template'][:90]}")
    return 0


# ----------------------------------------------------------------------
def cmd_analizar(args: argparse.Namespace) -> int:
    """Ejecuta el flujo completo: preparación + 5 agentes + reporte."""
    from src.orchestrator import Orquestador

    config = cargar_config()
    if not config.listo and not config.mock:
        print("FALTA LA CLAVE: copia .env.example a .env y pon tu MAAS_API_KEY.")
        print("Alternativa para probar sin clave: MAAS_MOCK=1")
        return 2

    cliente = ClienteMaaS(config) if (config.listo and not config.mock) else None

    def progreso(fase: str, estado: str, detalle: str) -> None:
        simbolos = {
            "en_curso": "[..]",
            "completada": "[OK]",
            "error": "[!!]",
            "omitida": "[--]",
        }
        print(f"{simbolos.get(estado, '[..]')} {fase}: {estado}" + (f" — {detalle}" if detalle else ""))

    orquestador = Orquestador(config, cliente, en_progreso=progreso)
    print(f"Modo: {'SIMULADO (mock)' if cliente is None else 'real'} · Modelo: {config.modelo}")
    print()
    caso = orquestador.ejecutar_caso(
        logs_dir=Path(args.logs_dir),
        ticket_path=Path(args.ticket),
        problemas_path=Path(args.problemas) if args.problemas else KIT / "problemas_conocidos.json",
    )

    dir_caso = Path(CARPETA_CASOS) / caso["case_id"]
    print()
    print("=" * 70)
    print(f"Caso: {caso['case_id']}")
    for nombre, fase in caso["fases"].items():
        estado = fase.get("estado", "pendiente")
        dur = fase.get("duracion_s")
        extra = f" ({dur:.1f}s)" if isinstance(dur, (int, float)) else ""
        print(f"  {nombre:14} {estado}{extra}")
        if fase.get("error"):
            print(f"      error: {fase['error']}")
    print()
    print(f"Reporte    : {dir_caso / 'reporte.md'}")
    print(f"Caso JSON  : {dir_caso / 'caso.json'}")
    print(f"Auditoría  : {dir_caso / 'auditoria.jsonl'}")
    return 0 if all(f.get("estado") == "completada" for f in caso["fases"].values()) else 1


# ----------------------------------------------------------------------
def cmd_web(args: argparse.Namespace) -> int:
    """Lanza la interfaz web."""
    import uvicorn

    print(f"Abriendo la interfaz web en http://127.0.0.1:{args.puerto}")
    uvicorn.run(
        "web.app:app",
        host="127.0.0.1",
        port=args.puerto,
        log_level="warning",
        app_dir=str(Path(__file__).resolve().parent),
    )
    return 0


# ----------------------------------------------------------------------
def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cli.py",
        description="El analista de incidentes que nunca duerme — sistema multiagente AMS sobre Huawei Cloud MaaS",
    )
    sub = parser.add_subparsers(dest="comando", required=True)

    p = sub.add_parser("probar-maas", help="prueba la conexión con MaaS (Fase 0)")
    p.set_defaults(func=cmd_probar_maas)

    p = sub.add_parser("preparar", help="prepara la evidencia de un directorio de logs (Fase 1, sin modelo)")
    p.add_argument("--logs-dir", required=True, help="directorio con los archivos .log")
    p.set_defaults(func=cmd_preparar)

    p = sub.add_parser("analizar", help="ejecuta el flujo completo de análisis")
    p.add_argument("--logs-dir", required=True, help="directorio con los archivos .log")
    p.add_argument("--ticket", required=True, help="ruta al ticket.json del incidente")
    p.add_argument(
        "--problemas",
        default=str(KIT / "problemas_conocidos.json"),
        help="ruta a la base de problemas conocidos (KI)",
    )
    p.set_defaults(func=cmd_analizar)

    p = sub.add_parser("web", help="lanza la interfaz web")
    p.add_argument("--puerto", type=int, default=8000)
    p.set_defaults(func=cmd_web)

    return parser


def main() -> int:
    _configurar_logging_simple()
    parser = construir_parser()
    args = parser.parse_args()
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("\nInterrumpido.")
        return 130
    except FileNotFoundError as exc:
        print(f"Archivo no encontrado: {exc}")
        return 1
    except json.JSONDecodeError as exc:
        print(f"JSON inválido: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
