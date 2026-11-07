"""Formulario para crear o editar rutinas y comandos personalizados sin dictarlos.

- Cuándo: al decir una frase propia («modo juego»), a una hora y días (opcional
  «solo una vez»), o al conectar/desconectar los auriculares.
- Qué: lista ordenada de acciones elegidas entre las herramientas permitidas en
  rutinas; los campos de cada acción se generan a partir de su esquema
  (lista desplegable para opciones, número, casilla o texto; «Examinar…» para
  elegir el programa en «Abrir programa»).
- Permiso: «sin preguntar» solo si todas las acciones son de riesgo bajo; las
  que apagan/reinician/suspenden avisan con cuenta atrás.

Se ejecuta en el hilo de Tk; guarda con ``jarvis_routines.crear_rutina`` o
``actualizar_rutina``, que validan todo de nuevo.
"""

import json
import tkinter as tk
from collections.abc import Callable
from tkinter import filedialog, messagebox, ttk

import jarvis_routines
from jarvis_theme import BG, CYAN, ERROR, MUTED, PANEL, PANEL_LIGHT, TEXT
from jarvis_tool_registry import ToolError, ToolRegistry, ToolRisk

ETIQUETAS_VALOR = {
    "headphones": "auriculares", "speakers": "parlantes",
    "shutdown": "apagar", "restart": "reiniciar", "sleep": "suspender",
    "play": "reanudar", "pause": "pausar", "toggle": "reproducir/pausar",
    "next": "siguiente", "previous": "anterior", "stop": "detener",
    "timer": "temporizador", "reminder": "recordatorio", "alarm": "alarma",
    "disable": "desactivar", "extend": "extender", "duplicate": "duplicar",
    "set_primary": "hacer principal",
    "brave_minimize": "minimizar Brave", "brave_maximize": "maximizar Brave",
    "minimize": "minimizar Jarvis", "maximize": "maximizar Jarvis",
    "date": "fecha", "time": "hora", "both": "fecha y hora",
    "today": "hoy", "tomorrow": "mañana", "memory": "memoria", "cpu": "procesador",
}
ETIQUETAS_CAMPO = {
    "percentage": "Porcentaje", "device": "Salida", "enabled": "Activado", "url": "Dirección web",
    "name": "Nombre", "action": "Acción", "query": "Búsqueda", "play_index": "Resultado nº",
    "monitor": "Monitor", "second_monitor": "Segundo monitor", "app": "Aplicación",
    "mute": "Silenciar", "target": "Programa o archivo", "kind": "Tipo", "text": "Texto",
    "delay_seconds": "Dentro de (segundos)", "at": "Fecha y hora (AAAA-MM-DD HH:MM)",
    "what": "Qué", "city": "Ciudad", "day": "Día", "match": "Título contiene",
}
DIAS = ("L", "M", "X", "J", "V", "S", "D")
DISPARADORES = (
    ("frase", "Cuando diga una frase (comando personalizado)"),
    ("time", "A una hora"),
    ("headphones_connected", "Al conectar los auriculares"),
    ("headphones_disconnected", "Al desconectar los auriculares"),
)
_DB_A_FORM = {
    jarvis_routines.DISPARADOR_FRASE: "frase",
    jarvis_routines.DISPARADOR_HORA: "time",
    jarvis_routines.DISPARADOR_AURICULARES_CONECTADOS: "headphones_connected",
    jarvis_routines.DISPARADOR_AURICULARES_DESCONECTADOS: "headphones_disconnected",
}


def herramientas_disponibles(registry: ToolRegistry) -> list[tuple[str, str]]:
    """(etiqueta, nombre) de las acciones que se pueden usar en rutinas."""
    disponibles = []
    for definicion in registry.definitions():
        if definicion.name in jarvis_routines._ROUTINE_ALLOWED_TOOLS:
            disponibles.append((definicion.display_name, definicion.name))
    return sorted(disponibles, key=lambda par: par[0].casefold())


