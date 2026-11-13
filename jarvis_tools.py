from collections.abc import Mapping
from typing import Protocol

from jarvis_actions import Verificador
from jarvis_commands import normalizar_url_web
import jarvis_routines
import jarvis_health
import jarvis_headset
import jarvis_media
import jarvis_power
import jarvis_reminders
import jarvis_screen_actions
import jarvis_system
import jarvis_system_info
from jarvis_tool_registry import ActionRecorder, ToolRegistry, ToolRisk


class ToolEventHost(Protocol):
    def _tool_set_system_volume(self, percentage: int) -> str: ...
    def _tool_set_output_device(self, device: str) -> str: ...
    def _tool_set_jarvis_audio(self, enabled: bool) -> str: ...
    def _tool_set_theme(self, theme: str) -> str: ...
    def _tool_set_compact_mode(self, enabled: bool) -> str: ...
    def _tool_set_voice(self, voice: str) -> str: ...
    def _tool_apply_gpu_profile(self) -> str: ...
    def _tool_gpu_profile_status(self) -> str: ...
    def _tool_clean_disk(self, categories: list) -> str: ...
    def _tool_move_window(
        self, app: str, monitor: int | None = None, screen_name: str | None = None
    ) -> str: ...
    def _tool_open_new_browser_tab(self) -> str: ...
    def _tool_navigate_browser(self, url: str) -> str: ...
    def _tool_launch_game(self, name: str) -> str: ...
    def _tool_manage_window(self, action: str) -> str: ...
    def _tool_window_control(self, app: str, action: str) -> str: ...
    def _tool_browser_history(self, action: str) -> str: ...
    def _tool_search_youtube(self, query: str, play_index: int | None = None) -> str: ...
    def _tool_configure_display(
        self, action: str, monitor: int, second_monitor: int | None = None
    ) -> str: ...
    def _tool_fix_display_scaling(self, monitor: int | None = None) -> str: ...


