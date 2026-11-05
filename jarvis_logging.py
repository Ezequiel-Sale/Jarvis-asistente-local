"""Registro en archivo para la app de escritorio (roadmap punto 4).

El .exe se ejecuta sin consola, así que sin esto los errores se perdían.
- Archivo rotativo ``logs/jarvis.log`` junto a la app (1 MB × 5 copias).
- Captura excepciones no controladas en el hilo principal, en hilos y en
  callbacks de Tk.
- Privacidad: los mensajes de registro no deben incluir lo que el usuario dice
  o escribe (ni respuestas del asistente); solo eventos, tiempos y errores.
"""

import logging
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_MAX_BYTES = 1_000_000
LOG_BACKUPS = 5
LOG_FORMAT = "%(asctime)s | %(levelname)s | %(threadName)s | %(module)s | %(message)s"

_file_handler: RotatingFileHandler | None = None


def configurar_registro(app_dir: Path) -> Path:
    """Añade el archivo de registro al logger raíz (idempotente). Devuelve su ruta."""
    global _file_handler
    log_dir = app_dir / "logs"
    log_path = log_dir / "jarvis.log"
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if _file_handler is None:
        log_dir.mkdir(parents=True, exist_ok=True)
        _file_handler = RotatingFileHandler(
            log_path,
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUPS,
            encoding="utf-8",
        )
        _file_handler.setFormatter(logging.Formatter(LOG_FORMAT))
        root.addHandler(_file_handler)
        _instalar_capturas()
    return log_path


def ruta_registro() -> Path | None:
    return Path(_file_handler.baseFilename) if _file_handler is not None else None


def _instalar_capturas() -> None:
    def excepthook(exc_type, exc, tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        logging.critical("Excepción no controlada.", exc_info=(exc_type, exc, tb))

    def thread_excepthook(args: threading.ExceptHookArgs) -> None:
        if args.exc_type is SystemExit:
            return
        name = args.thread.name if args.thread is not None else "desconocido"
        logging.critical(
            "Excepción no controlada en el hilo %s.",
            name,
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    sys.excepthook = excepthook
    threading.excepthook = thread_excepthook


def capturar_errores_tk(root) -> None:
    """Registra en el archivo los errores de callbacks de Tk (antes iban a stderr)."""

    def report(exc_type, exc, tb) -> None:
        logging.error("Error en un callback de la interfaz.", exc_info=(exc_type, exc, tb))

    root.report_callback_exception = report
