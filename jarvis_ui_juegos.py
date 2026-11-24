"""Página 🎮 JUEGOS en tarjetas con portada (2026-11-23, pedido del usuario; diseño en el lienzo «Vista
Juegos de Jarvis»).

Cada juego es una tarjeta chica (portada de ``ANCHO_TARJETA``×``ALTO_PORTADA``, nombre, dónde está y
JUGAR / acciones), en tantas columnas como entren. Las portadas las busca ``jarvis_portadas`` en un hilo
aparte, de a una, y cada una que llega se avisa con el evento ``("portada_juego", nombre)``; mientras,
la tarjeta muestra las iniciales. Si no se encontró, la tarjeta ofrece «SUBIR PORTADA»; clic derecho en
cualquier portada: cambiarla o buscarla de nuevo.

Las funciones reciben la app (``JarvisUIMixin``): usan ``app.games_list``, ``app.games_filter``,
``app.games_summary``, ``app._games`` y los botones de siempre (``_play_game``, ``_edit_game_actions``).
"""

from __future__ import annotations

import logging
import threading
import tkinter as tk
from tkinter import ttk

import jarvis_portadas
import jarvis_theme as t

ANCHO_TARJETA = 228
ALTO_PORTADA = 107  # 460×215 de Steam a este ancho
SEPARACION = 14
MAX_NOMBRE = 26


def iniciales(nombre: str) -> str:
    palabras = [p for p in "".join(c if c.isalnum() else " " for c in nombre).split() if p]
    if len(palabras) > 1:
        return "".join(p[0] for p in palabras[:3]).upper()
    return nombre[:3].upper()


def recortar(texto: str, largo: int = MAX_NOMBRE) -> str:
    return texto if len(texto) <= largo else texto[: largo - 1].rstrip() + "…"


def donde(juego) -> str:
    """«D:\\Games · eldenring.exe»; las bibliotecas de Steam, solo «Steam»."""
    padre = juego.carpeta.parent
    base = "Steam" if padre.name.casefold() == "common" else str(padre)
    if len(base) > 18:
        base = "…" + base[-17:]
    return f"{base} · {juego.ejecutable.name}" if juego.ejecutable is not None else base


