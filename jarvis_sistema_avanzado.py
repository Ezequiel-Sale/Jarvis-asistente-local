"""Más control de la PC (2026-11-27, pedido del usuario: «control total», puntos 5, 6 y 9).

- **Capturas y grabación de pantalla** (``screenshot`` / ``screen_record``): una captura de un monitor (o de
  todos) en ``Imágenes\\Jarvis``; una grabación de un monitor, sin sonido, en ``Videos\\Jarvis`` con mss +
  OpenCV (10 cuadros por segundo, hasta ``MAX_GRABACION`` s). «Dejá de grabar» la corta antes.
- **Servicios de Windows** (``service_status`` solo lee; ``service_control`` inicia, detiene o reinicia y
  siempre pregunta). Los imprescindibles del sistema (``PROTEGIDOS``) nunca se detienen.
- **Procesos colgados y prioridad** (``process_control``): qué ventanas no responden (``IsHungAppWindow``),
  forzar el cierre de una (pregunta) y cambiar la prioridad de un programa.
"""

from __future__ import annotations

import ctypes
import logging
import re
import subprocess
import threading
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# --- Capturas y grabación ----------------------------------------------------------------------

CUADROS_POR_SEGUNDO = 10
MAX_GRABACION = 600  # 10 min
GRABACION_POR_DEFECTO = 30


def carpeta_capturas() -> Path:
    return Path.home() / "Pictures" / "Jarvis"


def carpeta_grabaciones() -> Path:
    return Path.home() / "Videos" / "Jarvis"


def _nombre(prefijo: str, extension: str, ahora: datetime | None = None) -> str:
    return f"{prefijo}_{(ahora or datetime.now()):%Y%m%d_%H%M%S}.{extension}"


def _monitores(numero: int | None):
    import jarvis_vision

    monitores = jarvis_vision.listar_monitores()
    if numero is None:
        return monitores, None
    return monitores, jarvis_vision.elegir_monitor(monitores, numero)


def _rect_de_todos(monitores) -> tuple[int, int, int, int]:
    x0 = min(m.x for m in monitores)
    y0 = min(m.y for m in monitores)
    x1 = max(m.x + m.ancho for m in monitores)
    y1 = max(m.y + m.alto for m in monitores)
    return x0, y0, x1 - x0, y1 - y0


def captura(monitor: int | None = None, *, carpeta: Path | None = None) -> str:
    """La herramienta ``screenshot``: guarda un PNG y devuelve dónde."""
    import jarvis_vision

    monitores, elegido = _monitores(monitor)
    if not monitores:
        raise OSError("No encontré ningún monitor.")
    x, y, ancho, alto = (elegido.x, elegido.y, elegido.ancho, elegido.alto) if elegido else _rect_de_todos(monitores)
    imagen = jarvis_vision._captura_rapida(x, y, ancho, alto)
    destino = Path(carpeta or carpeta_capturas())
    destino.mkdir(parents=True, exist_ok=True)
    ruta = destino / _nombre("captura", "png")
    imagen.save(ruta)
    logging.info("Captura de pantalla guardada (%s, %dx%d).", f"monitor {monitor}" if monitor else "todos",
                 ancho, alto)
    donde = f"del monitor {monitor}" if monitor else "de todas las pantallas"
    return f"Saqué una captura {donde}: {ruta}"


@dataclass
class _Grabacion:
    ruta: Path
    parar: threading.Event
    hilo: threading.Thread


_grabacion: _Grabacion | None = None
_cerrojo = threading.Lock()


def grabando() -> bool:
    with _cerrojo:
        return _grabacion is not None and _grabacion.hilo.is_alive()


