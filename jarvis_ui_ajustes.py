"""Página ⚙ AJUSTES agrupada (2026-11-24, pedido del usuario: «que se agrupen las configs que se puedan agrupar
para que esa vista no esté tan cargada de golpe»; diseño en el lienzo «Vistas Sistema, Programas, Mensajes,
Tareas y Ajustes»).

A la izquierda, los grupos (``GRUPOS``); a la derecha, solo el elegido. El buscador de arriba filtra los grupos
por cualquier texto que tengan adentro (títulos, descripciones, botones), así no hay palabras clave que mantener.
Los ajustes en sí siguen armándose en ``JarvisUIMixin._build_settings_page`` (y sus ``_build_*_settings``),
cada uno dentro del marco de su grupo (``app._ajustes_grupos[id]``); sus botones y refrescos no cambian.
Con la ventana angosta, los grupos pasan arriba en dos o tres columnas.
"""

from __future__ import annotations

import tkinter as tk

import jarvis_theme
import jarvis_ui_kit as kit

# (id, letra, nombre, resumen, descripción)
GRUPOS = (
    ("voz", "V", "Voz y escucha", "Cómo te habla y te escucha",
     "La voz de Jarvis, la palabra de activación, la escucha continua y el filtro de tu voz."),
    ("gestos", "G", "Gestos y cámara", "Mano, puntero y gestos propios",
     "Controlá la PC con la mano frente a la cámara."),
    ("pantallas", "P", "Pantallas", "Monitores y barra de tareas",
     "Cómo están ubicadas tus pantallas y qué hace Jarvis con ellas."),
    ("apariencia", "A", "Apariencia", "Tema, animaciones y avisos",
     "Colores de Jarvis y cómo te avisa. También: «cambia al tema rojo»."),
    ("juego", "J", "Juegos y rendimiento", "Modo juego y GPU", "Qué hace Jarvis cuando abrís un juego."),
    ("conexiones", "C", "Conexiones", "Celular, tele e internet",
     "Dispositivos y servicios con los que Jarvis se comunica."),
    ("inicio", "I", "Inicio y rutina diaria", "Arranque y resumen de la mañana", "Qué hace Jarvis al prender la PC."),
)
POR_DEFECTO = "voz"
ANCHO_NAV = 250
ANCHO_ANGOSTO = 820  # por debajo, los grupos van arriba


