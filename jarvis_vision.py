"""Visión de pantalla bajo demanda (roadmap punto 6).

- Solo se captura cuando el usuario lo pide, y siempre se muestra una vista
  previa que debe confirmar antes de enviar nada al modelo.
- La captura vive solo en memoria: nunca se escribe en disco.
- El análisis lo hace un modelo local de Ollama (``core.MODEL_VISION``).
"""

import base64
import io
import logging
import os
import time
from dataclasses import dataclass

import requests

import jarvis_core as core
from jarvis_actions import CancellationToken, TiempoAgotado, ejecutar_con_limites

MAX_LADO = 1920  # se reduce la captura para acotar tiempo y tokens
TIMEOUT_ANALISIS = 300
PREGUNTA_POR_DEFECTO = "Describe qué se ve en esta pantalla y señala lo más relevante."


@dataclass(frozen=True, slots=True)
class Monitor:
    numero: int  # el mismo que muestra Windows en Configuración > Pantalla
    x: int
    y: int
    ancho: int
    alto: int
    principal: bool

    @property
    def descripcion(self) -> str:
        extra = ", principal" if self.principal else ""
        return f"Monitor {self.numero} ({self.ancho}×{self.alto}{extra})"


def listar_monitores() -> list[Monitor]:
    """Monitores en coordenadas físicas (con reconocimiento de DPI por monitor)."""
    if os.name != "nt":
        raise OSError("La captura de pantalla solo está disponible en Windows.")
    import ctypes
    import re
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]

    class MONITORINFOEXW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", wintypes.RECT),
            ("rcWork", wintypes.RECT),
            ("dwFlags", wintypes.DWORD),
            ("szDevice", wintypes.WCHAR * 32),
        ]

    monitor_enum_proc = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
        ctypes.POINTER(wintypes.RECT), wintypes.LPARAM,
    )
    encontrados: list[Monitor] = []

    def recolectar(hmonitor, _hdc, _rect, _data):
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(info)
        if user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
            match = re.search(r"(\d+)$", info.szDevice)
            numero = int(match.group(1)) if match else len(encontrados) + 1
            r = info.rcMonitor
            encontrados.append(
                Monitor(numero, r.left, r.top, r.right - r.left, r.bottom - r.top,
                        bool(info.dwFlags & 1))
            )
        return True

    anterior = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))  # per-monitor v2
    try:
        user32.EnumDisplayMonitors(None, None, monitor_enum_proc(recolectar), 0)
    finally:
        if anterior:
            user32.SetThreadDpiAwarenessContext(anterior)
    return sorted(encontrados, key=lambda m: m.numero)


def elegir_monitor(monitores: list[Monitor], numero: int | None) -> Monitor:
    if not monitores:
        raise OSError("No encontré ningún monitor.")
    if numero is None:
        return next((m for m in monitores if m.principal), monitores[0])
    for monitor in monitores:
        if monitor.numero == numero:
            return monitor
    disponibles = ", ".join(str(m.numero) for m in monitores)
    raise ValueError(f"No existe el monitor {numero}. Monitores disponibles: {disponibles}.")


def capturar_monitor(monitor: Monitor):
    """Devuelve una imagen PIL del monitor (solo en memoria)."""
    from PIL import ImageGrab

    return ImageGrab.grab(
        bbox=(monitor.x, monitor.y, monitor.x + monitor.ancho, monitor.y + monitor.alto),
        all_screens=True,
    )


def preparar_imagen(imagen, max_lado: int = MAX_LADO) -> bytes:
    """Reduce la captura si hace falta y la codifica en JPEG (en memoria)."""
    imagen = imagen.convert("RGB")
    escala = min(1.0, max_lado / max(imagen.size))
    if escala < 1.0:
        imagen = imagen.resize(
            (round(imagen.width * escala), round(imagen.height * escala))
        )
    buffer = io.BytesIO()
    imagen.save(buffer, format="JPEG", quality=88)
    return buffer.getvalue()


def _pedir_analisis(jpeg: bytes, pregunta: str) -> str:
    endpoint = core.LLM_URL.rsplit("/", 1)[0] + "/chat"
    payload = {
        "model": core.MODEL_VISION,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Eres JARVIS y analizas capturas de pantalla del usuario. Responde en "
                    "español, de forma clara y breve (máximo 6 frases), centrándote en lo "
                    "que pregunta. Cita textos visibles solo si son relevantes y no "
                    "inventes nada que no se vea en la imagen."
                ),
            },
            {
                "role": "user",
                "content": pregunta or PREGUNTA_POR_DEFECTO,
                "images": [base64.b64encode(jpeg).decode("ascii")],
            },
        ],
        "stream": False,
        "think": False,
        # Se libera antes que el modelo principal para que vuelva pronto a la GPU.
        "keep_alive": "5m",
        "options": {"num_ctx": 8192, "num_predict": 400, "temperature": 0.2},
    }
    try:
        respuesta = requests.post(endpoint, json=payload, timeout=TIMEOUT_ANALISIS)
        respuesta.raise_for_status()
    except requests.ConnectionError as error:
        raise RuntimeError(
            "No pude conectar con Ollama. Comprueba que la aplicación de Ollama esté abierta."
        ) from error
    except requests.HTTPError as error:
        detalle = ""
        try:
            detalle = respuesta.json().get("error", "")
        except ValueError:
            pass
        if "not found" in detalle:
            raise RuntimeError(
                f"El modelo de visión {core.MODEL_VISION} no está instalado en Ollama."
            ) from error
        raise RuntimeError(f"Ollama rechazó el análisis: {detalle or error}") from error
    contenido = (respuesta.json().get("message") or {}).get("content", "").strip()
    if not contenido:
        raise RuntimeError("El modelo de visión devolvió una respuesta vacía.")
    return contenido


def analizar_imagen(
    jpeg: bytes,
    pregunta: str,
    cancel_token: CancellationToken | None = None,
) -> str:
    inicio = time.perf_counter()
    try:
        texto = ejecutar_con_limites(
            lambda: _pedir_analisis(jpeg, pregunta),
            timeout=TIMEOUT_ANALISIS + 10,
            descripcion="análisis de pantalla",
            cancel_token=cancel_token,
        )
    except TiempoAgotado as error:
        core.registrar_latencia("vision", time.perf_counter() - inicio, ok=False)
        raise RuntimeError(
            f"El análisis de la pantalla tardó más de {TIMEOUT_ANALISIS} s."
        ) from error
    except Exception:
        core.registrar_latencia("vision", time.perf_counter() - inicio, ok=False)
        raise
    duracion = time.perf_counter() - inicio
    core.registrar_latencia("vision", duracion)
    logging.info("⏱️ Visión: %.2fs (%s KB enviados)", duracion, len(jpeg) // 1024)
    return texto
