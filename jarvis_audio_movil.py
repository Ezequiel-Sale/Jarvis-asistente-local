"""Audio de la PC para la app del celular: lo que suena por la salida actual.

Captura «loopback» de WASAPI con PyAudioWPatch (variante de PyAudio que trae la
captura de lo que se reproduce; se importa como ``pyaudiowpatch`` y no choca con
``pyaudio``). PCM de 16 bits en bloques de 20 ms, con la frecuencia y canales de la
salida (normalmente 48 kHz estéreo, ~1,5 Mbps): sin códec, así el celular lo pasa
directo a AudioTrack.

WASAPI no entrega nada mientras no suena nada, por eso se usa el modo con callback
y una cola: el generador espera con tiempo máximo y mira ``detener``.
"""

import logging
import queue
import threading
from collections.abc import Iterator

BLOQUE_MS = 20
MAX_COLA = 50  # ~1 s: si el envío se atrasa, se tira lo viejo


class AudioNoDisponible(OSError):
    pass


def capturar(detener: threading.Event) -> Iterator[bytes]:
    """Bloques PCM s16le de 20 ms hasta ``detener`` (el primero es la cabecera)."""
    try:
        import pyaudiowpatch as pa
    except ImportError as error:
        raise AudioNoDisponible("Falta PyAudioWPatch para capturar el audio de la PC.") from error
    p = pa.PyAudio()
    try:
        dispositivo = p.get_default_wasapi_loopback()
    except OSError as error:
        p.terminate()
        raise AudioNoDisponible(f"No encontré la salida de audio para capturar: {error}") from error
    frecuencia = int(dispositivo["defaultSampleRate"])
    canales = int(dispositivo["maxInputChannels"])
    cola: queue.Queue[bytes] = queue.Queue(maxsize=MAX_COLA)

    def recibir(datos, _cuadros, _tiempo, _estado):
        try:
            cola.put_nowait(datos)
        except queue.Full:  # el celular va atrasado: mejor saltar que acumular demora
            try:
                cola.get_nowait()
                cola.put_nowait(datos)
            except queue.Empty:
                pass
        return (None, pa.paContinue)

    flujo = p.open(format=pa.paInt16, channels=canales, rate=frecuencia, input=True,
                   input_device_index=dispositivo["index"],
                   frames_per_buffer=frecuencia * BLOQUE_MS // 1000, stream_callback=recibir)
    logging.info("Audio al celular: %s, %s Hz, %s canales.", dispositivo["name"], frecuencia, canales)
    try:
        flujo.start_stream()
        yield frecuencia.to_bytes(4, "big") + canales.to_bytes(2, "big")
        while not detener.is_set():
            try:
                yield cola.get(timeout=0.2)
            except queue.Empty:
                continue  # no suena nada
    finally:
        flujo.stop_stream()
        flujo.close()
        p.terminate()
