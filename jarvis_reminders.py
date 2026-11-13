"""Temporizadores, alarmas y recordatorios (tabla ``recordatorios``).

- Interpretación de frases en español: «pon un temporizador de 10 minutos»,
  «recuérdame en 20 minutos sacar la ropa», «recuérdame mañana a las 9 pagar
  la luz», «pon una alarma a las 7», «¿qué recordatorios tengo?», «cancela el
  temporizador».
- ``VigilanteRecordatorios`` (hilo) revisa cada segundo los vencidos, los marca
  como avisados de forma atómica y publica ``("reminder_due", recordatorio,
  atrasado)``; la app avisa con sonido, texto y voz.

Los textos de los recordatorios son datos personales: se guardan en la base de
datos (como la memoria) pero no se escriben en los registros.
"""

import logging
import math
import os
import queue
import re
import struct
import tempfile
import threading
import unicodedata
import wave
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

import jarvis_core as core

TIPOS = {"timer": "temporizador", "reminder": "recordatorio", "alarm": "alarma"}
MAXIMO = timedelta(days=60)
FORMATO = "%Y-%m-%d %H:%M:%S"
ATRASO_AVISABLE = timedelta(minutes=1)


@dataclass(frozen=True)
class Recordatorio:
    id: int
    tipo: str  # temporizador | recordatorio | alarma
    texto: str
    vence: datetime
    creado: datetime | None = None


# --- Base de datos -------------------------------------------------------------


def crear(tipo: str, texto: str, vence: datetime, *, ahora: datetime | None = None) -> Recordatorio:
    ahora = ahora or datetime.now()
    if tipo not in TIPOS.values():
        raise ValueError(f"Tipo de recordatorio no válido: {tipo}")
    if vence <= ahora:
        raise ValueError("La hora indicada ya pasó.")
    if vence - ahora > MAXIMO:
        raise ValueError("Solo puedo programar avisos para los próximos 60 días.")
    texto = " ".join(texto.split())[:200]
    with core.conexion_db() as conexion:
        cursor = conexion.execute(
            "INSERT INTO recordatorios (tipo, texto, vence, creado) VALUES (?, ?, ?, ?)",
            (tipo, texto, vence.strftime(FORMATO), ahora.strftime(FORMATO)),
        )
        identificador = int(cursor.lastrowid)
    logging.info("Programado %s %s para %s.", tipo, identificador, vence.strftime(FORMATO))
    return Recordatorio(identificador, tipo, texto, vence.replace(microsecond=0), ahora)


def _fila(fila) -> Recordatorio:
    return Recordatorio(
        int(fila[0]),
        str(fila[1]),
        str(fila[2]),
        datetime.strptime(fila[3], FORMATO),
        datetime.strptime(fila[4], FORMATO) if fila[4] else None,
    )


def pendientes() -> list[Recordatorio]:
    with core.conexion_db() as conexion:
        filas = conexion.execute(
            "SELECT id, tipo, texto, vence, creado FROM recordatorios "
            "WHERE estado = 'pendiente' ORDER BY vence, id"
        ).fetchall()
    return [_fila(f) for f in filas]


def cancelar(
    *, identificador: int | None = None, tipo: str | None = None, todos: bool = False
) -> list[Recordatorio]:
    """Cancela por id, el próximo de un tipo, o todos (opcionalmente de un tipo)."""
    lista = pendientes()
    if tipo is not None:
        lista = [r for r in lista if r.tipo == tipo]
    if identificador is not None:
        lista = [r for r in lista if r.id == identificador]
    elif not todos:
        lista = lista[:1]
    if not lista:
        return []
    with core.conexion_db() as conexion:
        conexion.executemany(
            "UPDATE recordatorios SET estado = 'cancelado' WHERE id = ? AND estado = 'pendiente'",
            [(r.id,) for r in lista],
        )
    logging.info("Cancelados %s aviso(s): %s.", len(lista), [r.id for r in lista])
    return lista


