"""Gestos con la mano por la cámara (fase 1: reconocer; las acciones llegan después).

MediaPipe Hand Landmarker (Google, Apache 2.0) da 21 puntos por mano en cada cuadro;
``clasificar`` decide el gesto con reglas sobre esos puntos y ``Estabilizador`` solo lo da
por bueno cuando se mantiene unos cuadros (un cuadro suelto no cuenta). Todo es local: los
cuadros solo viven en memoria, nunca se guardan ni salen del equipo.

El modelo ``hand_landmarker.task`` (7,8 MB) va en la carpeta ``gestos-modelo`` junto a
Jarvis (como ``speaker-onnx``; no va a GitHub). Sin MediaPipe o sin el modelo, los gestos
quedan como no disponibles y el resto de Jarvis funciona igual.

Medido con la cámara del usuario (2026-11-16, fase 0): solo da 640x480 (pedirle 720p por
DirectShow la deja sin imágenes; se usa Media Foundation), ~24 cuadros/s, el modelo tarda
~16 ms y la imagen llega muy oscura (brillo ~23/255): se aclara antes de buscar la mano.
"""

from __future__ import annotations

import importlib.util
import logging
import math
import queue
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path

CARPETA_MODELO = "gestos-modelo"
MODELO = "hand_landmarker.task"
TAMANO_MODELO = 7_819_105  # bytes (verificado al descargarlo)

GESTOS = ("indice", "dos_dedos", "pellizco", "puno", "mano_abierta", "otro")
NOMBRES = {
    "indice": "índice", "dos_dedos": "dos dedos · scroll", "pellizco": "pellizco", "puno": "puño",
    "mano_abierta": "mano abierta", "otro": "otro",
}

# Índices de MediaPipe: 0 muñeca; pulgar 1–4; índice 5–8; medio 9–12; anular 13–16; meñique 17–20.
MUNECA = 0
PUNTA_PULGAR = 4
BASE_INDICE, MEDIO_INDICE, PUNTA_INDICE = 5, 6, 8
DEDOS = {"indice": (6, 8), "medio": (10, 12), "anular": (14, 16), "menique": (18, 20)}  # (articulación, punta)

ESTIRADO = 1.15  # la punta, más lejos de la muñeca que la articulación media por este factor
PELLIZCO = 0.35  # distancia pulgar–índice / tamaño de la mano
CUADROS_ESTABLES = 4  # ~160 ms a 24 cuadros/s

BRILLO_OSCURO = 70.0  # por debajo se aclara la imagen
SIN_IMAGEN = 5.0  # s sin cuadros: la cámara dejó de responder
FRECUENCIA_ESTADO = 1.0  # s entre avisos de estado (cuadros/s) a la app


Punto = tuple[float, float]


def _distancia(a: Punto, b: Punto) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def dedos_estirados(puntos: Sequence[Punto]) -> set[str]:
    """Qué dedos (sin el pulgar) están estirados. Usa distancias a la muñeca, así que vale
    con la mano girada o de costado."""
    muneca = puntos[MUNECA]
    return {nombre for nombre, (media, punta) in DEDOS.items()
            if _distancia(muneca, puntos[punta]) > _distancia(muneca, puntos[media]) * ESTIRADO}


def clasificar(puntos: Sequence[Punto]) -> str:
    """Gesto de una mano a partir de sus 21 puntos (x, y normalizados)."""
    if len(puntos) < 21:
        return "otro"
    muneca = puntos[MUNECA]
    tamano = _distancia(muneca, puntos[9]) or 1e-6  # muñeca → base del dedo medio
    estirados = dedos_estirados(puntos)
    juntos = _distancia(puntos[PUNTA_PULGAR], puntos[PUNTA_INDICE]) / tamano < PELLIZCO
    # En el puño el pulgar también queda cerca del índice: es pellizco solo si el índice no está
    # enroscado del todo (su punta, más lejos de la muñeca que su nudillo).
    indice_abierto = _distancia(muneca, puntos[PUNTA_INDICE]) > _distancia(muneca, puntos[BASE_INDICE]) * 1.1
    if juntos and indice_abierto:
        return "pellizco"
    if not estirados:
        return "puno"
    if estirados == {"indice"}:
        return "indice"
    if estirados == {"indice", "medio"}:
        return "dos_dedos"
    if len(estirados) == 4:
        return "mano_abierta"
    return "otro"


