"""Fase 6 de los gestos [2026-11-17]: «enseñá tu gesto».

El usuario sostiene una forma de mano frente a la cámara unos segundos; Jarvis guarda varias
**huellas** (los 21 puntos de la mano expresados en un marco de la propia palma) y después la
reconoce por el vecino más cercano. Se asigna como los gestos propios de la fase 5 (rutina, frase
o atajo de teclado).

- La huella no depende de dónde está la mano en la imagen, ni de lo lejos que está de la cámara,
  ni de cómo está girada: origen en la muñeca, escala muñeca → base del dedo medio, ejes de la
  palma (muñeca → dedo medio y meñique → índice). Usa los puntos 3D reales (metros) si MediaPipe
  los da; si no, los de la imagen (en la imagen plana solo se compensa el giro sobre ese plano).
- Solo se buscan cuando el gesto de ``clasificar`` no es uno de los que Jarvis ya usa
  (``NUCLEO``: el puntero, el scroll, el puño, los pulgares…): enseñar un gesto nunca rompe esos.
- Se guardan en ``gestos_aprendidos.json`` en la carpeta de Jarvis (fuera de git y de la base):
  solo números de la forma de la mano, ninguna imagen.
"""

from __future__ import annotations

import json
import logging
import math
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

ARCHIVO = "gestos_aprendidos.json"
MAXIMO_GESTOS = 8
MAXIMO_MUESTRAS = 40  # por gesto (se toman parejas a lo largo de la grabación)
MINIMO_MUESTRAS = 15  # cuadros con la mano para aceptar una grabación (~0,6 s a 24 cuadros/s)
SEGUNDOS_GRABACION = 2.5
# Gestos que Jarvis ya usa para algo: si la forma grabada cae casi siempre en uno de estos, no se
# acepta (y nunca se buscan aprendidos cuando el cuadro es uno de estos).
NUCLEO = ("indice", "dos_dedos", "pellizco", "puno", "mano_abierta", "pulgar_arriba", "pulgar_abajo")
# Distancia: raíz del promedio de los cuadrados de lo que se separa cada uno de los 20 puntos, en
# «palmas» (muñeca → base del dedo medio). Un dedo entero distinto da ~0,27; el temblor de MediaPipe,
# ~0,03–0,06. El umbral de cada gesto sale de su propia grabación (3× lo que varían sus muestras),
# entre estos dos límites.
UMBRAL_MINIMO, UMBRAL_MAXIMO = 0.12, 0.20
FACTOR_UMBRAL = 3.0
PARECIDO_MAXIMO = 0.5  # si un gesto ya aprendido reconoce más de esta fracción de la grabación nueva