def _grabar(rect: tuple[int, int, int, int], ruta: Path, segundos: float, parar: threading.Event) -> None:
    import cv2
    import mss
    import numpy as np

    x, y, ancho, alto = rect
    ancho -= ancho % 2  # los códecs piden medidas pares
    alto -= alto % 2
    escritor = cv2.VideoWriter(str(ruta), cv2.VideoWriter_fourcc(*"mp4v"), CUADROS_POR_SEGUNDO, (ancho, alto))
    cuadros = 0
    inicio = time.monotonic()
    try:
        with mss.mss() as pantalla:
            zona = {"left": x, "top": y, "width": ancho, "height": alto}
            while not parar.is_set() and time.monotonic() - inicio < segundos:
                siguiente = inicio + (cuadros + 1) / CUADROS_POR_SEGUNDO
                cuadro = np.asarray(pantalla.grab(zona))[:, :, :3]  # BGRA → BGR
                escritor.write(np.ascontiguousarray(cuadro))
                cuadros += 1
                parar.wait(max(0.0, siguiente - time.monotonic()))
    except Exception:
        logging.exception("La grabación de pantalla se cortó.")
    finally:
        escritor.release()
        logging.info("Grabación de pantalla terminada: %d cuadros en %.1f s.", cuadros, time.monotonic() - inicio)


def grabar(monitor: int | None = None, seconds: int | None = None, *, carpeta: Path | None = None) -> str:
    """La herramienta ``screen_record``: empieza a grabar en segundo plano y vuelve enseguida."""
    global _grabacion
    segundos = max(1, min(MAX_GRABACION, int(seconds or GRABACION_POR_DEFECTO)))
    monitores, elegido = _monitores(monitor)
    if not monitores:
        raise OSError("No encontré ningún monitor.")
    rect = (elegido.x, elegido.y, elegido.ancho, elegido.alto) if elegido else _rect_de_todos(monitores)
    with _cerrojo:
        if _grabacion is not None and _grabacion.hilo.is_alive():
            raise RuntimeError("Ya estoy grabando la pantalla. Decí «dejá de grabar» para cortar.")
        destino = Path(carpeta or carpeta_grabaciones())
        destino.mkdir(parents=True, exist_ok=True)
        ruta = destino / _nombre("grabacion", "mp4")
        parar = threading.Event()
        hilo = threading.Thread(target=_grabar, args=(rect, ruta, segundos, parar), name="JarvisGrabaPantalla",
                                daemon=True)
        _grabacion = _Grabacion(ruta, parar, hilo)
        hilo.start()
    donde = f"el monitor {monitor}" if monitor else "todas las pantallas"
    return f"Grabando {donde} por {segundos} s (sin sonido). Se guarda en {ruta}. Decí «dejá de grabar» para cortar."


def dejar_de_grabar() -> str:
    with _cerrojo:
        actual = _grabacion
    if actual is None or not actual.hilo.is_alive():
        return "No estaba grabando la pantalla."
    actual.parar.set()
    actual.hilo.join(timeout=10)
    return f"Dejé de grabar. El video está en {actual.ruta}"


def grabacion(action: str = "start", monitor: int | None = None, seconds: int | None = None) -> str:
    if action == "stop":
        return dejar_de_grabar()
    return grabar(monitor, seconds)


# --- Servicios de Windows ----------------------------------------------------------------------

# Nunca se detienen ni reinician: sin estos Windows deja de andar o queda sin protección.
PROTEGIDOS = frozenset({
    "rpcss", "rpceptmapper", "dcomlaunch", "lsm", "eventlog", "plugplay", "power", "profsvc", "samss",
    "schedule", "winmgmt", "windefend", "mpssvc", "bfe", "wscsvc", "cryptsvc", "audiosrv", "audioendpointbuilder",
    "dhcp", "dnscache", "nsi", "lanmanworkstation", "userManager".lower(), "coremessagingregistrar", "brokerinfrastructure",
    "systemeventsbroker", "staterepository", "sense", "wdnissvc", "securityhealthservice", "gpsvc", "themes",
    "termservice", "trustedinstaller", "appinfo", "wininit", "csrss", "lsass",
})


@dataclass(frozen=True)
class Servicio:
    nombre: str
    visible: str
    estado: str  # Running / Stopped / …
    inicio: str  # Automatic / Manual / Disabled


