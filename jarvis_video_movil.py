"""Pantalla en vivo como vídeo H.264 para la app del celular (hasta 60 cuadros/s).

Captura con DXGI Desktop Duplication (``dxcam``: la copia la hace la tarjeta de
vídeo, ~3 ms en 2560×1440) y codifica con el codificador por hardware de la GPU
(AMF en AMD; si no está, Media Foundation, NVENC, Quick Sync o x264). Cada paquete
H.264 sale tal cual; ``jarvis_mobile`` le pone el marco y lo envía.

En 1080p a 60 cuadros/s la PC tarda ~14 ms por cuadro (casi todo en pasar de BGRA a
NV12) y envía ~8 Mbps. Si la red no da abasto, ``wfile.write`` bloquea y el bucle
se frena solo: siempre se codifica el cuadro más reciente, sin colas.
"""

import fractions
import logging
import re
import threading
import time
from collections.abc import Iterator

import jarvis_vision

CODIFICADORES = ("h264_amf", "h264_mf", "h264_nvenc", "h264_qsv", "libx264")
OPCIONES = {
    "h264_amf": {"usage": "ultralowlatency", "quality": "speed", "rc": "cbr"},
    "h264_nvenc": {"preset": "p1", "tune": "ull", "zerolatency": "1"},
    "h264_qsv": {"preset": "veryfast", "low_power": "1"},
    "h264_mf": {"rate_control": "cbr", "scenario": "display_remoting"},
    "libx264": {"preset": "ultrafast", "tune": "zerolatency"},
}
FPS_PERMITIDOS = (15, 30, 60)
GOP_SEGUNDOS = 2  # un cuadro clave cada 2 s: si algo se corrompe, se arregla solo


class VideoNoDisponible(OSError):
    pass


def _par(valor: float) -> int:
    return max(2, int(round(valor / 2)) * 2)


def tamano_salida(ancho_monitor: int, alto_monitor: int, ancho_max: int) -> tuple[int, int]:
    """Tamaño del vídeo: como mucho ``ancho_max`` de ancho, sin agrandar y en pares (NV12)."""
    ancho = min(ancho_monitor, ancho_max)
    return _par(ancho), _par(alto_monitor * ancho / ancho_monitor)


def _salida_dxgi(numero: int) -> int:
    """Índice de salida de dxcam del monitor con ese número fijo."""
    import dxcam

    salidas = re.findall(r"Output\[(\d+)\]", dxcam.output_info())
    for indice in map(int, salidas):
        camara = dxcam.create(output_idx=indice, output_color="BGRA", processor_backend="numpy")
        try:
            coincide = re.search(r"(\d+)$", camara._output.devicename or "")
        finally:
            camara.release()
        if coincide and jarvis_vision.numero_fijo(int(coincide.group(1))) == numero:
            return indice
    raise ValueError(f"No existe el monitor {numero}.")


def _abrir_codificador(ancho: int, alto: int, fps: int, kbps: int):
    import av

    errores = []
    for nombre in CODIFICADORES:
        try:
            codificador = av.CodecContext.create(nombre, "w")
            codificador.width, codificador.height = ancho, alto
            codificador.pix_fmt = "nv12" if nombre != "libx264" else "yuv420p"
            codificador.time_base = fractions.Fraction(1, fps)
            codificador.framerate = fps
            codificador.bit_rate = kbps * 1000
            codificador.gop_size = fps * GOP_SEGUNDOS
            codificador.max_b_frames = 0
            codificador.options = OPCIONES.get(nombre, {})
            codificador.open()
            return nombre, codificador
        except Exception as error:  # este codificador no está en esta PC
            errores.append(f"{nombre}: {error}")
    raise VideoNoDisponible("No hay codificador H.264 disponible. " + " | ".join(errores))


def transmitir(numero: int, fps: int, ancho_max: int, kbps: int,
               detener: threading.Event) -> Iterator[tuple[bytes, bool]]:
    """Paquetes H.264 (Annex B, con SPS/PPS en cada cuadro clave) hasta ``detener``."""
    try:
        import av
        import dxcam
    except ImportError as error:
        raise VideoNoDisponible(f"Falta un componente de vídeo: {error}") from error

    fps = fps if fps in FPS_PERMITIDOS else 30
    camara = dxcam.create(output_idx=_salida_dxgi(numero), output_color="BGRA",
                          processor_backend="numpy")
    try:
        ancho, alto = tamano_salida(camara.width, camara.height, ancho_max)
        nombre, codificador = _abrir_codificador(ancho, alto, fps, kbps)
        logging.info("Vídeo al celular: monitor %s, %sx%s a %s cuadros/s con %s.",
                     numero, ancho, alto, fps, nombre)
        intervalo = 1.0 / fps
        ultimo = None
        cuadro_n = 0
        siguiente = time.perf_counter()
        while not detener.is_set():
            imagen = camara.grab()  # None = la pantalla no cambió
            if imagen is not None:
                ultimo = imagen
            if ultimo is None:
                time.sleep(0.005)
                continue
            cuadro = av.VideoFrame.from_ndarray(ultimo, format="bgra").reformat(
                width=ancho, height=alto, format=codificador.pix_fmt)
            cuadro.pts = cuadro_n
            cuadro_n += 1
            for paquete in codificador.encode(cuadro):
                yield bytes(paquete), bool(paquete.is_keyframe)
            siguiente += intervalo
            espera = siguiente - time.perf_counter()
            if espera > 0:
                time.sleep(espera)
            else:  # la red o la PC van atrasadas: no se acumula retraso
                siguiente = time.perf_counter()
    finally:
        camara.release()
