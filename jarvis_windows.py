"""Mover ventanas de un monitor a otro («manda Brave a la TV», «trae todo al monitor 1»).

- Las ventanas se buscan por programa (nombre del ejecutable o alias) o por título.
- «todo» mueve todas las ventanas visibles salvo las de Jarvis.
- «esta ventana» es la ventana activa (si es Jarvis, la siguiente en primer plano).
- Una ventana maximizada se restaura, se mueve y se vuelve a maximizar; una
  normal conserva su posición relativa y su tamaño (ajustado si no cabe).
- Los números de monitor son los fijos (``jarvis_vision.listar_monitores``).
- Coordenadas físicas: el hilo usa reconocimiento de DPI por monitor (v2).
"""

import ctypes
import logging
import os
import unicodedata
from ctypes import wintypes
from dataclasses import dataclass

ALIAS = {
    "brave": ("brave",), "navegador": ("brave", "chrome", "msedge", "firefox"),
    "chrome": ("chrome",), "edge": ("msedge",), "firefox": ("firefox",),
    "discord": ("discord",), "spotify": ("spotify",), "steam": ("steamwebhelper", "steam"),
    "whatsapp": ("whatsapp", "whatsapp.root"), "explorador": ("explorer",),
    "explorador de archivos": ("explorer",), "carpeta": ("explorer",),
    "visual studio code": ("code",), "vscode": ("code",), "code": ("code",),
    "obs": ("obs64", "obs"), "terminal": ("windowsterminal",), "notepad": ("notepad",),
    "bloc de notas": ("notepad",),
}
TODO = {"todo", "todas", "todas las ventanas", "todo lo abierto"}
ACTIVA = {"esta ventana", "la ventana activa", "ventana activa", "esta", "esto"}

SW_MAXIMIZE, SW_RESTORE = 3, 9
SWP_NOZORDER, SWP_NOACTIVATE = 0x0004, 0x0010
GWL_EXSTYLE, WS_EX_TOOLWINDOW = -20, 0x00000080
DWMWA_CLOAKED = 14
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


class ErrorVentana(Exception):
    pass


@dataclass(frozen=True)
class Ventana:
    hwnd: int
    titulo: str
    programa: str  # ejecutable sin «.exe», en minúsculas
    pid: int


def normalizar(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in texto if not unicodedata.combining(c)).strip()


PROPIAS = {"jarvis", "j.a.r.v.i.s.", "j.a.r.v.i.s", "el asistente", "ti", "vos", "tu ventana"}


def elegir(ventanas: list[Ventana], app: str, propio_pid: int) -> list[Ventana]:
    """Ventanas que corresponden a ``app``. Las de Jarvis solo si se lo nombra («mueve Jarvis al
    monitor 2»): «todo» y los demás nombres no las arrastran."""
    clave = normalizar(app)
    if clave in PROPIAS:
        propias = [v for v in ventanas if v.pid == propio_pid]
        principal = [v for v in propias if "j.a.r.v.i.s" in normalizar(v.titulo)]
        return principal[:1] or propias[:1]
    ajenas = [v for v in ventanas if v.pid != propio_pid]
    if clave in TODO:
        return ajenas
    programas = ALIAS.get(clave, (clave.replace(" ", ""),))
    por_programa = [v for v in ajenas if v.programa in programas]
    if por_programa:
        return por_programa
    por_titulo = [v for v in ajenas if clave and clave in normalizar(v.titulo)]
    if por_titulo:
        return por_titulo
    parecido = programa_parecido(clave, {v.programa for v in ajenas if v.programa})
    return [v for v in ajenas if v.programa == parecido] if parecido else []


def clave_fonetica(texto: str) -> str:
    """Forma simplificada para comparar nombres mal transcritos.

    Whisper escribe en español lo que oye de un nombre en inglés: «Claude» →
    «Cloudy», «clau de», «Clouder», «Cláudel». Sin espacios, con «ou/ow/aw» → «au»,
    «y» → «i», letras que suenan igual unificadas y sin la terminación débil
    (-e, -i, -er, -el), todas quedan «claud».
    """
    import re

    clave = re.sub(r"[^a-z]", "", normalizar(texto))
    for viejo, nuevo in (("gua", "wa"), ("hua", "wa"), ("ou", "au"), ("ow", "au"), ("aw", "au"), ("y", "i"),
                         ("z", "s"), ("v", "b"),
                         ("ck", "c"), ("k", "c"), ("qu", "c"), ("ph", "f"), ("h", "")):
        clave = clave.replace(viejo, nuevo)
    clave = re.sub(r"(.)\1+", r"\1", clave)  # letras dobles
    return re.sub(r"(?:er|el|e|i)$", "", clave) if len(clave) > 4 else clave