def reclamar_vencidos(ahora: datetime | None = None) -> list[Recordatorio]:
    """Marca como avisados los vencidos y los devuelve (cada uno una sola vez)."""
    ahora = ahora or datetime.now()
    with core.conexion_db() as conexion:
        filas = conexion.execute(
            "SELECT id, tipo, texto, vence, creado FROM recordatorios "
            "WHERE estado = 'pendiente' AND vence <= ? ORDER BY vence, id",
            (ahora.strftime(FORMATO),),
        ).fetchall()
        reclamados = []
        for fila in filas:
            cursor = conexion.execute(
                "UPDATE recordatorios SET estado = 'avisado' WHERE id = ? AND estado = 'pendiente'",
                (fila[0],),
            )
            if cursor.rowcount == 1:
                reclamados.append(_fila(fila))
    return reclamados


# --- Textos ---------------------------------------------------------------------


def _plural(n: int, singular: str, plural: str) -> str:
    return f"{n} {singular if n == 1 else plural}"


def describir_duracion(segundos: float) -> str:
    segundos = max(0, int(round(segundos)))
    horas, resto = divmod(segundos, 3600)
    minutos, segundos = divmod(resto, 60)
    partes = []
    if horas:
        partes.append(_plural(horas, "hora", "horas"))
    if minutos:
        partes.append(_plural(minutos, "minuto", "minutos"))
    if segundos and not horas:
        partes.append(_plural(segundos, "segundo", "segundos"))
    if not partes:
        return "menos de un segundo"
    return " y ".join([", ".join(partes[:-1]), partes[-1]] if len(partes) > 1 else partes)


def describir_momento(vence: datetime, ahora: datetime) -> str:
    hora = f"{vence.hour}:{vence.minute:02d}"
    articulo = "la" if vence.hour == 1 else "las"
    dias = (vence.date() - ahora.date()).days
    if dias == 0:
        return f"hoy a {articulo} {hora}"
    if dias == 1:
        return f"mañana a {articulo} {hora}"
    from jarvis_system_info import DIAS_SEMANA, MESES

    return (
        f"el {DIAS_SEMANA[vence.weekday()]} {vence.day} de {MESES[vence.month - 1]} "
        f"a {articulo} {hora}"
    )


def describir_creado(r: Recordatorio, ahora: datetime) -> str:
    cuando = describir_momento(r.vence, ahora)
    if r.tipo == "temporizador":
        duracion = describir_duracion((r.vence - ahora).total_seconds())
        extra = f" ({r.texto})" if r.texto else ""
        return f"Temporizador de {duracion}{extra} en marcha. Te aviso {cuando}."
    if r.tipo == "alarma":
        extra = f": {r.texto}" if r.texto else "."
        return f"Alarma programada para {cuando}{extra}"
    return f"Te lo recordaré {cuando}: {r.texto}"


def describir_lista(lista: list[Recordatorio], ahora: datetime) -> str:
    if not lista:
        return "No tienes temporizadores, alarmas ni recordatorios pendientes."
    lineas = []
    for r in lista:
        falta = describir_duracion((r.vence - ahora).total_seconds())
        detalle = f" — {r.texto}" if r.texto else ""
        lineas.append(
            f"{r.id}. {r.tipo.capitalize()} {describir_momento(r.vence, ahora)} "
            f"(faltan {falta}){detalle}"
        )
    encabezado = "Tienes 1 aviso pendiente:" if len(lista) == 1 else f"Tienes {len(lista)} avisos pendientes:"
    return encabezado + "\n" + "\n".join(lineas)


def describir_aviso(r: Recordatorio, ahora: datetime) -> str:
    atraso = ahora - r.vence
    atrasado = (
        f" (era {describir_momento(r.vence, ahora)}; Jarvis no estaba abierto)"
        if atraso > ATRASO_AVISABLE
        else ""
    )
    if r.tipo == "temporizador":
        base = "Se acabó el temporizador"
        return base + (f" de {r.texto}" if r.texto else "") + f".{atrasado}"
    if r.tipo == "alarma":
        return "Alarma" + (f": {r.texto}" if r.texto else "") + f".{atrasado}"
    return f"Recordatorio: {r.texto}.{atrasado}"


