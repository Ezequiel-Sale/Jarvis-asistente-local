"""Tarjetas de acción del chat: icono, estado y detalle de cada ``ResultadoAccion``.

Lógica sin Tk (la dibuja ``JarvisUIMixin._add_action_card``).
"""

import json
from datetime import datetime

from jarvis_actions import EstadoAccion, ResultadoAccion


def hora_mensaje(momento: datetime | None = None, ahora: datetime | None = None) -> str:
    """Fecha y hora de un mensaje del chat: «12/11 14:22» (con el año si no es el actual)."""
    ahora = ahora or datetime.now()
    momento = momento or ahora
    return momento.strftime("%d/%m %H:%M" if momento.year == ahora.year else "%d/%m/%Y %H:%M")

ICONOS = {
    "configure_display": "🖥", "restore_display_config": "🖥", "fix_display_scaling": "🖥", "move_window": "🗔",
    "set_system_volume": "🔊", "set_app_volume": "🔊", "set_output_device": "🎧",
    "set_jarvis_audio": "🔈", "media_control": "⏯", "media_sequence": "⏯", "video_fullscreen": "⛶", "video_theater": "🎭", "apply_gpu_profile": "⚡",
    "mute_browser_tab": "🔇", "now_playing": "🎵", "search_youtube": "▶",
    "open_new_browser_tab": "🌐", "navigate_browser": "🌐", "send_file_to_phone": "📲", "send_clipboard_to_phone": "📋", "launch_game": "🎮",
    "open_program": "📂", "open_app": "📂", "manage_window": "🗔", "window_control": "🗔", "browser_history": "🔄", "power_control": "⏻",
    "cancel_power_action": "⏻", "lock_computer": "🔒", "set_reminder": "⏰",
    "cancel_reminder": "⏰", "list_reminders": "⏰", "schedule_routine": "⏱",
    "screen_click": "🖱", "screen_vision": "👁", "set_theme": "🎨",
    "get_weather": "☁", "web_search": "🔎", "get_headset_battery": "🔋", "get_system_status": "📊",
    "list_top_processes": "📊", "list_open_applications": "🗂",
    "get_current_datetime": "🕒", "morning_briefing": "☀", "undo_last_action": "↶",
    "show_commands_help": "?", "find_files": "🔎", "open_file": "📄",
    "tv_control": "📺", "tv_open_app": "📺", "tv_status": "📺", "get_jarvis_diagnostics": "🩺",
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


# --- Pasos de un plan en lenguaje llano (tarjeta «J.A.R.V.I.S. propone un plan») ------

_MONITORES = {
    "disable": "Desactivar el monitor {monitor}",
    "extend": "Activar el monitor {monitor} (escritorio extendido)",
    "duplicate": "Duplicar el monitor {monitor} en el monitor {second_monitor}",
    "set_primary": "Hacer principal el monitor {monitor}",
}
RIESGOS = {
    "low": ("RIESGO BAJO", "MUTED"),
    "medium": ("RIESGO MEDIO", "VIOLET"),
    "high": ("RIESGO ALTO", "ERROR"),
}


ETIQUETAS_SALIDA = {"speakers": "parlantes", "headphones": "auriculares"}


def _monitor(a: dict) -> str:
    return f" del monitor {a['monitor']}" if a.get("monitor") else ""


def _frase_especifica(herramienta: str, a: dict) -> str | None:
    """Frase en español llano para las acciones más comunes (None: se usa la genérica)."""
    accion = str(a.get("action", ""))
    if herramienta == "configure_display" and accion in _MONITORES:
        return _MONITORES[accion].format(**{"monitor": "?", "second_monitor": "?", **a})
    if herramienta == "restore_display_config":
        rutina = str(a.get("routine") or "").strip()
        if rutina:
            return f"Volver los monitores a como estaban antes de «{rutina}»"
        return "Volver los monitores a como estaban antes de la rutina"
    if herramienta == "fix_display_scaling":
        return f"Reajustar el escalado{_monitor(a)} (arregla la barra de tareas mal ubicada)"
    if herramienta == "media_control":
        verbos = {"play": "Reanudar el vídeo o la música", "pause": "Pausar el vídeo o la música",
                  "toggle": "Pausar o reanudar el vídeo o la música", "stop": "Detener el vídeo o la música",
                  "next": "Pasar al siguiente vídeo o canción", "previous": "Volver al vídeo o canción anterior"}
        if accion in verbos:
            return verbos[accion] + _monitor(a)
    if herramienta == "mute_browser_tab":
        verbo = "Silenciar" if a.get("mute", True) else "Devolverle el sonido a"
        cual = f" «{a['match']}»" if a.get("match") else ""
        donde = f" en el monitor {a['monitor']}" if a.get("monitor") else ""
        return f"{verbo} la pestaña{cual}{donde}"
    if herramienta == "video_fullscreen":
        verbo = "Poner en pantalla completa" if a.get("enabled", True) else "Sacar de pantalla completa"
        return f"{verbo} el vídeo{_monitor(a)}"
    if herramienta == "video_theater":
        if a.get("enabled", True):
            return f"Poner en modo teatro el vídeo{_monitor(a)}"
        return f"Quitar el modo teatro del vídeo{_monitor(a)}"
    if herramienta == "clean_disk":
        que = f" ({', '.join(str(x) for x in a['categories'])})" if a.get("categories") else ""
        return f"Liberar espacio en el disco{que}"
    if herramienta == "set_system_volume" and "percentage" in a:
        return f"Poner el volumen del equipo al {a['percentage']} %"
    if herramienta == "set_app_volume" and a.get("app"):
        if "percentage" in a:
            return f"Poner el volumen de {a['app']} al {a['percentage']} %"
        return f"{'Silenciar' if a.get('mute') else 'Devolverle el sonido a'} {a['app']}"
    if herramienta == "set_output_device" and a.get("device"):
        return f"Sacar el sonido por los {ETIQUETAS_SALIDA.get(str(a['device']), a['device'])}"
    if herramienta == "launch_game" and a.get("name"):
        return f"Abrir el juego «{a['name']}»"
    if herramienta in ("open_app", "open_program") and (a.get("name") or a.get("target")):
        return f"Abrir «{a.get('name') or a.get('target')}»"
    if herramienta == "window_control" and a.get("app"):
        verbos = {"window_minimize": "Minimizar", "window_maximize": "Maximizar",
                  "window_restore": "Restaurar", "window_focus": "Traer al frente"}
        return f"{verbos.get(accion, accion)} la ventana de {a['app']}"
    if herramienta == "move_window" and a.get("app"):
        destino = f"al monitor {a['monitor']}" if a.get("monitor") else f"a «{a.get('screen_name', '?')}»"
        return f"Mover la ventana de {a['app']} {destino}"
    if herramienta == "navigate_browser" and a.get("url"):
        return f"Abrir la página {a['url']}"
    if herramienta == "open_new_browser_tab":
        return "Abrir una pestaña nueva en el navegador"
    if herramienta == "browser_history" and accion:
        return {"back": "Volver a la página anterior", "forward": "Ir a la página siguiente",
                "reload": "Recargar la página (F5)"}.get(accion)
    if herramienta == "search_youtube" and a.get("query"):
        return f"Buscar «{a['query']}» en YouTube"
    if herramienta == "power_control" and accion:
        return {"shutdown": "Apagar el equipo", "restart": "Reiniciar el equipo",
                "sleep": "Suspender el equipo"}.get(accion)
    if herramienta == "set_theme" and a.get("theme"):
        return f"Cambiar el aspecto de Jarvis al tema «{a['theme']}»"
    if herramienta == "tv_control" and accion:
        if accion.startswith("hdmi"):
            return f"Poner la tele en la entrada HDMI {accion[4:]}"
        if accion in ("volume_up", "volume_down"):
            return f"{'Subir' if accion == 'volume_up' else 'Bajar'} el volumen de la tele ({a.get('steps', 1)} pasos)"
        if accion == "set_volume":
            return f"Poner el volumen de la tele al {a.get('level', '?')} %"
        return {"power_on": "Encender la tele", "power_off": "Apagar la tele", "mute": "Silenciar la tele",
                "play": "Dar play en la tele", "pause": "Pausar la tele", "play_pause": "Pausar o reanudar la tele",
                "home": "Ir al inicio de la tele", "back": "Volver atrás en la tele"}.get(accion, f"Tele: {accion}")
    if herramienta == "tv_open_app" and a.get("app"):
        import jarvis_tv

        return f"Abrir {jarvis_tv.APPS.get(str(a['app']), (a['app'],))[0]} en la tele"
    if herramienta == "screen_click":
        texto = f" y escribir «{a['text']}»" if a.get("text") else ""
        return f"Hacer clic en la pantalla{texto}"
    return None


def describir_paso(herramienta: str, argumentos: dict) -> str:
    """«Desactivar el monitor 2» en vez de {"action": "disable", "monitor": 2}."""
    frase = _frase_especifica(herramienta, argumentos)
    if frase is not None:
        return frase
    from jarvis_routine_form import ETIQUETAS_CAMPO, ETIQUETAS_VALOR

    partes = []
    for clave, valor in argumentos.items():
        if isinstance(valor, bool):
            texto = "sí" if valor else "no"
        elif isinstance(valor, list):
            texto = ", ".join(str(v) for v in valor)
        elif isinstance(valor, dict):
            texto = json.dumps(valor, ensure_ascii=False)
        else:
            texto = ETIQUETAS_VALOR.get(str(valor), str(valor))
        partes.append(f"{ETIQUETAS_CAMPO.get(clave, clave.replace('_', ' ').capitalize())}: {texto}")
    return " · ".join(partes)


# Por qué se pide autorización, en palabras de cualquiera.
_AVISOS = {
    "configure_display": "Cambia qué pantallas se usan. Después te pregunto si lo dejas así; "
                         "si no respondes en 15 segundos, vuelve solo a como estaba.",
    "restore_display_config": "Cambia qué pantallas se usan. Después te pregunto si lo dejas así; "
                              "si no respondes, vuelve solo a como estaba.",
    "clean_disk": "Borra archivos temporales y de la papelera para liberar espacio. No se pueden recuperar.",
    "power_control": "El equipo se apagará, reiniciará o suspenderá: guarda lo que estés haciendo.",
    "launch_game": "Abre un juego, que puede ocupar la pantalla y usar mucha memoria.",
    "manage_window": "Cambia el tamaño de una ventana.",
    "screen_click": "Jarvis moverá el mouse y hará clic por ti.",
    "mobile_remote_control": "El celular podrá mover el mouse y escribir en esta PC.",
}


def aviso(herramienta: str, nivel: str) -> str:
    """Una frase que explica qué implica la acción, sin tecnicismos."""
    if herramienta in _AVISOS:
        return _AVISOS[herramienta]
    if herramienta.startswith("mcp_"):
        return "Usa una integración externa y puede cambiar o borrar datos."
    if nivel == "high":
        return "Es una acción importante: revisa que sea lo que querías."
    return "Pide tu permiso antes de hacerlo."


def riesgo(nivel: str) -> tuple[str, str]:
    """(texto, token de color) del riesgo de un paso."""
    return RIESGOS.get(nivel, (nivel.upper(), "MUTED"))
