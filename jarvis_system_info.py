"""Consultas de solo lectura sobre el equipo: ventanas, procesos y recursos.

Ninguna función de este módulo cierra procesos ni cambia configuraciones.
La recolección (``obtener_*``) está separada del formato (``formatear_*``)
para poder testear el texto sin depender del sistema real.
"""

import os
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

import psutil

MUESTREO_CPU = 0.5
MAX_TITULO = 80

# Nombres legibles para ejecutables habituales (clave: nombre sin .exe, en minúsculas).
_NOMBRES_AMIGABLES = {
    "brave": "Brave",
    "chrome": "Google Chrome",
    "msedge": "Microsoft Edge",
    "firefox": "Firefox",
    "explorer": "Explorador de archivos",
    "code": "Visual Studio Code",
    "discord": "Discord",
    "steam": "Steam",
    "steamwebhelper": "Steam",
    "spotify": "Spotify",
    "windowsterminal": "Terminal de Windows",
    "powershell": "PowerShell",
    "cmd": "Símbolo del sistema",
    "notepad": "Bloc de notas",
    "taskmgr": "Administrador de tareas",
    "docker desktop": "Docker Desktop",
    "ollama app": "Ollama",
    "ollama": "Ollama",
    "llama-server": "Ollama (modelo cargado)",
    "vmmemwsl": "WSL/Docker (máquina virtual)",
    "vmmem": "WSL/Docker (máquina virtual)",
    "com.docker.backend": "Docker Desktop",
    "dwm": "Escritorio de Windows (dwm)",
    "searchhost": "Búsqueda de Windows",
    "msmpeng": "Antivirus de Windows",
    "svchost": "Servicios de Windows (svchost)",
    "claude": "Claude",
    "winword": "Word",
    "excel": "Excel",
    "powerpnt": "PowerPoint",
    "outlook": "Outlook",
    "vlc": "VLC",
    "obs64": "OBS Studio",
}
# Procesos que alojan apps de la Tienda: el título de la ventana es el nombre de la app.
_HOSTS_DE_APPS = {"applicationframehost"}
_PROCESOS_IGNORADOS = {"system idle process", "idle", "system", "registry", "memory compression"}


@dataclass(frozen=True, slots=True)
class VentanaAbierta:
    aplicacion: str
    titulo: str


@dataclass(frozen=True, slots=True)
class UsoProceso:
    nombre: str
    procesos: int
    memoria_bytes: int
    cpu_porcentaje: float


@dataclass(frozen=True, slots=True)
class Disco:
    unidad: str  # «C:»
    nombre: str  # etiqueta del volumen («Juegos»); puede estar vacía
    libre: int
    total: int

    @property
    def usado_pct(self) -> float:
        return (self.total - self.libre) / self.total * 100 if self.total else 0.0

    @property
    def titulo(self) -> str:
        return f"{self.unidad} ({self.nombre})" if self.nombre else self.unidad


@dataclass(frozen=True, slots=True)
class EstadoEquipo:
    cpu_porcentaje: float
    nucleos: int
    ram_usada: int
    ram_total: int
    disco_unidad: str
    disco_libre: int
    disco_total: int
    encendido_desde: float
    bateria_porcentaje: float | None = None
    bateria_cargando: bool | None = None
    discos: tuple[Disco, ...] = ()


MIN_DISCO = 1024**3


def _etiqueta_volumen(raiz: str) -> str:
    if os.name != "nt":
        return ""
    import ctypes

    buffer = ctypes.create_unicode_buffer(261)
    try:
        ok = ctypes.windll.kernel32.GetVolumeInformationW(raiz, buffer, 261, None, None, None, None, 0)
    except OSError:
        return ""
    return buffer.value.strip() if ok else ""


