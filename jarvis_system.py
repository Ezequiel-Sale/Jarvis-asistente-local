import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from jarvis_commands import normalizar_nombre_juego

GAME_DIRECTORIES = (
    Path(r"C:\Games"),
    Path(r"C:\Program Files (x86)\Steam\steamapps\common"),
)

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
    r"downloader|repair|updater|rapidcrc|epicwebhelper)",
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
    nombres = ", ".join(sorted(ejecutable.name for ejecutable in candidatos))
    raise ValueError(
        f"Encontré varios ejecutables posibles en «{carpeta.name}»: {nombres}."
    )

def localizar_ejecutable_juego(nombre: str) -> tuple[str, Path]:
    coincidencias = [
        carpeta
        for raiz in GAME_DIRECTORIES
        if raiz.is_dir()
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


def abrir_programa(target: str) -> str:
    """Abre un programa, acceso directo, documento o carpeta elegido por el usuario."""
    ruta = Path(str(target).strip().strip('"')).expanduser()
    if not ruta.exists():
        raise FileNotFoundError(f"No encuentro «{ruta}».")
    os.startfile(str(ruta))  # type: ignore[attr-defined]  # solo Windows
    return f"Abrí «{ruta.stem or ruta.name}»."
