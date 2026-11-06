"""Hablar mientras el modelo genera la respuesta.

``HabladorEnVivo`` recibe el texto a trozos (``alimentar``), lo corta en
frases, convierte cada frase en audio con Piper en un hilo y la reproduce en
otro, en orden y sin esperar a que termine la respuesta completa. Al final
(``terminar``) dice lo que falte, une los fragmentos en un único WAV (para el
botón «Reproducir en Jarvis») y borra los fragmentos.
"""

import logging
import queue
import re
import threading
import time
import wave
from collections.abc import Callable
from pathlib import Path

_FIN_FRASE = re.compile(r"(?<=[.!?…:;])\s+|\n+")
_FIN_ALTERNATIVO = re.compile(r"(?<=,)\s+")
MINIMO_FRASE = 12  # caracteres: evita pedir audio de trozos sueltos como «Sí.»
MAXIMO_SIN_PUNTO = 140  # si no llega un punto, corta en una coma
_FIN = object()


def limpiar_para_voz(texto: str) -> str:
    """Quita marcas de formato que Piper leería en voz alta."""
    texto = re.sub(r"```.*?```", " ", texto, flags=re.DOTALL)
    texto = re.sub(r"https?://\S+", "el enlace", texto)
    texto = re.sub(r"^\s*(?:[-*•]|\d+\.)\s+", "", texto, flags=re.MULTILINE)
    texto = re.sub(r"[*_#`>|]", "", texto)
    return " ".join(texto.split())


def extraer_frases(pendiente: str, *, final: bool = False) -> tuple[list[str], str]:
    """Separa las frases completas del texto pendiente.

    Devuelve ``(frases, resto)``. Las frases muy cortas se juntan con la
    siguiente; con ``final`` se devuelve también el resto.
    """
    frases: list[str] = []
    acumulado = ""
    inicio = 0
    for corte in _FIN_FRASE.finditer(pendiente):
        trozo = pendiente[inicio:corte.start()]
        inicio = corte.end()
        acumulado = f"{acumulado} {trozo}".strip() if acumulado else trozo.strip()
        if len(acumulado) >= MINIMO_FRASE:
            frases.append(acumulado)
            acumulado = ""
    resto = pendiente[inicio:]
    if acumulado:
        resto = f"{acumulado} {resto}".strip() if resto.strip() else acumulado
    if not final and len(resto) > MAXIMO_SIN_PUNTO:
        cortes = list(_FIN_ALTERNATIVO.finditer(resto))
        if cortes:
            ultimo = cortes[-1]
            frases.append(resto[: ultimo.start()].strip())
            resto = resto[ultimo.end():]
    if final and resto.strip():
        frases.append(resto.strip())
        resto = ""
    return [f for f in frases if f], resto


def reproducir_wav(ruta: str, cancelado: threading.Event) -> None:
    """Reproduce un WAV en la salida predeterminada; para si se cancela."""
    import pyaudio

    audio = pyaudio.PyAudio()
    try:
        with wave.open(ruta, "rb") as archivo:
            stream = audio.open(
                format=audio.get_format_from_width(archivo.getsampwidth()),
                channels=archivo.getnchannels(),
                rate=archivo.getframerate(),
                output=True,
            )
            try:
                while not cancelado.is_set():
                    datos = archivo.readframes(1024)
                    if not datos:
                        break
                    stream.write(datos)
            finally:
                stream.stop_stream()
                stream.close()
    finally:
        audio.terminate()


def unir_wavs(rutas: list[str], destino: str) -> str | None:
    rutas = [r for r in rutas if Path(r).is_file()]
    if not rutas:
        return None
    with wave.open(rutas[0], "rb") as primero:
        parametros = primero.getparams()
    with wave.open(destino, "wb") as salida:
        salida.setparams(parametros)
        for ruta in rutas:
            with wave.open(ruta, "rb") as archivo:
                salida.writeframes(archivo.readframes(archivo.getnframes()))
    return destino


