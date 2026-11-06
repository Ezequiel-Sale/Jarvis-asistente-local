"""Palabra de activación «Hey Jarvis» (openWakeWord, 100 % local).

Un hilo escucha el micrófono en bloques de 80 ms y se los pasa al modelo
``hey_jarvis`` de openWakeWord. Al detectarla graba la orden hasta que el
usuario deja de hablar y publica ``("wakeword_command", ruta_wav)`` en la cola
de la app; la app la procesa igual que una grabación hecha con F9.

Privacidad: el audio de la escucha solo vive en memoria (unos segundos para
estimar el ruido de fondo) y nunca sale del equipo; únicamente la orden
posterior a la palabra se guarda en un WAV temporal que la app borra tras
transcribirlo.
"""

import logging
import math
import os
import queue
import struct
import tempfile
import threading
import time
import wave
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

RATE = 16000
BLOQUE = 1280  # 80 ms, el tamaño recomendado por openWakeWord
SEGUNDOS_POR_BLOQUE = BLOQUE / RATE
MODELO = "hey_jarvis"
UMBRAL_DETECCION = 0.5
ESPERA_TRAS_DETECCION = 1.5  # s sin volver a disparar
_EPS = 1e-6
UMBRAL_REGISTRO = 0.2  # puntuaciones menores al umbral que se anotan en el log


def rms(bloque: bytes) -> float:
    """Energía media de un bloque PCM de 16 bits."""
    muestras = len(bloque) // 2
    if muestras == 0:
        return 0.0
    valores = struct.unpack(f"<{muestras}h", bloque[: muestras * 2])
    return math.sqrt(sum(v * v for v in valores) / muestras)


@dataclass
class SegmentadorVoz:
    """Decide cuándo termina la orden a partir de la energía de cada bloque.

    - Espera hasta ``espera_inicio`` s a que el usuario empiece a hablar.
    - Termina tras ``silencio_final`` s seguidos de silencio después de hablar.
    - Nunca graba más de ``maximo`` s.
    """

    ruido: float
    espera_inicio: float = 4.0
    # Pausa que cierra la orden: con 0,9 s se cortaban frases largas al hacer
    # una pausa natural para pensar.
    silencio_final: float = 1.3
    maximo: float = 25.0
    factor: float = 3.0
    minimo_umbral: float = 300.0
    # El pitido de aviso suena justo al empezar: si el micrófono lo capta no
    # debe contar como que el usuario ya habló.
    ignorar_inicio: float = 0.3

    def __post_init__(self) -> None:
        self.umbral = max(self.ruido * self.factor, self.minimo_umbral)
        self.duracion = 0.0
        self.hablo = False
        self._silencio = 0.0

    def agregar(self, energia: float, segundos: float = SEGUNDOS_POR_BLOQUE) -> bool:
        """Registra un bloque; devuelve True cuando la grabación debe terminar."""
        self.duracion += segundos
        if energia >= self.umbral and self.duracion > self.ignorar_inicio + _EPS:
            self.hablo = True
            self._silencio = 0.0
        else:
            self._silencio += segundos
        # Margen para errores de redondeo al sumar duraciones de bloques.
        if self.duracion >= self.maximo - _EPS:
            return True
        if not self.hablo:
            return self.duracion >= self.espera_inicio - _EPS
        return self._silencio >= self.silencio_final - _EPS