def _normal(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", str(texto).casefold())
    return " ".join("".join(c for c in texto if not unicodedata.combining(c)).replace("-", " ").split())


def _powershell(script: str, timeout: float = 30) -> str:
    resultado = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if resultado.returncode != 0:
        raise RuntimeError((resultado.stderr or resultado.stdout or "PowerShell falló.").strip()[:300])
    return resultado.stdout


def listar_servicios(leer=None) -> list[Servicio]:
    salida = (leer or _powershell)(
        "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-Service | ForEach-Object { "
        "'{0}|{1}|{2}|{3}' -f $_.Name, $_.DisplayName, $_.Status, $_.StartType }")
    servicios = []
    for linea in salida.splitlines():
        partes = linea.strip().split("|")
        if len(partes) == 4 and partes[0]:
            servicios.append(Servicio(*partes))
    return servicios


def buscar_servicio(nombre: str, servicios: list[Servicio]) -> list[Servicio]:
    """Por nombre exacto, por nombre visible exacto o que contengan todas las palabras."""
    clave = _normal(nombre)
    exactos = [s for s in servicios if _normal(s.nombre) == clave or _normal(s.visible) == clave]
    if exactos:
        return exactos
    palabras = clave.split()
    return [s for s in servicios if all(p in _normal(f"{s.nombre} {s.visible}") for p in palabras)]


_ESTADOS = {"Running": "en marcha", "Stopped": "detenido", "Paused": "en pausa", "StartPending": "iniciándose",
            "StopPending": "deteniéndose"}
_INICIOS = {"Automatic": "automático", "Manual": "manual", "Disabled": "deshabilitado"}


def _describir(s: Servicio) -> str:
    return (f"«{s.visible}» ({s.nombre}): {_ESTADOS.get(s.estado, s.estado)}, inicio "
            f"{_INICIOS.get(s.inicio, s.inicio)}")


def estado_servicio(name: str = "", only_running: bool = False, leer=None) -> str:
    """La herramienta ``service_status`` (solo lee)."""
    servicios = listar_servicios(leer)
    if name.strip():
        encontrados = buscar_servicio(name, servicios)
        if not encontrados:
            raise LookupError(f"No encontré ningún servicio que se llame «{name}».")
        return "; ".join(_describir(s) for s in encontrados[:8]) + "." + (
            f" (y {len(encontrados) - 8} más)" if len(encontrados) > 8 else "")
    en_marcha = [s for s in servicios if s.estado == "Running"]
    if only_running:
        return f"Hay {len(en_marcha)} servicios en marcha: " + ", ".join(s.visible for s in en_marcha[:40]) + "."
    return f"Hay {len(servicios)} servicios: {len(en_marcha)} en marcha y {len(servicios) - len(en_marcha)} detenidos."


def controlar_servicio(name: str, action: str, leer=None, ejecutar=None) -> str:
    """La herramienta ``service_control``: start / stop / restart (riesgo alto, siempre pregunta)."""
    if action not in ("start", "stop", "restart"):
        raise ValueError(f"Acción de servicio no válida: {action}.")
    encontrados = buscar_servicio(name, listar_servicios(leer))
    if not encontrados:
        raise LookupError(f"No encontré ningún servicio que se llame «{name}».")
    if len(encontrados) > 1:
        raise LookupError("Hay varios servicios con ese nombre: " + ", ".join(
            f"«{s.visible}» ({s.nombre})" for s in encontrados[:6]) + ". Decime cuál.")
    servicio = encontrados[0]
    if action in ("stop", "restart") and servicio.nombre.casefold() in PROTEGIDOS:
        raise PermissionError(f"«{servicio.visible}» es imprescindible para Windows: no lo detengo.")
    comando = {"start": "Start-Service", "stop": "Stop-Service -Force", "restart": "Restart-Service -Force"}[action]
    nombre = servicio.nombre.replace("'", "''")
    (ejecutar or _powershell)(f"{comando} -Name '{nombre}' -ErrorAction Stop", 60)
    logging.info("Servicio %s: %s.", servicio.nombre, action)
    hecho = {"start": "Inicié", "stop": "Detuve", "restart": "Reinicié"}[action]
    return f"{hecho} el servicio «{servicio.visible}»."


# --- Procesos colgados y prioridad -------------------------------------------------------------

PRIORIDADES = {
    "low": 0x40, "below_normal": 0x4000, "normal": 0x20, "above_normal": 0x8000, "high": 0x80,
}  # «realtime» no: puede congelar la PC
_PRIORIDAD_TEXTO = {"low": "baja", "below_normal": "por debajo de lo normal", "normal": "normal",
                    "above_normal": "por encima de lo normal", "high": "alta"}


def ventanas_colgadas(listar=None, colgada=None) -> list:
    """Ventanas de usuario que no responden (las que Windows marca «No responde»)."""
    import jarvis_windows

    if colgada is None:
        user32 = ctypes.windll.user32
        colgada = lambda hwnd: bool(user32.IsHungAppWindow(hwnd))  # noqa: E731
    return [v for v in (listar or jarvis_windows.listar_ventanas)() if colgada(v.hwnd)]


def _procesos(nombre: str):
    import psutil

    import jarvis_windows

    clave = jarvis_windows.nombre_app(nombre)
    propio = __import__("os").getpid()
    elegidos = []
    for proceso in psutil.process_iter(["pid", "name"]):
        nombre_proceso = (proceso.info.get("name") or "").casefold().removesuffix(".exe")
        if proceso.info["pid"] != propio and nombre_proceso and (nombre_proceso == clave or clave in nombre_proceso):
            elegidos.append(proceso)
    return elegidos


def control_procesos(action: str, name: str = "", priority: str = "normal", listar=None, colgada=None) -> str:
    """La herramienta ``process_control``: hung (qué no responde), kill_hung (fuerza el cierre de lo que no
    responde, o solo de ``name``) y priority (cambia la prioridad de ``name``)."""
    if action == "hung":
        colgadas = ventanas_colgadas(listar, colgada)
        if not colgadas:
            return "Ningún programa está colgado: todas las ventanas responden."
        return "No responden: " + ", ".join(sorted({v.programa or v.titulo for v in colgadas})) + "."
    if action == "kill_hung":
        import psutil

        colgadas = ventanas_colgadas(listar, colgada)
        if name.strip():
            import jarvis_windows

            clave = jarvis_windows.nombre_app(name)
            colgadas = [v for v in colgadas if clave in (v.programa or "").casefold()]
        if not colgadas:
            return f"«{name}» no está colgado." if name.strip() else "Ningún programa está colgado."
        cerrados = set()
        for pid in {v.pid for v in colgadas}:
            if pid == __import__("os").getpid():
                continue
            try:
                psutil.Process(pid).kill()
                cerrados.add(next(v.programa or v.titulo for v in colgadas if v.pid == pid))
            except psutil.Error:
                logging.exception("No se pudo cerrar el proceso colgado %s.", pid)
        if not cerrados:
            raise RuntimeError("Windows no me dejó cerrar los programas colgados.")
        logging.info("Programas colgados cerrados a la fuerza: %s.", ", ".join(sorted(cerrados)))
        return "Cerré a la fuerza: " + ", ".join(sorted(cerrados)) + "."
    if action == "priority":
        import psutil

        if priority not in PRIORIDADES:
            raise ValueError(f"Prioridad no válida: {priority}.")
        if not name.strip():
            raise ValueError("Decime de qué programa cambiar la prioridad.")
        procesos = _procesos(name)
        if not procesos:
            raise LookupError(f"No encontré «{name}» abierto.")
        cambiados = 0
        for proceso in procesos:
            try:
                proceso.nice(PRIORIDADES[priority])
                cambiados += 1
            except psutil.Error:
                logging.debug("No se pudo cambiar la prioridad de %s.", proceso.pid, exc_info=True)
        if not cambiados:
            raise RuntimeError(f"Windows no me dejó cambiar la prioridad de «{name}».")
        return f"Puse «{name}» en prioridad {_PRIORIDAD_TEXTO[priority]} ({cambiados} proceso{'s' if cambiados != 1 else ''})."
    raise ValueError(f"Acción de procesos no válida: {action}.")


# --- Frases ------------------------------------------------------------------------------------

_NUM = {"uno": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4}
_MON = r"(?:\s+(?:del|de\s+la|en\s+el|en\s+la)\s+(?:monitor|pantalla)\s+(?:numero\s+)?(?P<n>\d|uno|dos|tres|cuatro))?"
_CAPTURA = re.compile(
    r"^(?:(?:saca|saque|sacame|toma|hace|haz|hacer|sacar|tomar)\s+(?:una\s+)?)?(?:captura(?:\s+de\s+pantalla)?|"
    r"screenshot|pantallazo)" + _MON + r"$")
_GRABAR = re.compile(
    r"^(?:graba|grabame|grabar|empeza\s+a\s+grabar|comenza\s+a\s+grabar)\s+(?:la\s+)?(?:pantalla|monitor"
    r"(?:\s+(?P<m>\d|uno|dos|tres|cuatro))?)" + _MON +
    r"(?:\s+(?:por|durante)?\s*(?P<seg>\d{1,3})\s+(?P<unidad>segundos?|minutos?))?$")
_PARAR = re.compile(r"^(?:deja\s+de\s+grabar|para\s+(?:de\s+grabar|la\s+grabacion)|deten\s+la\s+grabacion|"
                    r"corta\s+la\s+grabacion|termina\s+la\s+grabacion)(?:\s+de\s+(?:la\s+)?pantalla)?$")
_SERVICIO = re.compile(
    r"^(?P<verbo>inicia|arranca|enciende|deten|para|detene|reinicia|reinicializa)\s+(?:el\s+)?servicio\s+"
    r"(?:de\s+(?:la\s+|el\s+)?)?(?P<name>.+)$")
_ESTADO_SERVICIO = re.compile(
    r"^(?:(?:como|cual)\s+esta|estado\s+del|esta\s+(?:andando|corriendo|activo|funcionando))\s+(?:el\s+)?"
    r"servicio\s+(?:de\s+(?:la\s+|el\s+)?)?(?P<name>.+)$")
_SERVICIOS_EN_MARCHA = re.compile(r"^(?:que|cuales)\s+servicios\s+(?:estan\s+)?(?:andando|corriendo|activos|en\s+marcha)$")
_COLGADOS = re.compile(r"^(?:que|cual(?:es)?)\s+(?:programas?|aplicaciones?|apps?|ventanas?)\s+"
                       r"(?:no\s+responden?|(?:estan?\s+)?colgad[oa]s?|se\s+colg(?:o|aron))$")
_CERRAR_COLGADOS = re.compile(
    r"^(?:cierra|cerra|mata|forza\s+el\s+cierre\s+de|forza\s+cierre\s+de)\s+(?:los\s+|las\s+|el\s+|la\s+)?"
    r"(?:(?:programas?|aplicaciones?|apps?|ventanas?)\s+)?(?:que\s+no\s+responden?|colgad[oa]s?)$")
_FORZAR = re.compile(r"^(?:forza\s+(?:el\s+)?cierre\s+de|mata\s+(?:el\s+proceso\s+(?:de\s+)?)?)(?P<name>.+)$")
_PRIORIDAD = re.compile(
    r"^(?:pon|pone|ponele|cambia|cambiale|subi|subile|baja|bajale)\s+(?:la\s+)?(?:prioridad\s+)?"
    r"(?:(?:de|a)\s+)?(?P<name>.+?)\s+(?:a|en)\s+(?:prioridad\s+)?(?P<nivel>alta|baja|normal|maxima|minima)$"
    r"|^(?P<verbo>subi|subile|baja|bajale)\s+(?:la\s+)?prioridad\s+(?:de|a)\s+(?P<name2>.+)$")


def _num(valor: str | None) -> int | None:
    if not valor:
        return None
    return int(valor) if valor.isdigit() else _NUM.get(valor)


def detectar(texto: str) -> tuple[str, dict] | None:
    from jarvis_commands import _normalizar_consulta

    consulta = _normalizar_consulta(texto)
    if (m := _CAPTURA.fullmatch(consulta)) is not None:
        return "screenshot", ({"monitor": _num(m.group("n"))} if m.group("n") else {})
    if _PARAR.fullmatch(consulta):
        return "screen_record", {"action": "stop"}
    if (m := _GRABAR.fullmatch(consulta)) is not None:
        argumentos: dict[str, object] = {"action": "start"}
        monitor = _num(m.group("m") or m.group("n"))
        if monitor:
            argumentos["monitor"] = monitor
        if m.group("seg"):
            argumentos["seconds"] = int(m.group("seg")) * (60 if m.group("unidad").startswith("minuto") else 1)
        return "screen_record", argumentos
    if (m := _SERVICIO.fullmatch(consulta)) is not None:
        verbo = m.group("verbo")
        accion = "restart" if verbo.startswith("reinicia") else "stop" if verbo in ("deten", "para", "detene") \
            else "start"
        return "service_control", {"name": m.group("name").strip(), "action": accion}
    if (m := _ESTADO_SERVICIO.fullmatch(consulta)) is not None:
        return "service_status", {"name": m.group("name").strip()}
    if _SERVICIOS_EN_MARCHA.fullmatch(consulta):
        return "service_status", {"only_running": True}
    if _COLGADOS.fullmatch(consulta):
        return "process_control", {"action": "hung"}
    if _CERRAR_COLGADOS.fullmatch(consulta):
        return "process_control", {"action": "kill_hung"}
    if (m := _FORZAR.fullmatch(consulta)) is not None:
        return "process_control", {"action": "kill_hung", "name": m.group("name").strip()}
    if (m := _PRIORIDAD.fullmatch(consulta)) is not None:
        if m.group("name2"):
            nivel = "above_normal" if m.group("verbo").startswith("sub") else "below_normal"
            return "process_control", {"action": "priority", "name": m.group("name2").strip(), "priority": nivel}
        nivel = {"alta": "high", "maxima": "high", "baja": "low", "minima": "low", "normal": "normal"}[m.group("nivel")]
        return "process_control", {"action": "priority", "name": m.group("name").strip(), "priority": nivel}
    return None


# --- Registro ----------------------------------------------------------------------------------

def registrar(registry, ToolRisk) -> None:
    registry.register(
        "screenshot",
        "Saca una captura de pantalla de un monitor (monitor, número fijo) o de todos, y la guarda en "
        "Imágenes\\Jarvis.",
        {"type": "object", "properties": {"monitor": {"type": "integer", "minimum": 1, "maximum": 9}},
         "required": [], "additionalProperties": False},
        lambda monitor=None: captura(monitor),
        label="Captura de pantalla", timeout_seconds=20,
    )
    registry.register(
        "screen_record",
        "Graba la pantalla sin sonido (action start, de un monitor o de todos, seconds hasta 600; por defecto "
        "30) o corta la grabación en curso (action stop). Se guarda en Videos\\Jarvis.",
        {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["start", "stop"]},
            "monitor": {"type": "integer", "minimum": 1, "maximum": 9},
            "seconds": {"type": "integer", "minimum": 1, "maximum": MAX_GRABACION}},
         "required": ["action"], "additionalProperties": False},
        grabacion,
        label="Grabar la pantalla", timeout_seconds=20,
    )
    registry.register(
        "service_status",
        "Consulta los servicios de Windows (solo lee): con name, el estado de ese servicio; con "
        "only_running, cuáles están en marcha; sin nada, un resumen.",
        {"type": "object", "properties": {"name": {"type": "string", "maxLength": 80},
                                          "only_running": {"type": "boolean"}},
         "required": [], "additionalProperties": False},
        lambda name="", only_running=False: estado_servicio(name, only_running),
        label="Servicios de Windows", timeout_seconds=30,
    )
    registry.register(
        "service_control",
        "Inicia (start), detiene (stop) o reinicia (restart) un servicio de Windows por su nombre. Nunca "
        "detiene los imprescindibles del sistema.",
        {"type": "object", "properties": {"name": {"type": "string", "minLength": 2, "maxLength": 80},
                                          "action": {"type": "string", "enum": ["start", "stop", "restart"]}},
         "required": ["name", "action"], "additionalProperties": False},
        lambda name, action: controlar_servicio(name, action),
        risk=ToolRisk.HIGH, requires_confirmation=True, label="Controlar un servicio", timeout_seconds=75,
    )
    registry.register(
        "process_control",
        "Programas colgados y prioridad: hung (qué ventanas no responden), kill_hung (cierra a la fuerza los "
        "que no responden, o solo name) y priority (pone a name en prioridad low, below_normal, normal, "
        "above_normal o high).",
        {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["hung", "kill_hung", "priority"]},
            "name": {"type": "string", "maxLength": 80},
            "priority": {"type": "string", "enum": list(PRIORIDADES)}},
         "required": ["action"], "additionalProperties": False},
        lambda action, name="", priority="normal": control_procesos(action, name, priority),
        risk=ToolRisk.MEDIUM, label="Procesos", timeout_seconds=30,
    )
