# El analista de incidentes que nunca duerme

Sistema multiagente para Application Managed Services (AMS) sobre **Huawei Cloud MaaS** (modelo `glm-5.2`): recibe los logs de un incidente y entrega el triage, la causa raíz, el plan de resolución y los textos de comunicación, con la evidencia citada y los datos sensibles protegidos.

Solución al Reto 1 del hackathon AMS ("El analista de incidentes que nunca duerme").

---

## Requisitos

- Python 3.11 o superior
- Clave de MaaS (la entrega la organización)

## Instalación

```bash
cd ams-incident-analyst
pip install -r requirements.txt

# configura tu clave (nunca la subas al repositorio)
copy .env.example .env
# edita .env y pon MAAS_API_KEY=...
```

## Uso

### Fase 0 — probar la conexión con MaaS

```bash
python cli.py probar-maas
```

### Fase 1 — preparar la evidencia (sin modelo, sin clave)

```bash
python cli.py preparar --logs-dir kit/datos/checkout-incident
```

Muestra las métricas de preparación (líneas originales, evidencias, datos protegidos por tipo, reducción de tamaño) y la evidencia generada.

### Flujo completo — el incidente del kit de principio a fin

```bash
python cli.py analizar --logs-dir kit/datos/checkout-incident --ticket kit/datos/checkout-incident/ticket.json --problemas kit/datos/problemas_conocidos.json
```

Ejecuta las 6 fases (preparación + 5 agentes) y deja el resultado en `casos/<case_id>/`:

| Archivo | Contenido |
|---|---|
| `reporte.md` | Reporte final legible, con los ids de evidencia enlazados a sus líneas de log (redactadas) |
| `caso.json` | El caso completo |
| `fases/*.json` | El JSON de salida de cada fase |
| `auditoria.jsonl` | Cada prompt, cada respuesta, duración, tokens y cada tool ejecutada |

Cada llamada puede tardar de 1 a 3 minutos; el flujo completo ronda los 10-15 minutos.

### Interfaz web

```bash
python cli.py web
# abre http://127.0.0.1:8000
```

Permite cargar el directorio de logs y el ticket, ver el progreso fase por fase y leer el reporte final.

### Sin clave (modo simulado)

```bash
# en .env: MAAS_MOCK=1  (o como variable de entorno)
python cli.py analizar --logs-dir kit/datos/checkout-incident --ticket kit/datos/checkout-incident/ticket.json
```

Corre la tubería completa con salidas simuladas, marcadas como tales: sirve para probar la mecánica sin gastar llamadas.

### Pruebas

```bash
python -m pytest tests/ -q
```

36 pruebas: redacción de datos sensibles (ningún dato del kit sobrevive), parsers de los 4 formatos, contratos de evidencia y tendencias, limpieza de JSON, validadores de los 5 agentes, prohibición de quick fixes rechazados, comunicado al negocio sin jerga, y el flujo completo end-to-end en modo mock.

---

## Otros incidentes

El sistema funciona con otros logs y otro ticket sin cambiar código ni prompts:

```bash
python cli.py analizar --logs-dir /ruta/a/otros/logs --ticket /ruta/a/otro/ticket.json --problemas /ruta/a/problemas.json
```

Los parsers reconocen los formatos estándar (Spring Boot, monitor de Oracle, nginx access/error); cualquier línea de otro formato se conserva como evidencia genérica. El ticket necesita al menos `ticket_id` y `description` (`business_rules` y `recent_changes` mejoran el triage y la causa raíz).

## Documentación

- `ARQUITECTURA.md` — diagrama, decisiones justificadas y alternativas descartadas.
- `prompts/` — bitácora de prompts: los prompts finales (fuente de verdad, los agentes los cargan en ejecución) y la historia de iteraciones de cada agente.
- `kit/` — datos entregados por la organización.

## Reglas del reto, dónde se cumplen

| Regla | Dónde |
|---|---|
| Agentes con MaaS | `src/maas_client.py` (endpoint compatible OpenAI, modelo glm-5.2) |
| Prompts propios en el repo | `prompts/` (cargados en ejecución) + historia de iteraciones |
| Nada hardcodeado del incidente | código y prompts genéricos; los resultados del kit viven en `casos/` |
| Clave fuera del repo | `.env` (gitignored), solo la lee `src/config.py` |
| Datos sensibles nunca al modelo | redacción determinista en `src/evidence/redaction.py` + prueba `tests/test_redaction.py` |
| Solo lectura | tools de solo lectura + validador que rechaza comandos de escritura |
| Anti-inyección en logs | neutralización en preparación + regla "dato, no instrucción" en todos los prompts |
