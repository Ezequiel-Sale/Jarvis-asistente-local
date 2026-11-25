"""Piezas de interfaz compartidas por las vistas rediseñadas (2026-11-24, diseño en el lienzo «Vistas Sistema,
Programas, Mensajes, Tareas y Ajustes»): interruptor, tarjeta, títulos de sección, botones y una rejilla que
pone tantas columnas como entren.

Los colores se leen de ``jarvis_theme`` por nombre de token en el momento de dibujar (``color``), así un cambio
de tema no deja colores viejos en lo que se dibuja después.
"""

from __future__ import annotations

import tkinter as tk

import jarvis_theme


def color(token: str) -> str:
    return getattr(jarvis_theme, token)


DEMORA_AJUSTE_MS = 90


def postergar(widget: tk.Misc, funcion, ms: int = DEMORA_AJUSTE_MS, *, clave: str = "general") -> None:
    """Corre ``funcion`` cuando dejan de llegar pedidos (``ms`` sin otro con la misma ``clave``): al arrastrar el
    borde de la ventana llega un <Configure> por píxel y rearmar todo en cada uno la trababa (2026-11-24)."""
    pendientes = widget.__dict__.setdefault("_jarvis_postergados", {})
    anterior = pendientes.pop(clave, None)
    if anterior is not None:
        try:
            widget.after_cancel(anterior)
        except tk.TclError:
            pass

    def correr() -> None:
        pendientes.pop(clave, None)
        try:
            if widget.winfo_exists():
                funcion()
        except tk.TclError:
            pass

    try:
        pendientes[clave] = widget.after(ms, correr)
    except tk.TclError:
        pass


def tarjeta(padre: tk.Misc, *, fondo: str = "PANEL", borde: str = "BORDER", padx: int = 16, pady: int = 14) -> tk.Frame:
    return tk.Frame(padre, bg=color(fondo), padx=padx, pady=pady, highlightthickness=1,
                    highlightbackground=color(borde))


def titulo_seccion(padre: tk.Misc, texto: str, *, fondo: str = "PANEL") -> tk.Label:
    return tk.Label(padre, text=texto, bg=color(fondo), fg=color("VIOLET"), font=("Segoe UI", 9, "bold"), anchor="w")


def nota(padre: tk.Misc, texto: str, *, fondo: str = "PANEL", fg: str = "MUTED") -> tk.Label:
    etiqueta = tk.Label(padre, text=texto, bg=color(fondo), fg=color(fg), font=("Segoe UI", 9), anchor="w",
                        justify="left", wraplength=520)
    # El ancho real (con la ventana angosta el texto se cortaba a la derecha).
    etiqueta.bind("<Configure>", lambda e: e.width > 50 and postergar(
        etiqueta, lambda: etiqueta.configure(wraplength=e.width - 8), clave="ancho"))
    return etiqueta


def boton(padre: tk.Misc, texto: str, comando, *, primario: bool = False, fondo: str = "PANEL_LIGHT",
          fg: str | None = None, **opciones) -> tk.Button:
    """Primario: relleno del color de acento; si no, discreto sobre ``fondo``."""
    return tk.Button(
        padre, text=texto, command=comando, relief="flat", bd=0, cursor="hand2",
        bg=color("CYAN") if primario else color(fondo),
        fg=color(fg) if fg else (color("BG") if primario else color("ORB_BRIGHT")),
        activebackground=color("ORB_BRIGHT") if primario else color("HOVER"),
        activeforeground=color("BG") if primario else color("TEXT"),
        font=opciones.pop("font", ("Segoe UI", 9, "bold")), padx=opciones.pop("padx", 12),
        pady=opciones.pop("pady", 5), **opciones)


def ficha(padre: tk.Misc, texto: str, *, fondo: str = "CYAN_DARK", fg: str = "ORB_BRIGHT") -> tk.Label:
    return tk.Label(padre, text=texto, bg=color(fondo), fg=color(fg), font=("Segoe UI", 8, "bold"), padx=8, pady=2)