def programa_parecido(nombre: str, programas: set[str], minimo: float = 0.8) -> str | None:
    """El programa abierto que más se parece a ``nombre`` (si hay uno claro), o None."""
    from difflib import SequenceMatcher

    buscado = clave_fonetica(nombre)
    if len(buscado) < 3:
        return None
    puntajes = sorted(((SequenceMatcher(None, buscado, clave_fonetica(p)).ratio(), p) for p in programas),
                      reverse=True)
    if not puntajes or puntajes[0][0] < minimo:
        return None
    if len(puntajes) > 1 and puntajes[1][0] >= puntajes[0][0] - 0.05:
        return None  # dos parecidos por igual: mejor no adivinar
    logging.info("«%s» se tomó como la ventana de %s (nombre parecido).", nombre, puntajes[0][1])
    return puntajes[0][1]


def nombres_para_voz(ventanas: list[Ventana], propio_pid: int) -> list[str]:
    """Nombres de los programas abiertos para la pista de Whisper («Claude», «Discord»…)."""
    import jarvis_system_info

    nombres = []
    for ventana in ventanas:
        if ventana.pid == propio_pid or not ventana.programa:
            continue
        nombre = jarvis_system_info.nombre_aplicacion(ventana.programa + ".exe", ventana.titulo)
        if nombre and len(nombre) <= 24 and nombre not in nombres:
            nombres.append(nombre)
    return nombres


def nuevo_rect(
    ventana: tuple[int, int, int, int],
    origen: tuple[int, int, int, int],
    destino: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    """Misma posición relativa en el área de trabajo de destino; tamaño ajustado si no cabe."""
    l, t, r, b = ventana
    ol, ot, or_, ob = origen
    dl, dt, dr, db = destino
    ancho, alto = min(r - l, dr - dl), min(b - t, db - dt)
    fx = (l - ol) / max(1, (or_ - ol) - (r - l)) if (or_ - ol) > (r - l) else 0.0
    fy = (t - ot) / max(1, (ob - ot) - (b - t)) if (ob - ot) > (b - t) else 0.0
    fx, fy = min(max(fx, 0.0), 1.0), min(max(fy, 0.0), 1.0)
    x = dl + round(fx * ((dr - dl) - ancho))
    y = dt + round(fy * ((db - dt) - alto))
    return x, y, x + ancho, y + alto


# --- Windows -------------------------------------------------------------------

class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]


class _WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [("length", wintypes.UINT), ("flags", wintypes.UINT), ("showCmd", wintypes.UINT),
                ("ptMinPosition", wintypes.POINT), ("ptMaxPosition", wintypes.POINT),
                ("rcNormalPosition", wintypes.RECT)]


def _user32():
    if os.name != "nt":
        raise ErrorVentana("Mover ventanas solo está disponible en Windows.")
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user32.MonitorFromWindow.restype = wintypes.HMONITOR
    user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    user32.MonitorFromPoint.restype = wintypes.HMONITOR
    user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
    user32.GetWindowLongW.restype = ctypes.c_long
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindow.restype = wintypes.HWND
    user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    return user32


def _programa(pid: int) -> str:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        buffer = ctypes.create_unicode_buffer(1024)
        tam = wintypes.DWORD(len(buffer))
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(tam)):
            return os.path.splitext(os.path.basename(buffer.value))[0].lower()
        return ""
    finally:
        kernel32.CloseHandle(handle)


def _es_ventana_de_usuario(user32, hwnd) -> bool:
    if not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, 4):  # GW_OWNER
        return False
    if user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
        return False
    if user32.GetWindowTextLengthW(hwnd) == 0:
        return False
    oculta = wintypes.DWORD()
    dwm = ctypes.WinDLL("dwmapi")
    if dwm.DwmGetWindowAttribute(wintypes.HWND(hwnd), DWMWA_CLOAKED, ctypes.byref(oculta),
                                 ctypes.sizeof(oculta)) == 0 and oculta.value:
        return False  # ventanas de otros escritorios virtuales o apps suspendidas
    return True


def listar_ventanas() -> list[Ventana]:
    user32 = _user32()
    encontradas: list[Ventana] = []
    callback_t = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def recolectar(hwnd, _lparam):
        try:
            if _es_ventana_de_usuario(user32, hwnd):
                largo = user32.GetWindowTextLengthW(hwnd)
                titulo = ctypes.create_unicode_buffer(largo + 1)
                user32.GetWindowTextW(hwnd, titulo, largo + 1)
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                encontradas.append(Ventana(int(hwnd), titulo.value, _programa(pid.value), pid.value))
        except Exception:
            logging.debug("No se pudo leer una ventana.", exc_info=True)
        return True

    user32.EnumWindows(callback_t(recolectar), 0)
    return encontradas


