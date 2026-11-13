"""Voz a texto en la tarjeta gráfica: Whisper large-v3-turbo con ONNX Runtime + DirectML.

El servidor de voz (speaches, en Docker) solo usa tarjetas NVIDIA, así que con la RX 9070 XT
transcribía en el procesador. Medido el 2026-11-13 con 84 órdenes sintetizadas (3 voces, con y
sin ruido): el modelo «small» actual 20,0 % de palabras mal y 1,7 s por orden; turbo en el
procesador 12,6 % y 6,8 s; **turbo con el codificador en la GPU (DirectML, fp16) y el
decodificador en el procesador (fp32) 13,2 % y 0,54 s**. El decodificador va en el procesador
porque con DirectML la rama con caché del decodificador «merged» devuelve basura tras el primer
token, y en fp16 el procesador lo emula y tarda 2 s; la versión int8 fallaba mucho con ruido.

Si falta el modelo (carpeta ``whisper-onnx`` junto a Jarvis), falta DirectML o algo falla,
``transcribir`` devuelve None y Jarvis usa el servidor de voz de siempre.
"""

from __future__ import annotations

import logging
import threading
import time
import wave
from pathlib import Path

import numpy as np

ENCODER = "onnx/encoder_model_fp16.onnx"
DECODER = "onnx/decoder_model_merged.onnx"
TOKENIZER = "tokenizer.json"
ARCHIVOS = (ENCODER, DECODER, TOKENIZER)
REPOSITORIO = "onnx-community/whisper-large-v3-turbo"

# Tokens especiales de Whisper large-v3.
SOT, ES, TRANSCRIBE, NO_TS, PREV, EOT = 50258, 50262, 50360, 50364, 50362, 50257
MAX_TOKENS = 96
MAX_SEGUNDOS = 29.5  # el modelo ve 30 s: lo más largo, al servidor de siempre

# Espectrograma log-mel igual al de Whisper (128 bandas para large-v3).
FRECUENCIA, N_FFT, SALTO, BANDAS, TRAMAS = 16000, 400, 160, 128, 3000


def _hz_a_mel(hz: np.ndarray) -> np.ndarray:
    """Escala mel de Slaney (la de librosa, que usa Whisper)."""
    hz = np.asarray(hz, dtype=np.float64)
    mel = hz / (200.0 / 3)
    lineal_hasta, log_paso = 1000.0, np.log(6.4) / 27.0
    mel_lineal = lineal_hasta / (200.0 / 3)
    return np.where(hz >= lineal_hasta, mel_lineal + np.log(np.maximum(hz, 1e-10) / lineal_hasta) / log_paso, mel)


def _mel_a_hz(mel: np.ndarray) -> np.ndarray:
    mel = np.asarray(mel, dtype=np.float64)
    lineal_hasta, log_paso = 1000.0, np.log(6.4) / 27.0
    mel_lineal = lineal_hasta / (200.0 / 3)
    return np.where(mel >= mel_lineal, lineal_hasta * np.exp(log_paso * (mel - mel_lineal)), mel * (200.0 / 3))


