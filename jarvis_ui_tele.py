"""Página 📺 TELE (2026-11-23, rediseño pedido por el usuario; diseño en el lienzo «Vista Televisor de
Jarvis»): tarjeta de estado con PRENDER/APAGAR a lo ancho, control remoto redondo, entradas como
selector, apps con buscador y frases de voz como etiquetas.

Se adapta al ancho: con la ventana ancha, el control va a la izquierda y lo demás a la derecha; con la
ventana angosta, todo en una columna. La grilla de apps y las etiquetas calculan cuántas entran por
fila. La página tiene scroll propio (rueda del ratón incluida).

Todo color sale de los tokens de ``jarvis_theme`` en el momento de dibujar, así el cambio de tema
(``recolorear``) los encuentra. Los botones llaman a ``app._tv_button(accion)``; el estado lo pinta
``JarvisUIMixin._paint_tv_status`` en ``app.tv_page_name``/``tv_page_pill``/``tv_page_detail`` y las
apps llegan por ``app._render_tv_apps(lista)``, igual que antes.
"""

from __future__ import annotations

import math
import tkinter as tk
from tkinter import ttk

import jarvis_theme as t

ANCHO_DOS_COLUMNAS = 880  # px del contenido: menos, y todo va en una columna
ANCHO_CONTROL = 380
ANCHO_APP = 170  # px por app en la grilla
DIAMETRO_CRUZ = 236
FRASES_VOZ = ("«prendé la tele»", "«subí el volumen de la tele»", "«abrí Netflix en la tele»",
              "«poné la tele en HDMI 1»", "«pausá la tele»")


