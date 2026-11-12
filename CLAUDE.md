# CLAUDE.md

Asistente de escritorio local (Windows, Python/Tkinter) controlado por voz y texto, en español.

**Antes de cualquier tarea, lee [PROJECT_CONTEXT.md](PROJECT_CONTEXT.md)**: arquitectura, módulos, BD, herramientas, convenciones y reglas que no deben romperse. Si cambias algo que ese documento describe, actualízalo en la misma tarea.

## Comandos

- Ejecutar: `.venv313/Scripts/python.exe jarvis_app.py`
- Tests: `.venv313/Scripts/python.exe -m unittest discover -s tests`
- Build: `.venv313/Scripts/pyinstaller.exe Jarvis.spec` (dependencias de build: `requirements-dev.txt`)
- CI: GitHub Actions ejecuta los tests en Windows en cada push a `main` (`.github/workflows/tests.yml`). Las versiones de `requirements.txt` están fijadas.

Usa `.venv313` (Python 3.13); `.venv` está incompleto.

## Reglas clave

- Las herramientas de riesgo HIGH siempre requieren confirmación; los planes de varios pasos se aprueban completos antes de ejecutarse. Aprobar el plan completo (que muestra cada paso y su riesgo) cuenta como esa confirmación, salvo para `CONFIRMACION_PROPIA` de `jarvis_agent` (energía, liberar disco, clics en pantalla, control remoto del celular) y las acciones destructivas MCP, que siempre preguntan aparte.
- Toda acción se ejecuta con `tool_registry.execute` (tiempo máximo, cancelación, verificación y registro). Cada herramienta nueva lleva `label`, `timeout_seconds` y, si se puede, un verificador en `jarvis_verifiers.py`.
- No toques widgets Tk desde hilos secundarios: publica eventos en `self.events`.
- Un comando nuevo se enruta en `_send_text` **y** en `_voice_worker` (`jarvis_app.py`).
- Los cambios de esquema van en `init_db.sql` más una migración condicional en `core.inicializar_base_datos()`.
- `jarvis_memory.db` contiene datos personales: no la uses en tests ni vuelques su contenido.
- Textos para el usuario, errores y logs, en español.