class Estabilizador:
    """Da un gesto por bueno cuando se repite ``cuadros`` veces seguidas (None = sin mano)."""

    def __init__(self, cuadros: int = CUADROS_ESTABLES) -> None:
        self.cuadros = cuadros
        self.actual: str | None = None
        self._candidato: str | None = None
        self._veces = 0

    def agregar(self, gesto: str | None) -> str | None | bool:
        """Devuelve el gesto nuevo (o None si la mano se fue) cuando cambia; False si no cambió."""
        if gesto == self._candidato:
            self._veces += 1
        else:
            self._candidato, self._veces = gesto, 1
        if self._veces >= self.cuadros and self._candidato != self.actual:
            self.actual = self._candidato
            return self.actual
        return False


class Desplazamiento:
    """Scroll con dos dedos (fase 2): el movimiento vertical de las puntas del índice y el
    medio se convierte en rueda del mouse mientras se mueve la mano, como en un touchpad.

    Subir la mano baja la página (como deslizar en el celular); ``invertir`` lo da vuelta.
    ``GANANCIA``: unidades de rueda por recorrer toda la altura de la imagen (120 = una
    muesca). Se suaviza la posición, se ignora el temblor (``ZONA_MUERTA`` por cuadro) y se
    manda en tandas cada ``INTERVALO`` s, para no hacer 24 acciones por segundo.
    """

    GANANCIA = 2600.0
    ZONA_MUERTA = 0.004  # fracción de la altura por cuadro: menos es temblor de la mano
    SUAVIZADO = 0.5  # peso del cuadro nuevo en el promedio exponencial
    INTERVALO = 0.2
    MINIMO = 60  # unidades: menos no vale una acción

    def __init__(self, invertir: bool = False) -> None:
        self.signo = -1.0 if invertir else 1.0
        self._y: float | None = None
        self._acumulado = 0.0
        self._ultimo_envio = 0.0

    def mover(self, y: float, ahora: float) -> int | None:
        """Nueva altura (0 arriba – 1 abajo) de los dedos; devuelve cuánto girar la rueda, o None."""
        if self._y is None:
            self._y, self._ultimo_envio = y, ahora  # primer cuadro: solo referencia (sin salto)
            return None
        suave = self._y + (y - self._y) * self.SUAVIZADO
        dy = suave - self._y
        self._y = suave
        if abs(dy) >= self.ZONA_MUERTA:
            # Subir la mano (dy < 0) → rueda negativa → la página baja.
            self._acumulado += dy * self.GANANCIA * self.signo
        if ahora - self._ultimo_envio >= self.INTERVALO and abs(self._acumulado) >= self.MINIMO:
            return self._enviar(ahora)
        return None

    def terminar(self, ahora: float = 0.0) -> int | None:
        """Se soltó el gesto: lo que quedó acumulado (si vale la pena) y vuelve a empezar."""
        pendiente = self._enviar(ahora) if abs(self._acumulado) >= self.MINIMO / 2 else None
        self._y, self._acumulado = None, 0.0
        return pendiente

    def _enviar(self, ahora: float) -> int:
        cantidad = int(round(self._acumulado))
        self._acumulado -= cantidad
        self._ultimo_envio = ahora
        return cantidad


def altura_dos_dedos(puntos: Sequence[Punto]) -> float:
    """Altura media de las puntas del índice y del medio (lo que mueve el scroll)."""
    return (puntos[PUNTA_INDICE][1] + puntos[12][1]) / 2


