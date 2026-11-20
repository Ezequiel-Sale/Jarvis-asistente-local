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
import jarvis_tv
from jarvis_theme import BG, CYAN, ERROR, HOVER, MUTED, PANEL, PANEL_LIGHT, TEXT
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
    "window_minimize": "minimizar", "window_maximize": "maximizar", "window_restore": "restaurar",
    "window_focus": "traer al frente",
    "back": "página anterior", "forward": "página siguiente", "reload": "recargar la página (F5)",
    "date": "fecha", "time": "hora", "both": "fecha y hora",
    "today": "hoy", "tomorrow": "mañana", "memory": "memoria", "cpu": "procesador",
    # Tele (tv_control). «back» ya es «página anterior» (navegador); en la tele es lo mismo: atrás.
    "power_on": "encender", "power_off": "apagar", "volume_up": "subir volumen",
    "volume_down": "bajar volumen", "set_volume": "fijar volumen", "play_pause": "reproducir/pausar",
    "rewind": "retroceder", "home": "inicio", "up": "arriba", "down": "abajo", "left": "izquierda",
    "right": "derecha", "ok": "OK", "settings": "ajustes", "input": "menú de entradas",
    "channel_up": "canal siguiente", "channel_down": "canal anterior",
    "hdmi1": "HDMI 1", "hdmi2": "HDMI 2", "hdmi3": "HDMI 3", "hdmi4": "HDMI 4",
}
# Las de la tele, aparte: «back», «forward», «sleep»… ya significan otra cosa en otras acciones.
ETIQUETAS_POR_HERRAMIENTA = {
    "tv_control": {
        "power_on": "Encender", "power_off": "Apagar",
        "hdmi1": "Entrada HDMI 1", "hdmi2": "Entrada HDMI 2", "hdmi3": "Entrada HDMI 3", "hdmi4": "Entrada HDMI 4",
        "set_volume": "Fijar volumen (0-100)", "volume_up": "Subir volumen", "volume_down": "Bajar volumen",
        "mute": "Silenciar / quitar silencio", "play_pause": "Reproducir / pausar", "play": "Reproducir",
        "pause": "Pausar", "stop": "Detener", "next": "Siguiente", "previous": "Anterior",
        "forward": "Adelantar", "rewind": "Retroceder", "home": "Ir al inicio", "back": "Atrás",
        "settings": "Abrir el panel (configuración)", "input": "Abrir el panel (entradas)",
        "up": "Flecha arriba", "down": "Flecha abajo", "left": "Flecha izquierda", "right": "Flecha derecha",
        "ok": "OK", "channel_up": "Canal siguiente", "channel_down": "Canal anterior",
    },
    "tv_open_app": {clave: nombre for clave, (nombre, _enlace) in jarvis_tv.APPS.items()},
    "move_window": {
        "left": "Mitad izquierda", "right": "Mitad derecha", "top": "Mitad de arriba", "bottom": "Mitad de abajo",
        "top_left": "Arriba a la izquierda", "top_right": "Arriba a la derecha",
        "bottom_left": "Abajo a la izquierda", "bottom_right": "Abajo a la derecha",
        "center": "En el centro", "full": "Pantalla completa",
    },
}
# Sugerencias que dependen de la app en marcha (las apps aprendidas de la tele):
# herramienta → función que devuelve {etiqueta: valor}. Las registra JarvisApp.
SUGERENCIAS: dict[str, Callable[[], dict[str, str]]] = {}
ETIQUETAS_CAMPO = {
    "percentage": "Porcentaje", "device": "Salida", "enabled": "Activado", "url": "Dirección web",
    "name": "Nombre", "action": "Acción", "query": "Búsqueda", "play_index": "Resultado nº",
    "monitor": "Monitor", "second_monitor": "Segundo monitor", "app": "Aplicación",
    "mute": "Silenciar", "target": "Programa o archivo", "kind": "Tipo", "text": "Texto",
    "delay_seconds": "Dentro de (segundos)", "at": "Fecha y hora (AAAA-MM-DD HH:MM)",
    "what": "Qué", "city": "Ciudad", "day": "Día", "match": "Título contiene",
    "steps": "Pasos", "level": "Volumen (0-100)",
    "position": "Posición en el monitor", "screen_name": "Pantalla por nombre (p. ej. tv)",
    "seconds": "Segundos", "names": "Programas (separados por comas)", "path": "Archivo o carpeta",
    "program": "Programa (ventana)", "button": "Texto del botón", "wait_seconds": "Esperar hasta (segundos)",
}
DIAS = ("L", "M", "X", "J", "V", "S", "D")
DISPARADORES = (
    ("frase", "Cuando diga una frase (comando personalizado)"),
    ("time", "A una hora"),
    ("headphones_connected", "Al conectar los auriculares"),
    ("headphones_disconnected", "Al desconectar los auriculares"),
    ("game_started", "Al abrir un juego (modo juego)"),
    ("game_stopped", "Al cerrar el juego"),
    ("program_started", "Al abrir un programa"),
    ("program_stopped", "Al cerrar un programa"),
    ("usb_connected", "Al conectar un pendrive o disco USB"),
    ("usb_disconnected", "Al desconectar un pendrive o disco USB"),
    ("internet_lost", "Al quedarme sin internet"),
    ("internet_restored", "Al volver internet"),
)
_DB_A_FORM = {
    jarvis_routines.DISPARADOR_FRASE: "frase",
    jarvis_routines.DISPARADOR_HORA: "time",
    jarvis_routines.DISPARADOR_AURICULARES_CONECTADOS: "headphones_connected",
    jarvis_routines.DISPARADOR_AURICULARES_DESCONECTADOS: "headphones_disconnected",
    jarvis_routines.DISPARADOR_JUEGO_ABIERTO: "game_started",
    jarvis_routines.DISPARADOR_JUEGO_CERRADO: "game_stopped",
    jarvis_routines.DISPARADOR_PROGRAMA_ABIERTO: "program_started",
    jarvis_routines.DISPARADOR_PROGRAMA_CERRADO: "program_stopped",
    jarvis_routines.DISPARADOR_USB_CONECTADO: "usb_connected",
    jarvis_routines.DISPARADOR_USB_DESCONECTADO: "usb_disconnected",
    jarvis_routines.DISPARADOR_SIN_INTERNET: "internet_lost",
    jarvis_routines.DISPARADOR_CON_INTERNET: "internet_restored",
}


