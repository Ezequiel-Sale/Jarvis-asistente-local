import json
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

import jarvis_core as core
from jarvis_actions import Verificador
from jarvis_commands import normalizar_url_web
from jarvis_tool_registry import ToolDefinition, ToolError, ToolRegistry, ToolRisk

# Disparadores (valores guardados en rutinas.disparador)
DISPARADOR_HORA = "hora"
# Comando personalizado: se ejecuta al decir o escribir una de sus frases.
DISPARADOR_FRASE = "frase"
MAX_PASOS = 10
DISPARADOR_AURICULARES_CONECTADOS = "auriculares_conectados"
DISPARADOR_AURICULARES_DESCONECTADOS = "auriculares_desconectados"
# Modo juego automático (jarvis_games_watch): al abrir el primer juego / al cerrar el último.
DISPARADOR_JUEGO_ABIERTO = "juego_abierto"
DISPARADOR_JUEGO_CERRADO = "juego_cerrado"
DISPARADORES_EVENTO = frozenset(
    {DISPARADOR_AURICULARES_CONECTADOS, DISPARADOR_AURICULARES_DESCONECTADOS,
     DISPARADOR_JUEGO_ABIERTO, DISPARADOR_JUEGO_CERRADO}
)
# Valores que usa la herramienta schedule_routine (en inglés, como el resto de su esquema)
_TRIGGER_FROM_TOOL = {
    "time": DISPARADOR_HORA,
    "headphones_connected": DISPARADOR_AURICULARES_CONECTADOS,
    "headphones_disconnected": DISPARADOR_AURICULARES_DESCONECTADOS,
    "game_started": DISPARADOR_JUEGO_ABIERTO,
    "game_stopped": DISPARADOR_JUEGO_CERRADO,
}
_TRIGGER_TO_TOOL = {value: key for key, value in _TRIGGER_FROM_TOOL.items()}
_TRIGGER_LABELS = {
    DISPARADOR_AURICULARES_CONECTADOS: "al conectar los auriculares",
    DISPARADOR_AURICULARES_DESCONECTADOS: "al desconectar los auriculares",
    DISPARADOR_JUEGO_ABIERTO: "al abrir un juego",
    DISPARADOR_JUEGO_CERRADO: "al cerrar un juego",
}
PERMISO_PREGUNTAR = "preguntar"
PERMISO_AUTOMATICA = "automatica"
_ALL_DAYS = "1,2,3,4,5,6,7"


_WEEKDAY_NAMES = (
    "lunes",
    "martes",
    "miércoles",
    "jueves",
    "viernes",
    "sábado",
    "domingo",
)
_ROUTINE_ALLOWED_TOOLS = frozenset(
    {
        "set_system_volume",
        "set_output_device",
        "set_jarvis_audio",
        "set_theme",
        "set_voice",
        "video_fullscreen",
        "video_theater",
        "tv_control",
        "tv_open_app",
        "apply_gpu_profile",
        "open_new_browser_tab",
        "navigate_browser",
        "launch_game",
        "manage_window",
        "window_control",
        "browser_history",
        "search_youtube",
        "media_control",
        "mute_browser_tab",
        "set_app_volume",
        "lock_computer",
        # Riesgo alto: en una rutina no pide aprobación, avisa con cuenta atrás
        # (ver requiere_cuenta_atras y JarvisApp._routine_worker).
        "power_control",
        "cancel_power_action",
        "open_program",
        "open_app",
        "configure_display",
        "fix_display_scaling",
        "move_window",
        "set_reminder",
        "get_current_datetime",
        "get_headset_battery",
        "get_now_playing",
        "get_system_status",
        "get_jarvis_diagnostics",
        "get_weather",
    }
)
_LIST_PATTERN = re.compile(
    r"^(?:jarvis[,:\s]+)?(?:lista|listar|muestra|mostrar)\s+"
    r"(?:mis\s+)?rutinas\s*[?.!]*$",
    re.IGNORECASE,
)
_ROUTINE_ACTION_PATTERN = re.compile(
    r"^(?:jarvis[,:\s]+)?(?P<action>pausa|pausar|desactiva|desactivar|"
    r"reanuda|reanudar|activa|activar|elimina|eliminar|borra|borrar)\s+"
    r"(?:la\s+)?rutina\s+(?P<name>.+?)\s*[?.!]*$",
    re.IGNORECASE,
)
_CREATE_ROUTINE_INTENT = re.compile(
    r"\b(?:programa|programar|agenda|agendar|crea|crear)\s+"
    r"(?:una\s+)?rutina\b",
    re.IGNORECASE,
)
_CREATE_ROUTINE_HEADER = re.compile(
    r"\b(?:programa|programar|agenda|agendar|crea|crear)\s+"
    r"(?:una\s+)?rutina\s+(?:llamada|denominada|de nombre)\s+"
    r"(?P<name>.+?)\s+a\s+las?\s+"
    r"(?P<hour>\d{1,2})"
    # Minutos opcionales: «23:00», «23.30», «23 y 15», «7 y media», «7 y cuarto».
    r"(?:(?:\s*(?::|\.)\s*|\s+y\s+|\s+)(?P<minute>\d{2})\b"
    r"|\s+y\s+(?P<fraction>media|cuarto))?"
    # «23 horas», «11 de la noche», «7 de la mañana», «3 pm».
    r"(?:\s+(?:hs|h|horas|en\s+punto)\b"
    r"|\s+(?:de\s+la\s+)?(?P<period>ma[nñ]ana|tarde|noche|madrugada)\b"
    r"|\s*(?P<ampm>am|pm)\b)?",
    re.IGNORECASE,
)
_DAY_RANGE = re.compile(
    r"\b(?:de\s+)?lunes\s+a\s+viernes\b",
    re.IGNORECASE,
)
_EVERY_DAY = re.compile(
    r"\b(?:todos\s+los\s+d[ií]as|cada\s+d[ií]a)\b",
    re.IGNORECASE,
)
_WEEKDAY = re.compile(
    r"\b(lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bados?|domingos?)\b",
    re.IGNORECASE,
)
_ROUTINE_STEP = re.compile(
    r"(?P<power>(?P<power_verb>apaga|apagar|suspende|suspender|reinicia|reiniciar)"
    r"\s+(?:el\s+|la\s+)?(?:equipo|pc|ordenador|computadora))"
    r"|(?P<lock>bloquea(?:r)?\s+(?:el\s+|la\s+)?"
    r"(?:equipo|pc|ordenador|computadora|pantalla|sesi[oó]n))"
    r"|(?P<media_pause>(?:pausa|pausar|pon\s+pausa\s+a|det[eé]n|detener)\s+(?:la\s+)?"
    r"(?:m[uú]sica|reproducci[oó]n|canci[oó]n))"
    r"|(?P<media_play>(?:reanuda|reanudar|contin[uú]a|continuar)\s+(?:la\s+)?"
    r"(?:m[uú]sica|reproducci[oó]n))"
    r"|(?P<media_next>(?:(?:pasa|salta|pon)\s+(?:a\s+)?(?:la\s+)?)?siguiente\s+canci[oó]n)"
    r"|(?P<volume>"
    r"(?:sube|subir|aumenta|aumentar|baja|bajar|reduce|reducir|"
    r"ajusta|ajustar|establece|establecer|pon|poner)"
    r"\s+(?:el\s+)?volumen(?:\s+del\s+sistema)?\s+(?:al|a|en)\s+"
    r"(?P<percentage>\d{1,3})\s*(?:%|por\s+ciento|porciento))"
    r"|(?P<appvol>(?:pon|poner|baja|bajar|sube|subir|ajusta|ajustar|deja|dejar)"
    r"\s+(?:el\s+)?volumen\s+(?:de|del)\s+(?P<app>[a-z0-9]+)\s+(?:a|al|en)\s+"
    r"(?P<app_percentage>\d{1,3})\s*(?:%|por\s+ciento|porciento)?)"
    r"|(?P<new_tab>"
    r"(?:abre|abrir|abr[ií])\s+(?:una\s+)?(?:nueva\s+)?pesta[nñ]a)"
    r"|(?P<output>"
    r"(?:cambia|cambiar|pon|poner|pasa|pasar|usa|usar)\s+(?:la\s+)?salida"
    r"(?:\s+de\s+(?:audio|sonido))?\s+(?:a|al|en)\s+(?:los\s+|las\s+)?"
    r"(?P<device>auriculares|cascos|aud[ií]fonos|parlantes|altavoces|bocinas))"
    r"|(?P<navigate>"
    r"(?:navega|navegar|ve|ir)\s+(?:a|hacia)\s+"
    r"(?P<url>(?:https?://)?[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?"
    r"(?:/[^\s,;]*)?))",
    re.IGNORECASE,
)
_EVENT_ROUTINE = re.compile(
    r"^\s*(?:jarvis[,:\s]+)?"
    r"(?:(?:crea|crear|programa|programar|agenda|agendar)\s+una\s+"
    r"(?:rutina|automatizaci[oó]n)\s+(?:para\s+que\s+|que\s+)?)?"
    r"cuando\s+(?:yo\s+)?(?:se\s+)?"
    r"(?P<event>conect\w*|enchuf\w*|enciend\w*|desconect\w*|desenchuf\w*|apag\w*|quit\w*)"
    r"\s+(?:los\s+|mis\s+)?(?:auriculares|cascos|aud[ií]fonos|headset)(?:\s+g533)?"
    r"\s*[,:]?\s*(?P<rest>.+)$",
    re.IGNORECASE,
)
# «cuando abra un juego, cambia la salida a auriculares» / «cuando cierre el juego, …»
_GAME_ROUTINE = re.compile(
    r"^\s*(?:jarvis[,:\s]+)?"
    r"(?:(?:crea|crear|programa|programar|agenda|agendar)\s+una\s+"
    r"(?:rutina|automatizaci[oó]n)\s+(?:para\s+que\s+|que\s+)?)?"
    r"cuando\s+(?:yo\s+)?"
    r"(?:(?P<abrir>abra|abro|inicie|inicio|arranque|empiece)\s+(?:un|una|alg[uú]n|el|la|mi)?\s*"
    r"(?:juego|videojuego|partida)"
    r"|(?P<jugar>empiece\s+a\s+jugar|juegue|me\s+ponga\s+a\s+jugar)"
    r"|(?P<cerrar>cierre|cierro|salga\s+del?|termine)\s+(?:un|una|alg[uú]n|el|la|mi)?\s*"
    r"(?:juego|videojuego|partida)"
    r"|(?P<dejar>termine\s+de\s+jugar|deje\s+de\s+jugar))"
    r"\s*[,:]?\s*(?P<rest>.+)$",
    re.IGNORECASE,
)
_AUTOMATIC_WORDS = re.compile(
    r"\b(?:autom[aá]ticamente|de\s+forma\s+autom[aá]tica|sin\s+preguntar(?:me)?|"
    r"sin\s+pedir(?:me)?\s+(?:permiso|confirmaci[oó]n))\b",
    re.IGNORECASE,
)
_ACTION_SEPARATOR = re.compile(
    r"^(?:\s|,|;|[.!?¿¡]|y\b|luego\b|despu[eé]s\b|por\s+favor\b)*$",
    re.IGNORECASE,
)


