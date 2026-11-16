"""«Solo mi voz»: reconocimiento del hablante, 100 % local.

Modelo: WeSpeaker ResNet34-LM (``voxceleb_resnet34_LM.onnx``, CC-BY 4.0,
https://huggingface.co/Wespeaker/wespeaker-voxceleb-resnet34-LM), en la carpeta
``speaker-onnx`` junto a Jarvis (como ``whisper-onnx``; no va a GitHub). Entrada: 80
bandas mel estilo Kaldi (25 ms / 10 ms, ventana de Povey, preénfasis 0,97, sin dither,
media restada); salida: un vector de 256 números (la «huella» de una voz).

Registro: se graban unas frases, se calcula la huella de cada una y se guarda el
promedio normalizado en ``huella_voz.json`` (en la carpeta de Jarvis, fuera de la base de
datos y de GitHub). Nunca se guarda el audio. Verificación: similitud coseno entre la
huella de la frase y la tuya; si queda por debajo del umbral de la exigencia elegida,
la frase se descarta. Las frases cortas traen menos información, así que el umbral baja
un poco para ellas.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

import jarvis_core as core

CARPETA_MODELO = "speaker-onnx"
MODELO = "voxceleb_resnet34_LM.onnx"
SHA256_MODELO = "7bb2f06e9df17cdf1ef14ee8a15ab08ed28e8d0ef5054ee135741560df2ec068"
ARCHIVO_HUELLA = "huella_voz.json"
FRECUENCIA = 16000

# Exigencia → similitud mínima (coseno). Medido con este modelo: la misma persona suele
# dar 0,55–0,85 y otra persona 0,0–0,3.
EXIGENCIAS = {"baja": 0.28, "media": 0.38, "alta": 0.48}
EXIGENCIA_POR_DEFECTO = "media"
NOMBRES_EXIGENCIA = {"baja": "BAJA", "media": "MEDIA", "alta": "ALTA"}
VOZ_CORTA = 1.5  # s de voz: por debajo el umbral baja un poco
DESCUENTO_CORTA = 0.06
MINIMO_AUDIO = 0.5  # s: con menos no se puede decidir (se deja pasar)

# Frases para registrarse: variadas, en español, de 3–4 s cada una.
FRASES_REGISTRO = (
    "Jarvis, abre el navegador y busca el clima de mañana.",
    "Pon la tele en HDMI uno y sube el volumen.",
    "Recuérdame a las ocho que tengo que llamar a mi familia.",
    "¿Cuánta memoria está usando la tarjeta gráfica ahora?",
    "Mueve la ventana de música al monitor dos y maximízala.",
    "Hoy quiero jugar un rato, abre el juego y apaga las luces.",
)
MINIMO_FRASES = 4


# --- Bandas mel estilo Kaldi -----------------------------------------------------------

_LARGO = 400  # 25 ms
_PASO = 160  # 10 ms
_FFT = 512
_BANDAS = 80


def _mel(f):
    return 1127.0 * np.log(1.0 + np.asarray(f, dtype=np.float64) / 700.0)


def _banco_mel() -> np.ndarray:
    """Filtros triangulares de Kaldi (en la escala mel, de 20 Hz a 8 kHz): 80 × 257."""
    bajo, alto = _mel(20.0), _mel(FRECUENCIA / 2)
    centros = bajo + (alto - bajo) / (_BANDAS + 1) * np.arange(_BANDAS + 2)
    mel_fft = _mel(np.arange(_FFT // 2) * FRECUENCIA / _FFT)
    banco = np.zeros((_BANDAS, _FFT // 2 + 1))
    for i in range(_BANDAS):
        izquierda, centro, derecha = centros[i], centros[i + 1], centros[i + 2]
        subida = (mel_fft - izquierda) / (centro - izquierda)
        bajada = (derecha - mel_fft) / (derecha - centro)
        banco[i, : _FFT // 2] = np.maximum(0.0, np.minimum(subida, bajada))
    return banco


_BANCO = _banco_mel()
_VENTANA = (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(_LARGO) / (_LARGO - 1))) ** 0.85  # Povey


def bandas_mel(muestras: np.ndarray) -> np.ndarray:
    """PCM (escala de 16 bits) → (cuadros × 80) log-mel con la media restada."""
    x = np.asarray(muestras, dtype=np.float64)
    if len(x) < _LARGO:
        return np.zeros((0, _BANDAS), dtype=np.float32)
    cuadros = 1 + (len(x) - _LARGO) // _PASO
    indices = np.arange(_LARGO)[None, :] + _PASO * np.arange(cuadros)[:, None]
    marcos = x[indices]
    marcos = marcos - marcos.mean(axis=1, keepdims=True)  # sin componente continua
    marcos = np.concatenate([marcos[:, :1] * (1 - 0.97), marcos[:, 1:] - 0.97 * marcos[:, :-1]], axis=1)
    marcos = marcos * _VENTANA
    potencia = np.abs(np.fft.rfft(marcos, n=_FFT)) ** 2
    energia = np.log(np.maximum(potencia @ _BANCO.T, np.finfo(np.float32).eps))
    energia = energia - energia.mean(axis=0, keepdims=True)
    return energia.astype(np.float32)


def leer_wav(ruta: str) -> np.ndarray:
    with wave.open(ruta, "rb") as archivo:
        if archivo.getsampwidth() != 2 or archivo.getframerate() != FRECUENCIA:
            raise ValueError("El audio debe ser PCM de 16 bits a 16 kHz.")
        datos = np.frombuffer(archivo.readframes(archivo.getnframes()), dtype=np.int16)
        if archivo.getnchannels() > 1:
            datos = datos.reshape(-1, archivo.getnchannels()).mean(axis=1)
    return datos.astype(np.float64)


def solo_voz(muestras: np.ndarray, umbral: float = 300.0) -> tuple[np.ndarray, float]:
    """Quita los silencios (bloques de 30 ms con energía baja): la huella es de la voz.

    Devuelve (muestras con voz, segundos de voz).
    """
    bloque = 480
    partes = [muestras[i:i + bloque] for i in range(0, len(muestras) - bloque + 1, bloque)]
    con_voz = [p for p in partes if np.sqrt(np.mean(p * p)) >= umbral]
    if not con_voz:
        return np.zeros(0), 0.0
    voz = np.concatenate(con_voz)
    return voz, len(voz) / FRECUENCIA


def similitud(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))


def umbral_para(exigencia: str, segundos_voz: float) -> float:
    umbral = EXIGENCIAS.get(exigencia, EXIGENCIAS[EXIGENCIA_POR_DEFECTO])
    return umbral - DESCUENTO_CORTA if segundos_voz < VOZ_CORTA else umbral


def promedio(huellas: list[np.ndarray]) -> np.ndarray:
    normalizadas = [h / (np.linalg.norm(h) + 1e-9) for h in huellas]
    media = np.mean(normalizadas, axis=0)
    return media / (np.linalg.norm(media) + 1e-9)


@dataclass(frozen=True)
class Resultado:
    aceptada: bool
    similitud: float | None  # None: no se pudo medir (se deja pasar)
    segundos_voz: float


# --- Modelo y huella guardada -----------------------------------------------------------

class Verificador:
    """Carga el modelo una vez (perezosamente) y compara frases con tu huella."""

    def __init__(self, carpeta: Path | None = None) -> None:
        self.carpeta = Path(carpeta) if carpeta is not None else core.APP_DIR
        self._sesion = None
        self._cerrojo = threading.Lock()
        self._huella: np.ndarray | None = None
        self._huella_leida = False

    @property
    def ruta_modelo(self) -> Path:
        return self.carpeta / CARPETA_MODELO / MODELO

    @property
    def ruta_huella(self) -> Path:
        return self.carpeta / ARCHIVO_HUELLA

    def modelo_disponible(self) -> bool:
        return self.ruta_modelo.is_file()

    def _modelo(self):
        with self._cerrojo:
            if self._sesion is None:
                import onnxruntime as ort

                if not self.modelo_disponible():
                    raise FileNotFoundError(
                        f"Falta el modelo de reconocimiento de voz ({CARPETA_MODELO}\\{MODELO})."
                    )
                opciones = ort.SessionOptions()
                opciones.intra_op_num_threads = 2  # no competir con Whisper ni con el juego
                inicio = time.perf_counter()
                self._sesion = ort.InferenceSession(str(self.ruta_modelo), opciones,
                                                    providers=["CPUExecutionProvider"])
                logging.info("Reconocimiento de voz listo en %.1fs.", time.perf_counter() - inicio)
            return self._sesion

    def huella_de(self, muestras: np.ndarray) -> tuple[np.ndarray | None, float]:
        """(huella, segundos de voz); huella None si hay muy poca voz."""
        voz, segundos = solo_voz(muestras)
        if segundos < MINIMO_AUDIO:
            return None, segundos
        bandas = bandas_mel(voz)
        salida = self._modelo().run(None, {"feats": bandas[None, :, :]})[0][0]
        return np.asarray(salida, dtype=np.float64), segundos

    # Huella del usuario
    def huella(self) -> np.ndarray | None:
        if not self._huella_leida:
            self._huella_leida = True
            try:
                datos = json.loads(self.ruta_huella.read_text(encoding="utf-8"))
                self._huella = np.asarray(datos["huella"], dtype=np.float64)
            except FileNotFoundError:
                self._huella = None
            except (ValueError, KeyError, TypeError):
                logging.warning("La huella de voz guardada está dañada; hay que registrarse de nuevo.")
                self._huella = None
        return self._huella

    def registrada(self) -> bool:
        return self.huella() is not None

    def guardar_huella(self, huellas: list[np.ndarray]) -> float:
        """Guarda el promedio; devuelve la coherencia (similitud media de cada frase con él)."""
        media = promedio(huellas)
        coherencia = float(np.mean([similitud(h, media) for h in huellas]))
        self.ruta_huella.write_text(json.dumps({
            "modelo": MODELO, "frases": len(huellas), "coherencia": round(coherencia, 3),
            "creada": time.strftime("%Y-%m-%d %H:%M"), "huella": [round(float(v), 6) for v in media],
        }), encoding="utf-8")
        self._huella, self._huella_leida = media, True
        return coherencia

    def borrar_huella(self) -> None:
        self.ruta_huella.unlink(missing_ok=True)
        self._huella, self._huella_leida = None, True

    def verificar(self, ruta_wav: str, exigencia: str) -> Resultado:
        """¿Es tu voz? Sin huella o con muy poca voz, se deja pasar (no hay con qué decidir)."""
        propia = self.huella()
        if propia is None:
            return Resultado(True, None, 0.0)
        huella, segundos = self.huella_de(leer_wav(ruta_wav))
        if huella is None:
            return Resultado(True, None, segundos)
        valor = similitud(huella, propia)
        return Resultado(valor >= umbral_para(exigencia, segundos), valor, segundos)
