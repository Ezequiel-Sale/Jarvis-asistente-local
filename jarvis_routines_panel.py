"""Panel de rutinas y automatizaciones (roadmap punto 3).

Permite ver cada rutina con su disparador y pasos, pausarla o reanudarla,
cambiar su permiso (solo puede ser automática si todas sus acciones son de
riesgo bajo) y eliminarla. Opera en el hilo de Tk con consultas SQLite locales.

2026-11-23, rediseño pedido por el usuario (diseño en el lienzo «Vista Rutinas de Jarvis»): una
tarjeta por rutina con el texto completo (antes las filas cortaban el disparador y los pasos),
filtros (Todas, Automáticas, Preguntan, Pausadas) con su cantidad, buscador, y tantas columnas
como entren en el ancho. Los ejemplos para crearlas por voz van al final, como etiquetas.
"""

import logging
import tkinter as tk
from tkinter import messagebox, ttk

import jarvis_routines
import jarvis_theme as t
from jarvis_routine_form import RoutineForm
from jarvis_theme import (
    BG, CYAN, ERROR, HOVER, MUTED, PANEL, PANEL_LIGHT, TEXT, VIOLET,
)
from jarvis_tool_registry import ToolError, ToolRegistry

AYUDA = (
    "Pulsa «+ NUEVA» para crear una rutina o un comando personalizado con el formulario,\n"
    "o díselo a Jarvis. Ejemplos:\n"
    "«Cuando conecte los auriculares, cambia la salida a auriculares automáticamente»\n"
    "«Cuando desconecte los auriculares, cambia la salida a parlantes automáticamente»\n"
    "«Programa una rutina llamada Inicio a las 09:00 de lunes a viernes y pon el volumen al 30%»"
)
EJEMPLOS_VOZ = (
    "«Cuando conecte los auriculares, cambiá la salida a auriculares automáticamente»",
    "«Cuando desconecte los auriculares, cambiá la salida a parlantes»",
    "«Programá una rutina llamada Inicio a las 09:00 de lunes a viernes y poné el volumen al 30 %»",
)
FILTROS = (("todas", "Todas"), ("auto", "Automáticas"), ("preguntan", "Preguntan"), ("pausadas", "Pausadas"))
ANCHO_TARJETA = 380  # px por columna de tarjetas


_VALORES_LEGIBLES = {
    "headphones": "auriculares",
    "speakers": "parlantes",
    "True": "sí",
    "False": "no",
    "shutdown": "apagar",
    "restart": "reiniciar",
    "sleep": "suspender",
    "pause": "pausar",
    "play": "reanudar",
    "next": "siguiente",
    "previous": "anterior",
}


def coincide_filtro(routine: jarvis_routines.RoutineInfo, filtro: str) -> bool:
    automatica = routine.permission == jarvis_routines.PERMISO_AUTOMATICA
    return {"todas": True, "auto": automatica, "preguntan": not automatica,
            "pausadas": not routine.active}.get(filtro, True)


def tipo_disparador(trigger: str) -> str:
    """«hora», «frase» o «evento» (auriculares, USB, programas, internet…): elige el ícono."""
    if trigger == jarvis_routines.DISPARADOR_FRASE:
        return "frase"
    if trigger in (jarvis_routines.DISPARADOR_HORA, "time", "", None):
        return "hora"
    return "evento"