# Selector de acciones: categoría, nombre claro y para qué sirve. Antes era una lista alfabética
# de 35 nombres (y dos se llamaban igual, «Navegar en Brave»: la segunda no se podía elegir).
CATEGORIAS = (
    "Básicas", "Tele", "Monitores y pantalla", "Audio", "Música y vídeo", "Programas, juegos y ventanas",
    "Navegador", "Equipo y energía", "Avisos e información", "Jarvis", "Otras",
)
CATALOGO: dict[str, tuple[str, str, str]] = {
    "routine_wait": ("Básicas", "Esperar", "Pausa unos segundos antes del paso siguiente (p. ej. a que cargue algo)."),
    "say_text": ("Básicas", "Decir un mensaje", "Jarvis lo dice en voz alta y lo escribe en el chat y en el celular."),
    "notify_phone": ("Básicas", "Avisar al celular", "Una notificación en la app del celular, sin hablar en la PC."),
    "close_program": ("Programas, juegos y ventanas", "Cerrar un programa",
                      "Por su nombre, varios separados por comas (Discord, Spotify…). Nunca Jarvis ni el sistema."),
    "open_file": ("Programas, juegos y ventanas", "Abrir un archivo", "Un documento, foto o carpeta por su ruta."),
    "set_compact_mode": ("Jarvis", "Modo compacto", "Pasa Jarvis a la ventana pequeña o la vuelve normal."),
    "morning_briefing": ("Avisos e información", "Resumen del día", "Clima, avisos, rutinas y el estado del equipo."),
    "list_reminders": ("Avisos e información", "Decir los avisos pendientes", "Temporizadores, recordatorios y alarmas."),
    "tv_status": ("Tele", "Decir el estado de la tele", "Si está encendida, qué app tiene y el volumen."),
    "tv_control": ("Tele", "Controlar la tele",
                   "Encender, apagar, entrada HDMI, volumen, reproducción, inicio, atrás…"),
    "tv_open_app": ("Tele", "Abrir una app en la tele", "Netflix, YouTube o cualquier app instalada en la tele."),
    "configure_display": ("Monitores y pantalla", "Configurar monitores",
                          "Activar, desactivar o duplicar un monitor, o elegir el principal."),
    "fix_display_scaling": ("Monitores y pantalla", "Arreglar el escalado",
                            "Vuelve a poner bien el tamaño de las ventanas tras cambiar monitores."),
    "move_window": ("Monitores y pantalla", "Mover o acomodar una ventana",
                    "Lleva las ventanas de un programa a un monitor y, si quieres, a una parte: mitad "
                    "izquierda o derecha, arriba, abajo, una esquina, el centro o pantalla completa."),
    "set_system_volume": ("Audio", "Volumen del sistema", "Pone el volumen general en un porcentaje."),
    "set_app_volume": ("Audio", "Volumen de una aplicación", "Volumen o silencio de un programa (Spotify, Brave…)."),
    "set_output_device": ("Audio", "Salida de audio", "Cambia entre auriculares y parlantes."),
    "set_jarvis_audio": ("Audio", "Audio de Jarvis", "Activa o silencia la voz de Jarvis."),
    "media_control": ("Música y vídeo", "Reproducción", "Play, pausa, siguiente, anterior o detener."),
    "video_fullscreen": ("Música y vídeo", "Pantalla completa del vídeo", "Pone o quita la pantalla completa."),
    "video_theater": ("Música y vídeo", "Modo cine del vídeo", "Modo cine o teatro de YouTube y Twitch."),
    "search_youtube": ("Música y vídeo", "Buscar en YouTube", "Busca y, si quieres, reproduce un resultado."),
    "mute_browser_tab": ("Música y vídeo", "Silenciar una pestaña", "Silencia o activa el sonido de una pestaña."),
    "get_now_playing": ("Música y vídeo", "Decir qué está sonando", "Dice la canción o el vídeo actual."),
    "open_app": ("Programas, juegos y ventanas", "Abrir una aplicación", "Por su nombre (WhatsApp, Discord…)."),
    "open_program": ("Programas, juegos y ventanas", "Abrir un programa o archivo",
                     "Por su ruta exacta (con «Examinar…»), p. ej. un launcher."),
    "launch_game": ("Programas, juegos y ventanas", "Abrir un juego", "Por su nombre, de tus carpetas de juegos."),
    "press_window_button": ("Programas, juegos y ventanas", "Pulsar un botón en una ventana",
                            "P. ej. «Jugar» en un launcher; espera a que el botón aparezca."),
    "window_control": ("Programas, juegos y ventanas", "Minimizar, maximizar o traer al frente",
                       "Las ventanas de un programa (o todas)."),
    "manage_window": ("Programas, juegos y ventanas", "Ventana de Jarvis", "Minimizar o maximizar Jarvis."),
    "open_new_browser_tab": ("Navegador", "Abrir una pestaña en Brave", "Con una dirección web."),
    "navigate_browser": ("Navegador", "Controlar la ventana de Brave", "Minimizar o maximizar Brave."),
    "browser_history": ("Navegador", "Atrás, adelante o recargar", "En la pestaña activa de Brave."),
    "browser_tab": ("Navegador", "Cambiar de pestaña", "Siguiente, anterior, por número o por su título."),
    "power_control": ("Equipo y energía", "Apagar, reiniciar o suspender",
                      "Con cuenta atrás; puedes indicar «dentro de» cuánto."),
    "cancel_power_action": ("Equipo y energía", "Cancelar apagado o reinicio", "Anula uno programado."),
    "lock_computer": ("Equipo y energía", "Bloquear el equipo", "Bloquea la sesión de Windows."),
    "apply_gpu_profile": ("Equipo y energía", "Aplicar el undervolt de la GPU", "Vuelve a aplicar tu perfil."),
    "get_system_status": ("Equipo y energía", "Decir el estado del equipo", "Temperaturas, carga, memoria y discos."),
    "get_headset_battery": ("Equipo y energía", "Batería de los auriculares", "Dice la batería del G533."),
    "get_jarvis_diagnostics": ("Equipo y energía", "Diagnóstico de Jarvis", "Revisa voz, modelos y servicios."),
    "set_reminder": ("Avisos e información", "Programar un aviso", "Temporizador, recordatorio o alarma."),
    "get_current_datetime": ("Avisos e información", "Decir la fecha y la hora", "Fecha, hora o las dos."),
    "set_theme": ("Jarvis", "Cambiar el tema", "Colores de Jarvis."),
    "set_voice": ("Jarvis", "Cambiar la voz", "La voz con que habla Jarvis."),
    "windows_setting": ("Equipo y energía", "Ajuste de Windows", "Luz nocturna, modo oscuro, Wi-Fi o Bluetooth."),
    "open_settings": ("Equipo y energía", "Abrir Configuración", "Una página de Configuración de Windows."),
    "pc_doctor": ("Equipo y energía", "Revisar la salud de la PC", "Caídas, discos o errores recientes."),
    "replay_recording": ("Programas, juegos y ventanas", "Repetir una tarea grabada",
                         "Lo que le mostraste con «mirá cómo lo hago», por su nombre."),
}