class RoutineHost(Protocol):
    def _tool_schedule_routine(
        self,
        name: str,
        time: str = "",
        days: str = "1,2,3,4,5,6,7",
        steps_json: str = "",
        trigger: str = "time",
        automatic: bool = False,
        once: bool = False,
    ) -> str: ...


@dataclass(frozen=True, slots=True)
class RoutineStep:
    tool: str
    arguments: dict[str, object]


@dataclass(frozen=True, slots=True)
class Routine:
    identifier: int
    name: str
    time: str
    days: tuple[int, ...]
    steps: tuple[RoutineStep, ...]
    trigger: str = DISPARADOR_HORA
    permission: str = PERMISO_PREGUNTAR
    phrases: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RoutineInfo:
    """Vista completa de una rutina para el panel y los listados."""

    identifier: int
    name: str
    time: str
    days: tuple[int, ...]
    active: bool
    trigger: str
    permission: str
    steps: tuple[RoutineStep, ...]
    phrases: tuple[str, ...] = ()
    once: bool = False


@dataclass(frozen=True, slots=True)
class RoutineCreationRequest:
    name: str
    time: str
    days: str
    steps_json: str
    trigger: str = "time"
    automatic: bool = False
    once: bool = False

    def tool_arguments(self) -> dict[str, object]:
        """Argumentos para schedule_routine (solo incluye los no predeterminados)."""
        arguments: dict[str, object] = {
            "name": self.name,
            "days": self.days,
            "steps_json": self.steps_json,
        }
        if self.trigger == "time":
            arguments["time"] = self.time
        else:
            arguments["trigger"] = self.trigger
        if self.automatic:
            arguments["automatic"] = True
        if self.once:
            arguments["once"] = True
        return arguments


def _validate_name(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("debe ser texto.")
    name = " ".join(value.split())
    if len(name) < 2 or len(name) > 80:
        raise ValueError("el nombre debe tener entre 2 y 80 caracteres.")
    if any(ord(character) < 32 for character in name):
        raise ValueError("el nombre contiene caracteres no permitidos.")
    return name


def _validate_time(value: object) -> str:
    """HH:MM, o HH:MM:SS para lo programado al segundo («suspende el equipo dentro de 10 minutos»)."""
    if not isinstance(value, str) or re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d)?", value) is None:
        raise ValueError("usa el formato de 24 horas HH:MM, por ejemplo 08:30.")
    return value