# --- Interpretación de frases -----------------------------------------------------

_NUMEROS = {
    "un": 1, "uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
    "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "once": 11,
    "doce": 12, "trece": 13, "catorce": 14, "quince": 15, "dieciseis": 16,
    "diecisiete": 17, "dieciocho": 18, "diecinueve": 19, "veinte": 20,
    "veintiun": 21, "veintiuno": 21, "veintidos": 22, "veintitres": 23,
    "veinticuatro": 24, "veinticinco": 25, "veintiseis": 26, "veintisiete": 27,
    "veintiocho": 28, "veintinueve": 29, "treinta": 30, "cuarenta": 40,
    "cincuenta": 50, "sesenta": 60, "noventa": 90, "cien": 100,
}
_PAL = "|".join(sorted(_NUMEROS, key=len, reverse=True))
_UNIDADES_PAL = "un|uno|una|dos|tres|cuatro|cinco|seis|siete|ocho|nueve"
_NUM = rf"(?:\d+|(?:{_PAL})(?:\s+y\s+(?:{_UNIDADES_PAL}))?)"
_UNIDAD = r"(?:segundos?|segs?|minutos?|mins?|horas?|hs)"
_PARTE = (
    rf"(?:{_NUM}\s+{_UNIDAD}(?:\s+y\s+medi[ao])?|media\s+hora|un\s+cuarto\s+de\s+hora"
    rf"|hora\s+y\s+media)"
)
DURACION = rf"(?:{_PARTE}(?:\s*(?:y\s+)?{_PARTE})*)"

_HORA = rf"(?:\d{{1,2}}|{_PAL})"
CUANDO_ABS = (
    r"(?:(?P<dia>hoy|manana|pasado\s+manana)\s+)?"
    rf"a\s+las?\s+(?P<h>{_HORA})"
    r"(?:\s+(?P<m>\d{2})|\s+y\s+(?P<frac>media|cuarto|\d{1,2}))?"
    r"(?:\s+(?:de\s+la\s+|en\s+punto\s+de\s+la\s+)?(?P<franja>manana|tarde|noche|madrugada|am|pm)"
    r"|\s+(?:hs|horas|en\s+punto))?"
    r"(?:\s+(?P<dia2>de\s+hoy|de\s+manana|manana))?"
)


def _numero(texto: str) -> int:
    texto = texto.strip()
    if texto.isdigit():
        return int(texto)
    partes = re.split(r"\s+y\s+", texto)
    return sum(_NUMEROS[p] for p in partes)


def segundos_de(duracion: str) -> int:
    """Convierte una duración ya normalizada («1 hora y media», «10 minutos»…)."""
    total = 0.0
    for parte in re.finditer(_PARTE, duracion):
        frase = parte.group(0)
        if frase == "media hora":
            total += 1800
        elif frase == "un cuarto de hora":
            total += 900
        elif frase == "hora y media":
            total += 5400
        else:
            m = re.fullmatch(rf"({_NUM})\s+({_UNIDAD})(\s+y\s+medi[ao])?", frase)
            if m is None:
                continue
            valor = _numero(m.group(1)) + (0.5 if m.group(3) else 0)
            unidad = m.group(2)
            factor = 3600 if unidad.startswith("h") else 60 if unidad.startswith("m") else 1
            total += valor * factor
    return int(total)