def describir_paso(registry: ToolRegistry, paso: dict) -> str:
    try:
        etiqueta = registry.get(paso["tool"]).display_name
    except ToolError:
        etiqueta = paso["tool"]
    detalles = ", ".join(
        ETIQUETAS_VALOR.get(str(valor), str(valor)) for valor in paso["arguments"].values()
    )
    return f"{etiqueta} ({detalles})" if detalles else etiqueta


def argumentos_desde_campos(esquema: dict, valores: dict[str, object]) -> dict[str, object]:
    """Convierte lo escrito en el formulario al tipo del esquema; omite vacíos opcionales."""
    propiedades = esquema.get("properties", {})
    requeridos = set(esquema.get("required", []))
    argumentos: dict[str, object] = {}
    for clave, definicion in propiedades.items():
        valor = valores.get(clave)
        tipo = definicion.get("type")
        if isinstance(valor, str):
            valor = valor.strip()
        if valor in ("", None):
            if clave in requeridos:
                raise ValueError(f"Falta «{ETIQUETAS_CAMPO.get(clave, clave)}».")
            continue
        if tipo == "integer":
            try:
                valor = int(valor)
            except (TypeError, ValueError) as error:
                raise ValueError(f"«{ETIQUETAS_CAMPO.get(clave, clave)}» debe ser un número.") from error
        elif tipo == "boolean":
            valor = bool(valor)
        argumentos[clave] = valor
    return argumentos


