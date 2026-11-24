"""Widget de juegos en el escritorio (2026-11-23, pedido del usuario; diseño en el lienzo «Widget de juegos»).

Una ventana sin bordes de Jarvis que queda al fondo, sobre el fondo de pantalla y detrás de las demás
ventanas, con los juegos de la página 🎮 JUEGOS: portada, ▶ JUGAR y ⚙ para ver, quitar o editar
las acciones al abrir cada uno. Dos vistas: tarjetas (dos columnas) y lista (más juegos a la vista).
Se arrastra desde la cabecera, se agranda o achica desde la agarradera «◢» de abajo a la derecha
(las tarjetas son de ancho fijo: más ancho, más columnas) y recuerda posición, tamaño, vista, si
estaba abierto y la apariencia (preferencias ``widget_juegos_*``). Win+D también lo oculta: para
sobrevivir a eso habría que meterlo en la capa del escritorio, que usa Wallpaper Engine.

Solo el fondo es transparente, de 0 a 100 % (panel «Apariencia» del botón «◐», con barra, atajos,
vista y borde): son dos ventanas que se mueven juntas, el fondo con ``-alpha`` detrás y el contenido
con su color de fondo recortado (``-transparentcolor`` = ``color_clave``). Donde el fondo es
invisible, los clics pasan al escritorio.

Usa los datos de la app (``app._games``, ``app.games_watcher``, ``app.tool_registry``) y sus acciones
(``_play_game``, ``_edit_game_actions``, ``_load_games_page``); se vuelve a dibujar cuando la página
JUEGOS se dibuja (``jarvis_ui_juegos.render``), al abrir o cerrar un juego y al cambiar el tema.
Todo corre en el hilo de Tk.
"""

from __future__ import annotations

import logging
import tkinter as tk
from tkinter import ttk

import jarvis_acciones_juego
import jarvis_portadas
import jarvis_theme as t

ANCHO, ALTO = 380, 580
MINIMO = (300, 360)  # se agranda o achica desde la esquina de abajo a la derecha (2026-11-23)
MAXIMO = (1600, 1800)
MARGEN = 32
ATAJOS = (0, 25, 50, 75, 100)  # % de transparencia del fondo (panel «Apariencia», botón «◐»)
ANCHO_APARIENCIA = 300
ANCHO_TARJETA, ALTO_PORTADA = 158, 74  # 460×215 de Steam a este ancho
SEPARACION = 8
RELLENO = 24 + 16  # márgenes de la lista y barra de desplazamiento
MINIATURA = (64, 30)
MAX_NOMBRE = 20
PREF_VISIBLE = "widget_juegos_visible"
PREF_POSICION = "widget_juegos_posicion"
PREF_MODO = "widget_juegos_modo"
PREF_TAMANO = "widget_juegos_tamano"
PREF_TRANSPARENCIA = "widget_juegos_transparencia"  # 0 (opaco) … 100 (fondo invisible)
PREF_BORDE = "widget_juegos_borde"
PREF_OPACIDAD_VIEJA = "widget_juegos_opacidad"  # la de toda la ventana (versión anterior)
MODOS = ("tarjetas", "lista")


def posicion_de(texto: str | None) -> tuple[int, int] | None:
    """«120,-40» → (120, -40); cualquier otra cosa → None."""
    try:
        x, y = (int(parte) for parte in str(texto).split(","))
    except (TypeError, ValueError):
        return None
    return x, y


def tamano_de(texto: str | None) -> tuple[int, int]:
    """«520x700» → (520, 700), dentro de ``MINIMO``…``MAXIMO``; si no se entiende, el de siempre."""
    try:
        ancho, alto = (int(parte) for parte in str(texto).lower().split("x"))
    except (TypeError, ValueError):
        return ANCHO, ALTO
    return (min(MAXIMO[0], max(MINIMO[0], ancho)), min(MAXIMO[1], max(MINIMO[1], alto)))


def transparencia_de(texto) -> int:
    """«75» → 75, entre 0 y 100; si no se entiende, 0 (opaco)."""
    try:
        valor = round(float(str(texto).replace(",", ".")))
    except (TypeError, ValueError):
        return 0
    return min(100, max(0, valor))


def color_clave(fondo: str) -> str:
    """El color que Windows vuelve invisible en la ventana del contenido (``-transparentcolor``): el
    fondo del tema con el azul corrido en 1, para que nada más del tema lo use y el borde del texto
    suavizado quede del color del fondo."""
    rojo, verde, azul = (int(fondo[i:i + 2], 16) for i in (1, 3, 5))
    azul = azul - 1 if azul == 255 else azul + 1
    return f"#{rojo:02X}{verde:02X}{azul:02X}"