def listar_discos(particiones=None, uso=None, etiqueta=_etiqueta_volumen) -> list[Disco]:
    """Discos instalados (fijos y extraíbles con medio), sin lectores de CD ni unidades vacías."""
    particiones = particiones or psutil.disk_partitions
    uso = uso or psutil.disk_usage
    discos: dict[str, Disco] = {}
    for particion in particiones(all=False):
        opciones = particion.opts.split(",")
        if "cdrom" in opciones or not particion.fstype:
            continue
        try:
            espacio = uso(particion.mountpoint)
        except OSError:  # sin medio o sin permiso
            continue
        if espacio.total < MIN_DISCO:  # particiones de arranque o recuperación con letra
            continue
        unidad = particion.mountpoint.rstrip("\\/") or particion.mountpoint
        discos.setdefault(unidad, Disco(unidad, etiqueta(particion.mountpoint), espacio.free, espacio.total))
    return sorted(discos.values(), key=lambda d: d.unidad)


def nombre_aplicacion(ejecutable: str, titulo: str = "", pid: int | None = None) -> str:
    if pid is not None and pid == os.getpid():
        return "Jarvis"
    base = ejecutable.removesuffix(".exe").removesuffix(".EXE")
    clave = base.casefold()
    if clave in _HOSTS_DE_APPS and titulo:
        return titulo
    if clave == "jarvis":
        return "Jarvis"
    return _NOMBRES_AMIGABLES.get(clave, base[:1].upper() + base[1:] if base else "Desconocida")


# --- Recolección --------------------------------------------------------------

def obtener_ventanas_abiertas() -> list[VentanaAbierta]:
    """Ventanas de nivel superior visibles para el usuario (como en la barra de tareas)."""
    if os.name != "nt":
        raise OSError("La lista de ventanas solo está disponible en Windows.")

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    dwmapi = ctypes.WinDLL("dwmapi")
    gw_owner = 4
    gwl_exstyle = -20
    ws_ex_toolwindow = 0x00000080
    dwmwa_cloaked = 14

    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetWindow.restype = wintypes.HWND
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowLongW.restype = ctypes.c_long
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    dwmapi.DwmGetWindowAttribute.argtypes = [
        wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
    ]

    encontradas: list[tuple[int, str]] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def recolectar(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, gw_owner):
            return True
        if user32.GetWindowLongW(hwnd, gwl_exstyle) & ws_ex_toolwindow:
            return True
        cloaked = wintypes.DWORD()
        if dwmapi.DwmGetWindowAttribute(
            hwnd, dwmwa_cloaked, ctypes.byref(cloaked), ctypes.sizeof(cloaked)
        ) == 0 and cloaked.value:
            return True
        longitud = user32.GetWindowTextLengthW(hwnd)
        if longitud <= 0:
            return True
        buffer = ctypes.create_unicode_buffer(longitud + 1)
        user32.GetWindowTextW(hwnd, buffer, longitud + 1)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        encontradas.append((pid.value, buffer.value))
        return True

    user32.EnumWindows(recolectar, 0)

    ventanas: list[VentanaAbierta] = []
    for pid, titulo in encontradas:
        try:
            ejecutable = psutil.Process(pid).name()
        except (psutil.Error, OSError):
            ejecutable = ""
        ventanas.append(VentanaAbierta(nombre_aplicacion(ejecutable, titulo, pid), titulo))
    return ventanas


def obtener_uso_procesos() -> list[UsoProceso]:
    """Uso de memoria y CPU agrupado por nombre de programa.

    La CPU se mide en una ventana de ``MUESTREO_CPU`` segundos y se expresa
    como porcentaje del total del equipo (no por núcleo).
    """
    procesos = []
    for proceso in psutil.process_iter(["name"]):
        try:
            proceso.cpu_percent(None)
            procesos.append(proceso)
        except (psutil.Error, OSError):
            continue
    time.sleep(MUESTREO_CPU)
    nucleos = psutil.cpu_count() or 1

    agrupados: dict[str, list[float]] = {}
    for proceso in procesos:
        nombre = (proceso.info.get("name") or "").strip()
        if not nombre or nombre.casefold().removesuffix(".exe") in _PROCESOS_IGNORADOS:
            continue
        try:
            cpu = proceso.cpu_percent(None) / nucleos
            memoria = proceso.memory_info().rss
        except (psutil.Error, OSError):
            continue
        clave = nombre_aplicacion(nombre, pid=proceso.pid)
        datos = agrupados.setdefault(clave, [0, 0.0, 0.0])
        datos[0] += 1
        datos[1] += memoria
        datos[2] += cpu
    return [
        UsoProceso(nombre, int(cuenta), int(memoria), cpu)
        for nombre, (cuenta, memoria, cpu) in agrupados.items()
    ]


