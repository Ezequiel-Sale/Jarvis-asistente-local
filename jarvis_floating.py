"""Aviso flotante: burbuja pequeña en una esquina cuando Jarvis está en la bandeja.

- Muestra el estado (te escucho / procesando), lo que entendió y la respuesta, y
  se oculta sola unos segundos después.
- **No roba el foco**: ventana sin borde, siempre encima, con WS_EX_NOACTIVATE y
  WS_EX_TOOLWINDOW (no aparece en la barra de tareas ni activa la ventana), así
  no interrumpe juegos ni lo que estés escribiendo.
- Si hay una confirmación pendiente, muestra la pregunta con botones SÍ / NO
  (también vale decir «sí» o «no»).
- Clic en el texto → abre Jarvis.
Solo se usa desde el hilo de Tk.
"""

import ctypes
import os
import tkinter as tk

import jarvis_theme

ANCHO = 380
MARGEN = 16
FUNDIDO_PASOS = 8
FUNDIDO_MS = 20
OPACIDAD = 0.96


def recortar(texto: str, limite: int = 220) -> str:
    texto = " ".join(texto.split())
    return texto if len(texto) <= limite else texto[: limite - 1].rstrip() + "…"


def segundos_visible(texto: str) -> float:
    """Tiempo de lectura: 4 s más ~1 s por cada 15 palabras, como mucho 14 s."""
    return min(14.0, 4.0 + len(texto.split()) / 15)


def area_de_trabajo(root: tk.Misc) -> tuple[int, int, int, int]:
    """Área de trabajo del monitor principal (sin la barra de tareas), en coordenadas de Tk."""
    if os.name == "nt":
        try:
            from ctypes import wintypes

            rect = wintypes.RECT()
            if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):  # SPI_GETWORKAREA
                return rect.left, rect.top, rect.right, rect.bottom
        except (AttributeError, OSError):
            pass
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight() - 48


def _sin_foco(ventana: tk.Toplevel) -> None:
    """WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW: no se activa al mostrarse ni al hacer clic."""
    if os.name != "nt":
        return
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetParent(ventana.winfo_id()) or ventana.winfo_id()
        estilo = user32.GetWindowLongW(hwnd, -20)
        user32.SetWindowLongW(hwnd, -20, estilo | 0x08000000 | 0x00000080)
    except (AttributeError, OSError):
        pass