def catalogo_acciones(registry: ToolRegistry) -> list[tuple[str, str, str, str]]:
    """(categoría, título, descripción, nombre) de las acciones de rutinas, en orden de categoría
    y título. Las que no están en el catálogo van a «Otras» con su nombre de siempre."""
    filas = []
    for definicion in registry.definitions():
        if definicion.name not in jarvis_routines._ROUTINE_ALLOWED_TOOLS:
            continue
        categoria, titulo, descripcion = CATALOGO.get(definicion.name, ("Otras", definicion.display_name, ""))
        filas.append((categoria, titulo, descripcion, definicion.name))
    orden = {c: i for i, c in enumerate(CATEGORIAS)}
    return sorted(filas, key=lambda f: (orden.get(f[0], len(orden)), f[1].casefold()))


def _sin_acentos(texto: str) -> str:
    import unicodedata

    texto = unicodedata.normalize("NFKD", texto.casefold())
    return "".join(c for c in texto if not unicodedata.combining(c))


def filtrar_acciones(filas: list[tuple[str, str, str, str]], busqueda: str,
                     registry: ToolRegistry | None = None) -> list[tuple[str, str, str, str]]:
    """Las que contienen todas las palabras buscadas (en categoría, título, descripción o el
    nombre de siempre), sin importar acentos ni mayúsculas: «hdmi», «volumen tele», «jugar»."""
    palabras = _sin_acentos(busqueda).split()
    if not palabras:
        return list(filas)
    resultado = []
    for fila in filas:
        extra = registry.get(fila[3]).display_name if registry is not None else ""
        texto = _sin_acentos(" ".join((*fila[:3], extra)))
        if all(p in texto for p in palabras):
            resultado.append(fila)
    return resultado


