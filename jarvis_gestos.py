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

GESTOS = ("indice", "dos_dedos", "pellizco", "puno", "mano_abierta", "pulgar_arriba", "pulgar_abajo",
          "cuernos", "llamame", "ok", "otro")
# Fase 5: gestos que el usuario asigna a una rutina, una frase o un atajo de teclado. «paz» no es una
# clase de ``clasificar``: es «dos dedos» separados en V y con la mano quieta (lo detecta ``Atajos``),
# para no romper el scroll con dos dedos.
PROPIOS = ("cuernos", "llamame", "ok", "paz")
EMOJIS = {"cuernos": "🤘", "llamame": "🤙", "ok": "👌", "paz": "✌"}
NOMBRES = {
    "indice": "índice", "dos_dedos": "dos dedos · scroll", "pellizco": "pellizco", "puno": "puño",
    "mano_abierta": "mano abierta", "pulgar_arriba": "pulgar arriba", "pulgar_abajo": "pulgar abajo",
    "cuernos": "cuernos", "llamame": "llamame", "ok": "OK", "paz": "paz", "otro": "otro",
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


PULGAR_ESTIRADO = 0.75  # punta del pulgar–nudillo del índice / tamaño de la mano
PULGAR_VERTICAL = 0.45  # cuánto más arriba (o abajo) que su base tiene que estar la punta


def sentido_pulgar(puntos: Sequence[Punto], tamano: float) -> str | None:
    """Con los cuatro dedos cerrados: ``pulgar_arriba``, ``pulgar_abajo`` o None (puño).

    El pulgar está estirado si su punta queda lejos del nudillo del índice (en el puño queda
    pegado a los dedos). Arriba o abajo según la punta respecto de su base (y crece hacia abajo).
    """
    punta, base = puntos[PUNTA_PULGAR], puntos[2]
    if _distancia(punta, puntos[BASE_INDICE]) / tamano < PULGAR_ESTIRADO:
        return None
    vertical = (base[1] - punta[1]) / tamano
    if vertical > PULGAR_VERTICAL:
        return "pulgar_arriba"
    if vertical < -PULGAR_VERTICAL:
        return "pulgar_abajo"
    return None


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
    if juntos and {"medio", "anular", "menique"} <= estirados:
        return "ok"  # 👌: pulgar con índice y los otros tres estirados (antes que el pellizco)
    if juntos and indice_abierto:
        return "pellizco"
    if not estirados:
        pulgar = sentido_pulgar(puntos, tamano)
        return pulgar or "puno"
    if estirados == {"indice"}:
        return "indice"
    if estirados == {"indice", "medio"}:
        return "dos_dedos"
    if len(estirados) == 4:
        return "mano_abierta"
    if estirados == {"indice", "menique"}:
        return "cuernos"
    if estirados == {"menique"} and _distancia(puntos[PUNTA_PULGAR], puntos[BASE_INDICE]) / tamano >= PULGAR_ESTIRADO:
        return "llamame"
    return "otro"


SEPARACION_PAZ = 0.45  # puntas del índice y del medio / tamaño de la mano: más es una V


def en_v(puntos: Sequence[Punto]) -> bool:
    """«Dos dedos» con el índice y el medio separados en V (✌), no juntos (el scroll)."""
    tamano = _distancia(puntos[MUNECA], puntos[9]) or 1e-6
    return _distancia(puntos[PUNTA_INDICE], puntos[12]) / tamano >= SEPARACION_PAZ


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
    muesca). La posición se suaviza y el temblor se ignora con una «banda elástica»: un
    ancla sigue a la mano dejando ``BANDA`` de juego, así que temblar menos que eso no
    mueve nada pero un movimiento lento y continuo pasa entero. Se manda en tandas cada
    ``INTERVALO`` s (unas 20 por segundo, para que se vea continuo).

    2026-11-16: antes la zona muerta era por cuadro (0,004): el scroll lento se perdía entero
    y se sentía cortado; y las tandas eran cada 0,2 s: a tirones.
    """

    GANANCIA = 2600.0
    BANDA = 0.006  # fracción de la altura de la imagen: temblor que no mueve nada
    SUAVIZADO = 0.6  # peso del cuadro nuevo en el promedio exponencial
    INTERVALO = 0.05
    MINIMO = 20  # unidades: menos no vale una acción

    def __init__(self, invertir: bool = False) -> None:
        self.signo = -1.0 if invertir else 1.0
        self._y: float | None = None
        self._ancla = 0.0
        self._acumulado = 0.0
        self._ultimo_envio = 0.0
        # Diagnóstico de la sesión (log al terminar): cuadros, tandas, salteados, total.
        self.cuadros = self.tandas = self.salteados = 0
        self.total = 0

    def mover(self, y: float, ahora: float) -> int | None:
        """Nueva altura (0 arriba – 1 abajo) de los dedos; devuelve cuánto girar la rueda, o None."""
        self.cuadros += 1
        if self._y is None:
            self._y = self._ancla = y  # primer cuadro: solo referencia (sin salto)
            self._ultimo_envio = ahora
            return None
        self._y += (y - self._y) * self.SUAVIZADO
        diferencia = self._y - self._ancla
        if abs(diferencia) > self.BANDA:
            nueva = self._y - math.copysign(self.BANDA, diferencia)
            # Subir la mano (y baja) → rueda negativa → la página baja.
            self._acumulado += (nueva - self._ancla) * self.GANANCIA * self.signo
            self._ancla = nueva
        if ahora - self._ultimo_envio >= self.INTERVALO and abs(self._acumulado) >= self.MINIMO:
            return self._enviar(ahora)
        return None

    def saltear(self) -> None:
        """Un cuadro raro (borroso, sin mano) con el gesto todavía estable: no corta el scroll."""
        self.salteados += 1

    def terminar(self, ahora: float = 0.0) -> int | None:
        """Se soltó el gesto: lo que quedó acumulado (si vale la pena) y vuelve a empezar."""
        pendiente = self._enviar(ahora) if abs(self._acumulado) >= self.MINIMO / 2 else None
        if self.cuadros >= 10:
            logging.info("Scroll con gestos: %d cuadros, %d tandas, %d cuadros salteados, total %d.",
                         self.cuadros, self.tandas, self.salteados, self.total)
        self._y, self._acumulado = None, 0.0
        self.cuadros = self.tandas = self.salteados = self.total = 0
        return pendiente

    def _enviar(self, ahora: float) -> int:
        cantidad = int(round(self._acumulado))
        self._acumulado -= cantidad
        self._ultimo_envio = ahora
        self.tandas += 1
        self.total += cantidad
        return cantidad


class FiltroUnEuro:
    """Filtro «One Euro» (Casiez et al., 2012): suaviza mucho cuando la mano está casi quieta
    (sin temblor) y poco cuando se mueve rápido (sin retraso). Para el puntero."""

    def __init__(self, min_corte: float = 1.0, beta: float = 0.01, corte_derivada: float = 1.0) -> None:
        self.min_corte, self.beta, self.corte_derivada = min_corte, beta, corte_derivada
        self._x: float | None = None
        self._dx = 0.0
        self._t = 0.0

    @staticmethod
    def _alfa(corte: float, dt: float) -> float:
        tau = 1.0 / (2 * math.pi * corte)
        return 1.0 / (1.0 + tau / dt)

    def __call__(self, x: float, t: float) -> float:
        if self._x is None or t <= self._t:
            self._x, self._t = x, t
            return x
        dt = t - self._t
        dx = (x - self._x) / dt
        self._dx += self._alfa(self.corte_derivada, dt) * (dx - self._dx)
        corte = self.min_corte + self.beta * abs(self._dx)
        self._x += self._alfa(corte, dt) * (x - self._x)
        self._t = t
        return self._x

    def reiniciar(self) -> None:
        self._x = None


# Zona de la imagen (ya en espejo) que recorre el centro de la palma y cubre todo el monitor: la
# cámara del usuario está centrada arriba del monitor 1, así que la mano de frente cae en el
# centro. 2026-11-16: con (0,28–0,72 × 0,25–0,68) costaba llegar a los bordes: ahora ~30 % del ancho.
ZONA_PUNTERO = (0.35, 0.32, 0.65, 0.60)  # izquierda, arriba, derecha, abajo
# Centro de la palma: muñeca y los cuatro nudillos. Un solo punto (el nudillo del índice) temblaba
# varios píxeles por cuadro; el promedio de cinco casi no, y no se mueve al pellizcar.
PALMA = (0, 5, 9, 13, 17)


def centro_palma(puntos: Sequence[Punto]) -> Punto:
    return (sum(puntos[i][0] for i in PALMA) / len(PALMA), sum(puntos[i][1] for i in PALMA) / len(PALMA))


def a_pantalla(x: float, y: float, monitor, zona=ZONA_PUNTERO) -> tuple[int, int]:
    """Punto de la imagen (0–1) → píxel físico del monitor (``x``, ``y``, ``ancho``, ``alto``)."""
    izquierda, arriba, derecha, abajo = zona
    nx = min(1.0, max(0.0, (x - izquierda) / (derecha - izquierda)))
    ny = min(1.0, max(0.0, (y - arriba) / (abajo - arriba)))
    return round(monitor.x + nx * (monitor.ancho - 1)), round(monitor.y + ny * (monitor.alto - 1))


def _mediana(valores: list[float]) -> float:
    ordenados = sorted(valores)
    return ordenados[len(ordenados) // 2] if ordenados else 0.0


class Puntero:
    """Fase 3: con el índice estirado, el puntero sigue la mano; el pellizco hace clic o arrastra.

    ``actualizar`` devuelve acciones en orden: ``("mover", x, y)``, ``("click", x, y)``,
    ``("down", x, y)`` (empieza a arrastrar) y ``("up", x, y)`` (suelta).

    - Se arma con «índice» estable y sigue armado mientras el gesto sea índice o pellizco.
    - Sigue el centro de la palma, suavizado (``FiltroUnEuro``) y sin moverse por menos de
      ``MOVIMIENTO_MINIMO`` px: así no tiembla.
    - Apenas el pulgar se acerca al índice (``ACERCA``) el puntero se congela, para que el clic
      caiga donde apuntabas (y en la posición de unos 200 ms antes de empezar el pellizco).
    - Pellizco con histéresis (se cierra por debajo de ``CIERRA``, se abre por encima de
      ``ABRE``; ``CUADROS`` seguidos para cambiar). Soltarlo antes de ``CLIC_MAXIMO`` es un clic;
      sostenerlo aprieta el botón y se arrastra con la mano.
    - Cualquier otro gesto (o perder la mano) desarma y suelta el botón si estaba apretado.
    """

    # Pulgar–índice / tamaño de la mano (en la imagen plana, si no hay 3D): por debajo, el puntero se
    # queda quieto (va a hacer clic). 2026-11-16: con 0,60, el pulgar recogido al apuntar lo
    # congelaba solo y después saltaba.
    ACERCA = 0.45
    CIERRA, ABRE = 0.35, 0.50
    # Lo mismo en metros, con los puntos 3D reales de la mano (``hand_world_landmarks``). En la
    # imagen plana, con la mano girada o moviéndose, la punta del pulgar podía quedar «encima» de la
    # del índice estando a centímetros: clics sin pellizcar (2026-11-16). En 3D eso no pasa.
    ACERCA_M = 0.040
    CIERRA_M, ABRE_M = 0.030, 0.045
    CUADROS = 2
    CLIC_MINIMO = 0.08  # s de pellizco cerrado: menos es ruido de un par de cuadros, no un clic
    CLIC_MAXIMO = 0.45  # s
    MOVIMIENTO_CLIC = 90  # px de pantalla que puede moverse la mano durante el pellizco de un clic
    MOVIMIENTO_MINIMO = 1  # px (el deslizamiento entre cuadros de la app suaviza el resto)
    # «Banda elástica» en píxeles de pantalla: con la cámara de 640 px y la zona del 30 % de la
    # imagen, un píxel de error de la cámara son ~10 px en pantalla; la mano «quieta» saltaba 7–13 px
    # por cuadro (log del 2026-11-16) y el puntero temblaba. Dentro de este radio no se mueve; al
    # salir lo sigue (apenas BANDA px detrás).
    BANDA = 10.0
    HISTORIAL = 5  # cuadros (~200 ms): el clic va donde estaba el puntero antes de acercar el pulgar

    def __init__(self, monitor) -> None:
        self.monitor = monitor
        self.armado = False
        self.arrastrando = False
        # 2026-11-16: con min_corte 0,35 se atrasaba con la mano lenta y después «alcanzaba» de golpe
        # (tirones); el deslizamiento entre cuadros (app) ya suaviza, así que el filtro va más liviano.
        self._fx = FiltroUnEuro(min_corte=0.5, beta=0.006)
        self._fy = FiltroUnEuro(min_corte=0.5, beta=0.006)
        self._ancla: tuple[float, float] | None = None
        self._filtrado: tuple[int, int] | None = None
        self._pos: tuple[int, int] | None = None
        self._historial: list[tuple[int, int]] = []
        self._pellizco = False
        self._cuenta = 0
        self._desde: float | None = None  # inicio del pellizco
        self._donde: tuple[int, int] | None = None  # dónde se pellizcó (clic / inicio del arrastre)
        self._crudo: tuple[int, int] | None = None
        self._saltos_crudos: list[float] = []  # para medir el temblor (log al soltar)
        self._saltos_filtrados: list[float] = []
        self._congelados = 0  # cuadros quieto por el pulgar cerca (log al soltar)
        self._sin_mano = 0  # cuadros sin mano salteados sin soltar el puntero (log al soltar)
        self._crudo_desde: tuple[int, int] | None = None  # dónde estaba la mano al empezar el pellizco
        self._minima = math.inf  # distancia mínima pulgar–índice durante el pellizco (log)
        self.descartados = 0  # pellizcos que no fueron clic (muy cortos o con la mano en movimiento)

    def _medir(self, crudo: tuple[int, int], filtrado: tuple[int, int]) -> None:
        if self._crudo is not None and self._filtrado is not None:
            self._saltos_crudos.append(math.hypot(crudo[0] - self._crudo[0], crudo[1] - self._crudo[1]))
            self._saltos_filtrados.append(math.hypot(filtrado[0] - self._filtrado[0],
                                                     filtrado[1] - self._filtrado[1]))
        self._crudo, self._filtrado = crudo, filtrado

    def _desarmar(self) -> list[tuple]:
        acciones = [("up", *self._pos)] if self.arrastrando and self._pos else []
        if len(self._saltos_crudos) >= 40:
            # Solo números, para afinar: cuánto salta el puntero de un cuadro al otro.
            logging.info("Puntero con gestos: %d cuadros; salto por cuadro (mediana) %.1f px sin filtrar, "
                         "%.1f px filtrado; %d congelados por el pulgar, %d sin mano salteados.",
                         len(self._saltos_crudos), _mediana(self._saltos_crudos),
                         _mediana(self._saltos_filtrados), self._congelados, self._sin_mano)
        self.armado = self.arrastrando = self._pellizco = False
        self._cuenta, self._desde, self._donde = 0, None, None
        self._fx.reiniciar()
        self._fy.reiniciar()
        self._pos = self._crudo = self._filtrado = self._ancla = None
        self._historial, self._saltos_crudos, self._saltos_filtrados = [], [], []
        self._congelados = self._sin_mano = 0
        return acciones

    def soltar(self) -> list[tuple]:
        """Se apagan o pausan los gestos: nunca dejar el botón apretado."""
        return self._desarmar()

    # Con el puntero ya armado, estos gestos estables lo mantienen («otro»: el dedo medio un poco
    # abierto, un cuadro raro). Solo lo sueltan puño, mano abierta, dos dedos o perder la mano.
    SIGUE_ARMADO = ("indice", "pellizco", "ok", "otro")  # «ok»: pellizco con los otros dedos abiertos

    def _con_banda(self, fx: float, fy: float) -> tuple[int, int]:
        """El ancla solo se mueve cuando el punto filtrado sale del radio ``BANDA``: el temblor de la
        mano quieta no mueve el puntero, el movimiento de verdad sí."""
        if self._ancla is None:
            self._ancla = (fx, fy)
        else:
            dx, dy = fx - self._ancla[0], fy - self._ancla[1]
            distancia = math.hypot(dx, dy)
            if distancia > self.BANDA:
                k = (distancia - self.BANDA) / distancia
                self._ancla = (self._ancla[0] + dx * k, self._ancla[1] + dy * k)
        return round(self._ancla[0]), round(self._ancla[1])

    def _distancia_pellizco(self, puntos: Sequence[Punto], mundo) -> tuple[float, float, float, float, str]:
        """(distancia, acerca, cierra, abre, unidad): en metros con los puntos 3D; si no, relación."""
        if mundo is not None and len(mundo) >= 21:
            a, b = mundo[PUNTA_PULGAR], mundo[PUNTA_INDICE]
            d = math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(3)))
            return d, self.ACERCA_M, self.CIERRA_M, self.ABRE_M, "m"
        tamano = _distancia(puntos[MUNECA], puntos[9]) or 1e-6
        return (_distancia(puntos[PUNTA_PULGAR], puntos[PUNTA_INDICE]) / tamano,
                self.ACERCA, self.CIERRA, self.ABRE, "relación")

    def actualizar(self, estable: str | None, puntos: Sequence[Punto] | None, ahora: float,
                   mundo=None) -> list[tuple]:
        """``mundo``: los 21 puntos 3D reales de la mano en metros (MediaPipe), para el pellizco."""
        if not self.armado:
            if estable != "indice" or puntos is None:
                return []  # se arma solo apuntando con el índice
        elif estable not in self.SIGUE_ARMADO:
            return self._desarmar()
        elif puntos is None:
            # Un cuadro sin mano (borroso, mano rápida) con el gesto todavía estable: no se suelta
            # ni se reinicia (2026-11-16: se frenaba y al cuadro siguiente pegaba un salto).
            self._sin_mano += 1
            return []
        self.armado = True
        acciones: list[tuple] = []
        crudo = a_pantalla(*centro_palma(puntos), self.monitor)
        x, y = self._con_banda(self._fx(crudo[0], ahora), self._fy(crudo[1], ahora))
        self._medir(crudo, (x, y))

        razon, acerca, cierra, abre, unidad = self._distancia_pellizco(puntos, mundo)
        if self._pellizco:
            self._minima = min(self._minima, razon)
        quiere = razon < cierra if not self._pellizco else razon < abre
        self._cuenta = self._cuenta + 1 if quiere != self._pellizco else 0
        if self._cuenta >= self.CUADROS:
            self._pellizco, self._cuenta = quiere, 0
            if quiere:  # empezó el pellizco: dónde estaba el puntero antes de acercar el pulgar
                self._desde, self._crudo_desde, self._minima = ahora, crudo, razon
                self._donde = self._historial[0] if self._historial else (self._pos or (x, y))
            else:  # se soltó
                if self.arrastrando:
                    acciones.append(("up", *(self._pos or (x, y))))
                    self.arrastrando = False
                elif self._donde is not None:
                    duracion = ahora - (self._desde or ahora)
                    movimiento = (math.hypot(crudo[0] - self._crudo_desde[0], crudo[1] - self._crudo_desde[1])
                                  if self._crudo_desde else 0.0)
                    minima = f"{self._minima * 100:.1f} cm" if unidad == "m" else f"{self._minima:.2f}"
                    if duracion < self.CLIC_MINIMO or movimiento > self.MOVIMIENTO_CLIC:
                        # Un par de cuadros de ruido o la mano en movimiento: no era un clic.
                        self.descartados += 1
                        logging.info("Pellizco descartado (no es clic): %.0f ms, mínima %s, la mano se movió "
                                     "%.0f px.", duracion * 1000, minima, movimiento)
                    else:
                        acciones.append(("click", *self._donde))
                        logging.info("Clic con gestos: %.0f ms, distancia mínima %s, la mano se movió %.0f px.",
                                     duracion * 1000, minima, movimiento)
                self._desde, self._donde, self._crudo_desde = None, None, None

        if self._pellizco and not self.arrastrando and self._desde is not None \
                and ahora - self._desde >= self.CLIC_MAXIMO:
            acciones.append(("down", *self._donde))
            self.arrastrando = True
        # Quieto mientras el pulgar se acerca o pellizca (salvo arrastrando).
        congelado = not self.arrastrando and (self._pellizco or razon < acerca)
        if congelado:
            self._congelados += 1
        if not congelado and (self._pos is None or abs(x - self._pos[0]) + abs(y - self._pos[1])
                              >= self.MOVIMIENTO_MINIMO):
            self._pos = (x, y)
            acciones.append(("mover", x, y))
        if not congelado and self._pos is not None:
            self._historial = (self._historial + [self._pos])[-self.HISTORIAL:]
        return acciones


class Atajos:
    """Fase 4: gestos que disparan una acción (no continuos como el scroll o el puntero).

    ``actualizar`` devuelve una lista de ``(accion, repetida)``:

    - ``("pausa", False)``: mano abierta (estable) quieta ``QUIETA_PAUSA`` s (play/pausa). Una vez:
      para repetirla hay que cambiar de gesto o mover la mano.
    - ``("atras" | "adelante", False)``: la mano abierta cruza hacia un costado (al menos
      ``DESLIZAR`` de la imagen en ``VENTANA_DESLIZAR`` s, sobre todo horizontal). Como en el
      touchpad: hacia la derecha (en espejo) es atrás. ``ESPERA_DESLIZAR`` s entre deslizamientos.
    - ``("pulgar_arriba" | "pulgar_abajo", repetida)``: sostenido ``SOSTENER_PULGAR`` s; mientras
      siga, se repite cada ``REPETIR_PULGAR`` s con ``repetida=True`` (la app decide: una
      confirmación solo con la primera, el volumen con todas).

    2026-11-16: el deslizar no se detectaba nunca: exigía el gesto estable (4 cuadros: el comienzo
    quedaba afuera), borraba el rastro en cuanto un cuadro borroso dejaba de verse como mano abierta
    y pedía 0,22 de la imagen en 0,4 s. Ahora el rastro usa también el gesto de cada cuadro y
    aguanta ``SIN_ABIERTA`` s sin verla.
    """

    QUIETA_PAUSA = 1.2
    MOVIMIENTO_QUIETA = 0.035  # fracción de la imagen: más es «moviendo la mano»
    DESLIZAR = 0.18
    VENTANA_DESLIZAR = 0.6
    VERTICAL_MAXIMA = 0.6  # |dy| como mucho esta fracción de |dx|
    ESPERA_DESLIZAR = 1.0
    SIN_ABIERTA = 0.6  # s sin ver la mano abierta antes de olvidar su rastro
    SOSTENER_PULGAR = 0.4
    REPETIR_PULGAR = 0.6
    SOSTENER_PROPIO = 0.6  # gestos propios (fase 5): sostenidos este tiempo, con un anillo de progreso
    QUIETA_PAZ = 0.04  # ✌ tiene que estar quieta (si se mueve, es scroll)

    def __init__(self) -> None:
        self._quieta: list[tuple[float, float, float]] = []  # (t, x, y) con «mano abierta» estable
        self._rastro: list[tuple[float, float, float]] = []  # (t, x, y) con la mano abierta (cuadro o estable)
        self._ultima_abierta = -math.inf
        self._recorrido_maximo = 0.0  # para el log si no llegó a deslizar
        self._pausa_hecha = False
        self._ultimo_deslizar = -math.inf
        self._pulgar: str | None = None
        self._pulgar_desde = 0.0
        self._pulgar_ultimo: float | None = None
        self._propio: str | None = None  # gesto propio que se está sosteniendo
        self._propio_desde = 0.0
        self._propio_hecho = False
        self._paz: list[tuple[float, float, float]] = []
        self.progreso: tuple[str, float] | None = None  # (gesto, 0–1) para el indicador
        self.apuntando = False  # el puntero está armado (lo pone el hilo de la cámara)

    def actualizar(self, estable: str | None, puntos: Sequence[Punto] | None, ahora: float,
                   gesto: str | None = None) -> list[tuple]:
        """``gesto``: el de este cuadro, sin estabilizar (el deslizar no espera 4 cuadros)."""
        acciones: list[tuple] = []
        abierta = puntos is not None and (gesto == "mano_abierta" or estable == "mano_abierta")
        if abierta:
            x, y = centro_palma(puntos)
            self._ultima_abierta = ahora
            self._rastro.append((ahora, x, y))
            self._rastro = [m for m in self._rastro if ahora - m[0] <= self.VENTANA_DESLIZAR]
            acciones += self._deslizar(ahora, x, y)
        elif ahora - self._ultima_abierta > self.SIN_ABIERTA:
            self._olvidar_rastro()
        if estable != "mano_abierta":
            self._quieta, self._pausa_hecha = [], False
        elif puntos is not None and not acciones:
            x, y = centro_palma(puntos)
            self._quieta.append((ahora, x, y))
            self._quieta = [m for m in self._quieta if ahora - m[0] <= max(self.QUIETA_PAUSA, 1.5)]
            acciones += self._pausa(ahora, x, y)
        acciones += self._pulgares(estable, ahora)
        acciones += self._propios(estable, puntos, ahora)
        return acciones

    def _propio_actual(self, estable: str | None, puntos, ahora: float) -> str | None:
        """Qué gesto propio se está haciendo (✌ = dos dedos en V y quietos)."""
        if estable in ("cuernos", "llamame", "ok"):
            self._paz = []
            return estable
        if estable == "dos_dedos" and puntos is not None and en_v(puntos):
            x, y = centro_palma(puntos)
            self._paz = [m for m in self._paz + [(ahora, x, y)] if ahora - m[0] <= self.SOSTENER_PROPIO]
            xs, ys = [m[1] for m in self._paz], [m[2] for m in self._paz]
            if max(xs) - min(xs) <= self.QUIETA_PAZ and max(ys) - min(ys) <= self.QUIETA_PAZ:
                return "paz"
            return None
        if estable == "dos_dedos" and puntos is None and self._propio == "paz":
            return "paz"  # un cuadro sin mano no corta el sostener
        self._paz = []
        return None

    def _propios(self, estable: str | None, puntos, ahora: float) -> list[tuple]:
        """Fase 5: un gesto propio sostenido ``SOSTENER_PROPIO`` s dispara su acción una vez; se
        rearma al cambiar de gesto. ``progreso`` sigue cuánto falta (para el anillo). Mientras se usa
        el puntero (``apuntando``) no se disparan: un arrastre con los dedos abiertos parece 👌."""
        actual = None if self.apuntando else self._propio_actual(estable, puntos, ahora)
        if actual != self._propio:
            self._propio, self._propio_desde, self._propio_hecho = actual, ahora, False
        if actual is None or self._propio_hecho:
            self.progreso = None
            return []
        fraccion = min(1.0, (ahora - self._propio_desde) / self.SOSTENER_PROPIO)
        self.progreso = (actual, fraccion)
        if fraccion >= 1.0:
            self._propio_hecho, self.progreso = True, None
            return [(f"propio:{actual}", False)]
        return []

    def _olvidar_rastro(self) -> None:
        if self._recorrido_maximo >= 0.08:
            # Movió la mano abierta de costado pero no alcanzó: cuánto, para ajustar los umbrales.
            logging.info("Deslizar no alcanzó: recorrido horizontal máximo %.2f de la imagen en %.1f s "
                         "(hace falta %.2f).", self._recorrido_maximo, self.VENTANA_DESLIZAR, self.DESLIZAR)
        self._rastro, self._recorrido_maximo = [], 0.0

    def _deslizar(self, ahora: float, x: float, y: float) -> list[tuple]:
        t0, x0, y0 = self._rastro[0]
        dx, dy = x - x0, y - y0
        self._recorrido_maximo = max(self._recorrido_maximo, abs(dx))
        if ahora - self._ultimo_deslizar < self.ESPERA_DESLIZAR:
            return []
        if abs(dx) >= self.DESLIZAR and abs(dy) <= abs(dx) * self.VERTICAL_MAXIMA:
            logging.info("Deslizar: %.2f de la imagen en %.2f s.", dx, ahora - t0)
            self._ultimo_deslizar = ahora
            self._rastro, self._recorrido_maximo = [], 0.0
            self._quieta, self._pausa_hecha = [], True  # deslizar no es «quieta»
            return [("atras" if dx > 0 else "adelante", False)]
        return []

    def _pausa(self, ahora: float, x: float, y: float) -> list[tuple]:
        if self._pausa_hecha:
            # Ya pausó: se rearma si la mano se mueve (y vuelve a quedarse quieta).
            if any(math.hypot(x - mx, y - my) > self.MOVIMIENTO_QUIETA * 3 for _, mx, my in self._quieta):
                self._pausa_hecha = False
                self._quieta = self._quieta[-1:]
            return []
        if not self._quieta or ahora - self._quieta[0][0] < self.QUIETA_PAUSA:
            return []
        recientes = [m for m in self._quieta if ahora - m[0] <= self.QUIETA_PAUSA]
        # Quieta = todo el recorrido de la palma en ese tiempo cabe en MOVIMIENTO_QUIETA (no basta con
        # estar cerca de la posición actual: una mano que oscila pasaría por «quieta» en el medio).
        xs, ys = [m[1] for m in recientes], [m[2] for m in recientes]
        quieta = max(xs) - min(xs) <= self.MOVIMIENTO_QUIETA and max(ys) - min(ys) <= self.MOVIMIENTO_QUIETA
        if quieta:
            self._pausa_hecha = True
            return [("pausa", False)]
        return []

    def _pulgares(self, estable: str | None, ahora: float) -> list[tuple]:
        if estable not in ("pulgar_arriba", "pulgar_abajo"):
            self._pulgar, self._pulgar_ultimo = None, None
            return []
        if estable != self._pulgar:
            self._pulgar, self._pulgar_desde, self._pulgar_ultimo = estable, ahora, None
        if self._pulgar_ultimo is None:
            if ahora - self._pulgar_desde >= self.SOSTENER_PULGAR:
                self._pulgar_ultimo = ahora
                return [(estable, False)]
        elif ahora - self._pulgar_ultimo >= self.REPETIR_PULGAR:
            self._pulgar_ultimo = ahora
            return [(estable, True)]
        return []


SEGUIR = 0.3  # fracción del camino al objetivo en cada paso del deslizamiento
PASO_DESLIZAR = 0.006  # s entre pasos (~160 por segundo)


def deslizar(actual: tuple[float, float], objetivo: tuple[int, int]) -> tuple[float, float]:
    """Un paso del puntero hacia el objetivo: la cámara da ~24 cuadros por segundo y moverlo
    solo en cada cuadro se ve a tirones; así avanza de a poco entre cuadros (llega en ~40 ms)."""
    x = actual[0] + (objetivo[0] - actual[0]) * SEGUIR
    y = actual[1] + (objetivo[1] - actual[1]) * SEGUIR
    if abs(objetivo[0] - x) < 0.5 and abs(objetivo[1] - y) < 0.5:
        return float(objetivo[0]), float(objetivo[1])
    return x, y


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


# Qué mano se sigue, como la etiqueta MediaPipe, o None: cualquiera. Con la imagen que Jarvis ya
# espeja, MediaPipe llama «Left» a la mano derecha del usuario (al revés de lo que dice su
# documentación; comprobado el 2026-11-16: con «derecha» → «Right» no seguía la derecha y el
# usuario tuvo que elegir «izquierda»; el log mostró «se sigue Left» con su mano derecha).
MANOS = {"derecha": "Left", "izquierda": "Right", "cualquiera": None}
MANO_POR_DEFECTO = "derecha"


def elegir_mano(resultado, permitida: str | None) -> int | None:
    """Índice de la mano a seguir entre las detectadas (la de más confianza si hay varias), o None."""
    manos = getattr(resultado, "hand_landmarks", None) or []
    if not manos:
        return None
    if permitida is None:
        return 0
    lados = getattr(resultado, "handedness", None) or []
    candidatas = [(cat[0].score, i) for i, cat in enumerate(lados[:len(manos)])
                  if cat and cat[0].category_name == permitida]
    return max(candidatas)[1] if candidatas else None


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
                 al_desplazar: Callable[[int], None] | None = None, invertir_scroll: bool = False,
                 al_apuntar: Callable[[list], None] | None = None, monitor=None,
                 al_atajo: Callable[[str, bool], None] | None = None,
                 mano: str | None = MANOS[MANO_POR_DEFECTO]) -> None:
        """``al_desplazar(cantidad)``: girar la rueda; ``al_apuntar(acciones)``: mover el puntero y
        los clics del pellizco (``Puntero``) en ``monitor``. La app los hace en otro hilo, en orden
        y por el registro de herramientas; este hilo nunca espera."""
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
        self.al_apuntar = al_apuntar
        self.puntero = Puntero(monitor) if al_apuntar is not None and monitor is not None else None
        self._estado_puntero: str | None = None
        self.al_atajo = al_atajo  # fase 4: pausa, atrás/adelante, pulgares (la app decide qué hacer)
        self.mano = mano  # «Right», «Left» o None (cualquiera); la app la puede cambiar en marcha
        self._mano_vista: str | None = None  # para el log: qué mano se vio por última vez
        self.atajos = Atajos()

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
            # Dos manos: si se busca solo la derecha y la izquierda aparece primero, con una sola
            # la derecha quedaría sin seguir. La segunda solo cuesta cuando hay dos a la vista.
            running_mode=vision.RunningMode.VIDEO, num_hands=2,
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
                    self._soltar_puntero()
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
                puntos = mundo = None
                indice = elegir_mano(resultado, self.mano)
                self._anotar_mano(resultado, indice)
                if indice is not None:
                    puntos = [(p.x, p.y) for p in resultado.hand_landmarks[indice]]
                    mundos = getattr(resultado, "hand_world_landmarks", None)
                    if mundos and len(mundos) > indice:
                        mundo = [(p.x, p.y, p.z) for p in mundos[indice]]  # metros
                gesto = clasificar(puntos) if puntos else None
                cambio = estabilizador.agregar(gesto)
                if cambio is not False:
                    self._publicar_gesto(cambio)
                self._desplazar(estabilizador.actual, gesto, puntos)
                self._apuntar(estabilizador.actual, puntos, mundo)
                self._atajos(estabilizador.actual, puntos, gesto)
                if self.vista:
                    self.ultima_imagen = dibujar(rgb, puntos, cv2)
                cuadros_en_segundo += 1
                ahora = time.monotonic()
                if ahora - ultimo_estado >= FRECUENCIA_ESTADO:
                    self.cuadros_por_s = cuadros_en_segundo / (ahora - ultimo_estado)
                    self.events.put(("gesture_stats", round(self.cuadros_por_s, 1), round(brillo)))
                    cuadros_en_segundo, ultimo_estado = 0, ahora
        finally:
            self._soltar_puntero()  # nunca dejar el botón del mouse apretado
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
        if estable == "dos_dedos":
            # Mientras el gesto estable siga siendo «dos dedos», un cuadro borroso que sale como otro
            # gesto (o sin mano) se saltea sin perder la referencia: antes cortaba el scroll.
            if gesto == "dos_dedos" and puntos:
                cantidad = self.desplazamiento.mover(altura_dos_dedos(puntos), time.monotonic())
            else:
                self.desplazamiento.saltear()
                cantidad = None
        else:
            cantidad = self.desplazamiento.terminar(time.monotonic())
        if cantidad:
            self.al_desplazar(cantidad)

    def _anotar_mano(self, resultado, indice: int | None) -> None:
        """Log cuando cambia qué mano se ve (para confirmar que «derecha» es la derecha)."""
        lados = getattr(resultado, "handedness", None) or []
        vistas = sorted(c[0].category_name for c in lados if c)
        resumen = ",".join(vistas) or None
        if resumen != self._mano_vista:
            self._mano_vista = resumen
            if resumen:
                logging.info("Gestos: manos a la vista %s; se sigue %s.", resumen,
                             "ninguna (no es la elegida)" if indice is None else (self.mano or "cualquiera"))

    def _atajos(self, estable: str | None, puntos, gesto: str | None = None) -> None:
        if self.al_atajo is None:
            return
        self.atajos.apuntando = self.puntero is not None and self.puntero.armado
        for accion, repetida in self.atajos.actualizar(estable, puntos, time.monotonic(), gesto):
            self.al_atajo(accion, repetida)
        progreso = self.atajos.progreso
        cuarto = (progreso[0], int(progreso[1] * 4)) if progreso else None
        if cuarto != getattr(self, "_progreso_publicado", None):
            self._progreso_publicado = cuarto
            self.events.put(("gesture_progress", cuarto))

    def _apuntar(self, estable: str | None, puntos, mundo=None) -> None:
        """Fase 3: puntero con el índice; clic y arrastre con el pellizco."""
        if self.puntero is None:
            return
        acciones = self.puntero.actualizar(estable, puntos, time.monotonic(), mundo)
        if acciones:
            self.al_apuntar(acciones)
        self._publicar_puntero()

    def _soltar_puntero(self) -> None:
        if self.puntero is not None:
            acciones = self.puntero.soltar()
            if acciones:
                self.al_apuntar(acciones)
            self._publicar_puntero()

    def _publicar_puntero(self) -> None:
        estado = ("arrastrando" if self.puntero.arrastrando else "puntero" if self.puntero.armado else None)
        if estado != self._estado_puntero:
            self._estado_puntero = estado
            self.events.put(("gesture_pointer", estado))

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