class HabladorEnVivo:
    def __init__(
        self,
        sintetizar: Callable[[str, str], str],
        *,
        reproducir: Callable[[str, threading.Event], None] = reproducir_wav,
        prefijo: str | None = None,
    ) -> None:
        self.sintetizar = sintetizar
        self.reproducir = reproducir
        self.prefijo = prefijo or time.strftime("voz_%Y%m%d_%H%M%S")
        self.cancelado = threading.Event()
        self._lock = threading.Lock()
        self._texto = ""  # todo lo recibido
        self._pendiente = ""  # recibido pero aún no enviado a Piper
        self._cola_tts: queue.Queue = queue.Queue()
        self._cola_audio: queue.Queue = queue.Queue()
        self._segmentos: list[str] = []
        self._numero = 0
        self._hilos: list[threading.Thread] = []
        self.primera_voz: float | None = None  # momento en que empezó a sonar

    @property
    def empezo(self) -> bool:
        return bool(self._hilos)

    def nueva_ronda(self) -> None:
        """El modelo empieza otra respuesta (tras usar herramientas): lo pendiente
        de la anterior se dice y el texto definitivo se compara solo con la nueva."""
        with self._lock:
            frases, _ = extraer_frases(self._pendiente, final=True)
            self._pendiente = ""
            self._texto = ""
        for frase in frases:
            self._encolar(frase)

    def alimentar(self, trozo: str | None) -> None:
        """Añade texto; ``None`` marca el inicio de una nueva ronda del modelo."""
        if trozo is None:
            self.nueva_ronda()
            return
        if self.cancelado.is_set() or not trozo:
            return
        with self._lock:
            self._texto += trozo
            self._pendiente += trozo
            frases, self._pendiente = extraer_frases(self._pendiente)
        for frase in frases:
            self._encolar(frase)

    def _encolar(self, frase: str) -> None:
        frase = limpiar_para_voz(frase)
        if not frase:
            return
        if not self._hilos:
            for nombre, destino in (("JarvisVozTTS", self._hilo_tts), ("JarvisVozAudio", self._hilo_audio)):
                hilo = threading.Thread(target=destino, name=nombre, daemon=True)
                hilo.start()
                self._hilos.append(hilo)
        self._cola_tts.put(frase)

    def _hilo_tts(self) -> None:
        while True:
            frase = self._cola_tts.get()
            if frase is _FIN or self.cancelado.is_set():
                self._cola_audio.put(_FIN)
                return
            self._numero += 1
            try:
                ruta = self.sintetizar(frase, f"{self.prefijo}_{self._numero:03d}")
            except Exception:
                logging.exception("No se pudo convertir en voz un fragmento de la respuesta.")
                continue
            self._segmentos.append(ruta)
            self._cola_audio.put(ruta)

    def _hilo_audio(self) -> None:
        while True:
            ruta = self._cola_audio.get()
            if ruta is _FIN:
                return
            if self.cancelado.is_set():
                continue
            if self.primera_voz is None:
                self.primera_voz = time.perf_counter()
            try:
                self.reproducir(ruta, self.cancelado)
            except Exception:
                logging.exception("No se pudo reproducir un fragmento de la respuesta.")

    def terminar(
        self,
        texto_final: str,
        destino: str | None,
        *,
        timeout: float = 180,
        cancelar_si: Callable[[], bool] | None = None,
    ) -> str | None:
        """Dice lo que falte, espera a que termine y devuelve el WAV unido."""
        with self._lock:
            ya_recibido = self._texto
            if texto_final.startswith(ya_recibido):
                # Lo recibido ya se dijo o está pendiente; falta el final (p. ej. avisos).
                restante = self._pendiente + texto_final[len(ya_recibido):]
            else:
                # El texto definitivo no es el que se habló (p. ej. el modelo dijo
                # algo antes de usar una herramienta): decir también el definitivo.
                restante = f"{self._pendiente} {texto_final}"
            self._pendiente = ""
        frases, _ = extraer_frases(restante, final=True)
        for frase in frases:
            self._encolar(frase)
        if not self._hilos:
            return None
        self._cola_tts.put(_FIN)
        limite = time.monotonic() + timeout
        for hilo in self._hilos:
            while hilo.is_alive() and time.monotonic() < limite:
                if cancelar_si is not None and cancelar_si():
                    self.cancelar()
                hilo.join(0.1)
        ruta = None
        if destino and not self.cancelado.is_set():
            try:
                ruta = unir_wavs(self._segmentos, destino)
            except (OSError, wave.Error):
                logging.exception("No se pudieron unir los fragmentos de voz.")
        for segmento in self._segmentos:
            try:
                Path(segmento).unlink(missing_ok=True)
            except OSError:
                pass
        return ruta

    def cancelar(self) -> None:
        self.cancelado.set()
        self._cola_tts.put(_FIN)