def ventana_activa(ventanas: list[Ventana], propio_pid: int) -> list[Ventana]:
    """La ventana en primer plano; si es Jarvis, la siguiente (EnumWindows va en orden Z)."""
    user32 = _user32()
    activa = user32.GetForegroundWindow()
    for v in ventanas:
        if v.hwnd == activa and v.pid != propio_pid:
            return [v]
    ajenas = [v for v in ventanas if v.pid != propio_pid]
    return ajenas[:1]


def _area_trabajo(user32, hmonitor) -> tuple[int, int, int, int]:
    info = _MONITORINFO()
    info.cbSize = ctypes.sizeof(info)
    if not user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
        raise ErrorVentana("No pude leer el área del monitor.")
    r = info.rcWork
    return r.left, r.top, r.right, r.bottom


def _monitor_destino(user32, monitor) -> int:
    centro = wintypes.POINT(monitor.x + monitor.ancho // 2, monitor.y + monitor.alto // 2)
    return user32.MonitorFromPoint(centro, 2)  # MONITOR_DEFAULTTONEAREST


def mover(ventana: Ventana, monitor) -> bool:
    """Mueve una ventana al monitor (``jarvis_vision.Monitor``). True si quedó allí."""
    user32 = _user32()
    anterior = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    try:
        destino_h = _monitor_destino(user32, monitor)
        if user32.MonitorFromWindow(ventana.hwnd, 2) == destino_h:
            return True  # ya estaba
        colocacion = _WINDOWPLACEMENT()
        colocacion.length = ctypes.sizeof(colocacion)
        user32.GetWindowPlacement(ventana.hwnd, ctypes.byref(colocacion))
        maximizada = colocacion.showCmd == SW_MAXIMIZE
        if maximizada or user32.IsIconic(ventana.hwnd):
            user32.ShowWindow(ventana.hwnd, SW_RESTORE)
        rect = wintypes.RECT()
        user32.GetWindowRect(ventana.hwnd, ctypes.byref(rect))
        origen = _area_trabajo(user32, user32.MonitorFromWindow(ventana.hwnd, 2))
        x0, y0, x1, y1 = nuevo_rect(
            (rect.left, rect.top, rect.right, rect.bottom), origen, _area_trabajo(user32, destino_h)
        )
        user32.SetWindowPos(ventana.hwnd, None, x0, y0, x1 - x0, y1 - y0, SWP_NOZORDER | SWP_NOACTIVATE)
        if maximizada:
            user32.ShowWindow(ventana.hwnd, SW_MAXIMIZE)
        return user32.MonitorFromWindow(ventana.hwnd, 2) == destino_h
    finally:
        if anterior:
            user32.SetThreadDpiAwarenessContext(anterior)


def mover_ventanas(app: str, monitor) -> tuple[int, int, list[str]]:
    """(movidas, total, programas) de las ventanas de ``app`` hacia ``monitor``."""
    ventanas = listar_ventanas()
    propio = os.getpid()
    elegidas = ventana_activa(ventanas, propio) if normalizar(app) in ACTIVA else elegir(ventanas, app, propio)
    if not elegidas:
        raise ErrorVentana(f"No encontré ninguna ventana abierta de «{app}».")
    movidas = 0
    for ventana in elegidas:
        try:
            movidas += mover(ventana, monitor)
        except Exception:
            logging.exception("No se pudo mover la ventana de %s.", ventana.programa)
    programas = sorted({v.programa or v.titulo for v in elegidas})
    return movidas, len(elegidas), programas


def ventanas_en_monitor(app: str, monitor) -> tuple[int, int]:
    """Para verificar: (cuántas de ``app`` están en ``monitor``, cuántas hay)."""
    user32 = _user32()
    anterior = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    try:
        destino = _monitor_destino(user32, monitor)
        ventanas = listar_ventanas()
        propio = os.getpid()
        elegidas = ventana_activa(ventanas, propio) if normalizar(app) in ACTIVA else elegir(ventanas, app, propio)
        return sum(user32.MonitorFromWindow(v.hwnd, 2) == destino for v in elegidas), len(elegidas)
    finally:
        if anterior:
            user32.SetThreadDpiAwarenessContext(anterior)


# --- Minimizar, maximizar, restaurar y traer al frente («minimiza Discord») -------------

SW_MINIMIZE = 6
ACCIONES_ESTADO = {
    "window_minimize": ("minimicé", SW_MINIMIZE),
    "window_maximize": ("maximicé", SW_MAXIMIZE),
    "window_restore": ("restauré", SW_RESTORE),
    "window_focus": ("traje al frente", None),
}
VK_MENU, KEYEVENTF_KEYUP = 0x12, 0x0002


def enfocar(hwnd: int, user32=None) -> bool:
    """Trae una ventana al frente (la restaura si estaba minimizada).

    Windows solo deja cambiar el foco a la app que lo tiene; pulsar y soltar Alt
    antes de ``SetForegroundWindow`` es el truco habitual para que lo permita aunque
    la orden llegue por voz con Jarvis de fondo.
    """
    user32 = user32 or _user32()
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    user32.keybd_event(VK_MENU, 0, 0, 0)
    user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
    return bool(user32.SetForegroundWindow(hwnd))


def cambiar_estado(app: str, accion: str, *, ventanas: list[Ventana] | None = None,
                   propio_pid: int | None = None, mostrar=None) -> tuple[int, list[str]]:
    """Aplica ``accion`` a las ventanas de ``app``; devuelve (cuántas, programas).

    «traer al frente» usa solo la primera (la de más arriba); el resto, todas.
    """
    if accion not in ACCIONES_ESTADO:
        raise ErrorVentana(f"Acción de ventana no válida: {accion}.")
    ventanas = listar_ventanas() if ventanas is None else ventanas
    propio = os.getpid() if propio_pid is None else propio_pid
    clave = normalizar(app)
    if clave in ACTIVA:
        elegidas = ventana_activa(ventanas, propio)
    else:
        elegidas = elegir(ventanas, app, propio)
    if not elegidas:
        raise ErrorVentana(f"No encontré ninguna ventana abierta de «{app}».")
    if accion == "window_focus":
        elegidas = elegidas[:1]
    if mostrar is None:
        user32 = _user32()

        def mostrar(ventana: Ventana, comando: int | None) -> bool:
            if comando is None:
                return enfocar(ventana.hwnd, user32)
            user32.ShowWindow(ventana.hwnd, comando)
            return True

    hechas = 0
    for ventana in elegidas:
        try:
            hechas += bool(mostrar(ventana, ACCIONES_ESTADO[accion][1]))
        except Exception:
            logging.exception("No se pudo cambiar la ventana de %s.", ventana.programa)
    return hechas, sorted({v.programa or v.titulo for v in elegidas})


# --- Página anterior, siguiente y recargar en Brave ------------------------------------

TECLAS_NAVEGADOR = {
    "back": (("tecla", 0x12, 0, 0), ("tecla", 0x25, 0, 0), ("tecla", 0x25, 0, 2), ("tecla", 0x12, 0, 2)),
    "forward": (("tecla", 0x12, 0, 0), ("tecla", 0x27, 0, 0), ("tecla", 0x27, 0, 2), ("tecla", 0x12, 0, 2)),
    "reload": (("tecla", 0x74, 0, 0), ("tecla", 0x74, 0, 2)),  # F5
}


def ventana_brave(ventanas: list[Ventana], activa: int | None) -> Ventana | None:
    """La de Brave en primer plano o, si no, la última usada (la más arriba en orden Z)."""
    de_brave = [v for v in ventanas if v.programa == "brave"]
    for ventana in de_brave:
        if ventana.hwnd == activa:
            return ventana
    return de_brave[0] if de_brave else None


def navegar_historial(accion: str, *, enviar=None, esperar: float = 0.25) -> str:
    """Alt+← (atrás), Alt+→ (adelante) o F5 (recargar) en la ventana de Brave."""
    import time

    if accion not in TECLAS_NAVEGADOR:
        raise ErrorVentana(f"Acción del navegador no válida: {accion}.")
    user32 = _user32()
    ventana = ventana_brave(listar_ventanas(), user32.GetForegroundWindow())
    if ventana is None:
        raise ErrorVentana("No encontré ninguna ventana abierta de Brave.")
    if ventana.hwnd != user32.GetForegroundWindow():
        enfocar(ventana.hwnd, user32)
        time.sleep(esperar)  # que Brave tenga el foco antes de las teclas
    if enviar is None:
        import jarvis_input

        enviar = jarvis_input._send_input
    enviar(list(TECLAS_NAVEGADOR[accion]))
    return {"back": "Volví a la página anterior en Brave.",
            "forward": "Fui a la página siguiente en Brave.",
            "reload": "Recargué la página en Brave (F5)."}[accion]