class AvisoFlotante:
    def __init__(self, root: tk.Tk, al_abrir) -> None:
        self.root = root
        self.al_abrir = al_abrir
        self.ventana: tk.Toplevel | None = None
        self._ocultar_id: str | None = None
        self._estado = ""
        self._usuario = ""
        self._respuesta = ""
        self._pregunta: tuple[str, object, object] | None = None

    # --- API -------------------------------------------------------------------

    def estado(self, texto: str) -> None:
        """«TE ESCUCHO…», «PROCESANDO…»; vacío para quitarlo."""
        self._estado = texto
        self._mostrar(fijo=bool(texto))

    def usuario(self, texto: str) -> None:
        self._usuario, self._respuesta = recortar(texto, 140), ""
        self._mostrar(fijo=True)

    def respuesta(self, texto: str) -> None:
        self._respuesta = recortar(texto)
        self._estado = ""
        self._mostrar(fijo=False, segundos=segundos_visible(self._respuesta))

    def pregunta(self, texto: str, al_si, al_no) -> None:
        """Confirmación pendiente: queda visible hasta que se responda."""
        self._pregunta = (recortar(texto, 160), al_si, al_no)
        self._respuesta = ""  # la respuesta anterior ya no viene al caso
        self._mostrar(fijo=True)

    def quitar_pregunta(self, resultado: str = "") -> None:
        if self._pregunta is None:
            return
        self._pregunta = None
        if resultado:
            self._respuesta = resultado
        self._mostrar(fijo=False, segundos=3.0)

    def ocultar(self) -> None:
        self._cancelar_ocultado()
        if self.ventana is not None and self.ventana.winfo_exists():
            self._fundir(OPACIDAD, 0.0, al_terminar=self.ventana.withdraw)

    def destruir(self) -> None:
        self._cancelar_ocultado()
        if self.ventana is not None and self.ventana.winfo_exists():
            self.ventana.destroy()
        self.ventana = None

    def visible(self) -> bool:
        return bool(self.ventana is not None and self.ventana.winfo_exists()
                    and self.ventana.state() != "withdrawn")

    # --- Dibujo ----------------------------------------------------------------

    def _crear(self) -> tk.Toplevel:
        ventana = tk.Toplevel(self.root)
        ventana.withdraw()
        ventana.overrideredirect(True)
        ventana.attributes("-topmost", True)
        try:
            ventana.attributes("-alpha", 0.0)
        except tk.TclError:
            pass
        ventana.configure(bg=jarvis_theme.BORDER)
        ventana.update_idletasks()
        _sin_foco(ventana)
        self.ventana = ventana
        return ventana

    def _mostrar(self, *, fijo: bool, segundos: float = 6.0) -> None:
        ventana = self.ventana if self.ventana is not None and self.ventana.winfo_exists() else self._crear()
        for hijo in ventana.winfo_children():
            hijo.destroy()
        t = jarvis_theme
        marco = tk.Frame(ventana, bg=t.PANEL, padx=14, pady=10)
        marco.pack(fill="both", expand=True, padx=1, pady=1)
        cabecera = tk.Frame(marco, bg=t.PANEL)
        cabecera.pack(fill="x")
        color = t.ERROR if self._pregunta else (t.VIOLET if "PROCES" in self._estado else t.CYAN)
        punto = tk.Canvas(cabecera, width=12, height=12, bg=t.PANEL, highlightthickness=0)
        punto.create_oval(2, 2, 11, 11, fill=color, outline="")
        punto.pack(side="left", padx=(0, 8))
        tk.Label(cabecera, text="J.A.R.V.I.S.", bg=t.PANEL, fg=t.CYAN,
                 font=("Consolas", 9, "bold")).pack(side="left")
        if self._estado:
            tk.Label(cabecera, text=self._estado, bg=t.PANEL, fg=color,
                     font=("Consolas", 8, "bold")).pack(side="left", padx=(10, 0))
        cerrar = tk.Label(cabecera, text="✕", bg=t.PANEL, fg=t.MUTED, font=("Segoe UI", 9), cursor="hand2")
        cerrar.pack(side="right")
        cerrar.bind("<Button-1>", lambda _e: self.ocultar())
        textos = []
        if self._usuario:
            textos.append(("Tú: " + self._usuario, t.MUTED, ("Segoe UI", 9)))
        if self._respuesta:
            textos.append((self._respuesta, t.TEXT, ("Segoe UI", 10)))
        if self._pregunta:
            textos.append((self._pregunta[0], t.TEXT, ("Segoe UI", 10, "bold")))
        for texto, fg, fuente in textos:
            etiqueta = tk.Label(marco, text=texto, bg=t.PANEL, fg=fg, font=fuente, justify="left",
                                anchor="w", wraplength=ANCHO - 30, cursor="hand2")
            etiqueta.pack(fill="x", pady=(6, 0))
            etiqueta.bind("<Button-1>", lambda _e: self.al_abrir())
        if self._pregunta:
            _texto, al_si, al_no = self._pregunta
            botones = tk.Frame(marco, bg=t.PANEL)
            botones.pack(fill="x", pady=(8, 0))
            estilo = dict(relief="flat", padx=12, pady=5, font=("Consolas", 9, "bold"), cursor="hand2",
                          takefocus=0)
            tk.Button(botones, text="SÍ", command=al_si, bg=t.CYAN, fg=t.BG, **estilo).pack(side="left")
            tk.Button(botones, text="NO", command=al_no, bg=t.PANEL_LIGHT, fg=t.TEXT, **estilo).pack(
                side="left", padx=(6, 0))
            tk.Label(botones, text="o di «sí» / «no»", bg=t.PANEL, fg=t.MUTED,
                     font=("Segoe UI", 8)).pack(side="left", padx=(10, 0))
        ventana.update_idletasks()
        alto = ventana.winfo_reqheight()
        izq, arr, der, abj = area_de_trabajo(self.root)
        ventana.geometry(f"{ANCHO}x{alto}+{der - ANCHO - MARGEN}+{abj - alto - MARGEN}")
        nueva = ventana.state() == "withdrawn"
        if nueva:
            ventana.deiconify()
            _sin_foco(ventana)
            self._fundir(0.0, OPACIDAD)
        else:
            try:
                ventana.attributes("-alpha", OPACIDAD)
            except tk.TclError:
                pass
        ventana.lift()
        self._cancelar_ocultado()
        if not fijo and self._pregunta is None:
            self._ocultar_id = self.root.after(int(segundos * 1000), self._al_vencer)

    def _al_vencer(self) -> None:
        self._ocultar_id = None
        self._usuario = self._respuesta = ""
        self.ocultar()

    def _cancelar_ocultado(self) -> None:
        if self._ocultar_id is not None:
            try:
                self.root.after_cancel(self._ocultar_id)
            except tk.TclError:
                pass
            self._ocultar_id = None

    def _fundir(self, desde: float, hasta: float, al_terminar=None) -> None:
        ventana = self.ventana

        def paso(n: int) -> None:
            if ventana is None or not ventana.winfo_exists():
                return
            try:
                ventana.attributes("-alpha", desde + (hasta - desde) * n / FUNDIDO_PASOS)
            except tk.TclError:
                return
            if n < FUNDIDO_PASOS:
                ventana.after(FUNDIDO_MS, paso, n + 1)
            elif al_terminar is not None:
                al_terminar()

        paso(1)