def _parse_days(value: object) -> tuple[int, ...]:
    if not isinstance(value, str) or not value:
        raise ValueError("indica días del 1 al 7, donde 1 es lunes.")
    parts = value.split(",")
    if any(re.fullmatch(r"[1-7]", part) is None for part in parts):
        raise ValueError("usa números del 1 al 7 separados por comas.")
    days = tuple(sorted({int(part) for part in parts}))
    if len(days) != len(parts):
        raise ValueError("no repitas días.")
    return days


def _validate_days(value: object) -> str:
    return ",".join(map(str, _parse_days(value)))


def _validate_steps_json(value: object, registry: ToolRegistry) -> str:
    if not isinstance(value, str):
        raise ValueError("los pasos deben ser una lista JSON.")
    try:
        steps = json.loads(value)
    except json.JSONDecodeError as error:
        raise ValueError("los pasos deben ser una lista JSON válida.") from error
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_PASOS:
        raise ValueError(f"la rutina debe tener entre 1 y {MAX_PASOS} pasos.")

    normalized_steps: list[dict[str, object]] = []
    for index, step in enumerate(steps, start=1):
        if (
            not isinstance(step, dict)
            or set(step) != {"tool", "arguments"}
            or not isinstance(step["tool"], str)
            or not isinstance(step["arguments"], dict)
        ):
            raise ValueError(
                f"el paso {index} debe tener solo tool y arguments."
            )
        name = step["tool"]
        if name not in _ROUTINE_ALLOWED_TOOLS:
            raise ValueError(f"la herramienta {name!r} no se permite en rutinas.")
        try:
            arguments = registry.validate_arguments(name, step["arguments"])
        except (ToolError, ValueError) as error:
            raise ValueError(f"el paso {index} no es válido: {error}") from error
        normalized_steps.append({"tool": name, "arguments": arguments})

    return json.dumps(normalized_steps, ensure_ascii=False, separators=(",", ":"))


def _decode_steps(value: str) -> tuple[RoutineStep, ...]:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as error:
        raise ValueError("La rutina guardada contiene pasos JSON inválidos.") from error
    if not isinstance(payload, list) or not 1 <= len(payload) <= MAX_PASOS:
        raise ValueError("La rutina guardada no tiene una cantidad válida de pasos.")
    steps: list[RoutineStep] = []
    for index, step in enumerate(payload, start=1):
        if (
            not isinstance(step, dict)
            or set(step) != {"tool", "arguments"}
            or not isinstance(step["tool"], str)
            or not isinstance(step["arguments"], dict)
        ):
            raise ValueError(f"El paso {index} de la rutina guardada es inválido.")
        steps.append(RoutineStep(step["tool"], step["arguments"]))
    return tuple(steps)


def _validate_trigger(value: object) -> str:
    """Acepta el valor de la herramienta (time, headphones_connected…) o el de la BD."""
    if isinstance(value, str):
        if value in _TRIGGER_FROM_TOOL:
            return _TRIGGER_FROM_TOOL[value]
        if value == DISPARADOR_HORA or value in DISPARADORES_EVENTO or value == DISPARADOR_FRASE:
            return value
    raise ValueError(
        "el disparador debe ser time, headphones_connected, headphones_disconnected, "
        "game_started o game_stopped."
    )


def acciones_con_permiso(tool_names: Iterable[str], registry: ToolRegistry) -> list[str]:
    """Acciones que impiden que una rutina sea automática (riesgo medio o alto)."""
    bloqueantes: list[str] = []
    for name in tool_names:
        definition = registry.get(name)
        if definition.risk is not ToolRisk.LOW and definition.display_name not in bloqueantes:
            bloqueantes.append(definition.display_name)
    return bloqueantes


def _error_automatica(bloqueantes: list[str]) -> ValueError:
    return ValueError(
        "Esta rutina no puede ser automática porque "
        + ", ".join(f"«{b}»" for b in bloqueantes)
        + " siempre pide confirmación. Solo las rutinas con acciones de riesgo "
        "bajo (volumen, salida de audio, audio de Jarvis, abrir pestaña, la tele) pueden "
        "ejecutarse sin preguntar."
    )


def describir_disparador(
    trigger: str, time: str, days: tuple[int, ...], phrases: tuple[str, ...] = ()
) -> str:
    if trigger == DISPARADOR_FRASE:
        return "al decir " + " o ".join(f"«{p}»" for p in phrases) if phrases else "al decir su frase"
    if trigger in _TRIGGER_LABELS:
        return _TRIGGER_LABELS[trigger]
    return f"a las {time}, {_routine_days_label(days)}"


def _event_routine_name(trigger: str) -> str:
    base = {
        DISPARADOR_AURICULARES_CONECTADOS: "Al conectar auriculares",
        DISPARADOR_AURICULARES_DESCONECTADOS: "Al desconectar auriculares",
        DISPARADOR_JUEGO_ABIERTO: "Al abrir un juego",
        DISPARADOR_JUEGO_CERRADO: "Al cerrar un juego",
    }.get(trigger, "Rutina")
    existentes = {name.casefold() for _, name, _, _, _ in listar_rutinas()}
    if base.casefold() not in existentes:
        return base
    numero = 2
    while f"{base} {numero}".casefold() in existentes:
        numero += 1
    return f"{base} {numero}"


def _parsear_pasos(remainder: str) -> list[dict[str, object]]:
    steps: list[dict[str, object]] = []
    cursor = 0
    for step_match in _ROUTINE_STEP.finditer(remainder):
        gap = remainder[cursor : step_match.start()]
        if _ACTION_SEPARATOR.fullmatch(gap) is None:
            raise ValueError(
                f"No pude interpretar esta parte de la rutina: «{gap.strip()}»."
            )
        if step_match.group("power") is not None:
            verbo = step_match.group("power_verb").casefold()
            accion = "sleep" if verbo.startswith("susp") else "restart" if verbo.startswith("rein") else "shutdown"
            step = {"tool": "power_control", "arguments": {"action": accion}}
        elif step_match.group("lock") is not None:
            step = {"tool": "lock_computer", "arguments": {}}
        elif step_match.group("media_pause") is not None:
            step = {"tool": "media_control", "arguments": {"action": "pause"}}
        elif step_match.group("media_play") is not None:
            step = {"tool": "media_control", "arguments": {"action": "play"}}
        elif step_match.group("media_next") is not None:
            step = {"tool": "media_control", "arguments": {"action": "next"}}
        elif step_match.group("appvol") is not None:
            app = step_match.group("app").casefold()
            percentage = int(step_match.group("app_percentage"))
            if app in {"sistema", "equipo", "windows"}:
                step = {"tool": "set_system_volume", "arguments": {"percentage": percentage}}
            else:
                step = {"tool": "set_app_volume", "arguments": {"app": app, "percentage": percentage}}
            if not 0 <= percentage <= 100:
                raise ValueError("El volumen de la rutina debe estar entre 0 y 100%.")
        elif step_match.group("volume") is not None:
            percentage = int(step_match.group("percentage"))
            if not 0 <= percentage <= 100:
                raise ValueError("El volumen de la rutina debe estar entre 0 y 100%.")
            step = {
                "tool": "set_system_volume",
                "arguments": {"percentage": percentage},
            }
        elif step_match.group("new_tab") is not None:
            step = {"tool": "open_new_browser_tab", "arguments": {}}
        elif step_match.group("output") is not None:
            device_word = step_match.group("device").casefold()
            device = (
                "speakers"
                if device_word in {"parlantes", "altavoces", "bocinas"}
                else "headphones"
            )
            step = {"tool": "set_output_device", "arguments": {"device": device}}
        else:
            destination = step_match.group("url").rstrip(".!?")
            try:
                normalized_url = normalizar_url_web(destination)
            except ValueError as error:
                raise ValueError(f"No pude validar el sitio web de la rutina: {error}") from error
            step = {
                "tool": "navigate_browser",
                "arguments": {"url": normalized_url},
            }
        steps.append(step)
        cursor = step_match.end()

    if not steps:
        raise ValueError(
            "No reconocí acciones compatibles. Puedes pedir ajustar el volumen "
            "(general o de una aplicación), cambiar la salida de audio, pausar, "
            "reanudar o pasar la música, abrir una pestaña, navegar a una web, "
            "bloquear, apagar, reiniciar o suspender el equipo."
        )
    if _ACTION_SEPARATOR.fullmatch(remainder[cursor:]) is None:
        raise ValueError(
            f"No pude interpretar esta parte de la rutina: «{remainder[cursor:].strip()}»."
        )
    return steps