def obtener_estado_equipo() -> EstadoEquipo:
    unidad = os.environ.get("SystemDrive", "C:") + "\\" if os.name == "nt" else "/"
    memoria = psutil.virtual_memory()
    disco = psutil.disk_usage(unidad)
    bateria = psutil.sensors_battery()
    return EstadoEquipo(
        cpu_porcentaje=psutil.cpu_percent(interval=MUESTREO_CPU),
        nucleos=psutil.cpu_count() or 1,
        ram_usada=memoria.total - memoria.available,
        ram_total=memoria.total,
        disco_unidad=unidad.rstrip("\\/") or unidad,
        disco_libre=disco.free,
        disco_total=disco.total,
        encendido_desde=psutil.boot_time(),
        bateria_porcentaje=bateria.percent if bateria else None,
        bateria_cargando=bateria.power_plugged if bateria else None,
        discos=tuple(listar_discos()),
    )


# --- Formato ------------------------------------------------------------------

def _gb(cantidad: int) -> str:
    valor = cantidad / 1024**3
    texto = f"{valor:.1f}" if valor < 100 else f"{valor:.0f}"
    return texto.replace(".", ",") + " GB"


def _tamano(cantidad: int) -> str:
    if cantidad >= 1024**3:
        return _gb(cantidad)
    return f"{cantidad / 1024**2:.0f} MB"


def _porcentaje(valor: float) -> str:
    return f"{valor:.0f} %"


