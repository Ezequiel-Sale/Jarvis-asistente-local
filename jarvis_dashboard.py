"""Panel de inicio: reúne en segundo plano los datos que muestra la primera página.

Cada fuente falla por separado (sin internet no hay clima, pero el resto sí).
Lo lento se guarda en caché: el clima 30 min y la batería del G533 2 min.
Solo se llama desde un hilo secundario; la interfaz recibe el resultado por evento.
"""

import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import psutil
import requests

import jarvis_core as core
import jarvis_hardware

CACHE_CLIMA = 30 * 60
CACHE_BATERIA = 2 * 60
_cache: dict[str, tuple[float, object]] = {}


@dataclass
class DatosPanel:
    clima: str | None = None
    recordatorios: list[str] = field(default_factory=list)
    rutinas: list[str] = field(default_factory=list)
    sonando: str | None = None
    bateria: int | None = None
    auriculares: bool | None = None
    salud: jarvis_hardware.Temperaturas = field(default_factory=jarvis_hardware.Temperaturas)
    ram_pct: float = 0.0
    ram_texto: str = ""
    disco_pct: float = 0.0
    disco_texto: str = ""
    modelos_cargados: list[str] = field(default_factory=list)
    ollama_ok: bool = False


def saludo(hora: int) -> str:
    if 6 <= hora < 13:
        return "Buenos días"
    if 13 <= hora < 20:
        return "Buenas tardes"
    return "Buenas noches"


def _cacheado(clave: str, segundos: float, funcion):
    ahora = time.monotonic()
    guardado = _cache.get(clave)
    if guardado is not None and ahora - guardado[0] < segundos:
        return guardado[1]
    valor = funcion()
    _cache[clave] = (ahora, valor)
    return valor


def proximas_rutinas(rutinas, ahora: datetime, limite: int = 3) -> list[str]:
    """Las próximas rutinas por hora (activas), con «hoy/mañana/día»."""
    import jarvis_routines

    dias = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
    proximas = []
    for r in rutinas:
        if not r.active or r.trigger != jarvis_routines.DISPARADOR_HORA or not r.time:
            continue
        try:
            hora, minuto = (int(x) for x in r.time.split(":")[:2])
        except ValueError:
            continue
        for desplazamiento in range(8):
            dia = ahora + timedelta(days=desplazamiento)
            momento = dia.replace(hour=hora, minute=minuto, second=0, microsecond=0)
            if momento <= ahora or dia.isoweekday() not in r.days:
                continue
            cuando = ("hoy" if desplazamiento == 0 else "mañana" if desplazamiento == 1
                      else dias[momento.weekday()])
            proximas.append((momento, f"{r.name} · {cuando} {momento:%H:%M}"))
            break
    return [texto for _m, texto in sorted(proximas)[:limite]]


def recolectar(ultima_salud: jarvis_hardware.Temperaturas | None = None) -> DatosPanel:
    datos = DatosPanel()
    ahora = datetime.now()

    def intentar(nombre, funcion):
        try:
            funcion()
        except Exception as error:
            logging.debug("Panel: no se pudo leer %s (%s).", nombre, error)

    def clima() -> None:
        from plugins import clima as plugin

        datos.clima = _cacheado("clima", CACHE_CLIMA, lambda: plugin.consultar_clima())

    def recordatorios() -> None:
        import jarvis_reminders

        datos.recordatorios = [
            f"{r.texto or r.tipo} · {jarvis_reminders.describir_momento(r.vence, ahora)}"
            for r in sorted(jarvis_reminders.pendientes(), key=lambda r: r.vence)[:3]
        ]

    def rutinas() -> None:
        import jarvis_routines

        datos.rutinas = proximas_rutinas(jarvis_routines.listar_automatizaciones(), ahora)

    def sonando() -> None:
        import jarvis_media

        sesion = jarvis_media.sesion_actual(con_titulo=True)
        if sesion is not None and sesion.titulo:
            estado = "▶" if sesion.estado == "reproduciendo" else "⏸"
            artista = f" — {sesion.artista}" if sesion.artista else ""
            datos.sonando = f"{estado} {sesion.titulo}{artista} ({sesion.app})"

    def bateria() -> None:
        from jarvis_headset import consultar_g533, porcentaje_bateria

        estado = _cacheado("bateria", CACHE_BATERIA, consultar_g533)
        if estado is not None:
            datos.auriculares = estado.encendido
            if estado.encendido and estado.voltaje_mv:
                datos.bateria = porcentaje_bateria(estado.voltaje_mv)

    def memoria_y_disco() -> None:
        memoria = psutil.virtual_memory()
        datos.ram_pct = memoria.percent
        datos.ram_texto = f"{(memoria.total - memoria.available) / 1024**3:.0f}/{memoria.total / 1024**3:.0f} GB"
        disco = psutil.disk_usage(os.environ.get("SystemDrive", "C:") + "\\")
        datos.disco_pct = disco.percent
        datos.disco_texto = f"{disco.free / 1024**3:.0f} GB libres"

    def ollama() -> None:
        respuesta = requests.get(core.LLM_URL.rsplit("/", 1)[0] + "/ps", timeout=3)
        datos.modelos_cargados = [m["name"] for m in respuesta.json().get("models", [])]
        datos.ollama_ok = True

    for nombre, funcion in (("clima", clima), ("recordatorios", recordatorios), ("rutinas", rutinas),
                            ("sonando", sonando), ("batería", bateria), ("memoria", memoria_y_disco),
                            ("ollama", ollama)):
        intentar(nombre, funcion)
    datos.salud = ultima_salud if ultima_salud is not None else jarvis_hardware.sensores.leer()
    return datos