def _parsear_rutina_evento(
    match: re.Match[str],
    registry: ToolRegistry | None,
    trigger: str | None = None,
) -> RoutineCreationRequest:
    event = (match.groupdict().get("event") or "").casefold()
    trigger = trigger or (
        DISPARADOR_AURICULARES_DESCONECTADOS
        if event.startswith(("des", "apag", "quit"))
        else DISPARADOR_AURICULARES_CONECTADOS
    )
    remainder = match.group("rest")
    automatic = _AUTOMATIC_WORDS.search(remainder) is not None
    remainder = _AUTOMATIC_WORDS.sub(" ", remainder)
    steps = _parsear_pasos(remainder)
    if automatic and registry is not None:
        bloqueantes = acciones_con_permiso((s["tool"] for s in steps), registry)
        if bloqueantes:
            raise _error_automatica(bloqueantes)
    return RoutineCreationRequest(
        name=_event_routine_name(trigger),
        time="",
        days=_ALL_DAYS,
        steps_json=json.dumps(steps, ensure_ascii=False, separators=(",", ":")),
        trigger=_TRIGGER_TO_TOOL[trigger],
        automatic=automatic,
    )


_POWER_SHORTCUT = re.compile(
    r"^\s*(?:(?:oye\s+)?jarvis[,:\s]+)?(?:por\s+favor\s+)?"
    r"(?P<verbo>apaga|apagar|ap[aá]game|suspende|suspender|reinicia|reiniciar)\s+"
    r"(?:el\s+|la\s+|mi\s+)?(?:equipo|pc|ordenador|computadora)\s+(?P<resto>.+?)\s*[.!?]*$",
    re.IGNORECASE,
)
_POWER_NAMES = {"shutdown": "Apagado", "restart": "Reinicio", "sleep": "Suspensión"}


def _dias_recurrentes(texto: str) -> tuple[str | None, str]:
    """Días de «todos los días», «de lunes a viernes» o «los martes y jueves».

    Devuelve ``(días, texto sin esas palabras)``; días es None si no hay recurrencia.
    """
    if _DAY_RANGE.search(texto):
        return "1,2,3,4,5", _DAY_RANGE.sub(" ", texto, count=1)
    if _EVERY_DAY.search(texto):
        return _ALL_DAYS, _EVERY_DAY.sub(" ", texto, count=1)
    numeros = {"lunes": 1, "martes": 2, "miercoles": 3, "jueves": 4, "viernes": 5, "sabado": 6, "domingo": 7}
    encontrados = {
        numeros[m.group(1).casefold().replace("é", "e").replace("á", "a").removesuffix("s")
                if m.group(1).casefold().endswith(("bados", "ingos"))
                else m.group(1).casefold().replace("é", "e").replace("á", "a")]
        for m in _WEEKDAY.finditer(texto)
    }
    if encontrados:
        # Quitar solo la expresión de días («los martes y jueves»), no el resto
        # de la frase («a las 4 de la mañana»).
        texto = _FRASE_DIAS.sub(" ", texto)
        return ",".join(map(str, sorted(encontrados))), texto
    return None, texto


_DIA = r"(?:lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bados?|domingos?)"
_FRASE_DIAS = re.compile(
    rf"\b(?:(?:los|el)\s+)?{_DIA}(?:\s*(?:,|y)\s*(?:(?:los|el)\s+)?{_DIA})*\b",
    re.IGNORECASE,
)


def _nombre_libre(base: str) -> str:
    existentes = {name.casefold() for _, name, _, _, _ in listar_rutinas()}
    if base.casefold() not in existentes:
        return base
    numero = 2
    while f"{base} {numero}".casefold() in existentes:
        numero += 1
    return f"{base} {numero}"


def _parsear_atajo_energia(
    match: re.Match[str], ahora: datetime | None = None
) -> RoutineCreationRequest | None:
    """«apaga el equipo a las 2» (una vez), «… todos los días a las 23:30» o
    «suspende el equipo dentro de 10 minutos» (una vez, a la hora en que se cumplen)."""
    import jarvis_reminders

    verbo = match.group("verbo").casefold()
    accion = "sleep" if verbo.startswith("susp") else "restart" if verbo.startswith("rein") else "shutdown"
    dias, resto = _dias_recurrentes(match.group("resto"))
    normalizado, _ = jarvis_reminders._normalizar_con_mapa(resto)
    ahora = ahora or datetime.now()
    relativo = re.fullmatch(rf"(?:en|dentro\s+de)\s+(?:unos?\s+)?({jarvis_reminders.DURACION})", normalizado.strip())
    if relativo is not None:
        if dias is not None:
            return None  # «todos los días dentro de 10 minutos» no tiene sentido
        segundos = jarvis_reminders.segundos_de(relativo.group(1))
        if segundos <= 0:
            return None
        import jarvis_power

        # Al segundo: la rutina arranca la cuenta atrás de 60 s antes, para que el equipo se
        # apague justo al cumplirse el tiempo pedido (si falta menos, arranca enseguida).
        objetivo = (ahora + timedelta(seconds=segundos)).replace(microsecond=0)
        inicio = max(objetivo - timedelta(seconds=jarvis_power.CUENTA_ATRAS_RUTINA),
                     ahora.replace(microsecond=0) + timedelta(seconds=2))
        objetivo = inicio + timedelta(seconds=jarvis_power.CUENTA_ATRAS_RUTINA)
        pasos = json.dumps([{"tool": "power_control", "arguments": {"action": accion}}], separators=(",", ":"))
        return RoutineCreationRequest(
            name=_nombre_libre(f"{_POWER_NAMES[accion]} {objetivo:%H:%M:%S}"),
            time=f"{inicio:%H:%M:%S}",
            days=str(inicio.isoweekday()),
            steps_json=pasos,
            once=True,
        )
    cuando = re.search(jarvis_reminders.CUANDO_ABS, normalizado)
    if cuando is None:
        return None
    sobrante = (normalizado[: cuando.start()] + normalizado[cuando.end():]).strip()
    if sobrante:
        return None  # hay más cosas en la frase: que lo interprete el modelo
    momento = jarvis_reminders.momento_de(cuando, ahora)
    hora = f"{momento.hour:02d}:{momento.minute:02d}"
    pasos = json.dumps([{"tool": "power_control", "arguments": {"action": accion}}], separators=(",", ":"))
    una_vez = dias is None
    return RoutineCreationRequest(
        name=_nombre_libre(f"{_POWER_NAMES[accion]} {hora}"),
        time=hora,
        days=str(momento.isoweekday()) if una_vez else dias,
        steps_json=pasos,
        once=una_vez,
    )


