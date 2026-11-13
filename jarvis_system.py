import logging
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from jarvis_commands import normalizar_nombre_juego

GAME_DIRECTORIES = (
    Path(r"C:\Games"),
    Path(r"C:\Program Files (x86)\Steam\steamapps\common"),
)
# En cada disco (C:, D:, E:…) se buscan también estas carpetas de juegos.
GAME_FOLDERS_PER_DISK = ("Games", "Epic Games", "XboxGames", r"SteamLibrary\steamapps\common")


def carpetas_juegos(discos=None, steam=None) -> list[Path]:
    """Carpetas de juegos que existen: las fijas, «Games» de la carpeta del usuario, «Games»,
    «Epic Games», «XboxGames» y «SteamLibrary» en cada disco, Epic de Program Files y todas
    las bibliotecas de Steam.

    La usan el lanzador («abre Elden Ring») y el modo juego. Antes el lanzador solo
    miraba C:/Games y el Steam de C: (no veía D:/Games ni E:/Games).
    """
    if discos is None:
        import jarvis_system_info

        try:
            discos = [Path(d.unidad + "\\") for d in jarvis_system_info.listar_discos()]
        except Exception:
            logging.debug("No se pudieron leer los discos para buscar juegos.", exc_info=True)
            discos = []
    if steam is None:
        from jarvis_games_watch import bibliotecas_steam

        steam = bibliotecas_steam()
    programas = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    # «Games» de la carpeta del usuario (p. ej. C:\Users\…\Games\Hookshot, un emulador).
    candidatas = [*GAME_DIRECTORIES, Path.home() / "Games", *steam, programas / "Epic Games"]
    candidatas += [disco / sub for disco in discos for sub in GAME_FOLDERS_PER_DISK]
    vistas, carpetas = set(), []
    for carpeta in candidatas:
        clave = str(carpeta).lower().rstrip("\\/")
        if clave not in vistas and carpeta.is_dir():
            vistas.add(clave)
            carpetas.append(carpeta)
    return carpetas

GAME_EXECUTABLE_PREFERENCES = {
    "assassinscreedshadows": ("ACShadows.exe",),
    "callofdutymodernwarfare42026": ("bootstrapper.exe",),
    "reddeadredemption2": ("Launcher.exe",),
    "senuassagahellbladeii": (
        "Hellblade2.exe",
        r"Hellblade2\Binaries\Win64\Hellblade2-Win64-Shipping.exe",
    ),
    "garrysmod": ("gmod.exe", "gmod_win64.exe"),
    "left4dead2": ("left4dead2.exe",),
    "sekiroshadowsdietwice": ("sekiro.exe",),
    "projectzomboid": ("ProjectZomboid64.exe",),
}

GAME_NAME_ALIASES = {
    "assassinscreedshadows": (
        "assassins creed",
        "ac shadows",
    ),
    "callofdutymodernwarfare42026": (
        "call of duty",
        "modern warfare",
        "modern warfare 4",
        "cod",
        "mw4",
    ),
    "reddeadredemption2": (
        "red dead",
        "red dead 2",
        "rdr",
        "rdr2",
    ),
    "sekiroshadowsdietwice": (
        "sekiro",
    ),
    "senuassagahellbladeii": (
        "hellblade",
        "hellblade 2",
        "senua",
        "senuas saga hellblade 2",
        "senua saga hellblade ii",
        "senua saga hellblade 2",
    ),
    "garrysmod": (
        "garrys mod",
        "gmod",
    ),
    "left4dead2": (
        "left 4 dead",
        "l4d2",
        "left for dead",
    ),
    "projectzomboid": (
        "zomboid",
        "somboid",
    ),
}

GAME_EXECUTABLE_IGNORED_DIRECTORIES = {
    "commonredist",
    "artworkminisoundtrack",
    "bin",
    "easyanticheat",
    "gamingrepair",
    "installerresources",
    "jre",
    "jre64",
    "redist",
    "redistributables",
    "runtime",
    "thirdparty",
}

