"""Tarjetas de acción del chat: icono, estado y detalle de cada ``ResultadoAccion``.

Lógica sin Tk (la dibuja ``JarvisUIMixin._add_action_card``).
"""

import json

from jarvis_actions import EstadoAccion, ResultadoAccion

ICONOS = {
    "configure_display": "🖥", "fix_display_scaling": "🖥", "move_window": "🗔",
    "set_system_volume": "🔊", "set_app_volume": "🔊", "set_output_device": "🎧",
    "set_jarvis_audio": "🔈", "media_control": "⏯", "media_sequence": "⏯", "video_fullscreen": "⛶",
    "mute_browser_tab": "🔇", "now_playing": "🎵", "search_youtube": "▶",
    "open_new_browser_tab": "🌐", "navigate_browser": "🌐", "launch_game": "🎮",
    "open_program": "📂", "manage_window": "🗔", "power_control": "⏻",
    "cancel_power_action": "⏻", "lock_computer": "🔒", "set_reminder": "⏰",
    "cancel_reminder": "⏰", "list_reminders": "⏰", "schedule_routine": "⏱",
    "screen_click": "🖱", "screen_vision": "👁", "set_theme": "🎨",
    "get_weather": "☁", "get_headset_battery": "🔋", "get_system_status": "📊",
    "list_top_processes": "📊", "list_open_applications": "🗂",
    "get_current_datetime": "🕒", "get_jarvis_diagnostics": "🩺",
}
ORIGENES = {"directo": "directo", "agente": "IA", "rutina": "rutina", "frase": "frase", "pantalla": "pantalla"}


def icono(herramienta: str) -> str:
    if herramienta.startswith("mcp_"):
        return "📄"
    return ICONOS.get(herramienta, "⚙")


def estado(resultado: ResultadoAccion) -> tuple[str, str]:
    """(texto del estado, token de color de jarvis_theme)."""
    if resultado.estado is EstadoAccion.EXITO:
        if resultado.verificacion is not None and resultado.verificacion.ok:
            return "✓ VERIFICADA", "CYAN"
        return "✓ HECHO", "CYAN"
    return {
        EstadoAccion.CANCELADA: ("⦸ CANCELADA", "MUTED"),
        EstadoAccion.TIEMPO_AGOTADO: ("⏱ TIEMPO AGOTADO", "ERROR"),
        EstadoAccion.NO_VERIFICADA: ("! NO VERIFICADA", "ERROR"),
    }.get(resultado.estado, ("✕ FALLÓ", "ERROR"))


def resumen(resultado: ResultadoAccion) -> str:
    """Línea secundaria: duración y origen."""
    origen = ORIGENES.get(resultado.origen, resultado.origen)
    return f"{resultado.duracion:.1f} s · {origen}"


def detalle(resultado: ResultadoAccion) -> str:
    """Texto desplegable: parámetros, verificación y error."""
    lineas = []
    if resultado.argumentos:
        lineas.append("Parámetros: " + json.dumps(resultado.argumentos, ensure_ascii=False))
    if resultado.verificacion is not None:
        lineas.append(f"Verificación: {resultado.verificacion.detalle}")
    if resultado.error:
        lineas.append(f"Error: {resultado.error}")
    return "\n".join(lineas) or "Sin más detalles."