def parsear_solicitud_creacion(
    text: str,
    registry: ToolRegistry | None = None,
) -> RoutineCreationRequest | None:
    event_match = _EVENT_ROUTINE.search(text)
    if event_match is not None:
        return _parsear_rutina_evento(event_match, registry)
    game_match = _GAME_ROUTINE.search(text)
    if game_match is not None:
        return _parsear_rutina_evento(
            game_match, registry,
            DISPARADOR_JUEGO_CERRADO if (game_match.group("cerrar") or game_match.group("dejar"))
            else DISPARADOR_JUEGO_ABIERTO,
        )

    power_match = _POWER_SHORTCUT.match(text)
    if power_match is not None:
        try:
            atajo = _parsear_atajo_energia(power_match)
        except ValueError as error:
            raise ValueError(f"No pude interpretar la hora: {error}") from error
        if atajo is not None:
            return atajo

    if _CREATE_ROUTINE_INTENT.search(text) is None:
        return None

    match = _CREATE_ROUTINE_HEADER.search(text)
    if match is None:
        raise ValueError(
            "Para crear una rutina, indica un nombre y una hora, por ejemplo: "
            "«Programa una rutina llamada Inicio a las 09:00»."
        )

    name = _validate_name(match.group("name").strip(" ,.;:"))
    hour = int(match.group("hour"))
    if match.group("minute"):
        minute = int(match.group("minute"))
    else:
        minute = {"media": 30, "cuarto": 15}.get((match.group("fraction") or "").casefold(), 0)
    periodo = (match.group("period") or match.group("ampm") or "").casefold()
    if periodo in {"tarde", "noche", "pm"} and hour < 12:
        hour += 12
    elif periodo in {"mañana", "manana", "madrugada", "am"} and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        raise ValueError("La hora debe estar entre 00:00 y 23:59.")
    scheduled_time = f"{hour:02d}:{minute:02d}"

    remainder = text[match.end() :]
    automatic = _AUTOMATIC_WORDS.search(remainder) is not None
    remainder = _AUTOMATIC_WORDS.sub(" ", remainder)
    day_range = _DAY_RANGE.search(remainder)
    every_day = _EVERY_DAY.search(remainder)
    weekday_matches = list(_WEEKDAY.finditer(remainder))
    if day_range is not None:
        days = "1,2,3,4,5"
        remainder = _DAY_RANGE.sub(" ", remainder, count=1)
    elif every_day is not None:
        days = _ALL_DAYS
        remainder = _EVERY_DAY.sub(" ", remainder, count=1)
    elif weekday_matches:
        day_numbers = {
            "lunes": 1,
            "martes": 2,
            "miercoles": 3,
            "jueves": 4,
            "viernes": 5,
            "sabado": 6,
            "domingo": 7,
        }
        selected: set[int] = set()
        for day_match in weekday_matches:
            day_name = (
                day_match.group(1)
                .casefold()
                .replace("é", "e")
                .replace("á", "a")
            )
            if day_name.endswith(("bados", "ingos")):
                day_name = day_name[:-1]  # «los sábados», «los domingos»
            selected.add(day_numbers[day_name])
        days = ",".join(map(str, sorted(selected)))
        remainder = _FRASE_DIAS.sub(" ", remainder)  # también «los» de «los sábados»
        remainder = re.sub(r"\b(?:de|a)\b", " ", remainder, flags=re.IGNORECASE)
    else:
        days = _ALL_DAYS

    steps = _parsear_pasos(remainder)
    if automatic and registry is not None:
        bloqueantes = acciones_con_permiso((s["tool"] for s in steps), registry)
        if bloqueantes:
            raise _error_automatica(bloqueantes)

    return RoutineCreationRequest(
        name=name,
        time=scheduled_time,
        days=days,
        steps_json=json.dumps(steps, ensure_ascii=False, separators=(",", ":")),
        automatic=automatic,
    )


def crear_rutina(
    name: str,
    time: str,
    days: str,
    steps_json: str,
    registry: ToolRegistry,
    *,
    trigger: str = "time",
    automatic: bool = False,
    once: bool = False,
    phrases: Iterable[str] = (),
) -> int:
    """``once``: se desactiva sola tras ejecutarse (p. ej. «apaga el equipo a las 2»).
    ``phrases``: frases que activan una rutina con disparador «frase»."""
    name = _validate_name(name)
    trigger = _validate_trigger(trigger)
    if trigger == DISPARADOR_HORA:
        time = _validate_time(time)
    else:
        time = ""
    frases_json = _validate_phrases(phrases, trigger)
    days = _validate_days(days or _ALL_DAYS)
    steps_json = _validate_steps_json(steps_json, registry)
    if automatic:
        bloqueantes = acciones_con_permiso(
            (step["tool"] for step in json.loads(steps_json)), registry
        )
        if bloqueantes:
            raise _error_automatica(bloqueantes)
    permission = PERMISO_AUTOMATICA if automatic else PERMISO_PREGUNTAR
    with core.conexion_db() as connection:
        existing = connection.execute(
            "SELECT id FROM rutinas WHERE nombre = ? COLLATE NOCASE",
            (name,),
        ).fetchone()
        if existing is not None:
            raise ValueError(f"Ya existe una rutina llamada «{name}».")
        _comprobar_frases_libres(connection, frases_json, None)
        cursor = connection.execute(
            "INSERT INTO rutinas (nombre, hora, accion, activa, dias_semana, "
            "disparador, modo_permiso, una_vez, frases) VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?)",
            (name, time, steps_json, days, trigger, permission, int(bool(once)), frases_json),
        )
        return int(cursor.lastrowid)