GAME_EXECUTABLE_IGNORED_NAME = re.compile(
    r"^(?:unins|setup|install|vc_redist|dxsetup|dxwebsetup)"
    r"|(?:crash|report|installer|service|broker|cleaner|attestation|"
    r"downloader|repair|updater|rapidcrc|epicwebhelper)"
    # Paquetes de actualización («Red Dead Redemption 2 update 1311.23 - 1491.50.exe»):
    # con ▶ JUGAR se habría ejecutado la actualización en vez del juego.
    r"|\bupdate\b",
    re.IGNORECASE,
)

def encontrar_brave() -> Path | None:
    ejecutable = shutil.which("brave.exe") or shutil.which("brave")
    if ejecutable:
        return Path(ejecutable)

    directorios = (
        Path(os.environ.get("PROGRAMFILES", "C:\\Program Files")),
        Path(os.environ.get("PROGRAMFILES(X86)", "C:\\Program Files (x86)")),
        Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")),
    )
    for directorio in directorios:
        candidato = (
            directorio
            / "BraveSoftware"
            / "Brave-Browser"
            / "Application"
            / "brave.exe"
        )
        if candidato.is_file():
            return candidato
    return None

def obtener_ventanas_brave(brave: Path) -> list[int]:
    if os.name != "nt":
        return []

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    windows: list[int] = []
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    ]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    process_query_limited_information = 0x1000

    open_process = kernel32.OpenProcess
    open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    open_process.restype = wintypes.HANDLE
    query_image = kernel32.QueryFullProcessImageNameW
    query_image.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    query_image.restype = wintypes.BOOL

    def is_brave_window(hwnd: int) -> bool:
        process_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        process = open_process(
            process_query_limited_information,
            False,
            process_id.value,
        )
        if not process:
            return False
        try:
            image_buffer = ctypes.create_unicode_buffer(32768)
            image_length = wintypes.DWORD(len(image_buffer))
            if not query_image(process, 0, image_buffer, ctypes.byref(image_length)):
                return False
            return (
                Path(image_buffer.value).resolve().as_posix().casefold()
                == brave.resolve().as_posix().casefold()
            )
        except OSError:
            return False
        finally:
            kernel32.CloseHandle(process)

    enum_callback = ctypes.WINFUNCTYPE(
        wintypes.BOOL,
        wintypes.HWND,
        wintypes.LPARAM,
    )
    user32.EnumWindows.argtypes = [enum_callback, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL

    def collect_windows(hwnd: int, _lparam: int) -> bool:
        del _lparam
        if user32.IsWindowVisible(hwnd) and is_brave_window(hwnd):
            windows.append(hwnd)
        return True

    callback = enum_callback(collect_windows)
    user32.EnumWindows(callback, 0)
    return windows

def interactuar_con_ventana_brave(
    brave: Path,
    atajo: int,
    texto: str | None = None,
) -> bool:
    if os.name != "nt":
        return False

    import ctypes
    from ctypes import wintypes

    windows = obtener_ventanas_brave(brave)
    if not windows:
        return False

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.IsZoomed.argtypes = [wintypes.HWND]
    user32.IsZoomed.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.BringWindowToTop.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.keybd_event.argtypes = [
        wintypes.BYTE,
        wintypes.BYTE,
        wintypes.DWORD,
        ctypes.c_size_t,
    ]
    user32.keybd_event.restype = None
    foreground = user32.GetForegroundWindow()
    target = foreground if foreground in windows else windows[0]
    show_command = 3 if user32.IsZoomed(target) else 9
    user32.ShowWindow(target, show_command)
    user32.BringWindowToTop(target)
    user32.SetForegroundWindow(target)
    time.sleep(0.15)
    if user32.GetForegroundWindow() != target:
        raise OSError("Windows no permitió activar la ventana existente de Brave.")

    keybd_event = user32.keybd_event
    keybd_event(0x11, 0, 0, 0)
    keybd_event(atajo, 0, 0, 0)
    keybd_event(atajo, 0, 0x0002, 0)
    keybd_event(0x11, 0, 0x0002, 0)
    if texto is None:
        return True

    time.sleep(0.05)

    for character in texto:
        scan_code = ord(character)
        keybd_event(0, scan_code, 0x0004, 0)
        keybd_event(0, scan_code, 0x0004 | 0x0002, 0)
    keybd_event(0x0D, 0, 0, 0)
    keybd_event(0x0D, 0, 0x0002, 0)
    return True

def navegar_en_ventana_brave(url: str, brave: Path) -> bool:
    return interactuar_con_ventana_brave(brave, 0x4C, url)

def abrir_nueva_pestana_brave(brave: Path) -> bool:
    return interactuar_con_ventana_brave(brave, 0x54)

def cambiar_estado_ventana_brave(accion: str, brave: Path) -> bool:
    if accion not in {"minimize", "maximize"}:
        raise ValueError(f"Acción de ventana Brave desconocida: {accion}")
    ventanas = obtener_ventanas_brave(brave)
    if not ventanas:
        return False

    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL
    show_command = 6 if accion == "minimize" else 3
    user32.ShowWindow(ventanas[0], show_command)
    return True

def _nombre_coincide_con_juego(nombre: str, carpeta: str) -> bool:
    nombre_normalizado = normalizar_nombre_juego(nombre)
    carpeta_normalizada = normalizar_nombre_juego(carpeta)
    nombre_unido = nombre_normalizado.replace(" ", "")
    carpeta_unida = carpeta_normalizada.replace(" ", "")
    if len(nombre_unido) < 3:
        return False
    if nombre_unido in carpeta_unida:
        return True

    nombre_palabras = nombre_normalizado.split()
    carpeta_palabras = carpeta_normalizada.split()
    if nombre_palabras and all(palabra in carpeta_palabras for palabra in nombre_palabras):
        return True

    alias = GAME_NAME_ALIASES.get(carpeta_unida, ())
    return any(
        nombre_unido == normalizar_nombre_juego(alias_nombre).replace(" ", "")
        or all(
            palabra in normalizar_nombre_juego(alias_nombre).split()
            for palabra in nombre_palabras
        )
        for alias_nombre in alias
    )

def _buscar_ejecutables_juego(carpeta: Path) -> list[Path]:
    ejecutables: list[Path] = []
    for directorio, subdirectorios, archivos in os.walk(carpeta):
        subdirectorios[:] = [
            nombre
            for nombre in subdirectorios
            if normalizar_nombre_juego(nombre).replace(" ", "")
            not in GAME_EXECUTABLE_IGNORED_DIRECTORIES
        ]
        for archivo in archivos:
            if Path(archivo).suffix.lower() != ".exe":
                continue
            if GAME_EXECUTABLE_IGNORED_NAME.search(archivo):
                continue
            ejecutables.append(Path(directorio) / archivo)
    return ejecutables

# Ejecutables que acompañan al juego pero no lo inician (guías, bandas sonoras, ajustes,
# antitrampas, lanzadores intermedios, el «-Shipping» de Unreal…).
GAME_EXECUTABLE_SECONDARY = re.compile(
    r"guide|artbook|soundtrack|settings|config|generator|filelist|_eac|eac_|launcher|"
    r"shipping|protected|editor|benchmark|server|tool|readme|manual",
    re.IGNORECASE,
)


def _clave_ejecutable(texto: str) -> str:
    return re.sub(r"[^a-z0-9]", "", texto.lower())


def _iniciales(nombre: str) -> str:
    """«Pro Evolution Soccer 2018» → «pes2018» (los números van enteros)."""
    partes = re.findall(r"[a-z]+|\d+", nombre.lower())
    return "".join(p if p.isdigit() else p[0] for p in partes)


def _ejecutable_principal(nombre_carpeta: str, candidatos: list[Path]) -> Path | None:
    """Entre varios .exe, el que inicia el juego (o None si no hay uno claro)."""
    from difflib import SequenceMatcher

    principales = [c for c in candidatos if not GAME_EXECUTABLE_SECONDARY.search(c.stem)] or candidatos
    if len(principales) == 1:
        return principales[0]
    carpeta = _clave_ejecutable(nombre_carpeta)
    iniciales = _iniciales(nombre_carpeta)
    for candidato in principales:
        if _clave_ejecutable(candidato.stem) in (carpeta, iniciales):
            return candidato
    puntajes = sorted(((SequenceMatcher(None, carpeta, _clave_ejecutable(c.stem)).ratio(), c) for c in principales),
                      key=lambda p: p[0], reverse=True)
    if puntajes[0][0] >= 0.6 and (len(puntajes) == 1 or puntajes[0][0] - puntajes[1][0] >= 0.15):
        return puntajes[0][1]
    return None


def _elegir_ejecutable_juego(carpeta: Path) -> Path:
    preferencias = GAME_EXECUTABLE_PREFERENCES.get(
        normalizar_nombre_juego(carpeta.name).replace(" ", ""),
        (),
    )
    for nombre in preferencias:
        candidato = carpeta / nombre
        if candidato.is_file():
            return candidato

    ejecutables = _buscar_ejecutables_juego(carpeta)
    if not ejecutables:
        raise FileNotFoundError(
            f"No encontré un ejecutable de juego válido dentro de «{carpeta.name}»."
        )
    if len(ejecutables) == 1:
        return ejecutables[0]

    ejecutables_raiz = [ejecutable for ejecutable in ejecutables if ejecutable.parent == carpeta]
    if len(ejecutables_raiz) == 1:
        return ejecutables_raiz[0]
    candidatos = ejecutables_raiz or ejecutables
    elegido = _ejecutable_principal(carpeta.name, candidatos)
    if elegido is not None:
        return elegido
    nombres = ", ".join(sorted(ejecutable.name for ejecutable in candidatos))
    raise ValueError(
        f"Encontré varios ejecutables posibles en «{carpeta.name}»: {nombres}."
    )

def localizar_ejecutable_juego(nombre: str) -> tuple[str, Path]:
    coincidencias = [
        carpeta
        for raiz in carpetas_juegos()
        for carpeta in raiz.iterdir()
        if carpeta.is_dir() and _nombre_coincide_con_juego(nombre, carpeta.name)
    ]
    nombre_normalizado = normalizar_nombre_juego(nombre).replace(" ", "")
    coincidencias_exactas = [
        carpeta
        for carpeta in coincidencias
        if normalizar_nombre_juego(carpeta.name).replace(" ", "") == nombre_normalizado
    ]
    if coincidencias_exactas:
        coincidencias = coincidencias_exactas
    if not coincidencias:
        raise FileNotFoundError(
            f"No encontré «{nombre}» en las carpetas de juegos configuradas."
        )
    distintos = {normalizar_nombre_juego(c.name).replace(" ", "") for c in coincidencias}
    if len(coincidencias) > 1 and len(distintos) == 1:
        # El mismo juego en dos lugares (p. ej. el Steam de C: y una biblioteca vieja en
        # D:): se usa el primero, que sigue el orden de ``carpetas_juegos`` (fijas y
        # bibliotecas registradas en Steam antes que las de cada disco).
        coincidencias = coincidencias[:1]
    if len(coincidencias) > 1:
        opciones = ", ".join(sorted(carpeta.name for carpeta in coincidencias))
        raise ValueError(f"El nombre «{nombre}» coincide con varios juegos: {opciones}.")

    carpeta = coincidencias[0]
    return carpeta.name, _elegir_ejecutable_juego(carpeta)

def iniciar_ejecutable_juego(executable: Path) -> None:
    try:
        subprocess.Popen(
            [str(executable)],
            cwd=str(executable.parent),
            shell=False,
        )
    except OSError as error:
        if os.name != "nt" or error.winerror != 740:
            raise

        import ctypes

        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        shell_execute = shell32.ShellExecuteW
        shell_execute.argtypes = [
            ctypes.c_void_p,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_int,
        ]
        shell_execute.restype = ctypes.c_void_p
        result = shell_execute(
            None,
            "runas",
            str(executable),
            None,
            str(executable.parent),
            1,
        )
        if not result or result <= 32:
            error_code = ctypes.get_last_error() or int(result or 0)
            raise ctypes.WinError(error_code) from error


_PAQUETE_TIENDA = re.compile(r"^(?P<nombre>[^_]+)_[^_]*_[^_]*_[^_]*_(?P<editor>[^_]+)$")


def app_de_la_tienda(ruta: Path) -> str | None:
    r"""«shell:AppsFolder\Familia!App» si ``ruta`` está dentro de una app de la Tienda.

    Los .exe de ``Program Files\WindowsApps`` no se pueden ejecutar directamente
    (WinError 5, acceso denegado): hay que abrirlos por su identidad de paquete.
    La familia no incluye la versión, así que sigue valiendo cuando la app se actualiza.
    """
    partes = ruta.parts
    indice = next((i for i, p in enumerate(partes) if p.lower() == "windowsapps"), None)
    if indice is None or indice + 1 >= len(partes):
        return None
    coincidencia = _PAQUETE_TIENDA.match(partes[indice + 1])
    if coincidencia is None:
        return None
    familia = f"{coincidencia['nombre']}_{coincidencia['editor']}"
    app_id = "App"
    try:
        import xml.etree.ElementTree as ET

        manifiesto = Path(*partes[: indice + 2]) / "AppxManifest.xml"
        aplicaciones = [
            nodo for nodo in ET.parse(manifiesto).iter()
            if nodo.tag.rsplit("}", 1)[-1] == "Application" and nodo.get("Id")
        ]
        elegida = next(
            (a for a in aplicaciones
             if Path(a.get("Executable", "")).name.lower() == ruta.name.lower()),
            aplicaciones[0] if aplicaciones else None,
        )
        if elegida is not None:
            app_id = elegida.get("Id")
    except (OSError, ET.ParseError):
        pass  # sin manifiesto legible: «App» es el Id habitual
    return "shell:AppsFolder\\" + f"{familia}!{app_id}"


def abrir_programa(target: str) -> str:
    """Abre un programa, acceso directo, documento o carpeta elegido por el usuario."""
    ruta = Path(str(target).strip().strip('"')).expanduser()
    tienda = app_de_la_tienda(ruta)
    if tienda is not None:
        os.startfile(tienda)  # type: ignore[attr-defined]  # solo Windows
        return f"Abrí «{ruta.stem or ruta.name}»."
    if not ruta.exists():
        raise FileNotFoundError(f"No encuentro «{ruta}».")
    os.startfile(str(ruta))  # type: ignore[attr-defined]  # solo Windows
    return f"Abrí «{ruta.stem or ruta.name}»."


# --- Abrir aplicaciones instaladas por su nombre («abre WhatsApp») ----------------------

CACHE_APPS = 600.0
_cache_apps: tuple[float, list[tuple[str, str]]] | None = None
_NO_APPS = re.compile(r"^(?:desinstalar|uninstall|eliminar)\b|\b(?:help|ayuda|support|readme|léame)\b", re.IGNORECASE)


def _leer_apps_inicio() -> list[tuple[str, str]]:
    """(nombre, AppID) del menú Inicio con ``Get-StartApps`` (apps de la Tienda y de escritorio)."""
    import json
    import subprocess

    salida = subprocess.run(
        ["powershell", "-NoProfile", "-Command", "Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        creationflags=0x08000000 if os.name == "nt" else 0,
    ).stdout.strip()
    datos = json.loads(salida) if salida else []
    if isinstance(datos, dict):
        datos = [datos]
    return [(str(d.get("Name") or ""), str(d.get("AppID") or "")) for d in datos]


def apps_instaladas(*, leer=None, ahora: float | None = None) -> list[tuple[str, str]]:
    """Apps del menú Inicio (se guardan 10 minutos; sin enlaces web ni desinstaladores)."""
    global _cache_apps
    import time as _time

    ahora = _time.monotonic() if ahora is None else ahora
    if leer is None and _cache_apps is not None and ahora - _cache_apps[0] < CACHE_APPS:
        return _cache_apps[1]
    try:
        apps = (leer or _leer_apps_inicio)()
    except Exception:
        logging.warning("No se pudo leer la lista de aplicaciones del menú Inicio.", exc_info=True)
        apps = []
    apps = [(n, i) for n, i in apps if n and i and "://" not in i and not _NO_APPS.search(n)]
    if leer is None:
        _cache_apps = (ahora, apps)
    return apps


def buscar_app(nombre: str, apps: list[tuple[str, str]]) -> tuple[str, str] | None:
    """La app que corresponde a ``nombre``: igual, luego que empiece o contenga, luego parecida."""
    import jarvis_windows

    clave = jarvis_windows.normalizar(re.sub(
        r"^(?:(?:la|el)\s+)?(?:app(?:licacion|licación)?|programa)\s+(?:de\s+)?", "", nombre.strip(), flags=re.I))
    clave = clave.strip(" .¡!¿?")
    if len(clave) < 2:
        return None
    por_nombre = {jarvis_windows.normalizar(n): (n, i) for n, i in apps}
    if clave in por_nombre:
        return por_nombre[clave]
    empiezan = sorted((n for n in por_nombre if n.startswith(clave + " ") or (" " + clave + " ") in f" {n} "), key=len)
    if empiezan:
        return por_nombre[empiezan[0]]
    parecido = jarvis_windows.programa_parecido(clave, set(por_nombre))
    return por_nombre[parecido] if parecido else None


def abrir_app(name: str) -> str:
    """Abre una app instalada por su nombre del menú Inicio (Tienda o escritorio)."""
    encontrada = buscar_app(name, apps_instaladas())
    if encontrada is None:
        raise FileNotFoundError(f"No encontré la aplicación «{name}» en el menú Inicio.")
    nombre, app_id = encontrada
    os.startfile("shell:AppsFolder\\" + app_id)
    return f"Abrí {nombre}."


# --- Lista de juegos reconocidos (página «Juegos») --------------------------------------

# Carpetas dentro de las de juegos que no son juegos.
CARPETAS_NO_JUEGO = {"__macosx", "save games", "savegames", "directxredist", "steamworks shared",
                     "steam controller configs", "steamvr", "steamlinux runtime", "proton experimental",
                     "wallpaper_engine", "launcher"}


@dataclass(frozen=True)
class JuegoEncontrado:
    nombre: str  # nombre de la carpeta: es lo que se dice en «abre …»
    carpeta: Path
    ejecutable: Path | None
    problema: str | None = None  # por qué no se puede abrir (sin .exe, varios posibles…)


def listar_juegos(raices: list[Path] | None = None) -> list[JuegoEncontrado]:
    """Juegos de ``carpetas_juegos()`` con el .exe que se abriría, en el mismo orden de
    prioridad que el lanzador (un juego repetido en otro disco aparece una vez)."""
    vistos: set[str] = set()
    juegos: list[JuegoEncontrado] = []
    for raiz in carpetas_juegos() if raices is None else raices:
        try:
            carpetas = sorted((c for c in raiz.iterdir() if c.is_dir()), key=lambda c: c.name.casefold())
        except OSError:
            continue
        for carpeta in carpetas:
            clave = normalizar_nombre_juego(carpeta.name).replace(" ", "")
            if carpeta.name.lower() in CARPETAS_NO_JUEGO or clave in vistos:
                continue
            vistos.add(clave)
            try:
                juegos.append(JuegoEncontrado(carpeta.name, carpeta, _elegir_ejecutable_juego(carpeta)))
            except (OSError, ValueError) as error:
                juegos.append(JuegoEncontrado(carpeta.name, carpeta, None, str(error)))
    return sorted(juegos, key=lambda j: (j.ejecutable is None, j.nombre.casefold()))