def columnas_para(ancho_ventana: int) -> int:
    """Cuántas tarjetas entran por fila con la ventana de ese ancho."""
    return max(1, (ancho_ventana - RELLENO + SEPARACION) // (ANCHO_TARJETA + 2 + SEPARACION))


def filtrar(juegos, filtro: str) -> list:
    """Los que se pueden abrir (tienen ejecutable) y cuyo nombre contiene ``filtro``."""
    filtro = filtro.strip().casefold()
    return [j for j in juegos or [] if j.ejecutable is not None and (not filtro or filtro in j.nombre.casefold())]


def abierto(juego, jugando: set[str]) -> bool:
    """¿El vigilante de juegos lo ve abierto? (anota el nombre de la carpeta)."""
    nombres = {n.casefold() for n in jugando}
    return juego.carpeta.name.casefold() in nombres or juego.nombre.casefold() in nombres


def _recortar(texto: str, largo: int = MAX_NOMBRE) -> str:
    return texto if len(texto) <= largo else texto[: largo - 1].rstrip() + "…"


def _en_pantalla(x: int, y: int) -> bool:
    """¿Ese punto cae en algún monitor? (un monitor desconectado deja la posición guardada afuera)."""
    try:
        import ctypes
        from ctypes import wintypes

        punto = wintypes.POINT(x + 40, y + 20)
        return bool(ctypes.windll.user32.MonitorFromPoint(punto, 0))  # MONITOR_DEFAULTTONULL
    except Exception:
        return True


class WidgetJuegos:
    def __init__(self, app) -> None:
        self.app = app
        self.ventana: tk.Toplevel | None = None  # el contenido; su fondo es invisible
        self.fondo: tk.Toplevel | None = None  # detrás: el color del fondo, con la transparencia elegida
        self.apariencia: tk.Toplevel | None = None  # panel del botón «◐»
        self.transparencia = self._transparencia_guardada()
        self.borde = self._preferencia(PREF_BORDE) != "0"
        self._clave = color_clave(t.BG)
        self._guardado_pendiente = None
        self.seleccionado = ""  # juego con el panel de acciones abierto
        self.modo = self._preferencia(PREF_MODO) if self._preferencia(PREF_MODO) in MODOS else MODOS[0]
        self._fotos: dict[tuple[str, tuple[int, int]], tuple[float, object]] = {}
        self._arrastre: tuple[int, int] | None = None
        self._estirando: tuple[int, int, int, int] | None = None
        self._columnas = columnas_para(ANCHO)
        self._pidio_juegos = False

    # --- Preferencias ---------------------------------------------------------------------------

    @staticmethod
    def _preferencia(clave: str) -> str | None:
        try:
            import jarvis_core

            return jarvis_core.obtener_preferencia(clave)
        except Exception:
            return None

    @staticmethod
    def _guardar(clave: str, valor: str) -> None:
        try:
            import jarvis_core

            jarvis_core.guardar_preferencia(clave, valor)
        except Exception:
            logging.exception("No se pudo guardar la preferencia %s del widget de juegos.", clave)

    def _transparencia_guardada(self) -> int:
        guardada = self._preferencia(PREF_TRANSPARENCIA)
        if guardada is not None:
            return transparencia_de(guardada)
        vieja = self._preferencia(PREF_OPACIDAD_VIEJA)  # «0.80» de toda la ventana → 20 % de fondo
        try:
            return transparencia_de((1 - float(vieja)) * 100) if vieja else 0
        except ValueError:
            return 0

    # --- Mostrar, ocultar, redibujar -----------------------------------------------------------

    @property
    def visible(self) -> bool:
        return self.ventana is not None and bool(self.ventana.winfo_exists())

    def mostrar(self) -> None:
        if self.visible:
            self.refrescar()
            return
        # Dos ventanas sin bordes ni botón en la barra de tareas (2026-11-23, pedido del usuario: que
        # solo el fondo sea transparente, hasta el 100 %): atrás, un panel del color del fondo con
        # «-alpha»; adelante, el contenido, con su fondo recortado («-transparentcolor»). Tk no
        # puede dar transparencia parcial a una parte de una sola ventana.
        fondo = tk.Toplevel(self.app.root)
        fondo.withdraw()
        fondo.overrideredirect(True)
        fondo.title("Jarvis · Juegos (fondo)")
        self.fondo = fondo
        ventana = tk.Toplevel(self.app.root)
        ventana.withdraw()
        ventana.overrideredirect(True)
        ventana.title("Jarvis · Juegos")
        self.ventana = ventana
        self._clave = color_clave(t.BG)
        fondo.configure(bg=t.BG)
        try:
            ventana.attributes("-transparentcolor", self._clave)
        except tk.TclError:
            logging.debug("Esta ventana no admite un color transparente.", exc_info=True)
        self._aplicar_transparencia()
        x, y = self._posicion_inicial()
        ancho, alto = tamano_de(self._preferencia(PREF_TAMANO))
        self._columnas = columnas_para(ancho)  # antes de dibujar: si no, arranca con las de 380 px
        self._construir()
        for capa in (fondo, ventana):
            capa.geometry(f"{ancho}x{alto}+{x}+{y}")
        fondo.deiconify()
        ventana.deiconify()
        ventana.transient(fondo)  # Windows: el contenido queda siempre justo encima de su fondo
        ventana.bind("<Configure>", self._seguir_fondo, add="+")
        ventana.bind("<FocusIn>", lambda _e: self._apilar(), add="+")
        ventana.bind("<FocusOut>", lambda _e: ventana.after(150, self._si_perdio_foco))
        ventana.after(60, self.al_fondo)
        self._guardar(PREF_VISIBLE, "1")
        if getattr(self.app, "_games", None) is None and not self._pidio_juegos:
            self._pidio_juegos = True
            try:
                self.app._load_games_page()
            except Exception:
                logging.exception("El widget de juegos no pudo pedir la lista de juegos.")

    def ocultar(self) -> None:
        if self._guardado_pendiente is not None and self.visible:
            self.ventana.after_cancel(self._guardado_pendiente)
            self._guardar_transparencia()
        self._cerrar_apariencia()
        for capa in (self.ventana, self.fondo):
            if capa is not None and capa.winfo_exists():
                capa.destroy()
        self.ventana = self.fondo = None
        self._guardar(PREF_VISIBLE, "0")

    def reconstruir(self) -> None:
        """Al cambiar el tema: se vuelve a armar con los colores nuevos, en el mismo lugar."""
        if not self.visible:
            return
        geometria = self.ventana.geometry()
        self._cerrar_apariencia()
        for hijo in self.ventana.winfo_children():
            hijo.destroy()
        self._clave = color_clave(t.BG)
        self.fondo.configure(bg=t.BG)
        try:
            self.ventana.attributes("-transparentcolor", self._clave)
        except tk.TclError:
            pass
        self._construir()
        self.ventana.geometry(geometria)

    def refrescar(self) -> None:
        if self.visible:
            self._dibujar()

    def al_fondo(self) -> None:
        """Detrás de todas las ventanas (HWND_BOTTOM), sin activarlo: el contenido y, debajo, su fondo."""
        if not self.visible:
            return
        try:
            import ctypes

            for capa in (self.ventana, self.fondo):
                hwnd = int(capa.wm_frame(), 16)
                ctypes.windll.user32.SetWindowPos(hwnd, 1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
        except Exception:
            logging.debug("No se pudo mandar el widget de juegos al fondo.", exc_info=True)

    def _apilar(self) -> None:
        """Al activar el contenido (un clic), su fondo pasa justo debajo de él."""
        if not self.visible or self.fondo is None:
            return
        try:
            import ctypes

            arriba = int(self.ventana.wm_frame(), 16)
            ctypes.windll.user32.SetWindowPos(int(self.fondo.wm_frame(), 16), arriba, 0, 0, 0, 0,
                                              0x0001 | 0x0002 | 0x0010)
        except Exception:
            logging.debug("No se pudo acomodar el fondo del widget de juegos.", exc_info=True)

    def _seguir_fondo(self, evento) -> None:
        """El fondo copia la posición y el tamaño del contenido (al arrastrarlo o estirarlo)."""
        if evento.widget is not self.ventana or self.fondo is None:
            return
        self.fondo.geometry(f"{self.ventana.winfo_width()}x{self.ventana.winfo_height()}"
                            f"+{self.ventana.winfo_x()}+{self.ventana.winfo_y()}")

    def _aplicar_transparencia(self) -> None:
        if self.fondo is None:
            return
        try:
            self.fondo.attributes("-alpha", (100 - self.transparencia) / 100)
        except tk.TclError:
            logging.debug("Esta ventana no admite transparencia.", exc_info=True)

    def _si_perdio_foco(self) -> None:
        if not self.visible:
            return
        try:
            foco = self.ventana.focus_get()
        except (KeyError, tk.TclError):
            foco = None
        if foco is None or foco.winfo_toplevel() not in (self.ventana, self.apariencia):
            self.al_fondo()

    def _posicion_inicial(self) -> tuple[int, int]:
        guardada = posicion_de(self._preferencia(PREF_POSICION))
        if guardada is not None and _en_pantalla(*guardada):
            return guardada
        ancho, _alto = tamano_de(self._preferencia(PREF_TAMANO))
        return max(0, self.app.root.winfo_screenwidth() - ancho - MARGEN), MARGEN

    # --- Construcción ---------------------------------------------------------------------------

    def _boton(self, padre, texto, comando, *, fg=None, bg=None, ancho=None, **opciones) -> tk.Button:
        return tk.Button(padre, text=texto, command=comando, bg=bg or t.PANEL_LIGHT, fg=fg or t.ORB_BRIGHT,
                         activebackground=t.HOVER, activeforeground=t.TEXT, relief="flat", bd=0,
                         cursor="hand2", font=opciones.pop("font", ("Segoe UI", 9, "bold")),
                         width=ancho, **opciones)

    def _construir(self) -> None:
        ventana = self.ventana
        ventana.configure(bg=t.BORDER_STRONG if self.borde else self._clave)
        interior = tk.Frame(ventana, bg=self._clave)
        interior.pack(fill="both", expand=True, padx=1, pady=1)

        cabecera = tk.Frame(interior, bg=self._clave, padx=10, pady=8, cursor="fleur")
        cabecera.pack(fill="x")
        agarre = tk.Label(cabecera, text="⠿", bg=self._clave, fg=t.MUTED, font=("Segoe UI", 12))
        agarre.pack(side="left", padx=(0, 6))
        titulos = tk.Frame(cabecera, bg=self._clave)
        titulos.pack(side="left", fill="x", expand=True)
        titulo = tk.Label(titulos, text="JUEGOS", bg=self._clave, fg=t.CYAN, font=("Segoe UI", 12, "bold"), anchor="w")
        titulo.pack(anchor="w")
        self.contador = tk.Label(titulos, text="Buscando juegos…", bg=self._clave, fg=t.MUTED, font=("Segoe UI", 8),
                                 anchor="w")
        self.contador.pack(anchor="w")
        for widget in (cabecera, agarre, titulos, titulo, self.contador):
            widget.bind("<ButtonPress-1>", self._empezar_arrastre)
            widget.bind("<B1-Motion>", self._arrastrar)
            widget.bind("<ButtonRelease-1>", self._soltar)
        self._boton(cabecera, "✕", self._cerrar, ancho=3, pady=4).pack(side="right")
        self._boton(cabecera, "↗", self._abrir_jarvis, ancho=3, pady=4).pack(side="right", padx=(0, 6))
        self.boton_modo = self._boton(cabecera, "", self._cambiar_modo, ancho=3, pady=4)
        self.boton_modo.pack(side="right", padx=(0, 6))
        self.boton_apariencia = self._boton(cabecera, "◐", self._alternar_apariencia, ancho=3, pady=2,
                                            font=("Segoe UI", 11))
        self.boton_apariencia.pack(side="right", padx=(0, 6))

        caja = tk.Frame(interior, bg=t.PANEL_LIGHT, highlightthickness=1, highlightbackground=t.BORDER_STRONG)
        caja.pack(fill="x", padx=12, pady=(0, 8))
        tk.Label(caja, text="⌕", bg=t.PANEL_LIGHT, fg=t.MUTED, font=("Segoe UI", 11)).pack(side="left", padx=(10, 2))
        self.filtro = tk.StringVar(master=ventana)
        entrada = tk.Entry(caja, textvariable=self.filtro, bg=t.PANEL_LIGHT, fg=t.TEXT, insertbackground=t.CYAN,
                           relief="flat", font=("Segoe UI", 10))
        entrada.pack(side="left", fill="x", expand=True, ipady=5, padx=(0, 10))
        pista = tk.Label(entrada, text="Buscar un juego", bg=t.PANEL_LIGHT, fg=t.MUTED, font=("Segoe UI", 10))

        def enfocar(_evento=None) -> None:
            # Una ventana sin bordes no toma el teclado sola.
            ventana.focus_force()
            entrada.focus_set()

        entrada.bind("<Button-1>", enfocar, add="+")
        pista.bind("<Button-1>", enfocar)

        def mostrar_pista(*_args) -> None:
            if self.filtro.get():
                pista.place_forget()
            else:
                pista.place(x=1, rely=0.5, anchor="w")

        self.filtro.trace_add("write", mostrar_pista)
        self.filtro.trace_add("write", lambda *_: self._dibujar())
        mostrar_pista()

        pie = tk.Frame(interior, bg=self._clave, padx=14, pady=7)
        pie.pack(side="bottom", fill="x")
        tk.Frame(interior, bg=t.BORDER, height=1).pack(side="bottom", fill="x")
        agarradera = tk.Label(pie, text="◢", bg=self._clave, fg=t.MUTED, font=("Segoe UI", 10), cursor="size_nw_se")
        agarradera.pack(side="right", padx=(8, 0))
        agarradera.bind("<ButtonPress-1>", self._empezar_estirar)
        agarradera.bind("<B1-Motion>", self._estirar)
        agarradera.bind("<ButtonRelease-1>", self._soltar_estirar)
        self.agarradera = agarradera
        tk.Label(pie, text="● JARVIS", bg=self._clave, fg=t.CYAN, font=("Segoe UI", 8, "bold")).pack(side="right")
        # El aviso va último: con el widget angosto se recorta él, no «JARVIS» ni la agarradera.
        self.aviso = tk.Label(pie, text="", bg=self._clave, fg=t.MUTED, font=("Segoe UI", 8), anchor="w")
        self.aviso.pack(side="left", fill="x", expand=True)
        self._aviso_normal()

        self.panel_acciones = tk.Frame(interior, bg=t.PANEL_LIGHT, highlightthickness=1,
                                       highlightbackground=t.VIOLET, padx=12, pady=10)

        cuerpo = tk.Frame(interior, bg=self._clave)
        cuerpo.pack(fill="both", expand=True)
        self._cuerpo = cuerpo
        self.lienzo = tk.Canvas(cuerpo, bg=self._clave, highlightthickness=0, bd=0)
        barra = ttk.Scrollbar(cuerpo, orient="vertical", command=self.lienzo.yview,
                              style="Jarvis.Vertical.TScrollbar")
        self.lienzo.configure(yscrollcommand=barra.set)
        barra.pack(side="right", fill="y", padx=(0, 4), pady=4)
        self.lienzo.pack(side="left", fill="both", expand=True)
        self.lista = tk.Frame(self.lienzo, bg=self._clave, padx=12, pady=2)
        ventana_lista = self.lienzo.create_window((0, 0), window=self.lista, anchor="nw")
        self.lista.bind("<Configure>", lambda _e: self.lienzo.configure(scrollregion=self.lienzo.bbox("all")))
        def al_cambiar_ancho(evento) -> None:
            self.lienzo.itemconfigure(ventana_lista, width=evento.width)
            columnas = columnas_para(self.ventana.winfo_width())
            if columnas != self._columnas:
                self._columnas = columnas
                if self.modo == "tarjetas":
                    self._dibujar()

        self.lienzo.bind("<Configure>", al_cambiar_ancho)
        self.lista.jarvis_scroll = self.lienzo  # rueda del ratón (JarvisUIMixin._on_mousewheel)
        self.lienzo.jarvis_scroll = self.lienzo
        self._dibujar()

    # --- Dibujo ---------------------------------------------------------------------------------

    def _foto(self, nombre: str, tamano: tuple[int, int]):
        archivo = jarvis_portadas.ruta(nombre)
        if archivo is None:
            self._fotos.pop((nombre, tamano), None)
            return None
        marca = archivo.stat().st_mtime
        guardada = self._fotos.get((nombre, tamano))
        if guardada is not None and guardada[0] == marca:
            return guardada[1]
        try:
            from PIL import Image, ImageTk

            with Image.open(archivo) as imagen:
                chica = imagen.convert("RGB").resize(tamano, Image.LANCZOS)
            foto = ImageTk.PhotoImage(chica, master=self.ventana)
        except Exception:
            logging.exception("El widget no pudo abrir la portada de %s.", nombre)
            return None
        self._fotos[(nombre, tamano)] = (marca, foto)
        return foto

    def _juegos(self) -> tuple[list, list]:
        todos = filtrar(getattr(self.app, "_games", None), "")
        return todos, filtrar(todos, self.filtro.get())

    def _jugando(self) -> set[str]:
        vigilante = self.app.__dict__.get("games_watcher")
        try:
            return set(vigilante.jugando) if vigilante is not None else set()
        except Exception:
            return set()

    def _dibujar(self) -> None:
        if not self.visible:
            return
        for hijo in self.lista.winfo_children():
            hijo.destroy()
        self.boton_modo.configure(text="☰" if self.modo == "tarjetas" else "⊞")
        cargando = getattr(self.app, "_games", None) is None
        todos, visibles = self._juegos()
        self.contador.configure(text="Buscando juegos…" if cargando else f"{len(todos)} juego{'' if len(todos) == 1 else 's'} · Jarvis")
        jugando = self._jugando()
        if self.modo == "tarjetas":
            rejilla = tk.Frame(self.lista, bg=self._clave)
            rejilla.pack(anchor="n")  # tarjetas de ancho fijo, centradas; más ancho = más columnas
            columnas = self._columnas
            rejilla.grid_columnconfigure(tuple(range(columnas)), uniform="widget")
            for i, juego in enumerate(visibles):
                columna = i % columnas
                self._tarjeta(rejilla, juego, abierto(juego, jugando)).grid(
                    row=i // columnas, column=columna, sticky="nsew",
                    padx=(0, SEPARACION) if columna < columnas - 1 else 0, pady=(0, SEPARACION))
        else:
            for juego in visibles:
                self._fila(self.lista, juego, abierto(juego, jugando)).pack(fill="x", pady=(0, 4))
        if not cargando and not visibles:
            tk.Label(self.lista, text="Ningún juego con ese nombre." if self.filtro.get().strip() else
                     "No encontré juegos.", bg=self._clave, fg=t.MUTED, font=("Segoe UI", 9)).pack(pady=14)
        self._dibujar_acciones()

    def _cantidad_acciones(self, nombre: str) -> int:
        try:
            return len(self.app._game_actions(nombre))
        except Exception:
            return 0

    def _boton_acciones(self, padre, juego) -> tk.Button:
        cuantas = self._cantidad_acciones(juego.nombre)
        elegido = self.seleccionado == juego.nombre
        return self._boton(padre, f"⚙ {cuantas}" if cuantas else "⚙", lambda n=juego.nombre: self._alternar(n),
                           fg=t.VIOLET if cuantas or elegido else t.ORB_BRIGHT,
                           bg=t.CYAN_DARK if cuantas or elegido else t.PANEL_LIGHT, padx=8, pady=4)

    def _boton_jugar(self, padre, juego, texto: str) -> tk.Button:
        return self._boton(padre, texto, lambda n=juego.nombre: self._jugar(n), fg=t.BG, bg=t.CYAN, pady=4)

    def _tarjeta(self, padre, juego, corriendo: bool) -> tk.Frame:
        elegido = self.seleccionado == juego.nombre
        borde = t.VIOLET if elegido else (t.CYAN if corriendo else t.BORDER)
        tarjeta = tk.Frame(padre, bg=t.PANEL, highlightthickness=1, highlightbackground=borde)
        foto = self._foto(juego.nombre, (ANCHO_TARJETA, ALTO_PORTADA))
        if foto is not None:
            portada = tk.Label(tarjeta, image=foto, bg=t.PANEL, bd=0, height=ALTO_PORTADA)
        else:
            portada = tk.Frame(tarjeta, bg=t.PANEL_LIGHT, height=ALTO_PORTADA)
            portada.pack_propagate(False)
            tk.Label(portada, text=_iniciales(juego.nombre), bg=t.PANEL_LIGHT, fg=t.ORB_BRIGHT,
                     font=("Segoe UI", 16, "bold")).place(relx=0.5, rely=0.42, anchor="center")
            if jarvis_portadas.sin_resultado(juego.nombre):
                self._boton(portada, "＋ PORTADA", lambda n=juego.nombre: self._subir_portada(n), fg=t.CYAN,
                            bg=t.CYAN_DARK, padx=6, pady=1, font=("Segoe UI", 7, "bold")).place(
                    x=6, rely=1.0, y=-6, anchor="sw")
        portada.pack(fill="x")
        lapiz = self._boton(portada, "✎", None, bg=t.BG, fg=t.ORB_BRIGHT, padx=5, pady=0,
                            font=("Segoe UI", 9, "bold"))
        lapiz.configure(command=lambda: self._menu_portada(juego, lapiz.winfo_rootx(),
                                                           lapiz.winfo_rooty() + lapiz.winfo_height()))
        lapiz.place(relx=1.0, x=-6, y=6, anchor="ne")
        self._clic_derecho(portada, juego)
        if corriendo:
            tk.Label(portada, text="● ABIERTO", bg=t.BG, fg=t.CYAN, font=("Segoe UI", 7, "bold"),
                     padx=6).place(x=6, y=6)
        cuerpo = tk.Frame(tarjeta, bg=t.PANEL, padx=8, pady=6)
        cuerpo.pack(fill="x")
        tk.Label(cuerpo, text=_recortar(juego.nombre), bg=t.PANEL, fg=t.TEXT, font=("Segoe UI", 9, "bold"),
                 anchor="w").pack(fill="x", pady=(0, 5))
        botones = tk.Frame(cuerpo, bg=t.PANEL)
        botones.pack(fill="x")
        self._boton_jugar(botones, juego, "▶ JUGAR").pack(side="left", fill="x", expand=True)
        self._boton_acciones(botones, juego).pack(side="left", padx=(5, 0))
        return tarjeta

    def _fila(self, padre, juego, corriendo: bool) -> tk.Frame:
        elegido = self.seleccionado == juego.nombre
        fila = tk.Frame(padre, bg=t.PANEL, padx=6, pady=5, highlightthickness=1,
                        highlightbackground=t.VIOLET if elegido else (t.CYAN if corriendo else t.BORDER))
        foto = self._foto(juego.nombre, MINIATURA)
        if foto is not None:
            caja = tk.Label(fila, image=foto, bg=t.PANEL, bd=0)
            caja.pack(side="left")
        else:
            caja = tk.Frame(fila, bg=t.PANEL_LIGHT, width=MINIATURA[0], height=MINIATURA[1])
            caja.pack_propagate(False)
            caja.pack(side="left")
            tk.Label(caja, text=_iniciales(juego.nombre), bg=t.PANEL_LIGHT, fg=t.ORB_BRIGHT,
                     font=("Segoe UI", 8, "bold")).place(relx=0.5, rely=0.5, anchor="center")
        caja.configure(cursor="hand2")
        self._clic_derecho(caja, juego)
        caja.bind("<Button-1>", lambda e: self._menu_portada(juego, e.x_root, e.y_root))
        textos = tk.Frame(fila, bg=t.PANEL)
        textos.pack(side="left", fill="x", expand=True, padx=(8, 6))
        tk.Label(textos, text=_recortar(juego.nombre, 24), bg=t.PANEL, fg=t.TEXT, font=("Segoe UI", 9, "bold"),
                 anchor="w").pack(fill="x")
        cuantas = self._cantidad_acciones(juego.nombre)
        estado = "● Abierto" if corriendo else (f"{cuantas} {'acciones' if cuantas > 1 else 'acción'} al abrir"
                                                if cuantas else "Sin acciones")
        tk.Label(textos, text=estado, bg=t.PANEL, fg=t.CYAN if corriendo else t.MUTED, font=("Segoe UI", 8),
                 anchor="w").pack(fill="x")
        self._boton_jugar(fila, juego, " ▶ ").pack(side="right", ipadx=4)
        self._boton_acciones(fila, juego).pack(side="right", padx=(0, 5))
        return fila

    def _dibujar_acciones(self) -> None:
        panel = self.panel_acciones
        for hijo in panel.winfo_children():
            hijo.destroy()
        nombres = {j.nombre for j in self._juegos()[0]}
        if not self.seleccionado or self.seleccionado not in nombres:
            self.seleccionado = ""
            panel.pack_forget()
            return
        nombre = self.seleccionado
        cabeza = tk.Frame(panel, bg=t.PANEL_LIGHT)
        cabeza.pack(fill="x")
        tk.Label(cabeza, text="ACCIONES AL ABRIR", bg=t.PANEL_LIGHT, fg=t.VIOLET, font=("Segoe UI", 7, "bold"),
                 anchor="w").pack(anchor="w")
        tk.Label(cabeza, text=_recortar(nombre, 32), bg=t.PANEL_LIGHT, fg=t.TEXT, font=("Segoe UI", 10, "bold"),
                 anchor="w").pack(anchor="w")
        self._boton(cabeza, "✕", lambda: self._alternar(nombre), ancho=3, bg=t.PANEL).place(relx=1.0, y=0,
                                                                                         anchor="ne")
        pasos = self.app._game_actions(nombre)
        lista = tk.Frame(panel, bg=t.PANEL_LIGHT)
        lista.pack(fill="x", pady=(8, 8))
        if not pasos:
            tk.Label(lista, text="Ninguna todavía: se abre el juego y nada más.", bg=t.PANEL_LIGHT, fg=t.MUTED,
                     font=("Segoe UI", 8), anchor="w").pack(fill="x")
        for indice, paso in enumerate(pasos):
            fila = tk.Frame(lista, bg=t.PANEL, padx=8, pady=4)
            fila.pack(fill="x", pady=(0, 4))
            tk.Label(fila, text=f"{indice + 1}.", bg=t.PANEL, fg=t.MUTED, font=("Segoe UI", 8, "bold")).pack(
                side="left")
            tk.Label(fila, text=_recortar(self._describir(paso), 38), bg=t.PANEL, fg=t.TEXT, font=("Segoe UI", 9),
                     anchor="w").pack(side="left", fill="x", expand=True, padx=(6, 4))
            self._boton(fila, "✕", lambda i=indice: self._quitar_accion(nombre, i), ancho=2, bg=t.PANEL,
                        fg=t.MUTED).pack(side="right")
        botones = tk.Frame(panel, bg=t.PANEL_LIGHT)
        botones.pack(fill="x")
        self._boton(botones, "＋ EDITAR ACCIONES", lambda: self._editar(nombre), fg=t.VIOLET, bg=t.CYAN_DARK,
                    pady=5).pack(side="left", fill="x", expand=True)
        self._boton(botones, "CAMBIAR PORTADA", lambda: self._subir_portada(nombre), bg=t.PANEL,
                    pady=5).pack(side="left", fill="x", expand=True, padx=(6, 0))
        panel.pack(side="bottom", fill="x", padx=12, pady=(0, 10), before=self._cuerpo)

    def _clic_derecho(self, portada: tk.Widget, juego) -> None:
        """Clic derecho en la portada (o en sus iniciales): el menú para cambiarla."""
        for widget in (portada, *portada.winfo_children()):
            if not isinstance(widget, tk.Button):
                widget.bind("<Button-3>", lambda e: self._menu_portada(juego, e.x_root, e.y_root))

    def _menu_portada(self, juego, x: int, y: int) -> None:
        """«Cambiar portada»: una imagen de la PC o volver a buscarla en Steam."""
        menu = tk.Menu(self.ventana, tearoff=0, bg=t.PANEL, fg=t.TEXT, activebackground=t.HOVER,
                       activeforeground=t.TEXT)
        menu.add_command(label="Elegir una imagen de la PC…", command=lambda: self._subir_portada(juego.nombre))
        menu.add_command(label="Buscar de nuevo en Steam", command=lambda: self._buscar_de_nuevo(juego))
        self.menu_portada = menu
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def _buscar_de_nuevo(self, juego) -> None:
        import jarvis_ui_juegos

        self._avisar(f"Buscando la portada de {_recortar(juego.nombre, 24)}…", t.CYAN)
        jarvis_ui_juegos.buscar_de_nuevo(self.app, juego)

    def _describir(self, paso: dict) -> str:
        try:
            from jarvis_routine_form import describir_paso

            return describir_paso(self.app.tool_registry, paso)
        except Exception:
            return str(paso.get("tool", "acción"))

    # --- Acciones -------------------------------------------------------------------------------

    def _aviso_normal(self) -> None:
        if self.visible:
            self.aviso.configure(text="Arrastrá para mover", fg=t.MUTED)

    def _avisar(self, texto: str, color=None) -> None:
        self.aviso.configure(text=texto, fg=color or t.TEXT)
        self.ventana.after(5000, self._aviso_normal)

    def _jugar(self, nombre: str) -> None:
        if self.app.busy:
            self._avisar("Jarvis está ocupado; probá en un momento.", t.ERROR)
            return
        self._avisar(f"Abriendo {_recortar(nombre, 28)}…", t.CYAN)
        self.app._play_game(nombre)

    def _alternar(self, nombre: str) -> None:
        self.seleccionado = "" if self.seleccionado == nombre else nombre
        self._dibujar()

    def _quitar_accion(self, nombre: str, indice: int) -> None:
        import jarvis_core

        pasos = self.app._game_actions(nombre)
        if not 0 <= indice < len(pasos):
            return
        quitada = self._describir(pasos.pop(indice))
        try:
            jarvis_acciones_juego.guardar(jarvis_core.obtener_preferencia, jarvis_core.guardar_preferencia,
                                          nombre, pasos)
        except Exception:
            logging.exception("No se pudo quitar la acción de %s.", nombre)
            self._avisar("No pude guardar el cambio.", t.ERROR)
            return
        logging.info("Widget de juegos: quité «%s» de las acciones de %s.", quitada, nombre)
        self.app._render_games()  # la página JUEGOS y este widget
        self._avisar(f"Quité «{_recortar(quitada, 26)}».")

    def _editar(self, nombre: str) -> None:
        self.app._edit_game_actions(nombre, padre=self.ventana)

    def _subir_portada(self, nombre: str) -> None:
        import jarvis_ui_juegos

        jarvis_ui_juegos.subir_portada(self.app, nombre, padre=self.ventana)

    # --- Panel «Apariencia» (botón «◐») ------------------------------------------------------

    def _alternar_apariencia(self) -> None:
        if self.apariencia is not None and self.apariencia.winfo_exists():
            self._cerrar_apariencia()
        else:
            self._abrir_apariencia()

    def _cerrar_apariencia(self) -> None:
        if self.apariencia is not None and self.apariencia.winfo_exists():
            self.apariencia.destroy()
        self.apariencia = None
        if self.visible:
            self.boton_apariencia.configure(bg=t.PANEL_LIGHT, fg=t.ORB_BRIGHT)

    def _abrir_apariencia(self) -> None:
        panel = tk.Toplevel(self.ventana)
        panel.withdraw()
        panel.overrideredirect(True)
        panel.transient(self.ventana)
        panel.configure(bg=t.BORDER_STRONG)
        self.apariencia = panel
        self.boton_apariencia.configure(bg=t.CYAN_DARK, fg=t.TEXT)
        caja = tk.Frame(panel, bg=t.PANEL, padx=16, pady=14)
        caja.pack(fill="both", expand=True, padx=1, pady=1)

        cabeza = tk.Frame(caja, bg=t.PANEL)
        cabeza.pack(fill="x")
        tk.Label(cabeza, text="APARIENCIA", bg=t.PANEL, fg=t.TEXT, font=("Segoe UI", 10, "bold")).pack(side="left")
        self._boton(cabeza, "✕", self._cerrar_apariencia, ancho=3, pady=2).pack(side="right")

        fila = tk.Frame(caja, bg=t.PANEL)
        fila.pack(fill="x", pady=(14, 4))
        tk.Label(fila, text="Transparencia del fondo", bg=t.PANEL, fg=t.TEXT,
                 font=("Segoe UI", 9, "bold")).pack(side="left")
        self.valor_transparencia = tk.Label(fila, text="", bg=t.PANEL, fg=t.CYAN, font=("Segoe UI", 13, "bold"))
        self.valor_transparencia.pack(side="right")
        self.barra = tk.Canvas(caja, width=ANCHO_APARIENCIA - 34, height=26, bg=t.PANEL, highlightthickness=0,
                               bd=0, cursor="hand2")
        self.barra.pack(fill="x")
        for evento in ("<ButtonPress-1>", "<B1-Motion>"):
            self.barra.bind(evento, self._barra_clic)
        self.barra.bind("<Configure>", lambda _e: self._pintar_apariencia())
        extremos = tk.Frame(caja, bg=t.PANEL)
        extremos.pack(fill="x")
        tk.Label(extremos, text="Opaco", bg=t.PANEL, fg=t.MUTED, font=("Segoe UI", 8)).pack(side="left")
        tk.Label(extremos, text="Invisible", bg=t.PANEL, fg=t.MUTED, font=("Segoe UI", 8)).pack(side="right")
        atajos = tk.Frame(caja, bg=t.PANEL)
        atajos.pack(fill="x", pady=(8, 6))
        atajos.grid_columnconfigure(tuple(range(len(ATAJOS))), weight=1, uniform="atajo")
        self.botones_atajo = {}
        for i, valor in enumerate(ATAJOS):
            boton = self._boton(atajos, str(valor), lambda v=valor: self.poner_transparencia(v), pady=3)
            boton.grid(row=0, column=i, sticky="ew", padx=(0, 6) if i < len(ATAJOS) - 1 else 0)
            self.botones_atajo[valor] = boton
        tk.Label(caja, text="Solo el fondo: las tarjetas, el buscador y los botones quedan siempre visibles.",
                 bg=t.PANEL, fg=t.MUTED, font=("Segoe UI", 8), justify="left", anchor="w",
                 wraplength=ANCHO_APARIENCIA - 40).pack(fill="x")

        tk.Frame(caja, bg=t.BORDER, height=1).pack(fill="x", pady=12)
        tk.Label(caja, text="Vista", bg=t.PANEL, fg=t.TEXT, font=("Segoe UI", 9, "bold"), anchor="w").pack(fill="x")
        vistas = tk.Frame(caja, bg=t.PANEL_LIGHT, padx=3, pady=3)
        vistas.pack(fill="x", pady=(6, 0))
        vistas.grid_columnconfigure((0, 1), weight=1, uniform="vista")
        self.botones_vista = {}
        for i, (modo, texto) in enumerate((("tarjetas", "TARJETAS"), ("lista", "LISTA"))):
            boton = self._boton(vistas, texto, lambda m=modo: self.poner_modo(m), pady=4)
            boton.grid(row=0, column=i, sticky="ew", padx=(0, 3) if i == 0 else 0)
            self.botones_vista[modo] = boton

        tk.Frame(caja, bg=t.BORDER, height=1).pack(fill="x", pady=12)
        borde = tk.Frame(caja, bg=t.PANEL)
        borde.pack(fill="x")
        textos = tk.Frame(borde, bg=t.PANEL)
        textos.pack(side="left", fill="x", expand=True)
        tk.Label(textos, text="Borde del widget", bg=t.PANEL, fg=t.TEXT, font=("Segoe UI", 9, "bold"),
                 anchor="w").pack(fill="x")
        tk.Label(textos, text="Para ubicarlo con el fondo invisible", bg=t.PANEL, fg=t.MUTED,
                 font=("Segoe UI", 8), anchor="w").pack(fill="x")
        self.interruptor = tk.Canvas(borde, width=46, height=26, bg=t.PANEL, highlightthickness=0, bd=0,
                                     cursor="hand2")
        self.interruptor.pack(side="right")
        self.interruptor.bind("<Button-1>", lambda _e: self.poner_borde(not self.borde))

        self._boton(caja, "RESTABLECER", self.restablecer_apariencia, bg=t.PANEL, fg=t.ORB_BRIGHT, pady=6,
                    highlightthickness=1, highlightbackground=t.BORDER_STRONG).pack(fill="x", pady=(14, 0))

        self._pintar_apariencia()
        panel.update_idletasks()
        x = self.boton_apariencia.winfo_rootx() + self.boton_apariencia.winfo_width() - ANCHO_APARIENCIA
        x = max(self.ventana.winfo_rootx() + 8, x)
        y = self.boton_apariencia.winfo_rooty() + self.boton_apariencia.winfo_height() + 6
        panel.geometry(f"{ANCHO_APARIENCIA}x{panel.winfo_reqheight()}+{x}+{y}")
        panel.deiconify()
        panel.focus_force()
        panel.bind("<FocusOut>", lambda _e: panel.after(150, self._apariencia_perdio_foco))
        panel.bind("<Escape>", lambda _e: self._cerrar_apariencia())

    def _apariencia_perdio_foco(self) -> None:
        if self.apariencia is None or not self.apariencia.winfo_exists():
            return
        try:
            foco = self.apariencia.focus_get()
        except (KeyError, tk.TclError):
            foco = None
        if foco is None or foco.winfo_toplevel() is not self.apariencia:
            self._cerrar_apariencia()

    def _pintar_apariencia(self) -> None:
        """La barra (relleno hasta el valor y perilla), el número, los atajos, la vista y el interruptor."""
        if self.apariencia is None or not self.apariencia.winfo_exists():
            return
        self.valor_transparencia.configure(text=f"{self.transparencia} %")
        barra = self.barra
        barra.delete("all")
        ancho = barra.winfo_width() if barra.winfo_width() > 1 else int(barra.cget("width"))
        radio, centro = 9, 13
        izquierda, derecha = radio + 1, max(radio + 2, ancho - radio - 1)
        x = izquierda + (derecha - izquierda) * self.transparencia / 100
        barra.create_line(izquierda, centro, derecha, centro, fill=t.BORDER_STRONG, width=8, capstyle="round")
        if self.transparencia:
            barra.create_line(izquierda, centro, x, centro, fill=t.CYAN, width=8, capstyle="round")
        barra.create_oval(x - radio, centro - radio, x + radio, centro + radio, fill=t.TEXT, outline=t.CYAN,
                          width=3)
        for valor, boton in self.botones_atajo.items():
            elegido = valor == self.transparencia
            boton.configure(bg=t.CYAN_DARK if elegido else t.PANEL_LIGHT, fg=t.TEXT if elegido else t.ORB_BRIGHT,
                            highlightthickness=1, highlightbackground=t.CYAN if elegido else t.BORDER)
        for modo, boton in self.botones_vista.items():
            elegido = modo == self.modo
            boton.configure(bg=t.CYAN if elegido else t.PANEL_LIGHT, fg=t.BG if elegido else t.ORB_BRIGHT)
        interruptor = self.interruptor
        interruptor.delete("all")
        color = t.CYAN if self.borde else t.BORDER_STRONG
        interruptor.create_oval(1, 1, 25, 25, fill=color, outline=color)
        interruptor.create_oval(21, 1, 45, 25, fill=color, outline=color)
        interruptor.create_rectangle(13, 1, 33, 25, fill=color, outline=color)
        perilla = 24 if self.borde else 4
        interruptor.create_oval(perilla, 4, perilla + 18, 22, fill=t.TEXT, outline=t.TEXT)

    def _barra_clic(self, evento) -> None:
        ancho = max(60, self.barra.winfo_width())
        izquierda, derecha = 10, ancho - 10
        valor = (evento.x - izquierda) / max(1, derecha - izquierda) * 100
        self.poner_transparencia(int(round(min(100, max(0, valor)) / 5) * 5))

    def poner_transparencia(self, valor) -> None:
        """De 0 (opaco) a 100 (fondo invisible); se guarda medio segundo después del último cambio."""
        valor = transparencia_de(valor)
        if valor != self.transparencia:
            self.transparencia = valor
            self._aplicar_transparencia()
            if self.visible:
                if self._guardado_pendiente is not None:
                    self.ventana.after_cancel(self._guardado_pendiente)
                self._guardado_pendiente = self.ventana.after(500, self._guardar_transparencia)
            else:
                self._guardar_transparencia()
        self._pintar_apariencia()

    def _guardar_transparencia(self) -> None:
        self._guardado_pendiente = None
        self._guardar(PREF_TRANSPARENCIA, str(self.transparencia))
        logging.info("Widget de juegos: fondo %d %% transparente.", self.transparencia)

    def poner_borde(self, encendido: bool) -> None:
        self.borde = bool(encendido)
        self._guardar(PREF_BORDE, "1" if self.borde else "0")
        if self.visible:
            self.ventana.configure(bg=t.BORDER_STRONG if self.borde else self._clave)
        self._pintar_apariencia()

    def poner_modo(self, modo: str) -> None:
        if modo in MODOS and modo != self.modo:
            self._cambiar_modo()
        self._pintar_apariencia()

    def restablecer_apariencia(self) -> None:
        self.poner_transparencia(0)
        self.poner_borde(True)

    def _cambiar_modo(self) -> None:
        self.modo = "lista" if self.modo == "tarjetas" else "tarjetas"
        self._guardar(PREF_MODO, self.modo)
        self._dibujar()

    def _abrir_jarvis(self) -> None:
        self.app._show_from_tray()
        self.app._show_page("juegos")

    def _cerrar(self) -> None:
        self.app._set_games_widget(False)

    # --- Arrastre -------------------------------------------------------------------------------

    def _empezar_estirar(self, evento) -> None:
        self._estirando = (evento.x_root, evento.y_root, self.ventana.winfo_width(), self.ventana.winfo_height())

    def _estirar(self, evento) -> None:
        if self._estirando is None:
            return
        x0, y0, ancho, alto = self._estirando
        nuevo = tamano_de(f"{ancho + evento.x_root - x0}x{alto + evento.y_root - y0}")
        self.ventana.geometry(f"{nuevo[0]}x{nuevo[1]}")

    def _soltar_estirar(self, _evento) -> None:
        if self._estirando is None:
            return
        self._estirando = None
        self._guardar(PREF_TAMANO, f"{self.ventana.winfo_width()}x{self.ventana.winfo_height()}")
        self.al_fondo()

    def _empezar_arrastre(self, evento) -> None:
        self._arrastre = (evento.x_root - self.ventana.winfo_x(), evento.y_root - self.ventana.winfo_y())

    def _arrastrar(self, evento) -> None:
        if self._arrastre is not None:
            dx, dy = self._arrastre
            self.ventana.geometry(f"+{evento.x_root - dx}+{evento.y_root - dy}")

    def _soltar(self, _evento) -> None:
        if self._arrastre is None:
            return
        self._arrastre = None
        self._guardar(PREF_POSICION, f"{self.ventana.winfo_x()},{self.ventana.winfo_y()}")
        self.al_fondo()


def _iniciales(nombre: str) -> str:
    from jarvis_ui_juegos import iniciales

    return iniciales(nombre)