def actualizar_rutina(
    identifier: int,
    name: str,
    time: str,
    days: str,
    steps_json: str,
    registry: ToolRegistry,
    *,
    trigger: str = "time",
    automatic: bool = False,
    once: bool = False,
    phrases: Iterable[str] = (),
) -> None:
    """Reemplaza la configuración de una rutina (formulario de edición)."""
    name = _validate_name(name)
    trigger = _validate_trigger(trigger)
    time = _validate_time(time) if trigger == DISPARADOR_HORA else ""
    days = _validate_days(days or _ALL_DAYS)
    steps_json = _validate_steps_json(steps_json, registry)
    frases_json = _validate_phrases(phrases, trigger)
    if automatic:
        bloqueantes = acciones_con_permiso((s["tool"] for s in json.loads(steps_json)), registry)
        if bloqueantes:
            raise _error_automatica(bloqueantes)
    with core.conexion_db() as connection:
        otro = connection.execute(
            "SELECT id FROM rutinas WHERE nombre = ? COLLATE NOCASE AND id <> ?",
            (name, identifier),
        ).fetchone()
        if otro is not None:
            raise ValueError(f"Ya existe otra rutina llamada «{name}».")
        _comprobar_frases_libres(connection, frases_json, identifier)
        cursor = connection.execute(
            "UPDATE rutinas SET nombre = ?, hora = ?, accion = ?, dias_semana = ?, "
            "disparador = ?, modo_permiso = ?, una_vez = ?, frases = ?, ultima_ejecucion = NULL "
            "WHERE id = ?",
            (
                name, time, steps_json, days, trigger,
                PERMISO_AUTOMATICA if automatic else PERMISO_PREGUNTAR,
                int(bool(once)), frases_json, identifier,
            ),
        )
        if cursor.rowcount != 1:
            raise ValueError("No encontré esa rutina.")


def normalizar_frase(texto: str) -> str:
    """Forma comparable de una frase: sin tildes, signos ni «Hey Jarvis» delante."""
    import unicodedata

    texto = unicodedata.normalize("NFKD", texto.casefold())
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = re.sub(r"[^a-z0-9]+", " ", texto).strip()
    return re.sub(r"^(?:(?:hey|oye|ey)\s+)?(?:jarvis\s+)?(?:por\s+favor\s+)?", "", texto).strip()


def _validate_phrases(phrases: Iterable[str], trigger: str) -> str:
    frases: list[str] = []
    vistas: set[str] = set()
    for frase in phrases or ():
        frase = " ".join(str(frase).split())
        clave = normalizar_frase(frase)
        if not clave:
            continue
        if len(clave) < 3 or len(frase) > 80:
            raise ValueError(f"La frase «{frase}» debe tener entre 3 y 80 caracteres.")
        if clave not in vistas:
            vistas.add(clave)
            frases.append(frase)
    if trigger == DISPARADOR_FRASE and not frases:
        raise ValueError("Indica al menos una frase para activar el comando.")
    if trigger != DISPARADOR_FRASE:
        frases = []
    return json.dumps(frases, ensure_ascii=False)


def _comprobar_frases_libres(connection, frases_json: str, propio: int | None) -> None:
    nuevas = {normalizar_frase(f) for f in json.loads(frases_json)}
    if not nuevas:
        return
    for identifier, name, frases in connection.execute(
        "SELECT id, nombre, frases FROM rutinas WHERE disparador = ?", (DISPARADOR_FRASE,)
    ).fetchall():
        if propio is not None and int(identifier) == propio:
            continue
        usadas = {normalizar_frase(f) for f in _leer_frases(frases)}
        repetidas = nuevas & usadas
        if repetidas:
            raise ValueError(f"La frase «{sorted(repetidas)[0]}» ya la usa la rutina «{name}».")


def _leer_frases(valor: object) -> tuple[str, ...]:
    try:
        datos = json.loads(str(valor or "[]"))
    except json.JSONDecodeError:
        return ()
    return tuple(str(f) for f in datos if isinstance(f, str)) if isinstance(datos, list) else ()


_NUMEROS_VOZ = {
    "0": "cero", "1": "uno", "2": "dos", "3": "tres", "4": "cuatro", "5": "cinco",
    "6": "seis", "7": "siete", "8": "ocho", "9": "nueve", "10": "diez",
}
# Grafías que suenan igual en español: el STT escribe «dosca» por «2K» o junta palabras
# («Modulgaming» por «modo gaming»).
_MISMO_SONIDO = (("qu", "k"), ("c", "k"), ("ka", "k"), ("z", "s"), ("v", "b"), ("h", ""), ("ll", "y"))
PARECIDO_MINIMO = 0.85
MARGEN_PARECIDO = 0.05


def clave_sonido(texto: str) -> str:
    """Forma «por sonido» de una frase: números en letras, sin espacios y con las
    letras que suenan igual unificadas («Modo gaming 2K» ≈ «Modulgaming dosca»)."""
    partes = []
    for palabra in normalizar_frase(texto).split():
        m = re.fullmatch(r"(\d+)([a-z]*)", palabra)
        if m and m.group(1) in _NUMEROS_VOZ:
            palabra = _NUMEROS_VOZ[m.group(1)] + m.group(2)
        partes.append(palabra)
    clave = "".join(partes)
    for antes, despues in _MISMO_SONIDO:
        clave = clave.replace(antes, despues)
    return clave


def elegir_por_frase(texto: str, candidatas: list[tuple[object, tuple[str, ...]]]):
    """La candidata cuya frase coincide con lo dicho: exacta o, si no hay, la única
    que se le parece mucho por sonido (el STT a veces escribe mal la frase)."""
    from difflib import SequenceMatcher

    clave = normalizar_frase(texto)
    if len(clave) < 3:
        return None
    for candidata, frases in candidatas:
        if clave in {normalizar_frase(f) for f in frases}:
            return candidata
    sonido = clave_sonido(texto)
    if len(sonido) < 6:
        return None
    puntajes = []
    for candidata, frases in candidatas:
        mejor = max((SequenceMatcher(None, sonido, clave_sonido(f)).ratio()
                     for f in frases if len(clave_sonido(f)) >= 6), default=0.0)
        puntajes.append((mejor, candidata))
    puntajes.sort(key=lambda par: par[0], reverse=True)
    if not puntajes or puntajes[0][0] < PARECIDO_MINIMO:
        return None
    if len(puntajes) > 1 and puntajes[0][0] - puntajes[1][0] < MARGEN_PARECIDO:
        return None  # dos rutinas se parecen igual: mejor no adivinar
    logging.info("Frase de rutina reconocida por parecido (%.2f).", puntajes[0][0])
    return puntajes[0][1]


_DESHACER = re.compile(
    r"^(?:desactiva|desactivar|desactivame|quita|quitar|quitame|sal|salir|salte|sali|apaga|apagar"
    r"|deshaz|deshacer|termina|terminar|cierra|cerrar|cancela|cancelar|anula|anular)"
    r"\s+(?:(?:de|del|el|la)\s+)?(?P<resto>.+)$"
)


def frase_a_deshacer(texto: str) -> str | None:
    """«Desactiva el modo gaming 2K» → «modo gaming 2k»; None si no es una orden así."""
    m = _DESHACER.fullmatch(normalizar_frase(texto))
    return m.group("resto") if m else None


