import logging
import os
import tempfile
import threading
import wave

import pyaudio

CHUNK = 1024
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 16000


class GrabadorAudio:
    """Captura audio del micrófono hasta que se solicite detener."""

    def __init__(self) -> None:
        self._audio = pyaudio.PyAudio()
        self._stream = None
        self._frames: list[bytes] = []
        self._errors: list[Exception] = []
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._sample_width = 0
        self._closed = False

    def iniciar(self) -> None:
        dispositivo = self._audio.get_default_input_device_info()
        logging.info("🎙️ Micrófono: %s", dispositivo["name"])
        stream = self._audio.open(
            format=FORMAT,
            channels=CHANNELS,
            rate=RATE,
            input=True,
            input_device_index=dispositivo["index"],
            frames_per_buffer=CHUNK,
        )
        self._stream = stream
        self._sample_width = self._audio.get_sample_size(FORMAT)
        self._thread = threading.Thread(
            target=self._capturar,
            args=(stream,),
            daemon=True,
        )
        self._thread.start()

    def _capturar(self, stream) -> None:
        try:
            while not self._stop_event.is_set():
                self._frames.append(
                    stream.read(CHUNK, exception_on_overflow=False)
                )
        except Exception as error:
            self._errors.append(error)
            self._stop_event.set()

    def detener(self) -> str | None:
        self._cerrar_stream()
        if self._errors:
            raise RuntimeError(f"No se pudo leer el micrófono: {self._errors[0]}") from self._errors[0]
        if not self._frames:
            logging.warning("No se capturó audio.")
            return None

        descriptor, ruta_wav = tempfile.mkstemp(suffix=".wav")
        os.close(descriptor)
        try:
            with wave.open(ruta_wav, "wb") as archivo:
                archivo.setnchannels(CHANNELS)
                archivo.setsampwidth(self._sample_width)
                archivo.setframerate(RATE)
                archivo.writeframes(b"".join(self._frames))
        except Exception:
            os.remove(ruta_wav)
            raise

        logging.info("💾 Audio capturado: %s bytes", os.path.getsize(ruta_wav))
        return ruta_wav

    def cancelar(self) -> None:
        self._cerrar_stream()

    def _cerrar_stream(self) -> None:
        if self._closed:
            return
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join()
        try:
            if self._stream is not None:
                try:
                    if self._stream.is_active():
                        self._stream.stop_stream()
                finally:
                    self._stream.close()
                    self._stream = None
        finally:
            self._audio.terminate()
            self._closed = True


def grabar_audio() -> str | None:
    """Graba hasta que el usuario presione ENTER y devuelve un WAV temporal."""
    grabador = GrabadorAudio()
    try:
        grabador.iniciar()
        input("🔴 Grabando... Presiona ENTER para detener.\n")
        return grabador.detener()
    except KeyboardInterrupt:
        logging.info("Grabación interrumpida.")
        grabador.cancelar()
        return None
    except (OSError, RuntimeError, EOFError):
        logging.exception("Falló la grabación del micrófono.")
        grabador.cancelar()
        return None


def limpiar_audio(ruta: str | None) -> None:
    """Elimina el archivo temporal de audio después de procesarlo."""
    if not ruta:
        return
    try:
        os.remove(ruta)
    except FileNotFoundError:
        pass
    except OSError:
        logging.exception("No se pudo eliminar el audio temporal %s.", ruta)