ANCHO_INTERRUPTOR, ALTO_INTERRUPTOR = 46, 26


def interruptor(padre: tk.Misc, encendido: bool | None, comando, *, fondo: str = "PANEL") -> tk.Canvas:
    """Un interruptor redondeado; ``encendido=None``: estado desconocido (gris, sin acción)."""
    lienzo = tk.Canvas(padre, width=ANCHO_INTERRUPTOR, height=ALTO_INTERRUPTOR, bg=color(fondo), highlightthickness=0,
                       bd=0, cursor="hand2" if encendido is not None else "")
    lienzo.jarvis_encendido = encendido
    pista = color("CYAN") if encendido else color("BORDER_STRONG")
    alto = ALTO_INTERRUPTOR - 2
    lienzo.create_oval(1, 1, alto + 1, alto + 1, fill=pista, outline=pista)
    lienzo.create_oval(ANCHO_INTERRUPTOR - alto - 1, 1, ANCHO_INTERRUPTOR - 1, alto + 1, fill=pista, outline=pista)
    lienzo.create_rectangle(alto // 2 + 1, 1, ANCHO_INTERRUPTOR - alto // 2 - 1, alto + 1, fill=pista, outline=pista)
    x = ANCHO_INTERRUPTOR - alto + 2 if encendido else 4
    perilla = color("TEXT") if encendido is not None else color("MUTED")
    lienzo.create_oval(x, 4, x + alto - 6, alto - 2, fill=perilla, outline=perilla)
    lienzo.jarvis_alternar = comando if encendido is not None else None  # lo mismo que el clic
    if lienzo.jarvis_alternar is not None:
        lienzo.bind("<Button-1>", lambda _e: comando())
    return lienzo


class Rejilla(tk.Frame):
    """Hijos en tantas columnas como entren (``ancho_minimo`` cada una, hasta ``max_columnas``); se
    reacomoda al cambiar el ancho. Se agregan con ``agregar(widget)`` (hijos de la rejilla)."""

    def __init__(self, padre: tk.Misc, ancho_minimo: int, *, separacion: int = 12, max_columnas: int = 6,
                 fondo: str = "BG") -> None:
        super().__init__(padre, bg=color(fondo))
        self.ancho_minimo, self.separacion, self.max_columnas = ancho_minimo, separacion, max_columnas
        self.columnas = 1
        self._hijos: list[tk.Widget] = []
        self.grid_columnconfigure(0, weight=1, uniform="rejilla")
        if hasattr(padre, "jarvis_scroll"):
            self.jarvis_scroll = padre.jarvis_scroll
        self.bind("<Configure>", self._al_cambiar_ancho, add="+")

    @staticmethod
    def columnas_para(ancho: int, ancho_minimo: int, separacion: int, max_columnas: int) -> int:
        return max(1, min(max_columnas, (ancho + separacion) // (ancho_minimo + separacion)))

    def agregar(self, widget: tk.Widget) -> tk.Widget:
        self._hijos.append(widget)
        self._ubicar(widget, len(self._hijos) - 1)
        return widget

    def _ubicar(self, widget: tk.Widget, indice: int) -> None:
        fila, columna = divmod(indice, self.columnas)
        widget.grid(row=fila, column=columna, sticky="nsew",
                    padx=(0, self.separacion if columna < self.columnas - 1 else 0), pady=(0, self.separacion))

    def _al_cambiar_ancho(self, evento) -> None:
        columnas = self.columnas_para(evento.width, self.ancho_minimo, self.separacion, self.max_columnas)
        if columnas == self.columnas:
            return
        for anterior in range(self.columnas):
            self.grid_columnconfigure(anterior, weight=0, uniform="")
        self.columnas = columnas
        for columna in range(columnas):
            self.grid_columnconfigure(columna, weight=1, uniform="rejilla")
        for indice, widget in enumerate(self._hijos):
            self._ubicar(widget, indice)