def buscar_rutina_por_frase(texto: str) -> Routine | None:
    """Rutina activa cuya frase coincide con lo dicho o escrito (ver ``elegir_por_frase``)."""
    if len(normalizar_frase(texto)) < 3:
        return None
    with core.conexion_db() as connection:
        filas = connection.execute(
            "SELECT id, nombre, hora, accion, dias_semana, disparador, modo_permiso, frases "
            "FROM rutinas WHERE activa = 1 AND disparador = ? ORDER BY id",
            (DISPARADOR_FRASE,),
        ).fetchall()
        fila = elegir_por_frase(texto, [(f, _leer_frases(f[7])) for f in filas])
        if fila is None:
            return None
        frases = _leer_frases(fila[7])
        rutina = _routine_from_row(connection, fila[:7])
        if rutina is None:
            return None
        return Routine(
            rutina.identifier, rutina.name, rutina.time, rutina.days,
            rutina.steps, rutina.trigger, rutina.permission, frases,
        )


def frases_de_rutinas() -> list[str]:
    """Las frases de las rutinas activas (pista de vocabulario para el STT)."""
    with core.conexion_db() as connection:
        filas = connection.execute(
            "SELECT frases FROM rutinas WHERE activa = 1 AND disparador = ? ORDER BY id",
            (DISPARADOR_FRASE,),
        ).fetchall()
    return [frase for (valor,) in filas for frase in _leer_frases(valor)]


def _routine_from_row(connection, row) -> Routine | None:
    identifier, name, scheduled_time, actions_json, days_value, trigger, permission = row
    try:
        days = _parse_days(str(days_value))
        steps = _decode_steps(str(actions_json))
    except ValueError:
        logging.exception(
            "Se desactivó la rutina %s porque tiene una configuración inválida.",
            name,
        )
        connection.execute("UPDATE rutinas SET activa = 0 WHERE id = ?", (identifier,))
        return None
    return Routine(
        int(identifier),
        str(name),
        str(scheduled_time),
        days,
        steps,
        str(trigger),
        str(permission),
    )


def rutinas_para_evento(trigger: str) -> list[Routine]:
    """Rutinas activas asociadas a un evento (p. ej. auriculares conectados)."""
    if trigger not in DISPARADORES_EVENTO:
        raise ValueError(f"Evento desconocido: {trigger}")
    routines: list[Routine] = []
    with core.conexion_db() as connection:
        rows = connection.execute(
            "SELECT id, nombre, hora, accion, dias_semana, disparador, modo_permiso "
            "FROM rutinas WHERE activa = 1 AND disparador = ? ORDER BY id",
            (trigger,),
        ).fetchall()
        for row in rows:
            routine = _routine_from_row(connection, row)
            if routine is not None:
                routines.append(routine)
    return routines


# Margen para una rutina que no se revisó justo a su hora (Jarvis ocupado, la confirmación
# de guardado tardó…). Con 59 s, las de HH:MM salen durante su minuto como siempre y nunca
# después; las de HH:MM:SS, en el minuto siguiente a su segundo.
MARGEN_VENCIDA = timedelta(seconds=59)


def momento_programado(hora: str, ahora: datetime) -> datetime | None:
    """Fecha y hora de hoy (o de ayer, pasada la medianoche) para «HH:MM» o «HH:MM:SS»."""
    try:
        partes = [int(x) for x in hora.split(":")]
        momento = ahora.replace(hour=partes[0], minute=partes[1],
                                second=partes[2] if len(partes) > 2 else 0, microsecond=0)
    except (ValueError, IndexError):
        return None
    if momento - ahora > timedelta(hours=12):  # «23:59:50» revisado a las 00:00:20
        momento -= timedelta(days=1)
    return momento


def reclamar_rutinas_vencidas(ahora: datetime | None = None) -> list[Routine]:
    """Rutinas por hora que vencieron hace menos de ``MARGEN_VENCIDA`` y no corrieron ese día."""
    now = ahora or datetime.now().astimezone()
    minutos = {(now - timedelta(minutes=m)).strftime("%H:%M") for m in range(3)}
    due: list[Routine] = []
    with core.conexion_db() as connection:
        rows = connection.execute(
            "SELECT id, nombre, hora, accion, dias_semana, disparador, modo_permiso, ultima_ejecucion "
            "FROM rutinas WHERE activa = 1 AND disparador = 'hora' AND substr(hora, 1, 5) IN "
            f"({','.join('?' * len(minutos))}) ORDER BY id",
            tuple(sorted(minutos)),
        ).fetchall()
        for *row, ultima in rows:
            momento = momento_programado(str(row[2]), now)
            if momento is None or not timedelta(0) <= now - momento <= MARGEN_VENCIDA:
                continue
            date_key = momento.date().isoformat()
            if ultima == date_key:
                continue
            routine = _routine_from_row(connection, row)
            if routine is None or momento.isoweekday() not in routine.days:
                continue
            cursor = connection.execute(
                "UPDATE rutinas SET ultima_ejecucion = ?, "
                "activa = CASE WHEN una_vez = 1 THEN 0 ELSE activa END "
                "WHERE id = ? AND (ultima_ejecucion IS NULL OR ultima_ejecucion <> ?)",
                (date_key, routine.identifier, date_key),
            )
            if cursor.rowcount == 1:
                due.append(routine)
    return due


def listar_rutinas() -> list[tuple[int, str, str, tuple[int, ...], bool]]:
    with core.conexion_db() as connection:
        rows = connection.execute(
            "SELECT id, nombre, hora, dias_semana, activa FROM rutinas "
            "ORDER BY hora, id"
        ).fetchall()
    routines: list[tuple[int, str, str, tuple[int, ...], bool]] = []
    for identifier, name, time, days, active in rows:
        routines.append(
            (
                int(identifier),
                str(name),
                str(time),
                _parse_days(str(days)),
                bool(active),
            )
        )
    return routines


def _find_routine_id(name_or_id: str) -> int | None:
    candidate = name_or_id.strip().strip("\"'“”‘’")
    if candidate.isdecimal():
        identifier = int(candidate)
        return identifier if identifier > 0 else None
    normalized = candidate.casefold()
    return next(
        (
            identifier
            for identifier, name, _, _, _ in listar_rutinas()
            if name.casefold() == normalized
        ),
        None,
    )


def _routine_days_label(days: tuple[int, ...]) -> str:
    if days == (1, 2, 3, 4, 5, 6, 7):
        return "todos los días"
    if days == (1, 2, 3, 4, 5):
        return "de lunes a viernes"
    return ", ".join(_WEEKDAY_NAMES[day - 1] for day in days)