def construir(app, cuerpo: tk.Frame) -> dict[str, tk.Frame]:
    """Cabecera con buscador, barra de grupos y un marco (sin mostrar) por grupo. Devuelve {id: marco}."""
    t = jarvis_theme
    cabecera = tk.Frame(cuerpo, bg=t.BG)
    cabecera.pack(fill="x", pady=(0, 16))
    cabecera.grid_columnconfigure(0, weight=1)
    # El título «AJUSTES» ya está en la cabecera de la página.
    kit.nota(cabecera, "Agrupados por tema. Elegí un grupo o buscá un ajuste por su nombre.", fondo="BG").grid(
        row=0, column=0, sticky="we")
    caja = tk.Frame(cabecera, bg=t.PANEL_LIGHT, highlightthickness=1, highlightbackground=t.BORDER_STRONG)
    caja.grid(row=0, column=1, sticky="e", padx=(16, 0))
    tk.Label(caja, text="⌕", bg=t.PANEL_LIGHT, fg=t.MUTED, font=("Segoe UI", 11)).pack(side="left", padx=(10, 2))
    app._ajustes_busqueda = tk.StringVar(master=cuerpo)
    entrada = tk.Entry(caja, textvariable=app._ajustes_busqueda, bg=t.PANEL_LIGHT, fg=t.TEXT, insertbackground=t.CYAN,
                       relief="flat", font=("Segoe UI", 10), width=30)
    entrada.pack(side="left", ipady=6, padx=(0, 10))
    pista = tk.Label(entrada, text="Buscar un ajuste (p. ej. «cámara»)", bg=t.PANEL_LIGHT, fg=t.MUTED,
                     font=("Segoe UI", 10))
    pista.bind("<Button-1>", lambda _e: entrada.focus_set())

    def al_escribir(*_args) -> None:
        if app._ajustes_busqueda.get():
            pista.place_forget()
        else:
            pista.place(x=1, rely=0.5, anchor="w")
        filtrar(app, app._ajustes_busqueda.get())

    app._ajustes_busqueda.trace_add("write", al_escribir)

    zona = tk.Frame(cuerpo, bg=t.BG)
    zona.pack(fill="both", expand=True)
    zona.grid_columnconfigure(1, weight=1)
    nav = tk.Frame(zona, bg=t.BG, width=ANCHO_NAV)
    nav.grid(row=0, column=0, sticky="nw", padx=(0, 20))
    contenido = tk.Frame(zona, bg=t.BG)
    contenido.grid(row=0, column=1, sticky="nsew")
    app._ajustes_zona, app._ajustes_nav_marco, app._ajustes_contenido = zona, nav, contenido
    app._ajustes_titulo = tk.Label(contenido, text="", bg=t.BG, fg=t.TEXT, font=("Segoe UI", 14, "bold"), anchor="w")
    app._ajustes_titulo.pack(fill="x")
    app._ajustes_descripcion = kit.nota(contenido, "", fondo="BG")
    app._ajustes_descripcion.pack(fill="x", pady=(2, 12))
    app._ajustes_vacio = tk.Label(nav, text="Ningún ajuste con ese nombre.", bg=t.BG, fg=t.MUTED,
                                  font=("Segoe UI", 9), anchor="w")
    app._ajustes_grupos = {}
    app._ajustes_nav = {}
    for indice, (ident, letra, nombre, resumen, _descripcion) in enumerate(GRUPOS):
        boton = tk.Frame(nav, bg=t.BG, padx=10, pady=8, cursor="hand2", highlightthickness=1,
                         highlightbackground=t.BG)
        icono = tk.Label(boton, text=letra, width=3, bg=t.PANEL_LIGHT, fg=t.ORB_BRIGHT, font=("Segoe UI", 11, "bold"),
                         pady=3)
        icono.pack(side="left")
        textos = tk.Frame(boton, bg=t.BG)
        textos.pack(side="left", fill="x", expand=True, padx=(10, 0))
        titulo = tk.Label(textos, text=nombre, bg=t.BG, fg=t.TEXT, font=("Segoe UI", 10, "bold"), anchor="w")
        titulo.pack(fill="x")
        detalle = tk.Label(textos, text=resumen, bg=t.BG, fg=t.MUTED, font=("Segoe UI", 8), anchor="w")
        detalle.pack(fill="x")
        for widget in (boton, icono, textos, titulo, detalle):
            widget.bind("<Button-1>", lambda _e, g=ident: mostrar(app, g))
        boton.jarvis_visible = True
        app._ajustes_nav[ident] = (boton, icono, (textos, titulo, detalle), indice)
        marco = tk.Frame(contenido, bg=t.BG)
        marco.jarvis_scroll = getattr(cuerpo, "jarvis_scroll", None)
        app._ajustes_grupos[ident] = marco
    app._ajustes_pie = tk.Frame(contenido, bg=t.BG)
    app._ajustes_pie.pack(fill="x", side="bottom")
    app._ajustes_actual = None
    app._ajustes_columnas_nav = 0
    zona.bind("<Configure>", lambda e: _acomodar(app, e.width), add="+")
    _acomodar(app, 1000)
    al_escribir()
    return app._ajustes_grupos


def _acomodar(app, ancho: int) -> None:
    """Ancha: grupos en una columna a la izquierda. Angosta: arriba, en 2 o 3 columnas."""
    angosta = ancho < ANCHO_ANGOSTO
    columnas = (3 if ancho >= 560 else 2) if angosta else 1
    if columnas == app._ajustes_columnas_nav:
        return
    app._ajustes_columnas_nav = columnas
    nav = app._ajustes_nav_marco
    if angosta:
        nav.grid_configure(row=0, column=0, columnspan=2, sticky="we", padx=0, pady=(0, 16))
        app._ajustes_contenido.grid_configure(row=1, column=0, columnspan=2)
    else:
        nav.grid_configure(row=0, column=0, columnspan=1, sticky="nw", padx=(0, 20), pady=0)
        app._ajustes_contenido.grid_configure(row=0, column=1, columnspan=1)
    for columna in range(3):
        nav.grid_columnconfigure(columna, weight=1 if columna < columnas else 0, uniform="nav" if columna < columnas else "")
    _ubicar_nav(app)


def _ubicar_nav(app) -> None:
    visibles = [i for i in app._ajustes_nav if app._ajustes_nav[i][0].jarvis_visible]
    columnas = max(1, app._ajustes_columnas_nav)
    for ident, (boton, *_resto) in app._ajustes_nav.items():
        boton.grid_forget()
    for posicion, ident in enumerate(visibles):
        fila, columna = divmod(posicion, columnas)
        app._ajustes_nav[ident][0].grid(row=fila, column=columna, sticky="we", pady=(0, 6),
                                        padx=(0, 6) if columnas > 1 and columna < columnas - 1 else 0)
    if visibles:
        app._ajustes_vacio.grid_forget()
    else:
        app._ajustes_vacio.grid(row=0, column=0, sticky="w")


