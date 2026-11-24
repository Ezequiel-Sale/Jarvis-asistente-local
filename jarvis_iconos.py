"""Íconos de línea del tema «Nébula» (2026-11-23, pedido del usuario: un tema futurista con íconos
y botones propios).

Se dibujan con PIL a partir de figuras en una grilla de 24×24 (las mismas del diseño en el
lienzo), a 4 veces el tamaño y reducidos con suavizado, en el color que se pida. Así toman el
color del tema y no dependen de archivos de imagen. Los demás temas siguen con los símbolos de
texto de siempre (⌂, ▤, ⏱…).
"""

from __future__ import annotations

ESCALA = 4
GROSOR = 1.6

# Figuras en la grilla de 24: ("linea", puntos…), ("cerrada", puntos…), ("rect", x0, y0, x1, y1),
# ("rect_redondo", x0, y0, x1, y1, radio), ("circulo", cx, cy, r), ("punto", cx, cy),
# ("arco", x0, y0, x1, y1, desde, hasta) — ángulos de PIL: 0 = derecha, en sentido horario.
FIGURAS: dict[str, tuple] = {
    "inicio": (("linea", (3, 11), (12, 4), (21, 11)), ("linea", (5, 10), (5, 20), (19, 20), (19, 10)),
               ("linea", (10, 20), (10, 14), (14, 14), (14, 20))),
    "chat": (("cerrada", (4, 5), (20, 5), (20, 16), (9, 16), (4, 20)), ("linea", (8, 9), (16, 9)),
             ("linea", (8, 12), (13, 12))),
    "rutinas": (("circulo", 12, 12, 8), ("linea", (12, 7), (12, 12), (15, 14)), ("linea", (12, 1.5), (12, 4)),
                ("linea", (12, 20), (12, 22.5))),
    "juegos": (("cerrada", (6, 8), (18, 8), (20.5, 16.5), (17.5, 18.5), (14, 15.5), (10, 15.5), (6.5, 18.5),
                (3.5, 16.5)), ("linea", (8, 10.5), (8, 13.5)), ("linea", (6.5, 12), (9.5, 12)),
               ("punto", 16, 11), ("punto", 17.8, 13)),
    "tele": (("rect", 3, 5, 21, 17), ("linea", (8, 21), (16, 21)), ("linea", (12, 17), (12, 21))),
    "sistema": (("rect", 6, 6, 18, 18), ("rect", 9.5, 9.5, 14.5, 14.5), ("linea", (9, 2), (9, 6)),
                ("linea", (15, 2), (15, 6)), ("linea", (9, 18), (9, 22)), ("linea", (15, 18), (15, 22)),
                ("linea", (2, 9), (6, 9)), ("linea", (2, 15), (6, 15)), ("linea", (18, 9), (22, 9)),
                ("linea", (18, 15), (22, 15))),
    "programas": (("cerrada", (12, 3), (20, 7.5), (20, 16.5), (12, 21), (4, 16.5), (4, 7.5)),
                  ("linea", (4, 7.5), (12, 12), (20, 7.5)), ("linea", (12, 12), (12, 21))),
    "mensajeria": (("rect", 3, 5, 21, 19), ("linea", (3, 7), (12, 13), (21, 7))),
    "tareas": (("linea", (9, 6), (20, 6)), ("linea", (9, 12), (20, 12)), ("linea", (9, 18), (20, 18)),
               ("linea", (3.5, 6), (4.5, 7), (6.5, 5)), ("linea", (3.5, 12), (4.5, 13), (6.5, 11)),
               ("punto", 5, 18)),
    "ajustes": (("linea", (3, 6), (14, 6)), ("linea", (18, 6), (21, 6)), ("linea", (3, 12), (8, 12)),
                ("linea", (12, 12), (21, 12)), ("linea", (3, 18), (16, 18)), ("circulo", 16, 6, 2),
                ("circulo", 10, 12, 2), ("circulo", 18, 18, 2)),
    "mic": (("rect_redondo", 9, 3, 15, 14, 3), ("arco", 5, 4, 19, 18, 0, 180), ("linea", (12, 18), (12, 21))),
    "enviar": (("linea", (4, 12), (18, 12)), ("linea", (13, 6), (19, 12), (13, 18))),
}


def _rgba(color: str) -> tuple[int, int, int, int]:
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16), 255


def imagen(nombre: str, color: str, tamano: int = 20):
    """Imagen PIL RGBA del ícono ``nombre`` en ``color`` («#RRGGBB»), de ``tamano`` px."""
    from PIL import Image, ImageDraw

    lado = tamano * ESCALA
    k = lado / 24
    grosor = max(1, round(GROSOR * k))
    tinta = _rgba(color)
    lienzo = Image.new("RGBA", (lado, lado), (0, 0, 0, 0))
    dibujo = ImageDraw.Draw(lienzo)

    def p(x: float, y: float) -> tuple[float, float]:
        return x * k, y * k

    def redondear(puntos) -> None:  # uniones redondeadas, como stroke-linejoin: round
        r = grosor / 2
        for x, y in puntos:
            dibujo.ellipse((x - r, y - r, x + r, y + r), fill=tinta)

    for figura in FIGURAS[nombre]:
        tipo, *datos = figura
        if tipo in ("linea", "cerrada"):
            puntos = [p(*punto) for punto in datos]
            if tipo == "cerrada":
                puntos.append(puntos[0])
            dibujo.line(puntos, fill=tinta, width=grosor)
            redondear(puntos)
        elif tipo == "rect":
            x0, y0, x1, y1 = datos
            dibujo.rectangle((*p(x0, y0), *p(x1, y1)), outline=tinta, width=grosor)
        elif tipo == "rect_redondo":
            x0, y0, x1, y1, radio = datos
            dibujo.rounded_rectangle((*p(x0, y0), *p(x1, y1)), radius=radio * k, outline=tinta, width=grosor)
        elif tipo == "circulo":
            cx, cy, r = datos
            dibujo.ellipse((*p(cx - r, cy - r), *p(cx + r, cy + r)), outline=tinta, width=grosor)
        elif tipo == "punto":
            cx, cy = datos
            r = 1.0
            dibujo.ellipse((*p(cx - r, cy - r), *p(cx + r, cy + r)), fill=tinta)
        elif tipo == "arco":
            x0, y0, x1, y1, desde, hasta = datos
            dibujo.arc((*p(x0, y0), *p(x1, y1)), desde, hasta, fill=tinta, width=grosor)
        else:
            raise ValueError(f"Figura desconocida en el ícono {nombre}: {tipo}")
    return lienzo.resize((tamano, tamano), Image.LANCZOS)


class Iconos:
    """Caché de ``PhotoImage`` por (nombre, color, tamaño): Tk necesita que la referencia viva
    mientras el botón muestra la imagen."""

    def __init__(self, master) -> None:
        self.master = master
        self._cache: dict[tuple[str, str, int], object] = {}

    def foto(self, nombre: str, color: str, tamano: int = 20):
        clave = (nombre, color.upper(), tamano)
        if clave not in self._cache:
            from PIL import ImageTk

            self._cache[clave] = ImageTk.PhotoImage(imagen(nombre, color, tamano), master=self.master)
        return self._cache[clave]
