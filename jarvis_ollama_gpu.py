"""Comprueba que Ollama use la tarjeta gráfica y lo reinicia si no la detectó.

Si Ollama arranca con Windows antes de que el driver de la GPU esté listo, se
queda usando solo el procesador hasta que se reinicia (respuestas de 5–11 s en
vez de ~1 s, medido el 2026-11-06 con la RX 9070 XT). Ollama anota en su
registro qué dispositivos detectó al arrancar (``msg="inference compute"``);
si solo aparece ``library=cpu``, Jarvis reinicia la aplicación de Ollama.

Para no entrar en bucle se reintenta pocas veces y con espera: justo después
de encender el equipo el driver puede tardar en estar disponible.
"""

import logging
import os
import re
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

import requests

_LOCALAPPDATA = Path(os.environ.get("LOCALAPPDATA", ""))
REGISTRO_OLLAMA = _LOCALAPPDATA / "Ollama" / "server.log"
APP_OLLAMA = _LOCALAPPDATA / "Programs" / "Ollama" / "ollama app.exe"
URL_VERSION = "http://127.0.0.1:11434/api/version"

INTENTOS = 3
ESPERA_ENTRE_INTENTOS = 60.0
ESPERA_ARRANQUE = 60.0
# Solo se leen los últimos bytes del registro, donde está el arranque actual.
BYTES_FINALES = 512 * 1024

_INICIO = 'msg="Listening on'
_COMPUTO = re.compile(r'msg="inference compute".*?\blibrary=(\S+)')


def dispositivos_detectados(texto_registro: str) -> list[str] | None:
    """Bibliotecas detectadas en el último arranque (``["ROCm"]``, ``["cpu"]``…).

    ``None`` si el registro no tiene todavía un arranque completo.
    """
    inicio = texto_registro.rfind(_INICIO)
    if inicio == -1:
        return None
    librerias = [m.group(1) for m in _COMPUTO.finditer(texto_registro, inicio)]
    return librerias or None


def usa_solo_cpu(texto_registro: str) -> bool | None:
    librerias = dispositivos_detectados(texto_registro)
    if librerias is None:
        return None
    return all(lib.lower() == "cpu" for lib in librerias)


def leer_registro(ruta: Path = REGISTRO_OLLAMA) -> str | None:
    try:
        with open(ruta, "rb") as archivo:
            archivo.seek(0, os.SEEK_END)
            tamano = archivo.tell()
            archivo.seek(max(0, tamano - BYTES_FINALES))
            return archivo.read().decode("utf-8", errors="replace")
    except OSError:
        return None


def ollama_responde(timeout: float = 3.0) -> bool:
    try:
        return requests.get(URL_VERSION, timeout=timeout).ok
    except requests.RequestException:
        return False


def reiniciar_ollama(
    *,
    app: Path = APP_OLLAMA,
    espera: float = ESPERA_ARRANQUE,
    responde: Callable[[], bool] = ollama_responde,
) -> bool:
    """Cierra la app y el servidor de Ollama y vuelve a abrir la app."""
    import psutil

    for proceso in psutil.process_iter(["name"]):
        if (proceso.info.get("name") or "").lower() in {"ollama app.exe", "ollama.exe"}:
            try:
                proceso.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    time.sleep(3)
    subprocess.Popen(
        [str(app)],
        cwd=str(app.parent),
        creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        close_fds=True,
    )
    limite = time.monotonic() + espera
    while time.monotonic() < limite:
        if responde():
            return True
        time.sleep(1)
    return False


def asegurar_gpu(
    *,
    leer: Callable[[], str | None] = leer_registro,
    reiniciar: Callable[[], bool] = reiniciar_ollama,
    responde: Callable[[], bool] = ollama_responde,
    intentos: int = INTENTOS,
    espera: float = ESPERA_ENTRE_INTENTOS,
    dormir: Callable[[float], None] = time.sleep,
    instalada: Callable[[], bool] = lambda: APP_OLLAMA.is_file(),
    espera_inicial: float = ESPERA_ARRANQUE,
) -> str | None:
    """Reinicia Ollama si arrancó sin GPU. Devuelve un aviso para el usuario o None.

    No hace nada si Ollama no está instalado como app, no llega a responder o
    ya usa la GPU. Reinicia como mucho ``intentos`` veces.
    """
    if not instalada():
        return None
    # Al encender el equipo Jarvis y Ollama arrancan a la vez: darle tiempo.
    esperado = 0.0
    while not responde():
        if esperado >= espera_inicial:
            return None
        dormir(2)
        esperado += 2
    for intento in range(intentos + 1):
        texto = leer()
        solo_cpu = usa_solo_cpu(texto) if texto is not None else None
        if solo_cpu is None:
            return None
        if not solo_cpu:
            librerias = ", ".join(dispositivos_detectados(texto) or [])
            if intento == 0:
                logging.info("Ollama usa la tarjeta gráfica (%s).", librerias)
                return None
            logging.info("Ollama ya usa la tarjeta gráfica (%s) tras reiniciarlo.", librerias)
            return "Ollama se había iniciado sin la tarjeta gráfica; lo reinicié y ya la usa."
        if intento == intentos:
            break
        if intento > 0:
            # Tras un reinicio sin éxito, esperar a que el driver termine de cargar.
            dormir(espera)
        logging.warning(
            "Ollama solo detectó el procesador; reiniciándolo (intento %s de %s).",
            intento + 1,
            intentos,
        )
        if not reiniciar():
            logging.error("Ollama no volvió a responder tras reiniciarlo.")
            return "Reinicié Ollama porque no usaba la tarjeta gráfica, pero no volvió a responder."
        dormir(8)  # el registro de dispositivos aparece unos segundos después
    logging.error("Ollama sigue sin detectar la tarjeta gráfica tras %s reinicios.", intentos)
    return (
        "Ollama no detecta la tarjeta gráfica y funciona solo con el procesador, "
        "así que las respuestas serán lentas. Prueba a reiniciar Ollama más tarde "
        "o a actualizar el driver de la tarjeta."
    )