def momento_de(m: re.Match, ahora: datetime) -> datetime:
    """Convierte la coincidencia de ``CUANDO_ABS`` en una fecha y hora futura."""
    hora = _numero(m.group("h"))
    minuto = 0
    if m.group("m"):
        minuto = int(m.group("m"))
    elif m.group("frac") == "media":
        minuto = 30
    elif m.group("frac") == "cuarto":
        minuto = 15
    elif m.group("frac"):
        minuto = int(m.group("frac"))
    if hora > 23 or minuto > 59:
        raise ValueError("Esa hora no existe.")
    franja = m.group("franja")
    if franja in {"tarde", "noche", "pm"} and hora < 12:
        hora += 12
    elif franja in {"manana", "madrugada", "am"} and hora == 12:
        hora = 0
    dia = m.group("dia") or (m.group("dia2") or "").replace("de ", "") or None
    base = ahora.replace(hour=hora, minute=minuto, second=0, microsecond=0)
    if dia == "manana":
        return base + timedelta(days=1)
    if dia and dia.startswith("pasado"):
        return base + timedelta(days=2)
    if dia == "hoy":
        return base
    # Sin día: la próxima vez que llegue esa hora. «a las 6» a las 13:42 son las 18:00.
    candidatos = [base]
    if franja is None and 1 <= hora <= 12:
        candidatos.append(base + timedelta(hours=12) if hora < 12 else base - timedelta(hours=12))
    futuros = [c for c in candidatos if c > ahora]
    if futuros:
        return min(futuros)
    return min(candidatos) + timedelta(days=1)


def _normalizar_con_mapa(texto: str) -> tuple[str, list[int]]:
    """Como la normalización de comandos, pero recordando de qué carácter del
    original sale cada carácter, para recuperar el texto con tildes."""
    salida: list[str] = []
    mapa: list[int] = []
    for indice, caracter in enumerate(texto):
        for c in unicodedata.normalize("NFKD", caracter.casefold()):
            if unicodedata.combining(c):
                continue
            if not re.match(r"[a-z0-9]", c):
                c = " "
            if c == " " and (not salida or salida[-1] == " "):
                continue
            salida.append(c)
            mapa.append(indice)
    while salida and salida[-1] == " ":
        salida.pop()
        mapa.pop()
    normalizado = "".join(salida)
    prefijo = re.match(r"^(?:(?:oye\s+)?jarvis|por\s+favor)\s+", normalizado)
    if prefijo:
        corte = prefijo.end()
        normalizado, mapa = normalizado[corte:], mapa[corte:]
    return normalizado, mapa


def _original(texto: str, normalizado: str, mapa: list[int], inicio: int, fin: int) -> str:
    if inicio >= fin:
        return ""
    trozo = texto[mapa[inicio] : mapa[fin - 1] + 1]
    return trozo.strip(" ,.;:¡!¿?").strip()