def columnas_para(ancho: int) -> int:
    return max(1, int((ancho + SEPARACION) // (ANCHO_TARJETA + SEPARACION)))


# --- Construcción -------------------------------------------------------------------------------

def construir(app, page: tk.Frame) -> None:
    app._games = None
    app._portadas_img: dict[str, object] = {}
    app._portadas_buscando: set[str] = set()
    app._portadas_hilo = None
    cabecera = tk.Frame(page, bg=t.BG, padx=28, pady=14)
    cabecera.pack(fill="x")
    cabecera.grid_columnconfigure(0, weight=1)
    app.games_summary = tk.Label(cabecera, text="Buscando juegos…", bg=t.BG, fg=t.MUTED, font=("Segoe UI", 10),
                                 anchor="w", justify="left")
    app.games_summary.grid(row=0, column=0, sticky="w")
    derecha = tk.Frame(cabecera, bg=t.BG)
    derecha.grid(row=0, column=1, sticky="e")
    app.games_covers = tk.Label(derecha, text="", bg=t.PANEL_LIGHT, fg=t.VIOLET, font=("Segoe UI", 8, "bold"),
                                padx=10, pady=4)
    app.games_covers.pack(side="left", padx=(0, 10))
    caja = tk.Frame(derecha, bg=t.PANEL_LIGHT, highlightthickness=1, highlightbackground=t.BORDER_STRONG)
    caja.pack(side="left", padx=(0, 10))
    tk.Label(caja, text="⌕", bg=t.PANEL_LIGHT, fg=t.MUTED, font=("Segoe UI", 11)).pack(side="left", padx=(10, 2))
    app.games_filter = tk.StringVar(master=page)
    entrada = tk.Entry(caja, textvariable=app.games_filter, bg=t.PANEL_LIGHT, fg=t.TEXT, insertbackground=t.CYAN,
                       relief="flat", font=("Segoe UI", 10), width=22)
    entrada.pack(side="left", ipady=6, padx=(0, 10))
    pista = tk.Label(entrada, text="Buscar un juego", bg=t.PANEL_LIGHT, fg=t.MUTED, font=("Segoe UI", 10))
    pista.bind("<Button-1>", lambda _e: entrada.focus_set())

    def mostrar_pista(*_args) -> None:
        if app.games_filter.get():
            pista.place_forget()
        else:
            pista.place(x=1, rely=0.5, anchor="w")

    app.games_filter.trace_add("write", mostrar_pista)
    app.games_filter.trace_add("write", lambda *_: app._render_games())
    mostrar_pista()
    app._ui_button(derecha, "▣  WIDGET", app._toggle_games_widget, fg=t.VIOLET).pack(side="left", padx=(0, 10))
    app._ui_button(derecha, "⟳  ACTUALIZAR", lambda: app._load_games_page(forzar=True), fg=t.MUTED).pack(side="left")

    def acomodar_cabecera(evento) -> None:
        if evento.width < 760:
            derecha.grid_configure(row=1, column=0, sticky="w", pady=(10, 0))
        else:
            derecha.grid_configure(row=0, column=1, sticky="e", pady=0)
        app.games_summary.configure(wraplength=max(220, evento.width - (60 if evento.width < 760 else 560)))

    cabecera.bind("<Configure>", acomodar_cabecera, add="+")

    lienzo = tk.Canvas(page, bg=t.BG, highlightthickness=0, bd=0)
    barra = ttk.Scrollbar(page, orient="vertical", command=lienzo.yview, style="Jarvis.Vertical.TScrollbar")
    lienzo.configure(yscrollcommand=barra.set)
    barra.pack(side="right", fill="y", padx=(0, 6), pady=8)
    lienzo.pack(side="left", fill="both", expand=True)
    app.games_list = tk.Frame(lienzo, bg=t.BG, padx=28, pady=6)
    ventana = lienzo.create_window((0, 0), window=app.games_list, anchor="nw")
    app.games_list.bind("<Configure>", lambda _e: lienzo.configure(scrollregion=lienzo.bbox("all")))

    def al_cambiar_ancho(evento) -> None:
        lienzo.itemconfigure(ventana, width=evento.width)
        columnas = columnas_para(evento.width - 56)
        if columnas != app.__dict__.get("_games_columnas"):
            app._games_columnas = columnas
            app._render_games()

    lienzo.bind("<Configure>", al_cambiar_ancho)
    app.games_list.jarvis_scroll = lienzo
    lienzo.jarvis_scroll = lienzo


# --- Dibujo -------------------------------------------------------------------------------------

def _imagen(app, nombre: str):
    """PhotoImage de la portada guardada (a tamaño de tarjeta), o None."""
    archivo = jarvis_portadas.ruta(nombre)
    if archivo is None:
        app._portadas_img.pop(nombre, None)
        return None
    marca = archivo.stat().st_mtime
    guardada = app._portadas_img.get(nombre)
    if guardada is not None and guardada[0] == marca:
        return guardada[1]
    try:
        from PIL import Image, ImageTk

        with Image.open(archivo) as imagen:
            chica = imagen.convert("RGB").resize((ANCHO_TARJETA, ALTO_PORTADA), Image.LANCZOS)
        foto = ImageTk.PhotoImage(chica, master=app.root)
    except Exception:
        logging.exception("No se pudo abrir la portada de %s.", nombre)
        return None
    app._portadas_img[nombre] = (marca, foto)
    return foto


def _portada(app, padre: tk.Misc, juego) -> tk.Widget:
    foto = _imagen(app, juego.nombre)
    if foto is not None:
        portada = tk.Label(padre, image=foto, bg=t.PANEL, bd=0, cursor="hand2")
    else:
        portada = tk.Frame(padre, bg=t.PANEL_LIGHT, width=ANCHO_TARJETA, height=ALTO_PORTADA)
        portada.pack_propagate(False)
        tk.Label(portada, text=iniciales(juego.nombre), bg=t.PANEL_LIGHT, fg=t.ORB_BRIGHT,
                 font=("Segoe UI", 20, "bold")).place(relx=0.5, rely=0.42, anchor="center")
        if juego.nombre in app._portadas_buscando:
            tk.Label(portada, text="BUSCANDO PORTADA…", bg=t.PANEL_LIGHT, fg=t.MUTED,
                     font=("Consolas", 7, "bold")).place(x=8, rely=1.0, y=-6, anchor="sw")
        else:
            tk.Button(portada, text="＋ SUBIR PORTADA", command=lambda: subir_portada(app, juego.nombre),
                      bg=t.CYAN_DARK, fg=t.CYAN, activebackground=t.ACCENT_ACTIVE, activeforeground=t.TEXT,
                      relief="flat", padx=8, pady=2, font=("Segoe UI", 8, "bold"), cursor="hand2").place(
                relx=0.5, rely=1.0, y=-8, anchor="s")
    menu = tk.Menu(portada, tearoff=0, bg=t.PANEL, fg=t.TEXT, activebackground=t.HOVER, activeforeground=t.TEXT)
    menu.add_command(label="Cambiar portada…", command=lambda: subir_portada(app, juego.nombre))
    menu.add_command(label="Buscar de nuevo en Steam", command=lambda: buscar_de_nuevo(app, juego))

    def abrir_menu(evento) -> None:
        try:
            menu.tk_popup(evento.x_root, evento.y_root)
        finally:
            menu.grab_release()

    for widget in (portada, *portada.winfo_children()):
        widget.bind("<Button-3>", abrir_menu)
    return portada


def _tarjeta(app, padre: tk.Misc, juego) -> tk.Frame:
    tarjeta = tk.Frame(padre, bg=t.PANEL, highlightthickness=1, highlightbackground=t.BORDER)
    tarjeta.jarvis_scroll = padre.jarvis_scroll if hasattr(padre, "jarvis_scroll") else None
    _portada(app, tarjeta, juego).pack(fill="x")
    tk.Frame(tarjeta, bg=t.BORDER, height=1).pack(fill="x")
    cuerpo = tk.Frame(tarjeta, bg=t.PANEL, padx=10, pady=8)
    cuerpo.pack(fill="x")
    tk.Label(cuerpo, text=recortar(juego.nombre), bg=t.PANEL, fg=t.TEXT, font=("Segoe UI", 10, "bold"),
             anchor="w").pack(fill="x")
    tk.Label(cuerpo, text=recortar(donde(juego), 34), bg=t.PANEL, fg=t.MUTED, font=("Consolas", 8),
             anchor="w").pack(fill="x", pady=(1, 8))
    botones = tk.Frame(cuerpo, bg=t.PANEL)
    botones.pack(fill="x")
    tk.Button(botones, text="▶  JUGAR", command=lambda n=juego.nombre: app._play_game(n), bg=t.CYAN, fg=t.BG,
              activebackground=t.ORB_BRIGHT, activeforeground=t.BG, relief="flat", pady=5,
              font=("Segoe UI", 9, "bold"), cursor="hand2").pack(side="left", fill="x", expand=True)
    cuantas = len(app._game_actions(juego.nombre))
    tk.Button(botones, text=f"⚙ {cuantas}" if cuantas else "⚙", command=lambda n=juego.nombre: app._edit_game_actions(n),
              bg=t.PANEL_LIGHT if not cuantas else t.CYAN_DARK, fg=t.VIOLET if cuantas else t.CYAN,
              activebackground=t.HOVER, activeforeground=t.TEXT, relief="flat", padx=10, pady=5,
              font=("Segoe UI", 9, "bold"), cursor="hand2").pack(side="left", padx=(6, 0))
    return tarjeta


def render(app, juegos=None) -> None:
    if juegos is not None:
        app._games = juegos
    juegos = app._games or []
    for hijo in app.games_list.winfo_children():
        hijo.destroy()
    filtro = app.games_filter.get().strip().casefold()
    visibles = [j for j in juegos if not filtro or filtro in j.nombre.casefold()]
    listos = [j for j in visibles if j.ejecutable is not None]
    sin_exe = [j for j in visibles if j.ejecutable is None]
    todos = [j for j in juegos if j.ejecutable is not None]
    app.games_summary.configure(
        text=f"{len(todos)} juegos reconocidos. Abrilos con «abrí …» o con ▶ JUGAR. Busca en «Games», "
             "«Epic Games» y «SteamLibrary» de cada disco, en la carpeta «Games» de tu usuario y en Steam.")
    _pintar_contador(app, todos)
    columnas = app.__dict__.get("_games_columnas") or 4
    rejilla = tk.Frame(app.games_list, bg=t.BG)
    rejilla.pack(anchor="nw", fill="x")
    rejilla.jarvis_scroll = app.games_list.jarvis_scroll
    for i, juego in enumerate(listos):
        _tarjeta(app, rejilla, juego).grid(row=i // columnas, column=i % columnas, sticky="nw",
                                           padx=(0, SEPARACION), pady=(0, SEPARACION))
    if sin_exe:
        tk.Label(app.games_list, text="CARPETAS SIN UN JUEGO CLARO (no se pueden abrir por nombre)", bg=t.BG,
                 fg=t.MUTED, font=("Consolas", 9, "bold")).pack(anchor="w", pady=(16, 4))
        for juego in sin_exe:
            tk.Label(app.games_list, text=f"•  {juego.nombre}  —  {juego.problema}", bg=t.BG, fg=t.MUTED,
                     font=("Segoe UI", 9), anchor="w", justify="left", wraplength=900).pack(anchor="w")
    if not visibles:
        tk.Label(app.games_list, text="Ningún juego coincide con la búsqueda." if filtro else
                 "No encontré juegos en las carpetas de juegos.", bg=t.BG, fg=t.MUTED,
                 font=("Segoe UI", 10)).pack(anchor="w", pady=10)
    if juegos and filtro == "":
        buscar_portadas(app, todos)
    widget = app.__dict__.get("_games_widget")  # el widget del escritorio usa los mismos juegos
    if widget is not None:
        widget.refrescar()


def _pintar_contador(app, todos) -> None:
    con = sum(1 for j in todos if jarvis_portadas.ruta(j.nombre) is not None)
    texto = f"PORTADAS · {con} DE {len(todos)}"
    if app._portadas_buscando:
        texto += f" · BUSCANDO {len(app._portadas_buscando)}"
    app.games_covers.configure(text=texto)


# --- Búsqueda en segundo plano y subida a mano ------------------------------------------------

def buscar_portadas(app, juegos) -> None:
    """Un hilo que busca, de a una, las portadas que faltan (no repite las fallidas recientes)."""
    hilo = app.__dict__.get("_portadas_hilo")
    if hilo is not None and hilo.is_alive():
        return
    pendientes = [j for j in juegos if jarvis_portadas.hay_que_buscar(j.nombre)]
    if not pendientes:
        return
    app._portadas_buscando.update(j.nombre for j in pendientes)

    def trabajar() -> None:
        for juego in pendientes:
            try:
                jarvis_portadas.obtener(juego.nombre, juego.carpeta)
            except Exception:
                logging.exception("Falló la búsqueda de la portada de %s.", juego.nombre)
            app.events.put(("portada_juego", juego.nombre))

    app._portadas_hilo = threading.Thread(target=trabajar, name="JarvisPortadas", daemon=True)
    app._portadas_hilo.start()


def portada_lista(app, nombre: str) -> None:
    """Hilo de Tk: llegó (o no) la portada de ``nombre``."""
    app._portadas_buscando.discard(nombre)
    if not app._portadas_buscando or jarvis_portadas.ruta(nombre) is not None:
        render(app)


def subir_portada(app, nombre: str, padre=None) -> None:
    """``padre``: la ventana sobre la que se abre el diálogo (el widget del escritorio, si Jarvis está
    en la bandeja: con la ventana principal oculta, el diálogo no se vería)."""
    from tkinter import filedialog, messagebox

    padre = padre or app.root
    archivo = filedialog.askopenfilename(
        parent=padre, title=f"Portada para «{nombre}»",
        filetypes=[("Imágenes", "*.jpg *.jpeg *.png *.webp *.bmp"), ("Todos los archivos", "*.*")])
    if not archivo:
        return
    try:
        jarvis_portadas.guardar_manual(nombre, archivo)
    except (OSError, ValueError) as error:
        messagebox.showerror("No pude usar esa imagen", str(error), parent=padre)
        return
    logging.info("Portada de %s subida a mano.", nombre)
    render(app)


def buscar_de_nuevo(app, juego) -> None:
    """Borra la portada (también una subida a mano) y la busca otra vez en Steam, aunque haya un
    filtro escrito en la página (``render`` solo busca sin filtro)."""
    jarvis_portadas.olvidar(juego.nombre)
    buscar_portadas(app, [juego])
    render(app)
