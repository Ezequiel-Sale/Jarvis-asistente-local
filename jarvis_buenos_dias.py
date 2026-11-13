"""Resumen «Buenos días»: clima, avisos y rutinas del día, estado del equipo y auriculares.

Se pide con «buenos días», «buen día» o «resumen del día» (herramienta ``morning_briefing``)
y sale solo una vez al día, la primera vez que usas la PC por la mañana
(``JarvisApp._check_morning_brief``; se apaga en Ajustes o con «desactiva el resumen de la
mañana»). ``componer`` arma el texto sin tocar hardware ni red, para poder probarlo.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from datetime import datetime, timedelta

PREFERENCIA_ACTIVO = "resumen_matutino"          # "0" = apagado; por defecto, activo
PREFERENCIA_ULTIMO = "resumen_matutino_ultimo"   # fecha ISO del último resumen automático
HORA_DESDE, HORA_HASTA = 5, 12                   # el automático solo sale por la mañana
INACTIVO_MAX = 90.0                              # segundos: «estás usando la PC»

_DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
_MESES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
          "septiembre", "octubre", "noviembre", "diciembre")


def _normalizar(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFKD", texto.casefold())
    sin_tildes = "".join(c for c in sin_tildes if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^\w\s]", " ", sin_tildes).split())


_PIDE = re.compile(
    r"^(?:(?:hola|hey|oye)\s+)?(?:jarvis\s+)?"
    r"(?:buenos\s+dias|buen\s+dia|"
    r"(?:dame\s+|dime\s+|quiero\s+)?(?:el\s+)?resumen\s+(?:del?\s+)?(?:dia|la\s+manana)(?:\s+de\s+hoy)?|"
    r"como\s+viene\s+(?:el\s+)?(?:dia|hoy))"
    r"(?:\s+jarvis)?$"
)
_INTERRUPTOR = re.compile(
    r"^(?P<accion>activa|activar|enciende|habilita|desactiva|desactivar|apaga|deshabilita|quita)\s+"
    r"(?:el\s+)?resumen\s+(?:de\s+la\s+manana|matutino|del\s+dia|de\s+buenos\s+dias)$"
)


def detectar(texto: str) -> tuple[str, dict[str, object]] | None:
    """«Buenos días» → ("morning_briefing", {}); «desactiva el resumen de la mañana» → con enabled."""
    consulta = _normalizar(texto)
    if _PIDE.fullmatch(consulta):
        return "morning_briefing", {}
    interruptor = _INTERRUPTOR.fullmatch(consulta)
    if interruptor:
        activar = interruptor.group("accion").startswith(("activ", "encien", "habil"))
        return "morning_briefing", {"automatic": activar}
    return None


def saludo(hora: int) -> str:
    if 5 <= hora < 13:
        return "Buenos días"
    if 13 <= hora < 20:
        return "Buenas tardes"
    return "Buenas noches"


def _gb(bytes_: int) -> str:
    return f"{bytes_ / 1024**3:.0f} GB"


def componer(
    ahora: datetime,
    *,
    clima: str | None = None,
    recordatorios=(),       # jarvis_reminders.Recordatorio pendientes
    rutinas=(),             # textos «Noche · hoy 23:00» (jarvis_dashboard.proximas_rutinas)
    discos=(),              # jarvis_system_info.Disco
    alertas_temperatura=(),  # jarvis_hardware.alertas(...)
    bateria: int | None = None,
    limite_disco: float = 90.0,
) -> str:
    """Texto del resumen, en frases cortas para leerlo y escucharlo."""
    import jarvis_reminders

    partes = [f"{saludo(ahora.hour)}. Hoy es {_DIAS[ahora.weekday()]} {ahora.day} de "
              f"{_MESES[ahora.month - 1]} y son las {ahora.hour}:{ahora.minute:02d}."]
    if clima:
        partes.append(clima.strip())

    proximos = [r for r in sorted(recordatorios, key=lambda r: r.vence)
                if r.vence - ahora <= timedelta(hours=24)]
    if proximos:
        items = [f"{r.texto or r.tipo} {jarvis_reminders.describir_momento(r.vence, ahora)}" for r in proximos[:4]]
        partes.append(("Tienes " + ("estos avisos: " if len(items) > 1 else "un aviso: ")) + "; ".join(items) + ".")
    else:
        partes.append("No tienes recordatorios para hoy.")

    de_hoy = [t.split(" · ")[0] + " a las " + t.rsplit(" ", 1)[-1] for t in rutinas if " · hoy " in t]
    if de_hoy:
        partes.append("Rutinas de hoy: " + ", ".join(de_hoy) + ".")

    avisos = [f"el disco {d.unidad.rstrip(':')} está al {d.usado_pct:.0f} % (quedan {_gb(d.libre)})"
              for d in discos if d.usado_pct >= limite_disco]
    avisos += list(alertas_temperatura)
    if avisos:
        partes.append("Ojo: " + "; ".join(avisos) + ".")
    else:
        partes.append("El equipo está bien.")
    if bateria is not None:
        partes.append(f"Los auriculares tienen {bateria} % de batería"
                      + (", conviene cargarlos." if bateria <= 20 else "."))
    return " ".join(partes)


def generar(ultima_salud=None, ahora: datetime | None = None) -> str:
    """Reúne los datos (cada fuente falla por separado) y arma el resumen."""
    import jarvis_dashboard
    import jarvis_hardware
    import jarvis_reminders

    ahora = ahora or datetime.now()
    datos = jarvis_dashboard.recolectar(ultima_salud)
    try:
        pendientes = jarvis_reminders.pendientes()
    except Exception:
        logging.debug("Resumen: no se pudieron leer los recordatorios.", exc_info=True)
        pendientes = []
    return componer(
        ahora,
        clima=datos.clima,
        recordatorios=pendientes,
        rutinas=datos.rutinas,
        discos=datos.discos,
        alertas_temperatura=jarvis_hardware.alertas(datos.salud),
        bateria=datos.bateria if datos.auriculares else None,
        limite_disco=jarvis_hardware.LIMITE_DISCO,
    )


def toca_automatico(ahora: datetime, ultimo: str | None, activo: str | None,
                    inactivo_segundos: float | None) -> bool:
    """¿Sale hoy el resumen solo? Por la mañana, una vez al día y solo si estás usando la PC."""
    if activo == "0":
        return False
    if not HORA_DESDE <= ahora.hour < HORA_HASTA:
        return False
    if ultimo == ahora.date().isoformat():
        return False
    return inactivo_segundos is not None and inactivo_segundos <= INACTIVO_MAX


def segundos_inactivo() -> float | None:
    """Segundos desde la última tecla o movimiento del mouse (GetLastInputInfo)."""
    try:
        import ctypes
        from ctypes import wintypes

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        ahora = ctypes.windll.kernel32.GetTickCount() & 0xFFFFFFFF
        return ((ahora - info.dwTime) & 0xFFFFFFFF) / 1000.0
    except (AttributeError, OSError):
        return None