_VERBO_TEMPORIZADOR = (
    r"(?:pon|ponme|poneme|poner|crea|crear|programa|programar|inicia|iniciar|activa|activar"
    r"|empieza|arranca)"
)
_TEMPORIZADOR = re.compile(
    rf"^{_VERBO_TEMPORIZADOR}\s+(?:un\s+|una\s+)?(?:temporizador|timer|cronometro|cuenta\s+regresiva)"
    rf"\s+(?:de\s+|por\s+|para\s+)?(?P<dur>{DURACION})"
    r"(?:\s+(?:para|que\s+diga|llamado|de)\s+(?P<texto>.+))?$"
    rf"|^(?:temporizador|timer)\s+(?:de\s+)?(?P<dur2>{DURACION})$"
    rf"|^(?:avisame|despertame|despiertame)\s+(?:en|dentro\s+de)\s+(?P<dur3>{DURACION})$"
)
_ALARMA = re.compile(
    rf"^(?:{_VERBO_TEMPORIZADOR}\s+(?:una\s+)?alarma\s+(?:para\s+)?|(?:despiertame|despertame)\s+)"
    rf"(?P<cuando>{CUANDO_ABS})(?:\s+(?:para|que\s+diga)\s+(?P<texto>.+))?$"
)
_VERBO_RECORDAR = r"(?:recuerdame|recordame|acuerdame|avisame)"
_CUANDO = rf"(?:(?:en|dentro\s+de)\s+(?P<dur>{DURACION})|(?P<abs>{CUANDO_ABS}))"
_RECORDATORIO_FIN = re.compile(
    rf"^{_VERBO_RECORDAR}\s+(?:que\s+|de\s+|para\s+)?(?P<texto>.+?)\s+{_CUANDO}$"
)
_RECORDATORIO_INICIO = re.compile(
    rf"^{_VERBO_RECORDAR}\s+{_CUANDO}\s+(?:que\s+|de\s+|para\s+)?(?P<texto>.+)$"
)
_LISTAR = re.compile(
    r"^(?:que|cuales|cuantos|cuantas)\s+(?:son\s+(?:mis\s+)?)?(?:recordatorios|temporizadores|alarmas|avisos)"
    r"(?:\s+(?:tengo|hay))?(?:\s+(?:pendientes|activos|activas|programados|programadas))?$"
    r"|^(?:mis|lista\s+de|muestrame\s+(?:mis\s+|los\s+)?)\s*(?:recordatorios|temporizadores|alarmas)$"
    r"|^cuanto\s+(?:falta|queda|le\s+queda)(?:\s+(?:al|del|para\s+el)\s+(?:temporizador|timer))?$"
)
_TIPO_PAL = {
    "temporizador": "temporizador", "timer": "temporizador", "temporizadores": "temporizador",
    "alarma": "alarma", "alarmas": "alarma",
    "recordatorio": "recordatorio", "recordatorios": "recordatorio",
}
_CANCELAR = re.compile(
    r"^(?:cancela|cancelar|borra|borrar|elimina|eliminar|quita|quitar|para|parar|deten|detener)\s+"
    r"(?P<todos>todos\s+los\s+|todas\s+las\s+)?(?:el\s+|la\s+|los\s+|las\s+|mi\s+|mis\s+)?"
    r"(?P<tipo>temporizadores|temporizador|timer|alarmas|alarma|recordatorios|recordatorio)"
    r"(?:\s+(?:numero\s+)?(?P<id>\d+))?$"
)


def detectar_comando(texto: str, ahora: datetime | None = None) -> tuple[str, dict[str, object]] | None:
    """Devuelve ``(herramienta, argumentos)`` para temporizadores y recordatorios."""
    ahora = ahora or datetime.now()
    normalizado, mapa = _normalizar_con_mapa(texto)
    if not normalizado:
        return None

    if (m := _LISTAR.fullmatch(normalizado)) is not None:
        return "list_reminders", {}

    if (m := _CANCELAR.fullmatch(normalizado)) is not None:
        argumentos: dict[str, object] = {"kind": _clave_tipo(_TIPO_PAL[m.group("tipo")])}
        if m.group("id"):
            argumentos["id"] = int(m.group("id"))
        if m.group("todos") or m.group("tipo") in {"temporizadores", "alarmas", "recordatorios"}:
            argumentos["all"] = True
        return "cancel_reminder", argumentos

    if (m := _TEMPORIZADOR.fullmatch(normalizado)) is not None:
        grupo = next(g for g in ("dur", "dur2", "dur3") if m.group(g))
        segundos = segundos_de(m.group(grupo))
        if segundos <= 0:
            return None
        texto_aviso = ""
        if m.group("texto"):
            texto_aviso = _original(texto, normalizado, mapa, m.start("texto"), m.end("texto"))
        return "set_reminder", {"kind": "timer", "text": texto_aviso, "delay_seconds": segundos}

    if (m := _ALARMA.fullmatch(normalizado)) is not None:
        try:
            vence = momento_de(re.fullmatch(CUANDO_ABS, m.group("cuando")), ahora)
        except (ValueError, KeyError):
            return None
        texto_aviso = ""
        if m.group("texto"):
            texto_aviso = _original(texto, normalizado, mapa, m.start("texto"), m.end("texto"))
        return "set_reminder", {"kind": "alarm", "text": texto_aviso, "at": vence.strftime("%Y-%m-%d %H:%M")}

    for patron in (_RECORDATORIO_FIN, _RECORDATORIO_INICIO):
        m = patron.fullmatch(normalizado)
        if m is None:
            continue
        texto_aviso = _original(texto, normalizado, mapa, m.start("texto"), m.end("texto"))
        texto_aviso = re.sub(r"^(?:que|de|para)\s+", "", texto_aviso, flags=re.IGNORECASE)
        if not texto_aviso:
            return None
        if m.group("dur"):
            segundos = segundos_de(m.group("dur"))
            if segundos <= 0:
                return None
            return "set_reminder", {"kind": "reminder", "text": texto_aviso, "delay_seconds": segundos}
        try:
            vence = momento_de(re.fullmatch(CUANDO_ABS, m.group("abs")), ahora)
        except (ValueError, KeyError):
            return None
        return "set_reminder", {"kind": "reminder", "text": texto_aviso, "at": vence.strftime("%Y-%m-%d %H:%M")}
    return None