def aclarar(cuadro, cv2, clahe):
    """Imagen oscura (brillo < 70): ganancia hasta x4 y realce local de contraste."""
    brillo = float(cuadro.mean())
    if brillo >= BRILLO_OSCURO:
        return cuadro, brillo
    ganancia = min(4.0, 90.0 / max(brillo, 1.0))
    cuadro = cv2.convertScaleAbs(cuadro, alpha=ganancia, beta=0)
    lab = cv2.cvtColor(cuadro, cv2.COLOR_BGR2LAB)
    lab[:, :, 0] = clahe.apply(lab[:, :, 0])
    return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR), brillo


def ruta_modelo(carpeta: Path) -> Path:
    return Path(carpeta) / CARPETA_MODELO / MODELO


def que_falta(carpeta: Path) -> str | None:
    """None si se pueden usar los gestos; si no, qué falta (para decírselo al usuario)."""
    if importlib.util.find_spec("mediapipe") is None or importlib.util.find_spec("cv2") is None:
        return "falta MediaPipe (pip install mediapipe)"
    if not ruta_modelo(carpeta).is_file():
        return f"falta el modelo de la mano ({CARPETA_MODELO}\\{MODELO})"
    return None


# --- Hilo de la cámara ---------------------------------------------------------------

class DetectorGestos(threading.Thread):
    """Lee la cámara, busca la mano y publica en ``events``:

    - ``("gesture", gesto | None)`` cuando cambia el gesto estable (None: no hay mano);
    - ``("gesture_stats", cuadros_por_s, brillo)`` una vez por segundo;
    - ``("gestures_error", mensaje)`` si la cámara o el modelo fallan (el hilo termina).

    Mientras ``puede_usar()`` da False (modo juego) cierra la cámara (se apaga su luz) y espera.
    ``vista`` (la ventana de cámara de Ajustes) recibe la última imagen con los puntos dibujados.
    """

    def __init__(self, events: queue.Queue, puede_usar: Callable[[], bool], carpeta: Path, *,
                 abrir_camara: Callable | None = None, crear_modelo: Callable | None = None,
                 al_desplazar: Callable[[int], None] | None = None, invertir_scroll: bool = False) -> None:
        """``al_desplazar(cantidad)``: girar la rueda (la app lo hace en otro hilo, por el registro
        de herramientas; este hilo nunca espera)."""
        super().__init__(name="JarvisGestos", daemon=True)
        self.events = events
        self.puede_usar = puede_usar
        self.carpeta = Path(carpeta)
        self._abrir_camara = abrir_camara
        self._crear_modelo = crear_modelo
        self._stop_event = threading.Event()
        self.vista = False  # la vista de cámara está abierta: guardar la imagen anotada
        self.ultima_imagen = None  # RGB (numpy) para la vista; solo en memoria
        self.gesto: str | None = None
        self.cuadros_por_s = 0.0
        self.al_desplazar = al_desplazar
        self.desplazamiento = Desplazamiento(invertir_scroll)

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        try:
            self._bucle()
        except Exception as error:
            logging.exception("Los gestos dejaron de funcionar.")
            self.events.put(("gestures_error", str(error) or type(error).__name__))

    # Separado para poder reemplazarlo en los tests.
    def _modelo(self):
        if self._crear_modelo is not None:
            return self._crear_modelo()
        from mediapipe.tasks.python import BaseOptions, vision

        opciones = vision.HandLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(ruta_modelo(self.carpeta))),
            running_mode=vision.RunningMode.VIDEO, num_hands=1,
            # Un poco más bajos que lo normal (0,5): una vez encontrada, la mano se sigue aunque
            # gire (con la palma hacia el piso el detector la pierde: la ve casi de canto).
            min_hand_detection_confidence=0.5, min_hand_presence_confidence=0.4, min_tracking_confidence=0.4)
        return vision.HandLandmarker.create_from_options(opciones)

    def _camara(self, cv2):
        if self._abrir_camara is not None:
            return self._abrir_camara()
        return cv2.VideoCapture(0, cv2.CAP_MSMF)  # 640x480: lo que da la cámara

    def _bucle(self) -> None:
        import cv2
        import mediapipe as mp

        modelo = self._modelo()
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        estabilizador = Estabilizador()
        camara = None
        inicio = time.perf_counter()
        ultimo_cuadro = time.monotonic()
        ultimo_estado = time.monotonic()
        cuadros_en_segundo = 0
        brillo = 0.0
        try:
            while not self._stop_event.is_set():
                if not self.puede_usar():
                    if camara is not None:
                        camara.release()
                        camara = None
                        logging.info("Gestos en pausa: cámara cerrada.")
                        if estabilizador.actual is not None:
                            estabilizador = Estabilizador()
                            self._publicar_gesto(None)
                    self._stop_event.wait(0.5)
                    continue
                if camara is None:
                    camara = self._camara(cv2)
                    if not camara.isOpened():
                        raise RuntimeError("no pude abrir la cámara (¿la está usando otro programa?)")
                    ultimo_cuadro = time.monotonic()
                    logging.info("Gestos: cámara abierta.")
                ok, cuadro = camara.read()
                if not ok:
                    if time.monotonic() - ultimo_cuadro > SIN_IMAGEN:
                        raise RuntimeError("la cámara no entrega imágenes")
                    self._stop_event.wait(0.02)
                    continue
                ultimo_cuadro = time.monotonic()
                cuadro = cv2.flip(cuadro, 1)  # espejo: mover la mano a la derecha va a la derecha
                cuadro, brillo = aclarar(cuadro, cv2, clahe)
                rgb = cv2.cvtColor(cuadro, cv2.COLOR_BGR2RGB)
                imagen = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                resultado = modelo.detect_for_video(imagen, int((time.perf_counter() - inicio) * 1000))
                puntos = None
                if resultado.hand_landmarks:
                    puntos = [(p.x, p.y) for p in resultado.hand_landmarks[0]]
                gesto = clasificar(puntos) if puntos else None
                cambio = estabilizador.agregar(gesto)
                if cambio is not False:
                    self._publicar_gesto(cambio)
                self._desplazar(estabilizador.actual, gesto, puntos)
                if self.vista:
                    self.ultima_imagen = dibujar(rgb, puntos, cv2)
                cuadros_en_segundo += 1
                ahora = time.monotonic()
                if ahora - ultimo_estado >= FRECUENCIA_ESTADO:
                    self.cuadros_por_s = cuadros_en_segundo / (ahora - ultimo_estado)
                    self.events.put(("gesture_stats", round(self.cuadros_por_s, 1), round(brillo)))
                    cuadros_en_segundo, ultimo_estado = 0, ahora
        finally:
            if camara is not None:
                camara.release()
            try:
                modelo.close()
            except Exception:
                pass

    def _desplazar(self, estable: str | None, gesto: str | None, puntos) -> None:
        """Fase 2: con «dos dedos» estable, el movimiento vertical hace scroll."""
        if self.al_desplazar is None:
            return
        if estable == "dos_dedos" and gesto == "dos_dedos" and puntos:
            cantidad = self.desplazamiento.mover(altura_dos_dedos(puntos), time.monotonic())
        else:
            cantidad = self.desplazamiento.terminar(time.monotonic())
        if cantidad:
            self.al_desplazar(cantidad)

    def _publicar_gesto(self, gesto: str | None) -> None:
        self.gesto = gesto
        self.events.put(("gesture", gesto))


CONEXIONES = ((0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (5, 9), (9, 10), (10, 11),
              (11, 12), (9, 13), (13, 14), (14, 15), (15, 16), (13, 17), (17, 18), (18, 19), (19, 20), (0, 17))


def dibujar(rgb, puntos: Sequence[Punto] | None, cv2):
    """Copia de la imagen con la mano dibujada (para la vista de cámara)."""
    imagen = rgb.copy()
    if puntos:
        alto, ancho = imagen.shape[:2]
        xy = [(int(x * ancho), int(y * alto)) for x, y in puntos]
        for a, b in CONEXIONES:
            cv2.line(imagen, xy[a], xy[b], (255, 194, 66), 2)
        for p in xy:
            cv2.circle(imagen, p, 4, (255, 60, 40), -1)
    return imagen
