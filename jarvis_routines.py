import json
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import jarvis_core as core
from jarvis_actions import Verificador
from jarvis_commands import normalizar_url_web
from jarvis_tool_registry import ToolDefinition, ToolError, ToolRegistry, ToolRisk

# Disparadores (valores guardados en rutinas.disparador)
DISPARADOR_HORA = "hora"
DISPARADOR_AURICULARES_CONECTADOS = "auriculares_conectados"
DISPARADOR_AURICULARES_DESCONECTADOS = "auriculares_desconectados"
DISPARADORES_EVENTO = frozenset(
    {DISPARADOR_AURICULARES_CONECTADOS, DISPARADOR_AURICULARES_DESCONECTADOS}
)
# Valores que usa la herramienta schedule_routine (en inglés, como el resto de su esquema)
_TRIGGER_FROM_TOOL = {
    "time": DISPARADOR_HORA,
    "headphones_connected": DISPARADOR_AURICULARES_CONECTADOS,
    "headphones_disconnected": DISPARADOR_AURICULARES_DESCONECTADOS,
}
_TRIGGER_TO_TOOL = {value: key for key, value in _TRIGGER_FROM_TOOL.items()}
_TRIGGER_LABELS = {
    DISPARADOR_AURICULARES_CONECTADOS: "al conectar los auriculares",
    DISPARADOR_AURICULARES_DESCONECTADOS: "al desconectar los auriculares",
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
        "open_new_browser_tab",
        "navigate_browser",
        "launch_game",
        "manage_window",
        "search_youtube",
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
    r"(?P<name>.+?)\s+a\s+las\s+"
    r"(?P<hour>\d{1,2})(?:\s*(?::|\.|y)\s*|\s+)(?P<minute>\d{1,2})\b",
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
    r"\b(lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo)\b",
    re.IGNORECASE,
)
_ROUTINE_STEP = re.compile(
    r"(?P<volume>"
    r"(?:sube|subir|aumenta|aumentar|baja|bajar|reduce|reducir|"
    r"ajusta|ajustar|establece|establecer|pon|poner)"
    r"\s+(?:el\s+)?volumen(?:\s+del\s+sistema)?\s+(?:al|a|en)\s+"
    r"(?P<percentage>\d{1,3})\s*(?:%|por\s+ciento|porciento))"
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


@dataclass(frozen=True, slots=True)
class RoutineCreationRequest:
    name: str
    time: str
    days: str
    steps_json: str
    trigger: str = "time"
    automatic: bool = False

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
    if not isinstance(value, str) or re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value) is None:
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
    if not isinstance(steps, list) or not 1 <= len(steps) <= 5:
        raise ValueError("la rutina debe tener entre 1 y 5 pasos.")

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
    if not isinstance(payload, list) or not 1 <= len(payload) <= 5:
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
        if value == DISPARADOR_HORA or value in DISPARADORES_EVENTO:
            return value
    raise ValueError(
        "el disparador debe ser time, headphones_connected o headphones_disconnected."
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
        "bajo (volumen, salida de audio, audio de Jarvis, abrir pestaña) pueden "
        "ejecutarse sin preguntar."
    )


def describir_disparador(trigger: str, time: str, days: tuple[int, ...]) -> str:
    if trigger in _TRIGGER_LABELS:
        return _TRIGGER_LABELS[trigger]
    return f"a las {time}, {_routine_days_label(days)}"


def _event_routine_name(trigger: str) -> str:
    base = "Al conectar auriculares" if trigger == DISPARADOR_AURICULARES_CONECTADOS else (
        "Al desconectar auriculares"
    )
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
        if step_match.group("volume") is not None:
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
            "No reconocí acciones compatibles. Por ahora puedes pedir ajustar "
            "el volumen, abrir una pestaña o navegar a una dirección web."
        )
    if _ACTION_SEPARATOR.fullmatch(remainder[cursor:]) is None:
        raise ValueError(
            f"No pude interpretar esta parte de la rutina: «{remainder[cursor:].strip()}»."
        )
    return steps


def _parsear_rutina_evento(
    match: re.Match[str],
    registry: ToolRegistry | None,
) -> RoutineCreationRequest:
    event = match.group("event").casefold()
    trigger = (
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


def parsear_solicitud_creacion(
    text: str,
    registry: ToolRegistry | None = None,
) -> RoutineCreationRequest | None:
    event_match = _EVENT_ROUTINE.search(text)
    if event_match is not None:
        return _parsear_rutina_evento(event_match, registry)

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
    minute = int(match.group("minute"))
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
            selected.add(day_numbers[day_name])
        days = ",".join(map(str, sorted(selected)))
        remainder = _WEEKDAY.sub(" ", remainder)
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
) -> int:
    name = _validate_name(name)
    trigger = _validate_trigger(trigger)
    if trigger == DISPARADOR_HORA:
        time = _validate_time(time)
    else:
        time = ""
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
        cursor = connection.execute(
            "INSERT INTO rutinas (nombre, hora, accion, activa, dias_semana, "
            "disparador, modo_permiso) VALUES (?, ?, ?, 1, ?, ?, ?)",
            (name, time, steps_json, days, trigger, permission),
        )
        return int(cursor.lastrowid)


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


def reclamar_rutinas_vencidas(ahora: datetime | None = None) -> list[Routine]:
    now = ahora or datetime.now().astimezone()
    date_key = now.date().isoformat()
    minute = now.strftime("%H:%M")
    weekday = now.isoweekday()
    due: list[Routine] = []
    with core.conexion_db() as connection:
        rows = connection.execute(
            "SELECT id, nombre, hora, accion, dias_semana, disparador, modo_permiso "
            "FROM rutinas WHERE activa = 1 AND disparador = 'hora' AND hora = ? "
            "AND (ultima_ejecucion IS NULL OR ultima_ejecucion <> ?) "
            "ORDER BY id",
            (minute, date_key),
        ).fetchall()
        for row in rows:
            routine = _routine_from_row(connection, row)
            if routine is None or weekday not in routine.days:
                continue
            cursor = connection.execute(
                "UPDATE rutinas SET ultima_ejecucion = ? WHERE id = ? "
                "AND (ultima_ejecucion IS NULL OR ultima_ejecucion <> ?)",
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
            "SELECT id, nombre, hora, dias_semana, activa, disparador, modo_permiso, accion "
            "FROM rutinas ORDER BY disparador <> 'hora', hora, id"
        ).fetchall()
    routines: list[RoutineInfo] = []
    for identifier, name, time, days, active, trigger, permission, actions in rows:
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
    lines = [
        f"{routine.identifier}. {routine.name} — "
        f"{describir_disparador(routine.trigger, routine.time, routine.days)}, "
        f"{'automática' if routine.permission == PERMISO_AUTOMATICA else 'pregunta antes'} "
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
            "o headphones_disconnected (al desconectarlos). "
            "steps_json debe ser una cadena JSON como "
            "'[{\"tool\":\"set_system_volume\",\"arguments\":{\"percentage\":30}}]'. "
            "automatic=true solo si el usuario pide que se ejecute sin preguntar; "
            "únicamente se permite si todas las acciones son de riesgo bajo. "
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