def _clave_tipo(tipo: str) -> str:
    return {v: k for k, v in TIPOS.items()}[tipo]


# --- Herramientas -------------------------------------------------------------------


def herramienta_crear(
    kind: str,
    text: str = "",
    delay_seconds: int | None = None,
    at: str | None = None,
    *,
    ahora: datetime | None = None,
) -> str:
    # Segundos enteros: la BD no guarda microsegundos y el texto diría «9 min 59 s».
    ahora = (ahora or datetime.now()).replace(microsecond=0)
    if (delay_seconds is None) == (at is None):
        raise ValueError("Indica delay_seconds o at, pero no ambos.")
    if delay_seconds is not None:
        if delay_seconds < 1:
            raise ValueError("La duración debe ser de al menos un segundo.")
        vence = ahora + timedelta(seconds=int(delay_seconds))
    else:
        try:
            vence = datetime.strptime(str(at).strip(), "%Y-%m-%d %H:%M")
        except ValueError as error:
            raise ValueError("La fecha debe tener el formato AAAA-MM-DD HH:MM.") from error
    tipo = TIPOS.get(kind)
    if tipo is None:
        raise ValueError("kind debe ser timer, reminder o alarm.")
    if tipo == "recordatorio" and not text.strip():
        raise ValueError("Falta qué quieres que te recuerde.")
    return describir_creado(crear(tipo, text, vence, ahora=ahora), ahora)


def herramienta_listar() -> str:
    return describir_lista(pendientes(), datetime.now())


def herramienta_cancelar(
    kind: str | None = None, id: int | None = None, all: bool = False  # noqa: A002
) -> str:
    tipo = TIPOS.get(kind) if kind else None
    cancelados = cancelar(identificador=id, tipo=tipo, todos=all)
    if not cancelados:
        que = f"{tipo}s" if tipo else "avisos"
        return f"No hay {que} pendientes que cancelar." if id is None else f"No encuentro el aviso {id}."
    if len(cancelados) == 1:
        r = cancelados[0]
        detalle = f" ({r.texto})" if r.texto else ""
        return f"Cancelé el {r.tipo}{detalle} de {describir_momento(r.vence, datetime.now())}."
    return f"Cancelé {len(cancelados)} avisos."


