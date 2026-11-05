from collections.abc import Mapping
from typing import Protocol

from jarvis_actions import Verificador
from jarvis_commands import normalizar_url_web
import jarvis_routines
import jarvis_health
import jarvis_system_info
from jarvis_tool_registry import ActionRecorder, ToolRegistry, ToolRisk


class ToolEventHost(Protocol):
    def _tool_set_system_volume(self, percentage: int) -> str: ...
    def _tool_set_output_device(self, device: str) -> str: ...
    def _tool_set_jarvis_audio(self, enabled: bool) -> str: ...
    def _tool_open_new_browser_tab(self) -> str: ...
    def _tool_navigate_browser(self, url: str) -> str: ...
    def _tool_launch_game(self, name: str) -> str: ...
    def _tool_manage_window(self, action: str) -> str: ...
    def _tool_search_youtube(self, query: str, play_index: int | None = None) -> str: ...
    def _tool_configure_display(
        self, action: str, monitor: int, second_monitor: int | None = None
    ) -> str: ...


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
    _registrar_consultas_sistema(registry)
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
            "sistema, tiempo encendido y batería si existe."
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