def columnas_para(ancho: int, por_columna: int, minimo: int = 1, maximo: int = 8) -> int:
    """Cuántas columnas de ``por_columna`` px entran en ``ancho``."""
    return max(minimo, min(maximo, int(ancho // por_columna)))


def filtrar_apps(lista: list[tuple[str, str]], buscar: str) -> list[tuple[str, str]]:
    buscar = (buscar or "").strip().casefold()
    return [(clave, nombre) for clave, nombre in lista if buscar in nombre.casefold()] if buscar else list(lista)


def repartir(anchos: list[int], disponible: int, separacion: int = 8) -> list[int]:
    """Fila de cada etiqueta en un acomodo «de corrido» (las etiquetas de voz): las que no entran
    en el ancho que queda pasan a la fila siguiente."""
    filas, fila, usado = [], 0, 0
    for ancho in anchos:
        if usado and usado + separacion + ancho > disponible:
            fila, usado = fila + 1, 0
        usado += (separacion if usado else 0) + ancho
        filas.append(fila)
    return filas


def construir(app, page: tk.Frame) -> None:
    import jarvis_tv

    # --- Página con scroll ------------------------------------------------------------------
    marco = tk.Frame(page, bg=t.BG)
    marco.pack(fill="both", expand=True)
    lienzo = tk.Canvas(marco, bg=t.BG, highlightthickness=0, bd=0)
    barra = ttk.Scrollbar(marco, orient="vertical", command=lienzo.yview, style="Jarvis.Vertical.TScrollbar")
    lienzo.configure(yscrollcommand=barra.set)
    barra.pack(side="right", fill="y", padx=(0, 4), pady=8)
    lienzo.pack(side="left", fill="both", expand=True)
    cuerpo = tk.Frame(lienzo, bg=t.BG, padx=20, pady=14)
    ventana = lienzo.create_window((0, 0), window=cuerpo, anchor="nw")
    cuerpo.bind("<Configure>", lambda _e: lienzo.configure(scrollregion=lienzo.bbox("all")))
    lienzo.bind("<Configure>", lambda e: lienzo.itemconfigure(ventana, width=e.width))
    cuerpo.jarvis_scroll = lienzo  # rueda del ratón (ver _on_mousewheel)
    lienzo.jarvis_scroll = lienzo

    def tarjeta(padre, titulo: str | None = None) -> tk.Frame:
        caja = tk.Frame(padre, bg=t.PANEL, padx=20, pady=16, highlightthickness=1, highlightbackground=t.BORDER)
        if titulo:
            tk.Label(caja, text=titulo, bg=t.PANEL, fg=t.MUTED, font=("Consolas", 9, "bold")).pack(anchor="w",
                                                                                              pady=(0, 12))
        return caja

    def boton(padre, texto, accion, *, fondo="PANEL_LIGHT", color="TEXT", encima="HOVER", ancho=None, tam=10,
              negrita=True, pad=(14, 9)) -> tk.Button:
        """Colores por NOMBRE de token: al pasar el ratón se leen del tema de ese momento (si no,
        después de cambiar de tema volvía el color del tema anterior)."""
        def tok(nombre: str) -> str:
            return getattr(t, nombre)

        b = tk.Button(padre, text=texto, command=lambda: app._tv_button(accion), bg=tok(fondo), fg=tok(color),
                      activebackground=tok(encima), activeforeground=tok(color), relief="flat", bd=0,
                      cursor="hand2", highlightthickness=0, padx=pad[0], pady=pad[1],
                      font=("Segoe UI", tam, "bold" if negrita else "normal"))
        if ancho:
            b.configure(width=ancho)
        b.bind("<Enter>", lambda _e: b.configure(bg=tok(encima)))
        b.bind("<Leave>", lambda _e: b.configure(bg=tok(fondo)))
        return b

    # --- Estado, a lo ancho -------------------------------------------------------------------
    estado = tarjeta(cuerpo)
    estado.pack(fill="x", pady=(0, 14))
    estado.grid_columnconfigure(1, weight=1)
    icono = tk.Canvas(estado, width=64, height=64, bg=t.PANEL, highlightthickness=0, bd=0)
    icono.create_rectangle(2, 2, 62, 62, fill=t.CYAN_DARK, outline=t.CYAN_DARK)
    icono.create_rectangle(15, 18, 49, 41, outline=t.CYAN, width=2)
    icono.create_line(24, 49, 40, 49, fill=t.CYAN, width=2)
    icono.create_line(32, 41, 32, 49, fill=t.CYAN, width=2)
    icono.grid(row=0, column=0, rowspan=2, sticky="w", padx=(0, 16))
    linea = tk.Frame(estado, bg=t.PANEL)
    linea.grid(row=0, column=1, sticky="sw")
    app.tv_page_name = tk.Label(linea, text="La tele", bg=t.PANEL, fg=t.TEXT, font=("Segoe UI", 16, "bold"))
    app.tv_page_name.pack(side="left")
    app.tv_page_pill = tk.Label(linea, text="…", bg=t.PANEL_LIGHT, fg=t.MUTED, font=("Segoe UI", 8, "bold"),
                                padx=10, pady=2)
    app.tv_page_pill.pack(side="left", padx=(12, 0))
    app.tv_page_detail = tk.Label(estado, text="Conectando con la tele…", bg=t.PANEL, fg=t.MUTED,
                                  font=("Segoe UI", 10), anchor="w", justify="left")
    app.tv_page_detail.grid(row=1, column=1, sticky="nw", pady=(4, 0))
    energia = tk.Frame(estado, bg=t.PANEL)
    boton(energia, "⏻  PRENDER", "power_on", fondo="CYAN", color="BG", encima="ORB_BRIGHT",
          pad=(20, 11)).pack(side="left")
    boton(energia, "⏻  APAGAR", "power_off", color="ERROR", pad=(20, 11)).pack(side="left", padx=(10, 0))

    def acomodar_estado(ancho: int) -> None:
        """Angosto: los botones de encendido pasan debajo del nombre."""
        if ancho < 640:
            energia.grid(row=2, column=0, columnspan=2, sticky="w", pady=(14, 0))
            app.tv_page_detail.configure(wraplength=max(200, ancho - 140))
        else:
            energia.grid(row=0, column=2, rowspan=2, sticky="e", padx=(16, 0))
            app.tv_page_detail.configure(wraplength=max(200, ancho - 520))

    # --- Columnas: control | entradas, apps, voz --------------------------------------------
    columnas = tk.Frame(cuerpo, bg=t.BG)
    columnas.pack(fill="x")
    izquierda = tk.Frame(columnas, bg=t.BG, width=ANCHO_CONTROL)
    derecha = tk.Frame(columnas, bg=t.BG)

    # Control remoto
    control = tarjeta(izquierda, "CONTROL")
    control.pack(fill="x")
    cruz = tk.Canvas(control, width=DIAMETRO_CRUZ, height=DIAMETRO_CRUZ, bg=t.PANEL, highlightthickness=0, bd=0,
                     cursor="hand2")
    cruz.pack(pady=(0, 18))
    c, r = DIAMETRO_CRUZ / 2, DIAMETRO_CRUZ / 2 - 2
    cruz.create_oval(c - r, c - r, c + r, c + r, fill=t.PANEL_LIGHT, outline=t.BORDER_STRONG, width=1)
    cruz.create_oval(c - r + 10, c - r + 10, c + r - 10, c + r - 10, outline=t.BORDER, width=1)

    def zona(accion: str, angulo: float) -> None:
        """Un cuarto del anillo (un sector de 90°) que responde al clic, con su flecha."""
        inicio = angulo - 45
        sector = cruz.create_arc(c - r + 12, c - r + 12, c + r - 12, c + r - 12, start=inicio, extent=90,
                                 fill=t.PANEL_LIGHT, outline="", style=tk.PIESLICE, tags=(accion, "zona"))
        rad = math.radians(angulo)
        dx, dy = math.cos(rad), -math.sin(rad)
        px, py = c + dx * r * 0.74, c + dy * r * 0.74
        lateral = (-dy, dx)
        punta = (px + dx * 9, py + dy * 9)
        base1 = (px - dx * 5 + lateral[0] * 10, py - dy * 5 + lateral[1] * 10)
        base2 = (px - dx * 5 - lateral[0] * 10, py - dy * 5 - lateral[1] * 10)
        flecha = cruz.create_line(*base1, *punta, *base2, fill=t.ORB_BRIGHT, width=3, capstyle="round",
                                  joinstyle="round", tags=(accion,))
        for item in (sector, flecha):
            cruz.tag_bind(item, "<Button-1>", lambda _e, a=accion: app._tv_button(a))
        cruz.tag_bind(sector, "<Enter>", lambda _e, s=sector: cruz.itemconfigure(s, fill=t.HOVER))
        cruz.tag_bind(sector, "<Leave>", lambda _e, s=sector: cruz.itemconfigure(s, fill=t.PANEL_LIGHT))

    for accion, angulo in (("right", 0), ("up", 90), ("left", 180), ("down", 270)):
        zona(accion, angulo)
    ok_r = 50
    cruz.create_oval(c - ok_r - 6, c - ok_r - 6, c + ok_r + 6, c + ok_r + 6, fill=t.CYAN_DARK, outline="",
                     tags=("ok",))
    ok = cruz.create_oval(c - ok_r, c - ok_r, c + ok_r, c + ok_r, fill=t.CYAN, outline="", tags=("ok",))
    cruz.create_text(c, c, text="OK", fill=t.BG, font=("Segoe UI", 16, "bold"), tags=("ok",))
    cruz.tag_bind("ok", "<Button-1>", lambda _e: app._tv_button("ok"))
    cruz.tag_bind("ok", "<Enter>", lambda _e: cruz.itemconfigure(ok, fill=t.ORB_BRIGHT))
    cruz.tag_bind("ok", "<Leave>", lambda _e: cruz.itemconfigure(ok, fill=t.CYAN))

    navegar = tk.Frame(control, bg=t.PANEL)
    navegar.pack(fill="x", pady=(0, 14))
    for i, (texto, accion) in enumerate((("↩  Atrás", "back"), ("⌂  Inicio", "home"), ("⚙  Panel", "settings"))):
        navegar.grid_columnconfigure(i, weight=1, uniform="nav")
        boton(navegar, texto, accion).grid(row=0, column=i, sticky="ew", padx=4)

    pildoras = tk.Frame(control, bg=t.PANEL)
    pildoras.pack()
    for titulo, botones in (("VOLUMEN", (("−", "volume_down", None), ("MUDO", "mute", "CYAN_DARK"),
                                         ("+", "volume_up", None))),
                            ("REPRODUCCIÓN", (("◀◀", "rewind", None), ("▶ ❚❚", "play_pause", "VIOLET"),
                                              ("▶▶", "forward", None)))):
        grupo = tk.Frame(pildoras, bg=t.PANEL)
        grupo.pack(side="left", padx=8)
        tk.Label(grupo, text=titulo, bg=t.PANEL, fg=t.MUTED, font=("Consolas", 8, "bold")).pack(pady=(0, 6))
        caja = tk.Frame(grupo, bg=t.PANEL_LIGHT, padx=4, pady=4, highlightthickness=1,
                        highlightbackground=t.BORDER)
        caja.pack()
        for texto, accion, destacado in botones:
            boton(caja, texto, accion, fondo=destacado or "PANEL_LIGHT",
                  color="BG" if destacado == "VIOLET" else "ORB_BRIGHT", encima="ACCENT_ACTIVE" if destacado else "HOVER",
                  tam=11, pad=(12, 7)).pack(side="left", padx=2)  # texto, no emoji (en Tk salen en recuadros)

    # Entradas
    entradas = tarjeta(derecha, "ENTRADAS")
    entradas.pack(fill="x", pady=(0, 14))
    selector = tk.Frame(entradas, bg=t.PANEL_LIGHT, padx=5, pady=5, highlightthickness=1,
                        highlightbackground=t.BORDER)
    selector.pack(fill="x")
    for n in range(1, 5):
        selector.grid_columnconfigure(n - 1, weight=1, uniform="hdmi")
        boton(selector, f"HDMI {n}", f"hdmi{n}", color="CYAN", tam=11).grid(row=0, column=n - 1, sticky="ew",
                                                                           padx=3)

    # Apps
    apps = tarjeta(derecha)
    apps.pack(fill="x", pady=(0, 14))
    cabecera = tk.Frame(apps, bg=t.PANEL)
    cabecera.pack(fill="x", pady=(0, 12))
    titulo_apps = tk.Label(cabecera, text="APPS", bg=t.PANEL, fg=t.MUTED, font=("Consolas", 9, "bold"))
    titulo_apps.pack(side="left")
    caja_busca = tk.Frame(cabecera, bg=t.PANEL_LIGHT, highlightthickness=1, highlightbackground=t.BORDER_STRONG)
    caja_busca.pack(side="right")
    tk.Label(caja_busca, text="⌕", bg=t.PANEL_LIGHT, fg=t.MUTED, font=("Segoe UI", 11)).pack(side="left",
                                                                                          padx=(10, 2))
    buscar = tk.StringVar(master=page)
    entrada = tk.Entry(caja_busca, textvariable=buscar, bg=t.PANEL_LIGHT, fg=t.TEXT, insertbackground=t.CYAN,
                       relief="flat", font=("Segoe UI", 10), width=22)
    entrada.pack(side="left", ipady=6, padx=(0, 10))
    pista = tk.Label(entrada, text="Buscar una app", bg=t.PANEL_LIGHT, fg=t.MUTED, font=("Segoe UI", 10))
    pista.bind("<Button-1>", lambda _e: entrada.focus_set())

    def mostrar_pista(*_args) -> None:
        if buscar.get():
            pista.place_forget()
        else:
            pista.place(x=1, rely=0.5, anchor="w")

    buscar.trace_add("write", mostrar_pista)
    mostrar_pista()
    rejilla = tk.Frame(apps, bg=t.PANEL)
    rejilla.pack(fill="x")
    tk.Label(apps, text="Las apps que abras con el control de la tele aparecen acá solas.", bg=t.PANEL,
             fg=t.MUTED, font=("Segoe UI", 9), justify="left").pack(anchor="w", pady=(10, 0))
    # Por nombre de token: la grilla se redibuja al buscar o cambiar el ancho, quizás con otro tema.
    insignias = (("CYAN_DARK", "CYAN"), ("ACCENT_ACTIVE", "TEXT"), ("PANEL_LIGHT", "VIOLET"))
    estado_apps = {"lista": [], "columnas": 0, "dibujadas": None}

    def tarjeta_app(padre, indice: int, clave: str, nombre: str) -> tk.Frame:
        fondo, letra = (getattr(t, token) for token in insignias[indice % len(insignias)])
        caja = tk.Frame(padre, bg=t.PANEL_LIGHT, padx=8, pady=7, cursor="hand2", highlightthickness=1,
                        highlightbackground=t.BORDER)
        insignia = tk.Label(caja, text=nombre[:1].upper(), bg=fondo, fg=letra, width=2,
                            font=("Segoe UI", 11, "bold"))
        insignia.pack(side="left", padx=(0, 10))
        rotulo = tk.Label(caja, text=nombre, bg=t.PANEL_LIGHT, fg=t.TEXT, font=("Segoe UI", 10), anchor="w")
        rotulo.pack(side="left", fill="x", expand=True)
        for w in (caja, insignia, rotulo):
            w.bind("<Button-1>", lambda _e, k=clave: app._tv_button(f"app:{k}"))
        for w in (caja, rotulo):
            w.bind("<Enter>", lambda _e: (caja.configure(bg=t.HOVER), rotulo.configure(bg=t.HOVER)))
            w.bind("<Leave>", lambda _e: (caja.configure(bg=t.PANEL_LIGHT), rotulo.configure(bg=t.PANEL_LIGHT)))
        return caja

    def dibujar_grilla(*_args) -> None:
        if not rejilla.winfo_exists():
            return
        visibles = filtrar_apps(estado_apps["lista"], buscar.get())
        columnas_n = estado_apps["columnas"] or 3
        clave = (tuple(visibles), columnas_n)
        if clave == estado_apps["dibujadas"]:
            return
        estado_apps["dibujadas"] = clave
        for hijo in rejilla.winfo_children():
            hijo.destroy()
        for col in range(8):
            rejilla.grid_columnconfigure(col, weight=1 if col < columnas_n else 0,
                                         uniform="app" if col < columnas_n else "")
        total = len(estado_apps["lista"])
        titulo_apps.configure(text=f"APPS · {len(visibles)}" if len(visibles) == total
                              else f"APPS · {len(visibles)} DE {total}")
        if not visibles:
            tk.Label(rejilla, text="Ninguna app con ese nombre.", bg=t.PANEL, fg=t.MUTED,
                     font=("Segoe UI", 10)).grid(row=0, column=0, columnspan=columnas_n, sticky="w")
            return
        for i, (clave_app, nombre) in enumerate(visibles):
            tarjeta_app(rejilla, i, clave_app, nombre).grid(row=i // columnas_n, column=i % columnas_n,
                                                            sticky="ew", padx=3, pady=3)

    def dibujar_apps(lista) -> None:
        """Lista de apps (fijas + aprendidas): la llama ``_refresh_tv_views``."""
        lista = [tuple(x) for x in lista]
        if lista == estado_apps["lista"] and estado_apps["dibujadas"] is not None:
            return
        estado_apps["lista"] = lista
        app._tv_apps_key = list(lista)
        dibujar_grilla()

    buscar.trace_add("write", dibujar_grilla)
    app._tv_apps_grid, app._tv_apps_key = rejilla, None
    app._render_tv_apps = dibujar_apps
    app._tv_buscar_app = buscar

    # Por voz: etiquetas que pasan de fila según el ancho
    voz = tarjeta(derecha, "POR VOZ")
    voz.pack(fill="x")
    etiquetas_frame = tk.Frame(voz, bg=t.PANEL)
    etiquetas_frame.pack(fill="x")
    etiquetas = [tk.Label(etiquetas_frame, text=frase, bg=t.PANEL_LIGHT, fg=t.VIOLET, font=("Segoe UI", 10),
                          padx=12, pady=6, highlightthickness=1, highlightbackground=t.BORDER)
                 for frase in FRASES_VOZ]

    def acomodar_etiquetas(ancho: int) -> None:
        """De corrido, cada fila con su propio largo (con grid, la segunda fila se alineaba con las
        columnas de la primera y quedaban huecos)."""
        anchos = [e.winfo_reqwidth() for e in etiquetas]
        alto = max(e.winfo_reqheight() for e in etiquetas)
        filas = repartir(anchos, max(120, ancho))
        x_fila: dict[int, int] = {}
        for etiqueta, fila, ancho_e in zip(etiquetas, filas, anchos):
            x = x_fila.get(fila, 0)
            etiqueta.place(x=x, y=fila * (alto + 8))
            x_fila[fila] = x + ancho_e + 8
        etiquetas_frame.configure(height=(max(filas) + 1) * (alto + 8))

    # --- Acomodo según el ancho --------------------------------------------------------------
    estado_ancho = {"modo": None}

    def acomodar(evento=None) -> None:
        ancho = (evento.width if evento is not None else cuerpo.winfo_width()) - 40
        if ancho < 200:
            return
        acomodar_estado(ancho)
        dos = ancho >= ANCHO_DOS_COLUMNAS
        if dos != estado_ancho["modo"]:
            estado_ancho["modo"] = dos
            app._tv_columnas = 2 if dos else 1
            izquierda.grid_forget()
            derecha.grid_forget()
            if dos:
                columnas.grid_columnconfigure(0, weight=0, minsize=ANCHO_CONTROL)
                columnas.grid_columnconfigure(1, weight=1)
                izquierda.grid(row=0, column=0, sticky="nw")
                derecha.grid(row=0, column=1, sticky="new", padx=(16, 0))
            else:
                columnas.grid_columnconfigure(0, weight=1, minsize=0)
                columnas.grid_columnconfigure(1, weight=0)
                izquierda.grid(row=0, column=0, sticky="new", pady=(0, 14))
                derecha.grid(row=1, column=0, sticky="new")
        ancho_derecha = ancho - ANCHO_CONTROL - 16 if dos else ancho
        estado_apps["columnas"] = columnas_para(ancho_derecha - 46, ANCHO_APP)
        dibujar_grilla()
        acomodar_etiquetas(ancho_derecha - 46)

    import jarvis_ui_kit

    # Al terminar el arrastre del borde, no con cada píxel (2026-11-24: se trababa).
    cuerpo.bind("<Configure>", lambda e: jarvis_ui_kit.postergar(cuerpo, lambda: acomodar(e), clave="acomodar"),
                add="+")
    dibujar_apps([(clave, nombre) for clave, (nombre, _enlace) in jarvis_tv.APPS.items()])
    acomodar_etiquetas(600)