def _resta(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _punto(v, k: float):
    return (v[0] * k, v[1] * k, v[2] * k)


def _producto(a, b) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cruz(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _largo(v) -> float:
    return math.sqrt(_producto(v, v))


def huella_mano(puntos: Sequence, mundo: Sequence | None = None) -> list[float] | None:
    """60 números: los puntos 1–20 en el marco de la palma (None si la mano no sirve)."""
    fuente = mundo if mundo is not None and len(mundo) >= 21 else puntos
    if fuente is None or len(fuente) < 21:
        return None
    p = [(q[0], q[1], q[2] if len(q) > 2 else 0.0) for q in fuente]
    if fuente is puntos:
        p = [(x, y, 0.0) for x, y, _z in p]  # la z de la imagen es relativa y ruidosa: plano
    origen = p[0]
    eje_y = _resta(p[9], origen)
    escala = _largo(eje_y)
    if escala < 1e-6:
        return None
    eje_y = _punto(eje_y, 1 / escala)
    lado = _resta(p[5], p[17])  # meñique → índice
    eje_x = _resta(lado, _punto(eje_y, _producto(lado, eje_y)))
    largo_x = _largo(eje_x)
    if largo_x < 1e-6:
        return None
    eje_x = _punto(eje_x, 1 / largo_x)
    eje_z = _cruz(eje_x, eje_y)
    huella: list[float] = []
    for q in p[1:]:
        d = _resta(q, origen)
        huella += [_producto(d, eje_x) / escala, _producto(d, eje_y) / escala, _producto(d, eje_z) / escala]
    return huella


def distancia(a: Sequence[float], b: Sequence[float]) -> float:
    """RMS de la separación de los 20 puntos (en palmas)."""
    n = min(len(a), len(b)) // 3
    if n == 0:
        return math.inf
    total = sum((a[i] - b[i]) ** 2 for i in range(n * 3))
    return math.sqrt(total / n)


def _vecino(huella: Sequence[float], muestras: Sequence[Sequence[float]]) -> float:
    return min((distancia(huella, m) for m in muestras), default=math.inf)


class Aprendidos:
    """Los gestos enseñados, para reconocerlos cuadro a cuadro (hilo de la cámara)."""

    def __init__(self, gestos: Sequence[dict] = ()) -> None:
        self.gestos = [g for g in gestos if g.get("muestras")]

    def __bool__(self) -> bool:
        return bool(self.gestos)

    def reconocer(self, huella: Sequence[float] | None) -> str | None:
        """El ``id`` del gesto más cercano si está dentro de su umbral; si no, None."""
        if huella is None or not self.gestos:
            return None
        mejor, mejor_d = None, math.inf
        for gesto in self.gestos:
            d = _vecino(huella, gesto["muestras"])
            if d < mejor_d:
                mejor, mejor_d = gesto, d
        if mejor is not None and mejor_d <= float(mejor.get("umbral", UMBRAL_MINIMO)):
            return mejor["id"]
        return None


def _submuestrear(muestras: list, maximo: int) -> list:
    if len(muestras) <= maximo:
        return list(muestras)
    paso = len(muestras) / maximo
    return [muestras[int(i * paso)] for i in range(maximo)]


def umbral_de(muestras: Sequence[Sequence[float]]) -> float:
    """``FACTOR_UMBRAL`` × la mediana de la distancia de cada muestra a su vecina más cercana."""
    cercanas = sorted(_vecino(m, muestras[:i] + muestras[i + 1:]) for i, m in enumerate(muestras))
    mediana = cercanas[len(cercanas) // 2] if cercanas else UMBRAL_MINIMO
    return min(UMBRAL_MAXIMO, max(UMBRAL_MINIMO, FACTOR_UMBRAL * mediana))


def aprender(nombre: str, emoji: str, muestras: list[list[float]], clases: list[str],
             existentes: Sequence[dict]) -> dict:
    """Valida una grabación y devuelve el gesto nuevo; ``ValueError`` (en español) si no sirve."""
    nombre = " ".join((nombre or "").split())[:30]
    if not nombre:
        raise ValueError("Ponele un nombre al gesto.")
    if any(g["nombre"].lower() == nombre.lower() for g in existentes):
        raise ValueError(f"Ya tenés un gesto llamado «{nombre}».")
    if len(existentes) >= MAXIMO_GESTOS:
        raise ValueError(f"Ya hay {MAXIMO_GESTOS} gestos enseñados: borrá alguno primero.")
    muestras = [m for m in muestras if m]
    if len(muestras) < MINIMO_MUESTRAS:
        raise ValueError("Casi no vi la mano: ponela de frente a la cámara, con buena luz, y volvé a grabar.")
    if clases:
        clase, veces = Counter(clases).most_common(1)[0]
        if clase in NUCLEO and veces > len(clases) / 2:
            from jarvis_gestos import NOMBRES

            raise ValueError(f"Esa forma Jarvis ya la usa como «{NOMBRES.get(clase, clase)}». Probá con otra.")
    for gesto in existentes:
        uno = Aprendidos([gesto])
        parecidas = sum(1 for m in muestras if uno.reconocer(m) is not None)
        if parecidas > len(muestras) * PARECIDO_MAXIMO:
            raise ValueError(f"Se parece mucho a «{gesto['nombre']}»: hacé una forma más distinta.")
    elegidas = _submuestrear(muestras, MAXIMO_MUESTRAS)
    usados = {g["id"] for g in existentes}
    numero = 1
    while f"g{numero}" in usados:
        numero += 1
    emoji = (emoji or "").strip()[:4] or "✋"
    return {"id": f"g{numero}", "nombre": nombre, "emoji": emoji, "umbral": round(umbral_de(elegidas), 4),
            "muestras": [[round(v, 4) for v in m] for m in elegidas]}


def cargar(carpeta: Path) -> list[dict]:
    ruta = Path(carpeta) / ARCHIVO
    if not ruta.exists():
        return []
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logging.warning("No se pudieron leer los gestos enseñados (%s).", ruta, exc_info=True)
        return []
    gestos = []
    for g in datos if isinstance(datos, list) else []:
        if (isinstance(g, dict) and isinstance(g.get("id"), str) and isinstance(g.get("nombre"), str)
                and isinstance(g.get("muestras"), list) and g["muestras"]):
            gestos.append(g)
    return gestos[:MAXIMO_GESTOS]


def guardar(carpeta: Path, gestos: Sequence[dict]) -> None:
    ruta = Path(carpeta) / ARCHIVO
    temporal = ruta.with_suffix(".tmp")
    temporal.write_text(json.dumps(list(gestos), ensure_ascii=False), encoding="utf-8")
    temporal.replace(ruta)