def estimar_ruido(energias) -> float:
    """Ruido de fondo: percentil 20 de la energía de los últimos segundos.

    No se usa la mediana: esos segundos incluyen el propio «Hey Jarvis» y, si
    ocupa la mitad, el umbral de silencio quedaba a la altura de la voz y las
    partes suaves de la orden contaban como silencio (la frase se cortaba).
    """
    valores = sorted(energias)
    return valores[len(valores) // 5] if valores else 0.0


def guardar_wav(bloques: list[bytes]) -> str:
    descriptor, ruta = tempfile.mkstemp(suffix=".wav", prefix="jarvis_orden_")
    os.close(descriptor)
    with wave.open(ruta, "wb") as archivo:
        archivo.setnchannels(1)
        archivo.setsampwidth(2)
        archivo.setframerate(RATE)
        archivo.writeframes(b"".join(bloques))
    return ruta


_ruta_pitido: str | None = None


def generar_pitido(ruta: str, *, volumen: float = 0.35) -> None:
    """Dos tonos cortos ascendentes (≈0,18 s) con fundido para que no chasquee."""
    frecuencia = 22050
    muestras: list[int] = []
    for tono, duracion in ((880.0, 0.08), (1320.0, 0.10)):
        total = int(frecuencia * duracion)
        fundido = int(frecuencia * 0.01)
        for i in range(total):
            envolvente = min(1.0, i / fundido, (total - i) / fundido)
            valor = math.sin(2 * math.pi * tono * i / frecuencia) * volumen * envolvente
            muestras.append(int(valor * 32767))
    with wave.open(ruta, "wb") as archivo:
        archivo.setnchannels(1)
        archivo.setsampwidth(2)
        archivo.setframerate(frecuencia)
        archivo.writeframes(struct.pack(f"<{len(muestras)}h", *muestras))


def reproducir_pitido() -> None:
    """Suena en la salida predeterminada sin bloquear (no depende del esquema
    de sonidos de Windows, que puede estar en «Sin sonidos»)."""
    global _ruta_pitido
    import winsound

    if _ruta_pitido is None or not os.path.exists(_ruta_pitido):
        ruta = os.path.join(tempfile.gettempdir(), "jarvis_pitido_activacion.wav")
        generar_pitido(ruta)
        _ruta_pitido = ruta
    winsound.PlaySound(
        _ruta_pitido, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT
    )


def disponible() -> bool:
    try:
        import openwakeword  # noqa: F401
    except ImportError:
        return False
    return True


class DetectorPalabraActivacion(threading.Thread):
    """Escucha «Hey Jarvis» y publica la orden siguiente como WAV.

    ``puede_escuchar`` se consulta en cada bloque: mientras devuelva False
    (Jarvis ocupado, grabando con F9 o hablando) se ignora el audio para no
    dispararse con su propia voz.
    """

    def __init__(
        self,
        events: queue.Queue[tuple],
        puede_escuchar: Callable[[], bool],
        *,
        umbral: float = UMBRAL_DETECCION,
    ) -> None:
        super().__init__(name="JarvisPalabraActivacion", daemon=True)
        self.events = events
        self.puede_escuchar = puede_escuchar
        self.umbral = umbral
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        try:
            self._escuchar()
        except Exception as error:
            logging.exception("La palabra de activación dejó de funcionar.")
            self.events.put(("wakeword_error", str(error)))

    def _escuchar(self) -> None:
        import numpy as np
        import pyaudio
        from openwakeword.model import Model

        modelo = Model(wakeword_models=[MODELO], inference_framework="onnx")
        audio = pyaudio.PyAudio()
        stream = audio.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=RATE,
            input=True,
            frames_per_buffer=BLOQUE,
        )
        logging.info("Palabra de activación «Hey Jarvis» lista.")
        self.events.put(("wakeword_ready",))
        energias: deque[float] = deque(maxlen=int(5 / SEGUNDOS_POR_BLOQUE))
        bloqueado_hasta = 0.0
        registrar_desde = 0.0
        try:
            while not self._stop_event.is_set():
                bloque = stream.read(BLOQUE, exception_on_overflow=False)
                if not self.puede_escuchar() or time.monotonic() < bloqueado_hasta:
                    modelo.reset()
                    continue
                energias.append(rms(bloque))
                puntuacion = modelo.predict(np.frombuffer(bloque, dtype=np.int16)).get(MODELO, 0.0)
                if puntuacion < self.umbral:
                    # Ayuda a ajustar el umbral si no reconoce bien la pronunciación.
                    if puntuacion >= UMBRAL_REGISTRO and time.monotonic() >= registrar_desde:
                        logging.info("Palabra de activación casi detectada (%.2f).", puntuacion)
                        registrar_desde = time.monotonic() + 2
                    continue
                logging.info("Palabra de activación detectada (%.2f).", puntuacion)
                self.events.put(("wakeword_detected",))
                ruido = estimar_ruido(energias)
                ruta = self._grabar_orden(stream, ruido)
                modelo.reset()
                bloqueado_hasta = time.monotonic() + ESPERA_TRAS_DETECCION
                if ruta is None:
                    self.events.put(("wakeword_no_command",))
                else:
                    self.events.put(("wakeword_command", ruta))
        finally:
            stream.stop_stream()
            stream.close()
            audio.terminate()

    def _grabar_orden(self, stream, ruido: float) -> str | None:
        segmentador = SegmentadorVoz(ruido)
        bloques: list[bytes] = []
        while not self._stop_event.is_set():
            bloque = stream.read(BLOQUE, exception_on_overflow=False)
            bloques.append(bloque)
            if segmentador.agregar(rms(bloque)):
                break
        # Diagnóstico sin contenido, por si una orden se corta antes de tiempo.
        logging.info(
            "Orden grabada: %.1f s (%s; ruido %.0f, umbral %.0f).",
            segmentador.duracion,
            "máximo alcanzado" if segmentador.duracion >= segmentador.maximo - _EPS else "fin por silencio",
            ruido,
            segmentador.umbral,
        )
        if not segmentador.hablo:
            return None
        return guardar_wav(bloques)