def _duracion(segundos: float) -> str:
    minutos = int(segundos // 60)
    dias, resto = divmod(minutos, 24 * 60)
    horas, minutos = divmod(resto, 60)
    partes = []
    if dias:
        partes.append(f"{dias} d")
    if horas:
        partes.append(f"{horas} h")
    if not dias:
        partes.append(f"{minutos} min")
    return " ".join(partes)


def _recortar_titulo(titulo: str) -> str:
    titulo = " ".join(titulo.split())
    return titulo if len(titulo) <= MAX_TITULO else titulo[: MAX_TITULO - 1] + "…"


def formatear_aplicaciones(
    ventanas: Sequence[VentanaAbierta],
    incluir_titulos: bool = False,
) -> str:
    if not ventanas:
        return "No encontré aplicaciones con ventanas abiertas."
    por_app: dict[str, list[str]] = {}
    for ventana in ventanas:
        por_app.setdefault(ventana.aplicacion, []).append(ventana.titulo)
    nombres = sorted(por_app, key=str.casefold)
    if not incluir_titulos:
        partes = [
            f"{nombre} ({len(por_app[nombre])} ventanas)" if len(por_app[nombre]) > 1 else nombre
            for nombre in nombres
        ]
        cantidad = len(nombres)
        encabezado = (
            "Tienes 1 aplicación con ventanas abiertas: "
            if cantidad == 1
            else f"Tienes {cantidad} aplicaciones con ventanas abiertas: "
        )
        return encabezado + ", ".join(partes) + "."
    lineas = ["Ventanas abiertas:"]
    for nombre in nombres:
        for titulo in por_app[nombre]:
            lineas.append(f"- {nombre}: «{_recortar_titulo(titulo)}»")
    return "\n".join(lineas)


def formatear_procesos(
    procesos: Sequence[UsoProceso],
    criterio: str = "memory",
    limite: int = 5,
) -> str:
    if criterio not in {"memory", "cpu"}:
        raise ValueError("El criterio debe ser memory o cpu.")
    if not procesos:
        return "No pude leer información de los procesos."
    clave = (
        (lambda p: p.memoria_bytes) if criterio == "memory" else (lambda p: p.cpu_porcentaje)
    )
    principales = sorted(procesos, key=clave, reverse=True)[:limite]
    titulo = (
        "Programas que más memoria usan:"
        if criterio == "memory"
        else "Programas que más CPU usan ahora mismo:"
    )
    lineas = [titulo]
    for indice, proceso in enumerate(principales, start=1):
        cuenta = f" ({proceso.procesos} procesos)" if proceso.procesos > 1 else ""
        dato = (
            _tamano(proceso.memoria_bytes)
            if criterio == "memory"
            else f"{proceso.cpu_porcentaje:.1f} % de CPU".replace(".", ",")
        )
        lineas.append(f"{indice}. {proceso.nombre}{cuenta} — {dato}")
    return "\n".join(lineas)


def _texto_discos(estado: EstadoEquipo) -> str:
    if len(estado.discos) <= 1:
        return f"Disco {estado.disco_unidad} {_gb(estado.disco_libre)} libres de {_gb(estado.disco_total)}."
    lineas = ["Discos:"]
    for disco in estado.discos:
        lineas.append(f"- {disco.titulo} {_gb(disco.libre)} libres de {_gb(disco.total)} "
                      f"({_porcentaje(disco.usado_pct)} usado)")
    return "\n".join(lineas)


def formatear_estado(estado: EstadoEquipo, ahora: float | None = None) -> str:
    ahora = time.time() if ahora is None else ahora
    ram_pct = estado.ram_usada / estado.ram_total * 100 if estado.ram_total else 0
    partes = [
        f"CPU al {_porcentaje(estado.cpu_porcentaje)} ({estado.nucleos} núcleos).",
        f"RAM: {_gb(estado.ram_usada)} de {_gb(estado.ram_total)} en uso ({_porcentaje(ram_pct)}).",
        _texto_discos(estado),
        f"Encendido desde hace {_duracion(ahora - estado.encendido_desde)} "
        f"({datetime.fromtimestamp(estado.encendido_desde).strftime('%d/%m %H:%M')}).",
    ]
    if estado.bateria_porcentaje is not None:
        carga = " (cargando)" if estado.bateria_cargando else ""
        partes.append(f"Batería: {_porcentaje(estado.bateria_porcentaje)}{carga}.")
    return "Estado del equipo:\n" + "\n".join(partes)


# --- Handlers de herramientas ---------------------------------------------------

def consultar_aplicaciones_abiertas(include_titles: bool = False) -> str:
    return formatear_aplicaciones(obtener_ventanas_abiertas(), include_titles)


def consultar_procesos(sort_by: str = "memory", limit: int = 5) -> str:
    return formatear_procesos(obtener_uso_procesos(), sort_by, limit)


def consultar_estado_equipo() -> str:
    texto = formatear_estado(obtener_estado_equipo())
    try:
        import jarvis_hardware

        lineas = jarvis_hardware.describir(jarvis_hardware.sensores.leer())
    except Exception:
        lineas = []
    return texto + ("\n" + "\n".join(lineas) if lineas else "")


# --- Fecha y hora -------------------------------------------------------------

DIAS_SEMANA = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
MESES = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)


def describir_fecha(ahora: datetime) -> str:
    return (
        f"{DIAS_SEMANA[ahora.weekday()]} {ahora.day} de {MESES[ahora.month - 1]} "
        f"de {ahora.year}"
    )


def describir_hora(ahora: datetime) -> str:
    return f"{ahora.hour}:{ahora.minute:02d}"


def consultar_fecha_hora(que: str = "both", *, ahora: datetime | None = None) -> str:
    """Fecha y/o hora del reloj de Windows, en español (sin depender del locale)."""
    ahora = ahora or datetime.now()
    articulo = "Es la" if ahora.hour == 1 else "Son las"
    hora = f"{articulo} {describir_hora(ahora)}."
    fecha = f"Hoy es {describir_fecha(ahora)}."
    if que == "time":
        return hora
    if que == "date":
        return fecha
    return f"{fecha} {hora}"


def contexto_fecha_hora(ahora: datetime | None = None) -> str:
    """Línea para el prompt del agente: el modelo no tiene reloj propio."""
    ahora = ahora or datetime.now()
    return f"Fecha y hora actuales: {describir_fecha(ahora)}, {describir_hora(ahora)}."