def filtros_mel(bandas: int = BANDAS) -> np.ndarray:
    """Banco de filtros triangulares (bandas × (N_FFT/2+1)) con normalización de Slaney."""
    fft_hz = np.linspace(0, FRECUENCIA / 2, N_FFT // 2 + 1)
    puntos = _mel_a_hz(np.linspace(_hz_a_mel(0.0), _hz_a_mel(FRECUENCIA / 2), bandas + 2))
    diferencias = np.diff(puntos)
    rampas = puntos[:, None] - fft_hz[None, :]
    filtros = np.zeros((bandas, len(fft_hz)))
    for i in range(bandas):
        bajada = -rampas[i] / diferencias[i]
        subida = rampas[i + 2] / diferencias[i + 1]
        filtros[i] = np.maximum(0, np.minimum(bajada, subida))
    filtros *= (2.0 / (puntos[2:bandas + 2] - puntos[:bandas]))[:, None]
    return filtros.astype(np.float32)


_FILTROS: np.ndarray | None = None


def log_mel(audio: np.ndarray) -> np.ndarray:
    """(1, 128, 3000) float32 a partir de audio mono a 16 kHz (relleno o recortado a 30 s)."""
    global _FILTROS
    if _FILTROS is None:
        _FILTROS = filtros_mel()
    muestras = FRECUENCIA * 30
    audio = np.asarray(audio, dtype=np.float32)[:muestras]
    audio = np.pad(audio, (0, muestras - len(audio)))
    relleno = np.pad(audio, (N_FFT // 2, N_FFT // 2), mode="reflect")
    ventana = (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(N_FFT) / N_FFT)).astype(np.float32)  # Hann periódica
    tramas = np.lib.stride_tricks.sliding_window_view(relleno, N_FFT)[::SALTO]
    potencia = np.abs(np.fft.rfft(tramas * ventana, axis=1)) ** 2  # (tramas, 201)
    mel = _FILTROS @ potencia.T[:, :-1]                             # la última trama sobra
    mel = np.log10(np.maximum(mel, 1e-10))
    mel = np.maximum(mel, mel.max() - 8.0)
    mel = (mel + 4.0) / 4.0
    return mel[None, :, :TRAMAS].astype(np.float32)


def leer_wav(ruta: str | Path) -> np.ndarray:
    """Audio mono a 16 kHz en float32 (convierte la frecuencia si hace falta)."""
    with wave.open(str(ruta)) as w:
        frecuencia, canales, ancho = w.getframerate(), w.getnchannels(), w.getsampwidth()
        datos = w.readframes(w.getnframes())
    if ancho != 2:
        raise ValueError("Solo se admite audio de 16 bits.")
    audio = np.frombuffer(datos, dtype=np.int16).astype(np.float32) / 32768.0
    if canales > 1:
        audio = audio.reshape(-1, canales).mean(axis=1)
    if frecuencia != FRECUENCIA and len(audio) > 1:
        tiempos = np.arange(len(audio)) / frecuencia
        audio = np.interp(np.arange(0, tiempos[-1], 1 / FRECUENCIA), tiempos, audio).astype(np.float32)
    return audio


class TranscriptorGPU:
    """Codificador en la GPU (DirectML) y decodificador en el procesador."""

    def __init__(self, carpeta: Path) -> None:
        import onnxruntime as ort
        from tokenizers import Tokenizer

        if "DmlExecutionProvider" not in ort.get_available_providers():
            raise RuntimeError("Esta instalación de ONNX Runtime no tiene DirectML.")
        ort.set_default_logger_severity(3)
        opciones = ort.SessionOptions()
        # Con todas las optimizaciones, ONNX Runtime 1.24 falla al cargar el modelo fp16.
        opciones.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_BASIC
        self.codificador = ort.InferenceSession(str(carpeta / ENCODER), opciones,
                                                providers=["DmlExecutionProvider"])
        self.decodificador = ort.InferenceSession(str(carpeta / DECODER), providers=["CPUExecutionProvider"])
        self.tokenizador = Tokenizer.from_file(str(carpeta / TOKENIZER))
        self.cache = [i.name for i in self.decodificador.get_inputs() if i.name.startswith("past_key_values")]
        self.salidas = [o.name for o in self.decodificador.get_outputs()]

    def _inicio(self, pista: str) -> list[int]:
        ids = self.tokenizador.encode(" " + pista.strip(), add_special_tokens=False).ids[-200:] if pista.strip() else []
        return ([PREV, *ids] if ids else []) + [SOT, ES, TRANSCRIBE, NO_TS]

    def transcribir_audio(self, audio: np.ndarray, pista: str = "") -> str:
        oculto = self.codificador.run(None, {"input_features": log_mel(audio)})[0]
        vacio = np.zeros((1, 20, 0, 64), dtype=np.float32)
        entrada = {"input_ids": np.array([self._inicio(pista)], dtype=np.int64),
                   "encoder_hidden_states": oculto, "use_cache_branch": np.array([False])}
        entrada.update({nombre: vacio for nombre in self.cache})
        generados: list[int] = []
        cache_codificador: dict[str, np.ndarray] | None = None
        for _ in range(MAX_TOKENS):
            salida = dict(zip(self.salidas, self.decodificador.run(None, entrada)))
            logits = salida["logits"][0, -1]
            logits[NO_TS + 1:] = -np.inf  # sin marcas de tiempo
            if not generados:
                logits[[220, EOT]] = -np.inf
            siguiente = int(np.argmax(logits))
            if siguiente == EOT:
                break
            generados.append(siguiente)
            if cache_codificador is None:
                cache_codificador = {n: salida[n.replace("past_key_values", "present")]
                                     for n in self.cache if ".encoder." in n}
            entrada = {"input_ids": np.array([[siguiente]], dtype=np.int64),
                       "encoder_hidden_states": oculto, "use_cache_branch": np.array([True])}
            for nombre in self.cache:
                entrada[nombre] = (cache_codificador[nombre] if ".encoder." in nombre
                                   else salida[nombre.replace("past_key_values", "present")])
        return self.tokenizador.decode(generados, skip_special_tokens=True).strip()


_transcriptor: TranscriptorGPU | None = None
_error: str | None = None
_pausado = False  # con un juego abierto: la memoria de video es para el juego
_cerrojo = threading.Lock()


def liberar() -> bool:
    """Saca el modelo de la tarjeta (~1,5 GB, medido) hasta ``reanudar``; mientras tanto se usa
    el servidor de voz de siempre. Devuelve True si había algo cargado."""
    global _transcriptor, _pausado
    with _cerrojo:
        _pausado = True
        cargado = _transcriptor is not None
        _transcriptor = None
    if cargado:
        import gc

        gc.collect()  # las sesiones de ONNX Runtime sueltan la memoria de video al destruirse
        logging.info("Voz a texto en la GPU liberada (juego abierto).")
    return cargado


def reanudar(carpeta: Path) -> bool:
    """Vuelve a cargar el modelo en la tarjeta (al cerrar el juego)."""
    global _pausado
    with _cerrojo:
        _pausado = False
    return cargar(carpeta)


def modelo_instalado(carpeta: Path) -> bool:
    return all((carpeta / archivo).is_file() for archivo in ARCHIVOS)


def cargar(carpeta: Path) -> bool:
    """Carga el modelo una sola vez (~3 s). False si no se puede: se usa el servidor de siempre."""
    global _transcriptor, _error
    with _cerrojo:
        if _transcriptor is not None:
            return True
        if _error is not None or _pausado:
            return False
        if not modelo_instalado(carpeta):
            _error = "modelo no instalado"
            logging.info("Voz a texto en la GPU: no está el modelo en %s; uso el servidor de voz.", carpeta)
            return False
        inicio = time.perf_counter()
        try:
            _transcriptor = TranscriptorGPU(carpeta)
        except Exception as error:  # DirectML ausente, controlador viejo, archivo dañado…
            _error = str(error)
            logging.warning("No pude cargar la voz a texto en la GPU (%s); uso el servidor de voz.", error)
            return False
        logging.info("Voz a texto en la GPU lista en %.1fs.", time.perf_counter() - inicio)
        return True


def transcribir(ruta_audio: str, pista: str, carpeta: Path) -> str | None:
    """Texto reconocido, o None si hay que usar el servidor de voz (sin modelo, audio largo, error)."""
    global _transcriptor, _error
    if not cargar(carpeta):
        return None
    try:
        audio = leer_wav(ruta_audio)
        if len(audio) / FRECUENCIA > MAX_SEGUNDOS:
            return None
        with _cerrojo:
            if _transcriptor is None:  # se liberó (juego) entre la carga y ahora
                return None
            return _transcriptor.transcribir_audio(audio, pista)
    except Exception as error:
        logging.warning("Falló la voz a texto en la GPU (%s); esta vez uso el servidor de voz.", error)
        if "DXGI" in str(error) or "device" in str(error).lower():
            _transcriptor, _error = None, str(error)  # la GPU se reinició: no insistir
        return None