def columnas_para(ancho: int) -> int:
    return max(1, min(4, int(ancho // ANCHO_TARJETA)))


class RoutinesPanel:
    def __init__(self, parent: tk.Misc, registry: ToolRegistry, *, incrustado: bool = False) -> None:
        """``incrustado``: sección de la ventana principal (un Frame que ocupa
        ``parent``) en vez de una ventana aparte."""
        self.registry = registry
        self.incrustado = incrustado
        self._filtro = "todas"
        self._tarjetas: list[tk.Frame] = []
        self._columnas = 0
        if incrustado:
            self.window = tk.Frame(parent, bg=BG)
            self.window.pack(fill="both", expand=True)
        else:
            self.window = tk.Toplevel(parent)
            self.window.title("J.A.R.V.I.S. | Rutinas y automatizaciones")
            self.window.geometry("900x620")
            self.window.minsize(560, 400)
            self.window.configure(bg=BG)
            self.window.bind("<Escape>", lambda _event: self.close())
        self.window.grid_rowconfigure(2, weight=1)
        self.window.grid_columnconfigure(0, weight=1)

        header = tk.Frame(self.window, bg=BG, padx=24, pady=14)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(0, weight=1)
        if not incrustado:  # incrustado, el título ya está en la cabecera principal
            tk.Label(header, text="RUTINAS Y AUTOMATIZACIONES", bg=BG, fg=CYAN,
                     font=("Segoe UI", 16, "bold")).grid(row=0, column=0, sticky="w")
        self._subtitulo = tk.Label(
            header, text="Lo que Jarvis hace solo: a una hora, al conectar algo o al oír una frase.",
            bg=BG, fg=MUTED, font=("Segoe UI", 10), justify="left", anchor="w",
        )
        self._subtitulo.grid(row=1, column=0, sticky="w")
        acciones = tk.Frame(header, bg=BG)
        acciones.grid(row=0, column=1, rowspan=2, sticky="e", padx=(12, 0))
        self._button(acciones, "⟳  ACTUALIZAR", self.refresh).pack(side="left", padx=(0, 8))
        tk.Button(acciones, text="+  NUEVA RUTINA", command=self._new_routine, bg=CYAN, fg=BG,
                  activebackground=t.ORB_BRIGHT, activeforeground=BG, relief="flat", padx=16, pady=8,
                  font=("Segoe UI", 9, "bold"), cursor="hand2").pack(side="left")
        header.bind("<Configure>", lambda e: self._subtitulo.configure(wraplength=max(200, e.width - 360)),
                    add="+")

        # Filtros y buscador
        barra = tk.Frame(self.window, bg=PANEL, padx=12, pady=10, highlightthickness=1,
                         highlightbackground=t.BORDER)
        barra.grid(row=1, column=0, sticky="ew", padx=24, pady=(0, 12))
        barra.grid_columnconfigure(1, weight=1)
        filtros = tk.Frame(barra, bg=PANEL)
        filtros.grid(row=0, column=0, sticky="w")
        self._botones_filtro: dict[str, tk.Button] = {}
        for clave, nombre in FILTROS:
            boton = tk.Button(filtros, text=nombre, command=lambda c=clave: self._elegir_filtro(c),
                              relief="flat", padx=12, pady=6, font=("Segoe UI", 9, "bold"), cursor="hand2",
                              bg=PANEL, fg=CYAN, activebackground=HOVER, activeforeground=TEXT)
            boton.pack(side="left", padx=(0, 6))
            self._botones_filtro[clave] = boton
        caja = tk.Frame(barra, bg=PANEL_LIGHT, highlightthickness=1, highlightbackground=t.BORDER_STRONG)
        caja.grid(row=0, column=2, sticky="e", padx=(12, 0))
        tk.Label(caja, text="⌕", bg=PANEL_LIGHT, fg=MUTED, font=("Segoe UI", 11)).pack(side="left", padx=(10, 2))
        self.busqueda = tk.StringVar(master=self.window)
        self._entrada = tk.Entry(caja, textvariable=self.busqueda, bg=PANEL_LIGHT, fg=TEXT, insertbackground=CYAN,
                                 relief="flat", font=("Segoe UI", 10), width=24)
        self._entrada.pack(side="left", ipady=6, padx=(0, 10))
        pista = tk.Label(self._entrada, text="Buscar una rutina", bg=PANEL_LIGHT, fg=MUTED, font=("Segoe UI", 10))
        pista.bind("<Button-1>", lambda _e: self._entrada.focus_set())

        def mostrar_pista(*_args) -> None:
            if self.busqueda.get():
                pista.place_forget()
            else:
                pista.place(x=1, rely=0.5, anchor="w")

        self.busqueda.trace_add("write", mostrar_pista)
        self.busqueda.trace_add("write", lambda *_a: self._dibujar())
        mostrar_pista()

        def acomodar_barra(evento) -> None:
            """Angosta: el buscador pasa debajo de los filtros."""
            if evento.width < 640:
                caja.grid_configure(row=1, column=0, columnspan=3, sticky="w", padx=0, pady=(10, 0))
            else:
                caja.grid_configure(row=0, column=2, columnspan=1, sticky="e", padx=(12, 0), pady=0)

        barra.bind("<Configure>", acomodar_barra, add="+")

        # Tarjetas (con scroll)
        body = tk.Frame(self.window, bg=BG)
        body.grid(row=2, column=0, sticky="nsew", padx=(24, 8))
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(body, bg=BG, highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(body, orient="vertical", command=self.canvas.yview,
                                  style="Jarvis.Vertical.TScrollbar")
        scrollbar.grid(row=0, column=1, sticky="ns", padx=(6, 0))
        self.canvas.configure(yscrollcommand=scrollbar.set)
        body.jarvis_scroll = self.canvas  # rueda del ratón (ver JarvisUIMixin._on_mousewheel)
        self.canvas.jarvis_scroll = self.canvas
        self.rows = tk.Frame(self.canvas, bg=BG)
        self.rows.jarvis_scroll = self.canvas
        self._rows_window = self.canvas.create_window((0, 0), window=self.rows, anchor="nw")
        self.rows.bind(
            "<Configure>",
            lambda _event: self.canvas.configure(scrollregion=self.canvas.bbox("all")),
        )
        self.canvas.bind("<Configure>", self._al_cambiar_ancho)

        self.refresh()
        self.focus()

    # --- Utilidades -------------------------------------------------------------

    def _button(self, parent: tk.Misc, text: str, command, *, fg: str | None = None,
                state: str = "normal", bg: str | None = None) -> tk.Button:
        return tk.Button(
            parent, text=text, command=command, bg=bg or PANEL_LIGHT, fg=fg or CYAN,
            activebackground=HOVER, activeforeground=TEXT, relief="flat",
            padx=12, pady=7, font=("Consolas", 8, "bold"), cursor="hand2",
            state=state, disabledforeground=MUTED,
        )

    def is_open(self) -> bool:
        try:
            return bool(self.window.winfo_exists())
        except tk.TclError:
            return False

    def focus(self) -> None:
        if not self.incrustado:
            self.window.deiconify()
        self.window.lift()
        self.window.focus_force()

    def close(self) -> None:
        if self.is_open():
            self.window.destroy()

    def _step_texts(self, routine: jarvis_routines.RoutineInfo) -> list[str]:
        textos = []
        for step in routine.steps:
            try:
                label = self.registry.get(step.tool).display_name
            except ToolError:
                label = step.tool
            detalle = ", ".join(
                _VALORES_LEGIBLES.get(str(value), f"{value} %" if key == "percentage" else str(value))
                for key, value in step.arguments.items()
            )
            textos.append(f"{label} ({detalle})" if detalle else label)
        return textos

    def _step_labels(self, routine: jarvis_routines.RoutineInfo) -> str:
        return " → ".join(self._step_texts(routine)) or "sin pasos válidos"

    # --- Contenido --------------------------------------------------------------

    def refresh(self) -> None:
        try:
            self._rutinas = jarvis_routines.listar_automatizaciones()
            self._error = None
        except Exception as error:
            logging.exception("No se pudieron cargar las rutinas.")
            self._rutinas, self._error = [], str(error)
        self._dibujar()

    def _elegir_filtro(self, clave: str) -> None:
        self._filtro = clave
        self._dibujar()

    def _visibles(self) -> list:
        buscar = self.busqueda.get().strip().casefold()
        salida = []
        for routine in self._rutinas:
            if not coincide_filtro(routine, self._filtro):
                continue
            if buscar:
                texto = " ".join((routine.name, self._cuando(routine), *self._step_texts(routine))).casefold()
                if buscar not in texto:
                    continue
            salida.append(routine)
        return salida

    @staticmethod
    def _cuando(routine: jarvis_routines.RoutineInfo) -> str:
        cuando = jarvis_routines.describir_disparador(routine.trigger, routine.time, routine.days, routine.phrases)
        if routine.once:
            cuando += ", una sola vez"
        return cuando[:1].upper() + cuando[1:]

    def _dibujar(self) -> None:
        for child in self.rows.winfo_children():
            child.destroy()
        self._tarjetas = []
        for clave, nombre in FILTROS:
            cuantas = sum(1 for r in self._rutinas if coincide_filtro(r, clave))
            marcado = clave == self._filtro
            self._botones_filtro[clave].configure(
                text=f"{nombre}  {cuantas}", bg=t.CYAN_DARK if marcado else PANEL,
                fg=TEXT if marcado else CYAN)
        if self._error:
            self._mensaje(f"No pude cargar las rutinas: {self._error}", ERROR)
        elif not self._rutinas:
            self._mensaje("Todavía no hay rutinas ni automatizaciones.", MUTED)
        else:
            visibles = self._visibles()
            if not visibles:
                self._mensaje("Ninguna rutina coincide con ese filtro.", MUTED)
            for routine in visibles:
                self._tarjetas.append(self._add_row(routine))
        self._ejemplos = self._tarjeta_ejemplos()
        self._columnas = 0
        self._acomodar(self.canvas.winfo_width())

    def _mensaje(self, texto: str, color: str) -> None:
        tk.Label(self.rows, text=texto, bg=BG, fg=color, font=("Segoe UI", 11)).grid(
            row=0, column=0, columnspan=4, sticky="w", padx=4, pady=16)

    def _al_cambiar_ancho(self, event) -> None:
        import jarvis_ui_kit

        self.canvas.itemconfigure(self._rows_window, width=event.width)
        # Las tarjetas se reacomodan al terminar el arrastre del borde (2026-11-24: se trababa).
        jarvis_ui_kit.postergar(self.canvas, lambda: self._acomodar(event.width), clave="acomodar")

    def _acomodar(self, ancho: int) -> None:
        """Tantas columnas de tarjetas como entren; el texto se ajusta al ancho de cada una."""
        if ancho < 50:
            ancho = 900
        columnas = columnas_para(ancho)
        ancho_columna = (ancho - 16 * (columnas - 1)) // columnas
        for col in range(4):
            self.rows.grid_columnconfigure(col, weight=1 if col < columnas else 0,
                                           uniform="tarjeta" if col < columnas else "")
        base = 1 if (self._error or not self._tarjetas) else 0
        for i, tarjeta in enumerate(self._tarjetas):
            tarjeta.grid(row=base + i // columnas, column=i % columnas, sticky="new",
                         padx=(0 if i % columnas == 0 else 8, 0 if i % columnas == columnas - 1 else 8),
                         pady=(0, 16))
            for etiqueta in getattr(tarjeta, "_jarvis_ajustables", ()):
                etiqueta.configure(wraplength=max(160, ancho_columna - etiqueta._jarvis_margen))
        filas = base + (len(self._tarjetas) + columnas - 1) // columnas
        self._ejemplos.grid(row=filas, column=0, columnspan=columnas, sticky="ew", pady=(0, 16))
        self._acomodar_etiquetas(ancho - 40)
        self._columnas = columnas

    # --- Tarjetas ---------------------------------------------------------------

    def _icono(self, padre: tk.Misc, tipo: str, activa: bool) -> tk.Canvas:
        fondo, trazo = {"hora": (t.CYAN_DARK, CYAN), "frase": (PANEL_LIGHT, VIOLET),
                        "evento": (t.ACCENT_ACTIVE, TEXT)}[tipo]
        if not activa:
            fondo, trazo = PANEL_LIGHT, MUTED
        lienzo = tk.Canvas(padre, width=44, height=44, bg=PANEL, highlightthickness=0, bd=0)
        lienzo.create_rectangle(1, 1, 43, 43, fill=fondo, outline=fondo)
        if tipo == "hora":
            lienzo.create_oval(12, 12, 32, 32, outline=trazo, width=2)
            lienzo.create_line(22, 16, 22, 22, 27, 25, fill=trazo, width=2, capstyle="round")
        elif tipo == "frase":
            lienzo.create_rectangle(18, 10, 26, 25, outline=trazo, width=2)
            lienzo.create_arc(13, 13, 31, 31, start=180, extent=180, style=tk.ARC, outline=trazo, width=2)
            lienzo.create_line(22, 31, 22, 35, fill=trazo, width=2)
        else:
            lienzo.create_line(18, 10, 18, 16, fill=trazo, width=2)
            lienzo.create_line(26, 10, 26, 16, fill=trazo, width=2)
            lienzo.create_rectangle(14, 16, 30, 24, outline=trazo, width=2)
            lienzo.create_line(22, 24, 22, 34, fill=trazo, width=2)
        return lienzo

    @staticmethod
    def _chip(padre: tk.Misc, texto: str, fondo: str, color: str) -> tk.Label:
        return tk.Label(padre, text=texto, bg=fondo, fg=color, font=("Segoe UI", 8, "bold"), padx=8, pady=2)

    def _add_row(self, routine: jarvis_routines.RoutineInfo) -> tk.Frame:
        """Una tarjeta: ícono, nombre y estado; disparador; pasos; permiso; acciones."""
        activa = routine.active
        tarjeta = tk.Frame(self.rows, bg=PANEL, padx=18, pady=16, highlightthickness=1,
                           highlightbackground=t.BORDER_STRONG if activa else t.BORDER)
        tarjeta.jarvis_scroll = self.canvas
        ajustables: list[tk.Label] = []

        def ajustable(etiqueta: tk.Label, margen: int) -> tk.Label:
            etiqueta._jarvis_margen = margen
            ajustables.append(etiqueta)
            return etiqueta

        arriba = tk.Frame(tarjeta, bg=PANEL)
        arriba.pack(fill="x")
        self._icono(arriba, tipo_disparador(routine.trigger), activa).pack(side="left", anchor="n", padx=(0, 12))
        textos = tk.Frame(arriba, bg=PANEL)
        textos.pack(side="left", fill="x", expand=True)
        linea = tk.Frame(textos, bg=PANEL)
        linea.pack(fill="x")
        ajustable(tk.Label(linea, text=routine.name, bg=PANEL, fg=TEXT if activa else MUTED,
                           font=("Segoe UI", 12, "bold"), justify="left", anchor="w"), 170).pack(side="left")
        if activa:
            self._chip(linea, "ACTIVA", t.CYAN_DARK, CYAN).pack(side="left", padx=(8, 0))
        else:
            self._chip(linea, "PAUSADA", PANEL_LIGHT, MUTED).pack(side="left", padx=(8, 0))
        ajustable(tk.Label(textos, text=self._cuando(routine), bg=PANEL, fg=CYAN if activa else MUTED,
                           font=("Segoe UI", 10), justify="left", anchor="w"), 100).pack(fill="x", pady=(3, 0))

        pasos = tk.Frame(tarjeta, bg=PANEL)
        pasos.pack(fill="x", pady=(12, 0))
        textos_pasos = self._step_texts(routine)
        for n, texto in enumerate(textos_pasos, start=1):
            fila = tk.Frame(pasos, bg=PANEL_LIGHT, padx=10, pady=6, highlightthickness=1,
                            highlightbackground=t.BORDER)
            fila.pack(fill="x", pady=(0, 6))
            tk.Label(fila, text=str(n), bg=PANEL_LIGHT, fg=CYAN, font=("Consolas", 9, "bold"),
                     width=2, anchor="nw").pack(side="left", anchor="n")
            ajustable(tk.Label(fila, text=texto, bg=PANEL_LIGHT, fg=TEXT, font=("Segoe UI", 9), justify="left",
                               anchor="w"), 90).pack(side="left", fill="x")
        if not textos_pasos:
            aviso = tk.Frame(pasos, bg=PANEL_LIGHT, padx=10, pady=6, highlightthickness=1,
                             highlightbackground=ERROR)
            aviso.pack(fill="x", pady=(0, 6))
            ajustable(tk.Label(aviso, text="⚠  Sin pasos válidos: editala para agregar alguno.", bg=PANEL_LIGHT,
                               fg=ERROR, font=("Segoe UI", 9), justify="left", anchor="w"), 70).pack(fill="x")

        permiso = tk.Frame(tarjeta, bg=PANEL)
        permiso.pack(fill="x", pady=(6, 0))
        try:
            bloqueantes = jarvis_routines.acciones_con_permiso((step.tool for step in routine.steps), self.registry)
        except ToolError:
            bloqueantes = ["herramienta desconocida"]
        automatica = routine.permission == jarvis_routines.PERMISO_AUTOMATICA
        if jarvis_routines.requiere_cuenta_atras(routine.steps):
            self._chip(permiso, "⏳ CUENTA ATRÁS", PANEL_LIGHT, MUTED).pack(side="left")
        elif bloqueantes:
            self._chip(permiso, "SIEMPRE PREGUNTA", t.CYAN_DARK, CYAN).pack(side="left")
            ajustable(tk.Label(permiso, text="por " + ", ".join(bloqueantes).lower(), bg=PANEL, fg=MUTED,
                               font=("Segoe UI", 8), justify="left", anchor="w"), 200).pack(side="left",
                                                                                            padx=(8, 0))
        else:
            self._button(permiso, "⚡ AUTOMÁTICA" if automatica else "✋ PREGUNTA ANTES",
                         lambda: self._toggle_permission(routine, not automatica),
                         fg=VIOLET if automatica else CYAN).pack(side="left")

        tk.Frame(tarjeta, bg=t.BORDER, height=1).pack(fill="x", pady=(14, 12))
        acciones = tk.Frame(tarjeta, bg=PANEL)
        acciones.pack(fill="x")
        for col in range(3):
            acciones.grid_columnconfigure(col, weight=1, uniform="acc")
        self._button(acciones, "⏸ PAUSAR" if activa else "▶ REANUDAR", lambda: self._toggle_active(routine),
                     bg=t.CYAN_DARK).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self._button(acciones, "✎ EDITAR", lambda: self._edit_routine(routine), fg=TEXT).grid(
            row=0, column=1, sticky="ew", padx=4)
        self._button(acciones, "✕ ELIMINAR", lambda: self._delete(routine), fg=ERROR).grid(
            row=0, column=2, sticky="ew", padx=(4, 0))
        tarjeta._jarvis_ajustables = ajustables
        return tarjeta

    def _tarjeta_ejemplos(self) -> tk.Frame:
        caja = tk.Frame(self.rows, bg=PANEL, padx=18, pady=14, highlightthickness=1, highlightbackground=t.BORDER)
        tk.Label(caja, text="CREALAS TAMBIÉN POR VOZ", bg=PANEL, fg=MUTED,
                 font=("Consolas", 9, "bold")).pack(anchor="w", pady=(0, 10))
        zona = tk.Frame(caja, bg=PANEL)
        zona.pack(fill="x")
        self._etiquetas_voz = [tk.Label(zona, text=ejemplo, bg=PANEL_LIGHT, fg=VIOLET, font=("Segoe UI", 9),
                                        padx=10, pady=5, highlightthickness=1, highlightbackground=t.BORDER,
                                        justify="left") for ejemplo in EJEMPLOS_VOZ]
        self._zona_voz = zona
        return caja

    def _acomodar_etiquetas(self, ancho: int) -> None:
        """Las etiquetas de ejemplo de corrido; en una ventana angosta, una debajo de otra y en
        varias líneas."""
        import jarvis_ui_tele

        etiquetas = getattr(self, "_etiquetas_voz", [])
        if not etiquetas:
            return
        for etiqueta in etiquetas:
            etiqueta.configure(wraplength=max(200, ancho - 60))
        anchos = [e.winfo_reqwidth() for e in etiquetas]
        filas = jarvis_ui_tele.repartir(anchos, max(200, ancho - 40))
        y, x_fila, alto_fila = 0, {}, {}
        for etiqueta, fila in zip(etiquetas, filas):
            alto_fila[fila] = max(alto_fila.get(fila, 0), etiqueta.winfo_reqheight())
        tope = {}
        for fila in sorted(alto_fila):
            tope[fila] = y
            y += alto_fila[fila] + 8
        for etiqueta, fila, ancho_e in zip(etiquetas, filas, anchos):
            x = x_fila.get(fila, 0)
            etiqueta.place(x=x, y=tope[fila])
            x_fila[fila] = x + ancho_e + 8
        self._zona_voz.configure(height=y)

    # --- Acciones ---------------------------------------------------------------

    def _new_routine(self) -> None:
        RoutineForm(self.window, self.registry, self.refresh, incrustado=self.incrustado)

    def _edit_routine(self, routine: jarvis_routines.RoutineInfo) -> None:
        RoutineForm(self.window, self.registry, self.refresh, routine, incrustado=self.incrustado)

    def _toggle_permission(self, routine: jarvis_routines.RoutineInfo, automatica: bool) -> None:
        if automatica and not messagebox.askyesno(
            "Ejecutar sin preguntar",
            f"«{routine.name}» se ejecutará automáticamente, sin pedir aprobación, "
            "cada vez que se active.\n\n¿Quieres continuar?",
            parent=self.window,
        ):
            return
        try:
            jarvis_routines.cambiar_permiso_rutina(routine.identifier, automatica, self.registry)
        except (ValueError, ToolError) as error:
            messagebox.showerror("No se pudo cambiar el permiso", str(error), parent=self.window)
        self.refresh()

    def _toggle_active(self, routine: jarvis_routines.RoutineInfo) -> None:
        jarvis_routines.cambiar_estado_rutina(routine.identifier, not routine.active)
        self.refresh()

    def _delete(self, routine: jarvis_routines.RoutineInfo) -> None:
        if messagebox.askyesno(
            "Eliminar rutina",
            f"¿Eliminar «{routine.name}»? Esta acción no se puede deshacer.",
            parent=self.window,
            icon="warning",
        ):
            jarvis_routines.eliminar_rutina(routine.identifier)
            self.refresh()


__all__ = ["RoutinesPanel"]