def mostrar(app, ident: str) -> None:
    """Muestra el grupo ``ident`` (y marca su botón)."""
    t = jarvis_theme
    if ident not in app._ajustes_grupos:
        ident = POR_DEFECTO
    anterior = app._ajustes_actual
    if anterior is not None and anterior != ident:
        app._ajustes_grupos[anterior].pack_forget()
    app._ajustes_grupos[ident].pack(fill="x", before=app._ajustes_pie)
    app._ajustes_actual = ident
    _, _letra, nombre, _resumen, descripcion = next(g for g in GRUPOS if g[0] == ident)
    app._ajustes_titulo.configure(text=nombre)
    app._ajustes_descripcion.configure(text=descripcion)
    for otro, (boton, icono, textos, _indice) in app._ajustes_nav.items():
        elegido = otro == ident
        fondo = t.CYAN_DARK if elegido else t.BG
        boton.configure(bg=fondo, highlightbackground=t.BORDER_STRONG if elegido else t.BG)
        for widget in textos:
            widget.configure(bg=fondo)
        icono.configure(bg=t.CYAN if elegido else t.PANEL_LIGHT, fg=t.BG if elegido else t.ORB_BRIGHT)
    lienzo = getattr(app._ajustes_grupos[ident], "jarvis_scroll", None)
    if lienzo is not None:
        lienzo.yview_moveto(0)


def _textos_de(widget: tk.Misc) -> list[str]:
    salida, pendientes = [], [widget]
    while pendientes:
        actual = pendientes.pop()
        pendientes.extend(actual.winfo_children())
        try:
            texto = actual.cget("text")
        except tk.TclError:
            continue
        if texto:
            salida.append(str(texto))
    return salida


def coincide(texto_buscado: str, textos: list[str]) -> bool:
    buscado = " ".join(texto_buscado.casefold().split())
    return not buscado or any(buscado in texto.casefold() for texto in textos)


def filtrar(app, texto: str) -> None:
    """Deja en la barra solo los grupos que tienen ``texto`` en algún lado; si el elegido no, muestra el primero."""
    if not getattr(app, "_ajustes_nav", None):
        return
    for ident, letra, nombre, resumen, descripcion in GRUPOS:
        boton = app._ajustes_nav[ident][0]
        boton.jarvis_visible = coincide(texto, [nombre, resumen, descripcion,
                                                *_textos_de(app._ajustes_grupos[ident])])
    _ubicar_nav(app)
    visibles = [i for i in app._ajustes_nav if app._ajustes_nav[i][0].jarvis_visible]
    if visibles and app._ajustes_actual not in visibles:
        mostrar(app, visibles[0])


def _descendientes(widget: tk.Misc) -> list:
    salida, pendientes = [], list(widget.winfo_children())
    while pendientes:
        actual = pendientes.pop()
        salida.append(actual)
        pendientes.extend(actual.winfo_children())
    return salida


def ajustar_textos(app) -> None:
    """Los textos de cada tarjeta se ajustan al ancho que les queda junto a sus botones (los ``wraplength``
    fijos de antes, pensados para la página entera, se cortaban con los grupos al costado)."""
    for marco in app._ajustes_grupos.values():
        for tarjeta in marco.winfo_children():
            etiquetas = [w for w in _descendientes(tarjeta)
                         if isinstance(w, tk.Label) and int(str(w.cget("wraplength") or 0)) > 0]
            if etiquetas:
                tarjeta.bind("<Configure>", lambda e, t=tarjeta, ls=etiquetas: kit.postergar(
                    t, lambda: _ajustar(ls, e.width), clave="textos"), add="+")


def _ajustar(etiquetas: list, ancho: int) -> None:
    if ancho < 100:
        return
    for etiqueta in etiquetas:
        if not etiqueta.winfo_exists():
            continue
        ocupado = 0
        if etiqueta.winfo_manager() == "grid":
            columna = int(etiqueta.grid_info()["column"])
            ocupado = sum(w.winfo_reqwidth() for w in etiqueta.master.grid_slaves()
                          if int(w.grid_info()["column"]) != columna)
        etiqueta.configure(wraplength=max(200, ancho - ocupado - 70))