def listar_automatizaciones() -> list[RoutineInfo]:
    """Todas las rutinas con disparador, permiso y pasos (para el panel)."""
    with core.conexion_db() as connection:
        rows = connection.execute(
            "SELECT id, nombre, hora, dias_semana, activa, disparador, modo_permiso, accion, "
            "frases, una_vez FROM rutinas ORDER BY disparador <> 'hora', hora, id"
        ).fetchall()
    routines: list[RoutineInfo] = []
    for identifier, name, time, days, active, trigger, permission, actions, frases, una_vez in rows:
        try:
            steps = _decode_steps(str(actions))
            parsed_days = _parse_days(str(days))
        except ValueError:
            steps, parsed_days = (), (1, 2, 3, 4, 5, 6, 7)
        routines.append(
            RoutineInfo(
                int(identifier),
                str(name),
                str(time),
                parsed_days,
                bool(active),
                str(trigger),
                str(permission),
                steps,
                _leer_frases(frases),
                bool(una_vez),
            )
        )
    return routines


def cambiar_estado_rutina(identifier: int, activa: bool) -> bool:
    """Pausa o reanuda. Devuelve False si ya estaba en ese estado o no existe."""
    with core.conexion_db() as connection:
        cursor = connection.execute(
            "UPDATE rutinas SET activa = ? WHERE id = ? AND activa <> ?",
            (int(activa), identifier, int(activa)),
        )
    return cursor.rowcount == 1


def eliminar_rutina(identifier: int) -> bool:
    with core.conexion_db() as connection:
        cursor = connection.execute("DELETE FROM rutinas WHERE id = ?", (identifier,))
    return cursor.rowcount == 1


def cambiar_permiso_rutina(identifier: int, automatica: bool, registry: ToolRegistry) -> None:
    """Cambia entre preguntar y automática; rechaza automática si hay acciones de riesgo."""
    with core.conexion_db() as connection:
        row = connection.execute(
            "SELECT accion FROM rutinas WHERE id = ?", (identifier,)
        ).fetchone()
        if row is None:
            raise ValueError("No encontré esa rutina.")
        if automatica:
            steps = _decode_steps(str(row[0]))
            bloqueantes = acciones_con_permiso((step.tool for step in steps), registry)
            if bloqueantes:
                raise _error_automatica(bloqueantes)
        connection.execute(
            "UPDATE rutinas SET modo_permiso = ? WHERE id = ?",
            (PERMISO_AUTOMATICA if automatica else PERMISO_PREGUNTAR, identifier),
        )


def requiere_cuenta_atras(steps) -> bool:
    """Rutinas que apagan, reinician o suspenden: en vez de pedir aprobación
    (nadie suele estar delante), avisan con una cuenta atrás cancelable."""
    return any(
        (step.tool if isinstance(step, RoutineStep) else step.get("tool")) == "power_control"
        for step in steps
    )


def puede_ejecutarse_sin_preguntar(routine: Routine, registry: ToolRegistry) -> bool:
    """Se vuelve a comprobar en cada ejecución por si cambió el riesgo de una herramienta."""
    if routine.permission != PERMISO_AUTOMATICA:
        return False
    try:
        return not acciones_con_permiso((step.tool for step in routine.steps), registry)
    except ToolError:
        return False


def _format_routines(routines: list[RoutineInfo]) -> str:
    if not routines:
        return "Todavía no hay rutinas guardadas."
    def permiso(routine: RoutineInfo) -> str:
        if requiere_cuenta_atras(routine.steps):
            return "avisa con cuenta atrás"
        return "automática" if routine.permission == PERMISO_AUTOMATICA else "pregunta antes"

    lines = [
        f"{routine.identifier}. {routine.name} — "
        f"{describir_disparador(routine.trigger, routine.time, routine.days, routine.phrases)}, "
        f"{permiso(routine)} "
        f"({'activa' if routine.active else 'pausada'})"
        for routine in routines
    ]
    return "Rutinas y automatizaciones:\n" + "\n".join(lines)


def procesar_comando(text: str) -> str | None:
    command = text.strip()
    if _LIST_PATTERN.fullmatch(command):
        return _format_routines(listar_automatizaciones())

    match = _ROUTINE_ACTION_PATTERN.fullmatch(command)
    if match is None:
        return None

    action = match.group("action").casefold()
    identifier = _find_routine_id(match.group("name"))
    if identifier is None:
        return f"No encontré la rutina «{match.group('name').strip()}»."

    if action in {"pausa", "pausar", "desactiva", "desactivar"}:
        return (
            "Rutina pausada."
            if cambiar_estado_rutina(identifier, False)
            else "La rutina ya estaba pausada."
        )
    if action in {"reanuda", "reanudar", "activa", "activar"}:
        return (
            "Rutina reanudada."
            if cambiar_estado_rutina(identifier, True)
            else "La rutina ya estaba activa."
        )
    return "Rutina eliminada." if eliminar_rutina(identifier) else "No encontré esa rutina."


def registrar_herramienta(
    registry: ToolRegistry,
    host: RoutineHost,
    verifier: Verificador | None = None,
) -> None:
    registry.register(
        "schedule_routine",
        (
            "Guarda una rutina con hasta cinco herramientas registradas. "
            "trigger indica cuándo se ejecuta: time (a una hora; usa time en formato "
            "HH:MM de 24 horas y days con números del 1 (lunes) al 7 (domingo) "
            "separados por comas), headphones_connected (al conectar los auriculares) "
            "headphones_disconnected (al desconectarlos), game_started (al abrir un juego) "
            "o game_stopped (al cerrar el último juego). "
            "steps_json debe ser una cadena JSON como "
            "'[{\"tool\":\"set_system_volume\",\"arguments\":{\"percentage\":30}}]'. "
            "automatic=true solo si el usuario pide que se ejecute sin preguntar; "
            "únicamente se permite si todas las acciones son de riesgo bajo. "
            "once=true para que se ejecute una sola vez (una hora concreta, no recurrente). "
            "Si incluye power_control, al llegar la hora avisa con una cuenta atrás "
            "cancelable en vez de pedir aprobación. "
            "Solo se ejecuta mientras Jarvis esté abierto."
        ),
        {
            "type": "object",
            "properties": {
                "name": {"type": "string", "minLength": 2, "maxLength": 80},
                "time": {"type": "string", "minLength": 5, "maxLength": 5},
                "days": {"type": "string", "minLength": 1, "maxLength": 13},
                "steps_json": {
                    "type": "string",
                    "minLength": 2,
                    "maxLength": 6000,
                },
                "trigger": {
                    "type": "string",
                    "enum": sorted(_TRIGGER_FROM_TOOL),
                },
                "automatic": {"type": "boolean"},
                "once": {"type": "boolean"},
            },
            "required": ["name", "steps_json"],
            "additionalProperties": False,
        },
        host._tool_schedule_routine,
        risk=ToolRisk.MEDIUM,
        requires_confirmation=True,
        argument_validators={
            "name": _validate_name,
            "time": _validate_time,
            "days": _validate_days,
            "steps_json": lambda value: _validate_steps_json(value, registry),
        },
        label="Programar rutina",
        timeout_seconds=10,
        verifier=verifier,
    )


def herramienta_definicion(registry: ToolRegistry, step: RoutineStep) -> ToolDefinition:
    return registry.get(step.tool)