def _trim_non_empty_text(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("debe ser texto.")
    value = value.strip()
    if not value:
        raise ValueError("no puede estar vacío.")
    return value


def crear_registro_herramientas(
    host: ToolEventHost,
    *,
    recorder: ActionRecorder | None = None,
    verificadores: Mapping[str, Verificador] | None = None,
) -> ToolRegistry:
    """Registra las herramientas de Jarvis.

    ``recorder`` recibe el resultado de cada ejecución (registro y métricas) y
    ``verificadores`` asocia comprobaciones posteriores por nombre de
    herramienta. Ambos son opcionales para poder usar el registro en tests sin
    tocar la base de datos ni el sistema.
    """
    registry = ToolRegistry(recorder=recorder)
    verificadores = dict(verificadores or {})

    registry.register(
        "set_system_volume",
        "Establece el volumen del sistema Windows entre 0 y 100.",
        {
            "type": "object",
            "properties": {
                "percentage": {"type": "integer", "minimum": 0, "maximum": 100}
            },
            "required": ["percentage"],
            "additionalProperties": False,
        },
        host._tool_set_system_volume,
        label="Ajustar volumen del sistema",
        timeout_seconds=10,
        verifier=verificadores.get("set_system_volume"),
    )
    registry.register(
        "set_output_device",
        "Cambia la salida de audio entre los altavoces y los auriculares G533.",
        {
            "type": "object",
            "properties": {
                "device": {
                    "type": "string",
                    "enum": ["speakers", "headphones"],
                }
            },
            "required": ["device"],
            "additionalProperties": False,
        },
        host._tool_set_output_device,
        label="Cambiar salida de audio",
        timeout_seconds=15,
        verifier=verificadores.get("set_output_device"),
    )
    registry.register(
        "set_jarvis_audio",
        "Activa o silencia la reproducción de audio de Jarvis.",
        {
            "type": "object",
            "properties": {"enabled": {"type": "boolean"}},
            "required": ["enabled"],
            "additionalProperties": False,
        },
        host._tool_set_jarvis_audio,
        label="Activar o silenciar el audio de Jarvis",
        timeout_seconds=20,
        verifier=verificadores.get("set_jarvis_audio"),
    )
    registry.register(
        "clean_disk",
        "Borra las categorías de limpieza elegidas por el usuario en la tarjeta de «libera espacio».",
        {
            "type": "object",
            "properties": {"categories": {"type": "array", "items": {"type": "string"}}},
            "required": ["categories"],
            "additionalProperties": False,
        },
        host._tool_clean_disk,
        risk=ToolRisk.HIGH,
        requires_confirmation=True,
        label="Liberar espacio",
        timeout_seconds=600,
        exposed=False,  # el modelo no puede borrar archivos: solo la tarjeta confirmada
    )
    import jarvis_voices

    registry.register(
        "set_voice",
        "Cambia la voz con la que habla Jarvis. " + "; ".join(
            f"{v.clave}: {v.descripcion}" for v in jarvis_voices.VOCES
        ) + ".",
        {
            "type": "object",
            "properties": {"voice": {"type": "string", "enum": [v.clave for v in jarvis_voices.VOCES]}},
            "required": ["voice"],
            "additionalProperties": False,
        },
        host._tool_set_voice,
        label="Cambiar la voz",
        timeout_seconds=20,
    )
    registry.register(
        "apply_gpu_profile",
        "Aplica a la GPU AMD el perfil de ajuste (undervolt) que el usuario exportó desde Adrenalin.",
        {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        host._tool_apply_gpu_profile,
        risk=ToolRisk.MEDIUM,
        label="Aplicar el undervolt de la GPU",
        timeout_seconds=20,
    )
    registry.register(
        "get_gpu_profile_status",
        "Solo lectura. Indica si la GPU tiene aplicado el perfil de undervolt y sus valores actuales.",
        {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        host._tool_gpu_profile_status,
        label="Consultar el undervolt de la GPU",
        timeout_seconds=15,
        record_result=False,
    )
    registry.register(
        "set_compact_mode",
        "Activa (enabled=true) o quita el modo compacto de Jarvis: una ventanita con el orbe siempre encima.",
        {
            "type": "object",
            "properties": {"enabled": {"type": "boolean"}},
            "required": ["enabled"],
            "additionalProperties": False,
        },
        host._tool_set_compact_mode,
        label="Cambiar a modo compacto",
        timeout_seconds=20,
    )
    registry.register(
        "set_theme",
        "Cambia el tema visual de Jarvis: cian (el original), mark (rojo y dorado) o claro.",
        {
            "type": "object",
            "properties": {"theme": {"type": "string", "enum": ["cian", "mark", "claro"]}},
            "required": ["theme"],
            "additionalProperties": False,
        },
        host._tool_set_theme,
        label="Cambiar el tema",
        timeout_seconds=20,
        verifier=verificadores.get("set_theme"),
    )
    registry.register(
        "open_new_browser_tab",
        "Abre una pestaña nueva en Brave.",
        {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        host._tool_open_new_browser_tab,
        label="Abrir pestaña en Brave",
        timeout_seconds=15,
        verifier=verificadores.get("open_new_browser_tab"),
    )
    registry.register(
        "navigate_browser",
        "Navega en Brave a una dirección web HTTP o HTTPS.",
        {
            "type": "object",
            "properties": {            "url": {"type": "string", "minLength": 1}},
            "required": ["url"],
            "additionalProperties": False,
        },
        host._tool_navigate_browser,
        risk=ToolRisk.MEDIUM,
        requires_confirmation=True,
        argument_validators={"url": normalizar_url_web},
        label="Navegar en Brave",
        timeout_seconds=15,
        verifier=verificadores.get("navigate_browser"),
    )
    registry.register(
        "launch_game",
        "Busca un juego instalado por nombre y solicita su lanzamiento.",
        {
            "type": "object",
            "properties": {"name": {"type": "string", "minLength": 1, "maxLength": 120}},
            "required": ["name"],
            "additionalProperties": False,
        },
        host._tool_launch_game,
        risk=ToolRisk.HIGH,
        requires_confirmation=True,
        argument_validators={"name": _trim_non_empty_text},
        label="Abrir juego",
        timeout_seconds=30,
        verifier=verificadores.get("launch_game"),
    )
    registry.register(
        "manage_window",
        "Minimiza o maximiza Jarvis o la ventana de Brave.",
        {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "brave_minimize",
                        "brave_maximize",
                        "minimize",
                        "maximize",
                    ],
                }
            },
            "required": ["action"],
            "additionalProperties": False,
        },
        host._tool_manage_window,
        risk=ToolRisk.HIGH,
        requires_confirmation=True,
        label="Gestionar ventana",
        timeout_seconds=20,
        verifier=verificadores.get("manage_window"),
    )
    registry.register(
        "search_youtube",
        "Busca una consulta en YouTube; opcionalmente abre un resultado ordinal.",
        {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1, "maxLength": 300},
                "play_index": {"type": "integer", "minimum": 1, "maximum": 100},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        host._tool_search_youtube,
        risk=ToolRisk.MEDIUM,
        requires_confirmation=True,
        argument_validators={"query": _trim_non_empty_text},
        label="Buscar en YouTube",
        timeout_seconds=30,
        verifier=verificadores.get("search_youtube"),
    )
    registry.register(
        "configure_display",
        (
            "Cambia la configuración de monitores de Windows: action=disable apaga el "
            "monitor indicado, extend lo activa como escritorio extendido y duplicate "
            "muestra la misma imagen en monitor y second_monitor, y set_primary lo hace monitor "
            "principal (solo 1 o 3). Los números son los de "
            "Windows (1, 2, 3). Tras el cambio el usuario debe confirmarlo o se revierte."
        ),
        {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["disable", "extend", "duplicate", "set_primary"]},
                "monitor": {"type": "integer", "minimum": 1, "maximum": 9},
                "second_monitor": {"type": "integer", "minimum": 1, "maximum": 9},
            },
            "required": ["action", "monitor"],
            "additionalProperties": False,
        },
        host._tool_configure_display,
        risk=ToolRisk.HIGH,
        requires_confirmation=True,
        label="Configurar monitores",
        timeout_seconds=60,
    )
    registry.register(
        "restore_display_config",
        (
            "Vuelve los monitores a como estaban antes de la rutina que los cambió "
            "(«desactiva el modo gaming 2K», «vuelve los monitores a la normalidad»). "
            "routine: nombre de esa rutina (opcional). Úsala en vez de desactivar monitores "
            "uno por uno; tras el cambio el usuario debe confirmarlo o se revierte."
        ),
        {
            "type": "object",
            "properties": {"routine": {"type": "string", "maxLength": 80}},
            "required": [],
            "additionalProperties": False,
        },
        host._tool_restore_display,
        risk=ToolRisk.HIGH,
        requires_confirmation=True,
        label="Volver a la configuración de monitores",
        timeout_seconds=90,
    )
    registry.register(
        "move_window",
        (
            "Mueve ventanas a otro monitor. app es el programa (brave, discord, spotify…), "
            "parte del título, «todo» para todas las ventanas o «esta ventana» para la activa. "
            "Indica monitor (número fijo 1, 2, 3) o screen_name (p. ej. «tv»)."
        ),
        {
            "type": "object",
            "properties": {
                "app": {"type": "string"},
                "monitor": {"type": "integer", "minimum": 1, "maximum": 9},
                "screen_name": {"type": "string"},
            },
            "required": ["app"],
            "additionalProperties": False,
        },
        host._tool_move_window,
        argument_validators={"app": _trim_non_empty_text},
        risk=ToolRisk.MEDIUM,
        label="Mover ventanas",
        timeout_seconds=20,
    )
    registry.register(
        "window_control",
        (
            "Minimiza, maximiza, restaura o trae al frente las ventanas de cualquier programa. "
            "app es el programa (discord, spotify, explorador…), parte del título, «todo» para "
            "todas las ventanas o «esta ventana» para la activa. Para Brave o Jarvis también sirve."
        ),
        {
            "type": "object",
            "properties": {
                "app": {"type": "string"},
                "action": {"type": "string",
                           "enum": ["window_minimize", "window_maximize", "window_restore", "window_focus"]},
            },
            "required": ["app", "action"],
            "additionalProperties": False,
        },
        host._tool_window_control,
        argument_validators={"app": _trim_non_empty_text},
        risk=ToolRisk.MEDIUM,
        label="Controlar ventanas",
        timeout_seconds=15,
    )
    registry.register(
        "browser_history",
        "En Brave: página anterior (back), página siguiente (forward) o recargar la página (reload, F5).",
        {
            "type": "object",
            "properties": {"action": {"type": "string", "enum": ["back", "forward", "reload"]}},
            "required": ["action"],
            "additionalProperties": False,
        },
        host._tool_browser_history,
        risk=ToolRisk.LOW,
        label="Navegar en Brave",
        timeout_seconds=10,
    )
    registry.register(
        "fix_display_scaling",
        (
            "Arregla la barra de tareas mal dibujada de un monitor con escala alta: baja "
            "su escala al 200 % y la devuelve a la que tenía. Sin monitor, usa el de "
            "mayor escala (la Smart TV)."
        ),
        {
            "type": "object",
            "properties": {"monitor": {"type": "integer", "minimum": 1, "maximum": 9}},
            "additionalProperties": False,
        },
        host._tool_fix_display_scaling,
        risk=ToolRisk.MEDIUM,
        label="Arreglar el escalado",
        timeout_seconds=30,
        verifier=verificadores.get("fix_display_scaling"),
    )
    _registrar_consultas_sistema(registry)
    registry.register(
        "morning_briefing",
        (
            "Resumen del día («buenos días», «¿cómo viene el día?»): clima, recordatorios y "
            "rutinas de hoy, avisos del equipo y batería de los auriculares. Con automatic=true/false "
            "activa o desactiva el resumen que sale solo cada mañana."
        ),
        {
            "type": "object",
            "properties": {"automatic": {"type": "boolean"}},
            "required": [],
            "additionalProperties": False,
        },
        host._tool_morning_briefing,
        label="Resumen del día",
        timeout_seconds=30,
        record_result=False,
    )
    _registrar_energia(registry)
    registry.register(
        "screen_click",
        (
            "Hace clic en un punto de la pantalla y, opcionalmente, escribe texto. Solo la "
            "usa la app después de que el usuario confirme la vista previa con la marca."
        ),
        {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "minimum": -20000, "maximum": 20000},
                "y": {"type": "integer", "minimum": -20000, "maximum": 20000},
                "text": {"type": "string", "maxLength": 500},
            },
            "required": ["x", "y"],
            "additionalProperties": False,
        },
        jarvis_screen_actions.ejecutar_en_pantalla,
        risk=ToolRisk.HIGH,
        requires_confirmation=True,
        label="Clic en pantalla",
        timeout_seconds=15,
        record_result=False,
        exposed=False,  # el modelo no puede hacer clics: solo la app, con confirmación
    )
    registry.register(
        "open_app",
        (
            "Abre una aplicación instalada por su nombre, tal como aparece en el menú Inicio "
            "(WhatsApp, Discord, Claude, Bloc de notas, Calculadora…), también las de la "
            "Microsoft Store. Úsala para «abre X» cuando no tengas una ruta."
        ),
        {
            "type": "object",
            "properties": {"name": {"type": "string", "minLength": 2, "maxLength": 80}},
            "required": ["name"],
            "additionalProperties": False,
        },
        jarvis_system.abrir_app,
        argument_validators={"name": _trim_non_empty_text},
        risk=ToolRisk.MEDIUM,
        label="Abrir aplicación",
        timeout_seconds=30,
    )
    registry.register(
        "open_program",
        (
            "Abre un programa, acceso directo (.lnk), documento o carpeta indicado por "
            "su ruta completa en el equipo (target). Si no conoces la ruta exacta, no la "
            "inventes: usa open_app con el nombre de la aplicación."
        ),
        {
            "type": "object",
            "properties": {"target": {"type": "string", "minLength": 3, "maxLength": 400}},
            "required": ["target"],
            "additionalProperties": False,
        },
        jarvis_system.abrir_programa,
        risk=ToolRisk.MEDIUM,
        requires_confirmation=True,
        label="Abrir programa",
        timeout_seconds=15,
    )
    jarvis_media.registrar_herramientas(registry, verificadores)
    jarvis_reminders.registrar_herramientas(
        registry, verificador=verificadores.get("set_reminder")
    )
    registry.register(
        "get_headset_battery",
        "Solo lectura. Porcentaje aproximado de batería de los auriculares G533 y si se están cargando.",
        {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        jarvis_headset.consultar_bateria,
        label="Consultar batería de los auriculares",
        timeout_seconds=10,
        record_result=False,
    )
    jarvis_routines.registrar_herramienta(
        registry,
        host,
        verifier=verificadores.get("schedule_routine"),
    )
    return registry


def _registrar_consultas_sistema(registry: ToolRegistry) -> None:
    """Herramientas de solo lectura sobre el equipo (roadmap punto 2).

    Son de riesgo bajo y no piden confirmación porque no cambian nada. Su
    resultado no se guarda en el historial de acciones porque puede contener
    nombres de programas o títulos de ventanas.
    """
    registry.register(
        "list_open_applications",
        (
            "Solo lectura. Lista las aplicaciones con ventanas abiertas en el equipo. "
            "Usa include_titles=true únicamente si el usuario pide explícitamente "
            "ver los títulos de las ventanas."
        ),
        {
            "type": "object",
            "properties": {"include_titles": {"type": "boolean"}},
            "required": [],
            "additionalProperties": False,
        },
        jarvis_system_info.consultar_aplicaciones_abiertas,
        label="Consultar aplicaciones abiertas",
        timeout_seconds=10,
        record_result=False,
    )
    registry.register(
        "list_top_processes",
        (
            "Solo lectura. Muestra los programas que más memoria (memory) o CPU (cpu) "
            "consumen, agrupando sus procesos. No cierra ni modifica procesos."
        ),
        {
            "type": "object",
            "properties": {
                "sort_by": {"type": "string", "enum": ["memory", "cpu"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": 15},
            },
            "required": [],
            "additionalProperties": False,
        },
        jarvis_system_info.consultar_procesos,
        label="Consultar procesos",
        timeout_seconds=15,
        record_result=False,
    )
    registry.register(
        "get_system_status",
        (
            "Solo lectura. Informa el uso de CPU, RAM, espacio libre en el disco del "
            "sistema, tiempo encendido, temperaturas de la GPU y la CPU, y batería si existe."
        ),
        {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        jarvis_system_info.consultar_estado_equipo,
        label="Consultar estado del equipo",
        timeout_seconds=10,
        record_result=False,
    )
    registry.register(
        "get_current_datetime",
        "Solo lectura. Devuelve la fecha (date), la hora (time) o ambas (both) del reloj del equipo.",
        {
            "type": "object",
            "properties": {"what": {"type": "string", "enum": ["date", "time", "both"]}},
            "required": [],
            "additionalProperties": False,
        },
        lambda what="both": jarvis_system_info.consultar_fecha_hora(what),
        label="Consultar fecha y hora",
        timeout_seconds=5,
        record_result=False,
    )
    registry.register(
        "get_jarvis_diagnostics",
        (
            "Solo lectura. Diagnóstico del propio Jarvis: si Ollama, la voz a texto y "
            "el texto a voz responden, tiempos de respuesta recientes y acciones "
            "fallidas en las últimas 24 horas."
        ),
        {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        jarvis_health.consultar_diagnostico,
        label="Diagnóstico de Jarvis",
        timeout_seconds=15,
    )


def _registrar_energia(registry: ToolRegistry) -> None:
    """Apagar, reiniciar, suspender y bloquear el equipo.

    Apagar, reiniciar y suspender son de riesgo alto: siempre piden
    confirmación, también como comando directo. Bloquear y cancelar un
    apagado programado no pierden trabajo, así que son de riesgo bajo.
    """
    registry.register(
        "power_control",
        (
            "Apaga (shutdown), reinicia (restart) o suspende (sleep) el equipo. "
            f"Apagar y reiniciar se programan con {jarvis_power.ESPERA_APAGADO} segundos de margen para cancelarlos. "
            "Úsala solo si el usuario lo pide explícitamente."
        ),
        {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": list(jarvis_power.ACCIONES_ENERGIA)}
            },
            "required": ["action"],
            "additionalProperties": False,
        },
        jarvis_power.ejecutar_accion_energia,
        risk=ToolRisk.HIGH,
        requires_confirmation=True,
        label="Apagar, reiniciar o suspender el equipo",
        timeout_seconds=15,
    )
    registry.register(
        "cancel_power_action",
        "Cancela un apagado o reinicio del equipo que esté programado.",
        {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        jarvis_power.cancelar_apagado,
        label="Cancelar apagado o reinicio",
        timeout_seconds=15,
    )
    registry.register(
        "lock_computer",
        "Bloquea la sesión de Windows (pantalla de bloqueo). No cierra programas.",
        {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        jarvis_power.bloquear_equipo,
        label="Bloquear el equipo",
        timeout_seconds=10,
    )