def registrar_herramientas(registry, verificador=None) -> None:
    registry.register(
        "set_reminder",
        (
            "Programa un temporizador (kind=timer), una alarma (alarm) o un recordatorio "
            "(reminder, con text obligatorio). Usa delay_seconds para «dentro de X» o at "
            "(AAAA-MM-DD HH:MM, hora local) para una hora concreta; nunca ambos. "
            "Solo avisa: NO apaga, suspende ni reinicia el equipo. Para «apaga/suspende/reinicia "
            "el equipo dentro de X» usa schedule_routine con power_control y once=true, y nunca "
            "digas que el equipo se apagará si solo programaste un aviso."
        ),
        {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["timer", "reminder", "alarm"]},
                "text": {"type": "string", "maxLength": 200},
                "delay_seconds": {"type": "integer", "minimum": 1, "maximum": 5184000},
                "at": {"type": "string", "minLength": 16, "maxLength": 16},
            },
            "required": ["kind"],
            "additionalProperties": False,
        },
        herramienta_crear,
        label="Programar aviso",
        timeout_seconds=10,
        verifier=verificador,
    )
    registry.register(
        "list_reminders",
        "Solo lectura. Lista los temporizadores, alarmas y recordatorios pendientes.",
        {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        herramienta_listar,
        label="Consultar avisos pendientes",
        timeout_seconds=10,
        record_result=False,
    )
    registry.register(
        "cancel_reminder",
        (
            "Cancela avisos pendientes: por id, el próximo de un tipo (kind) o todos "
            "(all=true, opcionalmente solo de un kind)."
        ),
        {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["timer", "reminder", "alarm"]},
                "id": {"type": "integer", "minimum": 1},
                "all": {"type": "boolean"},
            },
            "required": [],
            "additionalProperties": False,
        },
        herramienta_cancelar,
        label="Cancelar aviso",
        timeout_seconds=10,
    )


# --- Aviso ----------------------------------------------------------------------------

_ruta_alarma: str | None = None


def generar_alarma(ruta: str, *, volumen: float = 0.4) -> None:
    """Tres pitidos dobles (~2 s) para que se oiga aunque no mires la pantalla."""
    frecuencia = 22050
    muestras: list[int] = []

    def tono(hz: float, segundos: float) -> None:
        total = int(frecuencia * segundos)
        fundido = int(frecuencia * 0.008)
        for i in range(total):
            env = min(1.0, i / fundido, (total - i) / fundido)
            muestras.append(int(math.sin(2 * math.pi * hz * i / frecuencia) * volumen * env * 32767))

    def silencio(segundos: float) -> None:
        muestras.extend([0] * int(frecuencia * segundos))

    for _ in range(3):
        tono(988, 0.12)
        silencio(0.06)
        tono(1319, 0.16)
        silencio(0.35)
    with wave.open(ruta, "wb") as archivo:
        archivo.setnchannels(1)
        archivo.setsampwidth(2)
        archivo.setframerate(frecuencia)
        archivo.writeframes(struct.pack(f"<{len(muestras)}h", *muestras))


def reproducir_alarma() -> None:
    """Suena en la salida predeterminada y espera a que termine (~2 s)."""
    global _ruta_alarma
    import winsound

    if _ruta_alarma is None or not os.path.exists(_ruta_alarma):
        ruta = os.path.join(tempfile.gettempdir(), "jarvis_alarma.wav")
        generar_alarma(ruta)
        _ruta_alarma = ruta
    winsound.PlaySound(_ruta_alarma, winsound.SND_FILENAME | winsound.SND_NODEFAULT)


class VigilanteRecordatorios(threading.Thread):
    """Revisa cada segundo los avisos vencidos y los publica en la cola de la app."""

    def __init__(
        self,
        events: queue.Queue[tuple],
        *,
        intervalo: float = 1.0,
        reclamar: Callable[[], list[Recordatorio]] = reclamar_vencidos,
    ) -> None:
        super().__init__(name="JarvisRecordatorios", daemon=True)
        self.events = events
        self.intervalo = intervalo
        self.reclamar = reclamar
        self._stop_event = threading.Event()

    def run(self) -> None:
        errores = 0
        while not self._stop_event.wait(self.intervalo):
            try:
                for recordatorio in self.reclamar():
                    self.events.put(("reminder_due", recordatorio))
                errores = 0
            except Exception:
                errores += 1
                if errores == 1:
                    logging.exception("No se pudieron revisar los recordatorios.")

    def stop(self) -> None:
        self._stop_event.set()