def herramientas_disponibles(registry: ToolRegistry) -> list[tuple[str, str]]:
    """(etiqueta, nombre) de las acciones que se pueden usar en rutinas."""
    disponibles = []
    for definicion in registry.definitions():
        if definicion.name in jarvis_routines._ROUTINE_ALLOWED_TOOLS:
            disponibles.append((definicion.display_name, definicion.name))
    return sorted(disponibles, key=lambda par: par[0].casefold())


def _sugerencias(herramienta: str) -> dict[str, str]:
    funcion = SUGERENCIAS.get(herramienta)
    if funcion is None:
        return {}
    try:
        return dict(funcion())
    except Exception:
        return {}


def etiqueta_valor(herramienta: str, valor: object) -> str:
    propias = ETIQUETAS_POR_HERRAMIENTA.get(herramienta, {})
    if str(valor) not in propias:
        propias = {v: e for e, v in _sugerencias(herramienta).items()} or propias
    return propias.get(str(valor)) or ETIQUETAS_VALOR.get(str(valor), str(valor))


def describir_paso(registry: ToolRegistry, paso: dict) -> str:
    try:
        etiqueta = registry.get(paso["tool"]).display_name
    except ToolError:
        etiqueta = paso["tool"]
    etiqueta = CATALOGO.get(paso["tool"], ("", etiqueta))[1]  # el mismo nombre que en el selector
    detalles = ", ".join(etiqueta_valor(paso["tool"], valor) for valor in paso["arguments"].values())
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
        *,
        incrustado: bool = False,
        juego: str | None = None,
        pasos: list[dict] | None = None,
        al_guardar: Callable[[list[dict]], None] | None = None,
    ) -> None:
        """``incrustado``: el formulario cubre ``parent`` (la sección Rutinas de la
        ventana principal) en vez de abrir otra ventana; al cerrarse, vuelve la lista.

        ``juego``: solo las acciones que se hacen al abrir ese juego (página 🎮 JUEGOS): sin
        nombre, disparador ni permiso; al guardar se llama ``al_guardar(pasos validados)``."""
        self.juego = juego
        self.al_guardar = al_guardar
        self.registry = registry
        self.on_saved = on_saved
        self.routine = routine
        self.pasos: list[dict] = []
        self.campos: dict[str, tk.Variable] = {}

        if incrustado:
            self.window = tk.Frame(parent, bg=BG)
            self.window.place(relx=0, rely=0, relwidth=1, relheight=1)
        else:
            self.window = tk.Toplevel(parent)
            self.window.title(f"Acciones al abrir {juego}" if juego else
                              "Editar rutina" if routine else "Nueva rutina o comando")
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
        if incrustado:
            # La rueda la gestiona la ventana principal (JarvisUIMixin._on_mousewheel).
            self._contenedor.jarvis_scroll = lienzo
        else:
            self.window.bind(
                "<MouseWheel>", lambda e: lienzo.yview_scroll(int(-e.delta / 120), "units")
            )

        tk.Label(
            cuerpo, text=(f"ACCIONES AL ABRIR «{juego.upper()}»" if juego else
                          "EDITAR RUTINA" if routine else "NUEVA RUTINA O COMANDO"),
            bg=BG, fg=CYAN, font=("Segoe UI", 15, "bold"), wraplength=700, justify="left",
        ).pack(anchor="w")
        if juego:
            tk.Label(cuerpo, text="Se hacen en orden después de abrir el juego, por ejemplo: Esperar 10 s → "
                     "Pulsar un botón en una ventana («Jugar»). Sin acciones, el juego se abre como siempre.",
                     bg=BG, fg=MUTED, font=("Segoe UI", 9), wraplength=700, justify="left").pack(anchor="w", pady=(4, 0))
        # En modo juego, nombre, disparador y permiso se arman igual pero no se muestran.
        visible = cuerpo
        cuerpo = cuerpo if not juego else tk.Frame(visible, bg=BG)

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
        self.etiqueta_frase = tk.Label(
            self.marco_frase, text="Frases que lo activan (separadas por comas):",
            bg=PANEL, fg=MUTED, font=("Segoe UI", 9),
        )
        self.etiqueta_frase.pack(anchor="w")
        self._entrada(self.marco_frase, self.frases, 60).pack(anchor="w", fill="x")
        tk.Label(
            self.marco_frase,
            text="Ej.: modo juego, empieza a jugar. Funciona con «Hey Jarvis…», F9 o escribiéndola.",
            bg=PANEL, fg=MUTED, font=("Segoe UI", 8),
        ).pack(anchor="w")

        self.marco_hora = tk.Frame(cuerpo, bg=PANEL, padx=12, pady=8)
        # «HH:MM:SS» (lo programado al segundo) se edita como HH:MM.
        hora, minuto = (routine.time.split(":")[:2] if routine and routine.time else ("08", "00"))
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
        oculto, cuerpo = cuerpo, visible
        self._titulo(cuerpo, "ACCIONES (en orden)")
        lista = tk.Frame(cuerpo, bg=BG)
        lista.pack(fill="x")
        self.lista = tk.Listbox(
            lista, height=6, bg=PANEL, fg=TEXT, selectbackground=HOVER,
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
        tk.Label(nuevo, text="Añadir acción: busca o elige de la lista", bg=PANEL, fg=MUTED).pack(anchor="w")
        self.herramientas = herramientas_disponibles(registry)
        self.acciones = catalogo_acciones(registry)
        if juego:
            import jarvis_acciones_juego

            permitidas = jarvis_acciones_juego.herramientas_permitidas()
            self.acciones = [f for f in self.acciones if f[3] in permitidas]
        self.herramienta = tk.StringVar()  # título (o nombre de siempre) de la acción elegida
        self.busqueda = tk.StringVar()
        fila_buscar = tk.Frame(nuevo, bg=PANEL)
        fila_buscar.pack(fill="x", pady=(4, 6))
        tk.Label(fila_buscar, text="🔎", bg=PANEL, fg=MUTED, font=("Segoe UI Emoji", 10)).pack(side="left")
        self.campo_buscar = self._entrada(fila_buscar, self.busqueda, 40)
        self.campo_buscar.pack(side="left", fill="x", expand=True, padx=(6, 0), ipady=3)
        self.busqueda.trace_add("write", lambda *_: self._llenar_acciones())
        self.campo_buscar.bind("<Down>", lambda _e: self._enfocar_primera())
        self.campo_buscar.bind("<Return>", lambda _e: self._enfocar_primera())
        estilo = ttk.Style(self.window)
        estilo.configure("Rutina.Treeview", background=PANEL_LIGHT, fieldbackground=PANEL_LIGHT, foreground=TEXT,
                         rowheight=24, borderwidth=0, font=("Segoe UI", 10))
        estilo.map("Rutina.Treeview", background=[("selected", HOVER)], foreground=[("selected", CYAN)])
        marco_arbol = tk.Frame(nuevo, bg=PANEL)
        marco_arbol.pack(fill="x")
        self.arbol = ttk.Treeview(marco_arbol, style="Rutina.Treeview", show="tree", height=9,
                                  selectmode="browse")
        barra_arbol = ttk.Scrollbar(marco_arbol, orient="vertical", command=self.arbol.yview)
        self.arbol.configure(yscrollcommand=barra_arbol.set)
        self.arbol.pack(side="left", fill="x", expand=True)
        barra_arbol.pack(side="left", fill="y")
        self.arbol.tag_configure("categoria", foreground=CYAN, font=("Consolas", 9, "bold"))
        self.arbol.bind("<<TreeviewSelect>>", lambda _e: self._elegir_de_arbol())
        self.descripcion = tk.Label(nuevo, text="", bg=PANEL, fg=MUTED, font=("Segoe UI", 9), justify="left",
                                    anchor="w", wraplength=600)
        self.descripcion.pack(fill="x", pady=(6, 0))
        self.descripcion.bind("<Configure>", lambda e: self.descripcion.configure(wraplength=max(200, e.width - 4)))
        self._llenar_acciones()
        self.marco_campos = tk.Frame(nuevo, bg=PANEL)
        self.marco_campos.pack(fill="x", pady=(6, 0))
        self._boton(nuevo, "+ AÑADIR ACCIÓN", self._agregar).pack(anchor="e", pady=(6, 0))

        # Permiso
        cuerpo = oculto
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
        self._boton(pie, "← VOLVER" if incrustado else "CANCELAR", self.window.destroy).pack(side="right")
        self._boton(pie, "GUARDAR", self._guardar, fg=BG, bg=CYAN).pack(side="right", padx=(0, 8))

        if routine:
            self.pasos = [{"tool": s.tool, "arguments": dict(s.arguments)} for s in routine.steps]
        if pasos:
            self.pasos = [dict(p) for p in pasos]
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

    def _boton(self, parent: tk.Misc, texto: str, comando, *, fg: str | None = None, bg: str | None = None) -> tk.Button:
        return tk.Button(
            parent, text=texto, command=comando, bg=bg or PANEL_LIGHT, fg=fg or CYAN, activebackground=HOVER,
            activeforeground=TEXT, relief="flat", padx=10, pady=5,
            font=("Consolas", 8, "bold"), cursor="hand2",
        )

    # --- Comportamiento ---------------------------------------------------------

    def _actualizar_cuando(self) -> None:
        self.marco_frase.pack_forget()
        self.marco_hora.pack_forget()
        if self.disparador.get() in ("frase", "program_started", "program_stopped"):
            # En los de programa, el mismo campo guarda el nombre del programa (2026-11-19).
            self.etiqueta_frase.configure(
                text="Frases que lo activan (separadas por comas):" if self.disparador.get() == "frase"
                else "Programa (su nombre, p. ej. Spotify, Discord, chrome):")
            self.marco_frase.pack(fill="x", pady=(6, 0), before=self.ancla_cuando)
        elif self.disparador.get() == "time":
            self.marco_hora.pack(fill="x", pady=(6, 0), before=self.ancla_cuando)
        self._actualizar_permiso()

    def _llenar_acciones(self) -> None:
        """Rehace el árbol con lo que coincide con la búsqueda (categorías abiertas)."""
        self.arbol.delete(*self.arbol.get_children())
        visibles = filtrar_acciones(self.acciones, self.busqueda.get(), self.registry)
        categorias: dict[str, str] = {}
        for categoria, titulo, _descripcion, nombre in visibles:
            if categoria not in categorias:
                categorias[categoria] = self.arbol.insert("", "end", text=categoria.upper(), open=True,
                                                          tags=("categoria",))
            self.arbol.insert(categorias[categoria], "end", iid=f"accion:{nombre}", text=f"   {titulo}")
        if not visibles:
            self.arbol.insert("", "end", text="Ninguna acción coincide con la búsqueda.", tags=("categoria",))

    def _enfocar_primera(self) -> str:
        primera = next((h for c in self.arbol.get_children() for h in self.arbol.get_children(c)), None)
        if primera:
            self.arbol.focus_set()
            self.arbol.selection_set(primera)
            self.arbol.focus(primera)
            self.arbol.see(primera)
        return "break"

    def _elegir_de_arbol(self) -> None:
        seleccion = self.arbol.selection()
        if not seleccion or not seleccion[0].startswith("accion:"):
            return
        nombre = seleccion[0].split(":", 1)[1]
        fila = next(f for f in self.acciones if f[3] == nombre)
        self._elegida = nombre
        self.herramienta.set(fila[1])
        self._mostrar_campos()

    def _nombre_herramienta(self) -> str | None:
        """La acción elegida en el árbol o, si se escribió el título o el nombre de siempre
        («Control de la tele»), la que se llama así."""
        etiqueta = self.herramienta.get()
        elegida = getattr(self, "_elegida", None)
        if elegida and CATALOGO.get(elegida, ("", etiqueta))[1] == etiqueta:
            return elegida
        por_titulo = next((f[3] for f in self.acciones if f[1] == etiqueta), None)
        return por_titulo or next((nombre for e, nombre in self.herramientas if e == etiqueta), None)

    def _mostrar_campos(self) -> None:
        for hijo in self.marco_campos.winfo_children():
            hijo.destroy()
        self.campos = {}
        nombre = self._nombre_herramienta()
        if nombre is None:
            self.descripcion.configure(text="")
            return
        definicion = self.registry.get(nombre)
        fila = next((f for f in self.acciones if f[3] == nombre), None)
        riesgo = ("se puede usar sin preguntar" if definicion.risk is ToolRisk.LOW
                  else "pide confirmación salvo en comandos por frase")
        self.descripcion.configure(
            text=f"{fila[1] if fila else definicion.display_name}: {fila[2] if fila and fila[2] else ''} "
                 f"({riesgo}).".replace(":  (", " ("), fg=TEXT)
        esquema = definicion.parameters
        requeridos = set(esquema.get("required", []))
        for clave, definicion in esquema.get("properties", {}).items():
            fila = tk.Frame(self.marco_campos, bg=PANEL)
            fila.pack(fill="x", pady=2)
            texto = ETIQUETAS_CAMPO.get(clave, clave) + ("" if clave in requeridos else " (opcional)")
            tk.Label(fila, text=texto, bg=PANEL, fg=TEXT, width=28, anchor="w").pack(side="left")
            if "enum" in definicion:
                opciones = [etiqueta_valor(nombre, v) for v in definicion["enum"]]
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
            elif "examples" in definicion:
                # Lista con sugerencias (p. ej. las apps de la tele) que admite escribir otro valor.
                sugerencias = (_sugerencias(nombre)
                               or {etiqueta_valor(nombre, v): v for v in definicion["examples"]})
                variable = tk.StringVar(value="")
                ttk.Combobox(fila, textvariable=variable, values=list(sugerencias), width=30).pack(side="left")
                variable._jarvis_sugerencias = sugerencias  # type: ignore[attr-defined]
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
            sugerencias = getattr(variable, "_jarvis_sugerencias", None)
            if sugerencias is not None:
                valor = sugerencias.get(valor, valor)
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

    def _guardar_juego(self) -> None:
        import jarvis_acciones_juego

        try:
            pasos = jarvis_acciones_juego.validar(self.pasos, self.registry)
            if self.al_guardar is not None:
                self.al_guardar(pasos)
        except (ValueError, ToolError) as error:
            messagebox.showerror("No se pudo guardar", str(error), parent=self.window)
            return
        self.window.destroy()
        self.on_saved()

    def _guardar(self) -> None:
        if self.juego:
            self._guardar_juego()
            return
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