class RoutineForm:
    def __init__(
        self,
        parent: tk.Misc,
        registry: ToolRegistry,
        on_saved: Callable[[], None],
        routine: "jarvis_routines.RoutineInfo | None" = None,
    ) -> None:
        self.registry = registry
        self.on_saved = on_saved
        self.routine = routine
        self.pasos: list[dict] = []
        self.campos: dict[str, tk.Variable] = {}

        self.window = tk.Toplevel(parent)
        self.window.title("Editar rutina" if routine else "Nueva rutina o comando")
        self.window.configure(bg=BG)
        self.window.geometry("800x820")
        self.window.minsize(680, 480)
        self.window.transient(parent)
        self.window.bind("<Escape>", lambda _e: self.window.destroy())

        # Contenido desplazable: con algunas acciones el formulario no cabe en
        # pantallas con escalado alto; los botones de guardar quedan siempre fijos.
        self._contenedor = tk.Frame(self.window, bg=BG)
        self._contenedor.pack(fill="both", expand=True)
        lienzo = tk.Canvas(self._contenedor, bg=BG, highlightthickness=0)
        barra = tk.Scrollbar(self._contenedor, orient="vertical", command=lienzo.yview)
        lienzo.configure(yscrollcommand=barra.set)
        barra.pack(side="right", fill="y")
        lienzo.pack(side="left", fill="both", expand=True)
        cuerpo = tk.Frame(lienzo, bg=BG, padx=22, pady=16)
        ventana_cuerpo = lienzo.create_window((0, 0), window=cuerpo, anchor="nw")
        cuerpo.bind("<Configure>", lambda _e: lienzo.configure(scrollregion=lienzo.bbox("all")))
        lienzo.bind("<Configure>", lambda e: lienzo.itemconfigure(ventana_cuerpo, width=e.width))
        self.window.bind(
            "<MouseWheel>", lambda e: lienzo.yview_scroll(int(-e.delta / 120), "units")
        )

        tk.Label(
            cuerpo, text="EDITAR RUTINA" if routine else "NUEVA RUTINA O COMANDO",
            bg=BG, fg=CYAN, font=("Segoe UI", 15, "bold"),
        ).pack(anchor="w")

        # Nombre
        self.nombre = tk.StringVar(value=routine.name if routine else "")
        fila_nombre = tk.Frame(cuerpo, bg=BG)
        fila_nombre.pack(fill="x", pady=(10, 0))
        tk.Label(fila_nombre, text="Nombre", bg=BG, fg=TEXT, width=10, anchor="w").pack(side="left")
        # El campo debe ser hijo de su fila: si se crea en otro contenedor y se
        # coloca con «in_», Tk lo dibuja detrás de la fila y no se ve.
        self.campo_nombre = self._entrada(fila_nombre, self.nombre, 40)
        self.campo_nombre.pack(side="left", fill="x", expand=True)

        # Cuándo
        self._titulo(cuerpo, "¿CUÁNDO SE EJECUTA?")
        disparador = _DB_A_FORM.get(routine.trigger, "time") if routine else "frase"
        self.disparador = tk.StringVar(value=disparador)
        for valor, texto in DISPARADORES:
            tk.Radiobutton(
                cuerpo, text=texto, value=valor, variable=self.disparador,
                command=self._actualizar_cuando, bg=BG, fg=TEXT, selectcolor=PANEL,
                activebackground=BG, activeforeground=CYAN, font=("Segoe UI", 10),
                anchor="w",
            ).pack(anchor="w")

        self.marco_frase = tk.Frame(cuerpo, bg=PANEL, padx=12, pady=8)
        self.frases = tk.StringVar(value=", ".join(routine.phrases) if routine else "")
        tk.Label(
            self.marco_frase, text="Frases que lo activan (separadas por comas):",
            bg=PANEL, fg=MUTED, font=("Segoe UI", 9),
        ).pack(anchor="w")
        self._entrada(self.marco_frase, self.frases, 60).pack(anchor="w", fill="x")
        tk.Label(
            self.marco_frase,
            text="Ej.: modo juego, empieza a jugar. Funciona con «Hey Jarvis…», F9 o escribiéndola.",
            bg=PANEL, fg=MUTED, font=("Segoe UI", 8),
        ).pack(anchor="w")

        self.marco_hora = tk.Frame(cuerpo, bg=PANEL, padx=12, pady=8)
        hora, minuto = (routine.time.split(":") if routine and routine.time else ("08", "00"))
        self.hora = tk.StringVar(value=hora)
        self.minuto = tk.StringVar(value=minuto)
        fila_hora = tk.Frame(self.marco_hora, bg=PANEL)
        fila_hora.pack(anchor="w")
        tk.Label(fila_hora, text="Hora:", bg=PANEL, fg=TEXT).pack(side="left")
        tk.Spinbox(fila_hora, from_=0, to=23, width=3, format="%02.0f", textvariable=self.hora).pack(side="left", padx=4)
        tk.Label(fila_hora, text=":", bg=PANEL, fg=TEXT).pack(side="left")
        tk.Spinbox(fila_hora, from_=0, to=59, width=3, format="%02.0f", textvariable=self.minuto).pack(side="left", padx=4)
        dias_actuales = set(routine.days) if routine else set(range(1, 8))
        self.dias = [tk.BooleanVar(value=(i + 1) in dias_actuales) for i in range(7)]
        fila_dias = tk.Frame(self.marco_hora, bg=PANEL)
        fila_dias.pack(anchor="w", pady=(6, 0))
        tk.Label(fila_dias, text="Días:", bg=PANEL, fg=TEXT).pack(side="left")
        for letra, var in zip(DIAS, self.dias):
            tk.Checkbutton(
                fila_dias, text=letra, variable=var, bg=PANEL, fg=TEXT, selectcolor=PANEL_LIGHT,
                activebackground=PANEL,
            ).pack(side="left")
        self.una_vez = tk.BooleanVar(value=bool(routine and routine.once))
        tk.Checkbutton(
            self.marco_hora, text="Solo una vez (luego se pausa sola)", variable=self.una_vez,
            bg=PANEL, fg=TEXT, selectcolor=PANEL_LIGHT, activebackground=PANEL,
        ).pack(anchor="w", pady=(4, 0))
        self.ancla_cuando = tk.Frame(cuerpo, bg=BG)
        self.ancla_cuando.pack(fill="x")

        # Acciones
        self._titulo(cuerpo, "ACCIONES (en orden)")
        lista = tk.Frame(cuerpo, bg=BG)
        lista.pack(fill="x")
        self.lista = tk.Listbox(
            lista, height=6, bg=PANEL, fg=TEXT, selectbackground="#1D374B",
            highlightthickness=0, relief="flat", font=("Segoe UI", 10), activestyle="none",
        )
        self.lista.pack(side="left", fill="x", expand=True)
        botones_lista = tk.Frame(lista, bg=BG)
        botones_lista.pack(side="left", padx=(8, 0))
        for texto, accion in (("↑", -1), ("↓", 1)):
            self._boton(botones_lista, texto, lambda d=accion: self._mover(d)).pack(fill="x", pady=1)
        self._boton(botones_lista, "QUITAR", self._quitar, fg=ERROR).pack(fill="x", pady=1)

        nuevo = tk.Frame(cuerpo, bg=PANEL, padx=12, pady=10)
        nuevo.pack(fill="x", pady=(8, 0))
        tk.Label(nuevo, text="Añadir acción:", bg=PANEL, fg=MUTED).pack(anchor="w")
        self.herramientas = herramientas_disponibles(registry)
        self.herramienta = tk.StringVar()
        selector = ttk.Combobox(
            nuevo, textvariable=self.herramienta, state="readonly", width=45,
            values=[etiqueta for etiqueta, _ in self.herramientas],
        )
        selector.pack(anchor="w")
        selector.bind("<<ComboboxSelected>>", lambda _e: self._mostrar_campos())
        self.marco_campos = tk.Frame(nuevo, bg=PANEL)
        self.marco_campos.pack(fill="x", pady=(6, 0))
        self._boton(nuevo, "+ AÑADIR ACCIÓN", self._agregar).pack(anchor="e", pady=(6, 0))

        # Permiso
        self._titulo(cuerpo, "PERMISO")
        self.automatica = tk.BooleanVar(
            value=bool(routine and routine.permission == jarvis_routines.PERMISO_AUTOMATICA)
        )
        self.casilla_auto = tk.Checkbutton(
            cuerpo, text="Ejecutar sin preguntar (solo si todas las acciones son de riesgo bajo)",
            variable=self.automatica, bg=BG, fg=TEXT, selectcolor=PANEL, activebackground=BG,
        )
        self.casilla_auto.pack(anchor="w")
        self.nota_permiso = tk.Label(cuerpo, text="", bg=BG, fg=MUTED, font=("Segoe UI", 9), justify="left")
        self.nota_permiso.pack(anchor="w")

        # Guardar
        pie = tk.Frame(self.window, bg=BG, padx=22, pady=10)
        pie.pack(fill="x", side="bottom", before=self._contenedor)
        self._boton(pie, "CANCELAR", self.window.destroy).pack(side="right")
        self._boton(pie, "GUARDAR", self._guardar, fg=BG, bg=CYAN).pack(side="right", padx=(0, 8))

        if routine:
            self.pasos = [{"tool": s.tool, "arguments": dict(s.arguments)} for s in routine.steps]
        self._actualizar_lista()
        self._actualizar_cuando()
        self.window.lift()
        self.window.focus_force()

    # --- Widgets ---------------------------------------------------------------

    def _titulo(self, parent: tk.Misc, texto: str) -> None:
        tk.Label(parent, text=texto, bg=BG, fg=CYAN, font=("Consolas", 9, "bold")).pack(
            anchor="w", pady=(14, 4)
        )

    def _entrada(self, parent: tk.Misc, variable: tk.StringVar, ancho: int) -> tk.Entry:
        return tk.Entry(
            parent, textvariable=variable, width=ancho, bg=PANEL_LIGHT, fg=TEXT,
            insertbackground=TEXT, relief="flat", font=("Segoe UI", 10),
        )

    def _boton(self, parent: tk.Misc, texto: str, comando, *, fg: str = CYAN, bg: str = PANEL_LIGHT) -> tk.Button:
        return tk.Button(
            parent, text=texto, command=comando, bg=bg, fg=fg, activebackground="#1D374B",
            activeforeground=TEXT, relief="flat", padx=10, pady=5,
            font=("Consolas", 8, "bold"), cursor="hand2",
        )

    # --- Comportamiento ---------------------------------------------------------

    def _actualizar_cuando(self) -> None:
        self.marco_frase.pack_forget()
        self.marco_hora.pack_forget()
        if self.disparador.get() == "frase":
            self.marco_frase.pack(fill="x", pady=(6, 0), before=self.ancla_cuando)
        elif self.disparador.get() == "time":
            self.marco_hora.pack(fill="x", pady=(6, 0), before=self.ancla_cuando)
        self._actualizar_permiso()

    def _nombre_herramienta(self) -> str | None:
        etiqueta = self.herramienta.get()
        return next((nombre for e, nombre in self.herramientas if e == etiqueta), None)

    def _mostrar_campos(self) -> None:
        for hijo in self.marco_campos.winfo_children():
            hijo.destroy()
        self.campos = {}
        nombre = self._nombre_herramienta()
        if nombre is None:
            return
        esquema = self.registry.get(nombre).parameters
        requeridos = set(esquema.get("required", []))
        for clave, definicion in esquema.get("properties", {}).items():
            fila = tk.Frame(self.marco_campos, bg=PANEL)
            fila.pack(fill="x", pady=2)
            texto = ETIQUETAS_CAMPO.get(clave, clave) + ("" if clave in requeridos else " (opcional)")
            tk.Label(fila, text=texto, bg=PANEL, fg=TEXT, width=28, anchor="w").pack(side="left")
            if "enum" in definicion:
                opciones = [ETIQUETAS_VALOR.get(str(v), str(v)) for v in definicion["enum"]]
                variable = tk.StringVar(value=opciones[0] if clave in requeridos else "")
                ttk.Combobox(
                    fila, textvariable=variable, values=([""] if clave not in requeridos else []) + opciones,
                    state="readonly", width=30,
                ).pack(side="left")
                variable._jarvis_enum = dict(zip(opciones, definicion["enum"]))  # type: ignore[attr-defined]
            elif definicion.get("type") == "integer":
                variable = tk.StringVar(value="")
                tk.Spinbox(
                    fila, from_=definicion.get("minimum", 0), to=definicion.get("maximum", 100000),
                    textvariable=variable, width=10,
                ).pack(side="left")
                # El Spinbox se rellena solo con el mínimo: los opcionales quedan vacíos.
                variable.set(str(definicion.get("minimum", 0)) if clave in requeridos else "")
            elif definicion.get("type") == "boolean":
                variable = tk.BooleanVar(value=False)
                tk.Checkbutton(fila, variable=variable, bg=PANEL, selectcolor=PANEL_LIGHT,
                               activebackground=PANEL).pack(side="left")
            else:
                variable = tk.StringVar(value="")
                self._entrada(fila, variable, 36).pack(side="left", fill="x", expand=True)
                if nombre == "open_program" and clave == "target":
                    self._boton(fila, "EXAMINAR…", lambda v=variable: self._examinar(v)).pack(side="left", padx=(6, 0))
            self.campos[clave] = variable

    def _examinar(self, variable: tk.StringVar) -> None:
        ruta = filedialog.askopenfilename(
            parent=self.window, title="Elige el programa o archivo",
            filetypes=[("Programas y accesos directos", "*.exe *.lnk *.bat *.url"), ("Todos", "*.*")],
        )
        if ruta:
            variable.set(ruta.replace("/", "\\"))

    def _agregar(self) -> None:
        nombre = self._nombre_herramienta()
        if nombre is None:
            messagebox.showinfo("Añadir acción", "Elige primero una acción.", parent=self.window)
            return
        if len(self.pasos) >= jarvis_routines.MAX_PASOS:
            messagebox.showwarning("Añadir acción", f"Como máximo {jarvis_routines.MAX_PASOS} acciones.", parent=self.window)
            return
        valores: dict[str, object] = {}
        for clave, variable in self.campos.items():
            valor = variable.get()
            mapa = getattr(variable, "_jarvis_enum", None)
            if mapa is not None:
                valor = mapa.get(valor, "")
            valores[clave] = valor
        try:
            argumentos = argumentos_desde_campos(self.registry.get(nombre).parameters, valores)
            argumentos = self.registry.validate_arguments(nombre, argumentos)
        except (ToolError, ValueError) as error:
            messagebox.showerror("Acción no válida", str(error), parent=self.window)
            return
        self.pasos.append({"tool": nombre, "arguments": argumentos})
        self._actualizar_lista()

    def _quitar(self) -> None:
        seleccion = self.lista.curselection()
        if seleccion:
            del self.pasos[seleccion[0]]
            self._actualizar_lista()

    def _mover(self, direccion: int) -> None:
        seleccion = self.lista.curselection()
        if not seleccion:
            return
        i = seleccion[0]
        j = i + direccion
        if 0 <= j < len(self.pasos):
            self.pasos[i], self.pasos[j] = self.pasos[j], self.pasos[i]
            self._actualizar_lista()
            self.lista.selection_set(j)

    def _actualizar_lista(self) -> None:
        self.lista.delete(0, "end")
        for numero, paso in enumerate(self.pasos, start=1):
            self.lista.insert("end", f"{numero}. {describir_paso(self.registry, paso)}")
        self._actualizar_permiso()

    def _actualizar_permiso(self) -> None:
        if jarvis_routines.requiere_cuenta_atras(self.pasos):
            nota = "Apagar/reiniciar/suspender: al llegar el momento avisará con 60 s de cuenta atrás."
        elif self.disparador.get() == "frase":
            nota = "Un comando personalizado se ejecuta al decir la frase, sin pedir aprobación."
        else:
            nota = ""
        riesgosas = [
            p for p in self.pasos
            if self.registry.get(p["tool"]).risk is not ToolRisk.LOW
        ]
        habilitada = self.disparador.get() != "frase" and not riesgosas
        if not habilitada:
            self.automatica.set(False)
        self.casilla_auto.configure(state="normal" if habilitada else "disabled")
        self.nota_permiso.configure(text=nota)

    def _guardar(self) -> None:
        disparador = self.disparador.get()
        frases = [f.strip() for f in self.frases.get().split(",") if f.strip()]
        dias = ",".join(str(i + 1) for i, var in enumerate(self.dias) if var.get())
        try:
            hora = f"{int(self.hora.get()):02d}:{int(self.minuto.get()):02d}"
        except ValueError:
            messagebox.showerror("Hora no válida", "Revisa la hora y los minutos.", parent=self.window)
            return
        if disparador == "time" and not dias:
            messagebox.showerror("Faltan días", "Marca al menos un día.", parent=self.window)
            return
        if not self.pasos:
            messagebox.showerror("Sin acciones", "Añade al menos una acción.", parent=self.window)
            return
        nombre = self.nombre.get().strip()
        if not nombre and disparador == "frase" and frases:
            nombre = frases[0][:1].upper() + frases[0][1:]  # nombre por defecto: la frase
            self.nombre.set(nombre)
        datos = dict(
            trigger=disparador,
            automatic=self.automatica.get(),
            once=disparador == "time" and self.una_vez.get(),
            phrases=frases,
        )
        pasos_json = json.dumps(self.pasos, ensure_ascii=False)
        try:
            if self.routine is None:
                jarvis_routines.crear_rutina(
                    nombre, hora, dias or "1,2,3,4,5,6,7", pasos_json, self.registry, **datos
                )
            else:
                jarvis_routines.actualizar_rutina(
                    self.routine.identifier, nombre, hora, dias or "1,2,3,4,5,6,7",
                    pasos_json, self.registry, **datos,
                )
        except (ValueError, ToolError) as error:
            messagebox.showerror("No se pudo guardar", str(error), parent=self.window)
            return
        self.window.destroy()
        self.on_saved()
