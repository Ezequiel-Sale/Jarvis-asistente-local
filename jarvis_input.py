"""Mouse y teclado de la PC desde el modo touchpad de la app del celular.

Solo se usa con el control activado (herramienta de riesgo alto
``mobile_remote_control``, confirmada en la PC o con huella en el celular). Los
eventos llegan en lotes a ``POST /api/input``:

- ``{"t": "move", "dx": 12, "dy": -3}``: movimiento relativo (con la aceleración del mouse de Windows).
- ``{"t": "click", "b": "left"|"right"|"middle"}``, ``{"t": "down"|"up", "b": ...}``: botones.
- ``{"t": "scroll", "dy": 2}``: rueda (positivo = hacia arriba), en «muescas».
- ``{"t": "text", "s": "hola"}``: escribe el texto tal cual (Unicode, no depende del teclado).
- ``{"t": "key", "k": "enter"|"backspace"|"esc"|"tab"|"alttab"|"win"|"left"|...}``.

``eventos_a_entradas`` es pura (se prueba sin tocar el sistema); ``aplicar`` las envía
con ``SendInput``. Jarvis corre como administrador, así que funciona también sobre
ventanas elevadas.
"""

import os
from collections.abc import Callable

MAX_EVENTOS = 200
MAX_TEXTO = 500
MAX_MOVIMIENTO = 2000
MAX_SCROLL = 20

MOUSEEVENTF_MOVE = 0x0001
_BOTONES = {"left": (0x0002, 0x0004), "right": (0x0008, 0x0010), "middle": (0x0020, 0x0040)}
MOUSEEVENTF_WHEEL = 0x0800
WHEEL_DELTA = 120
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
VK_MENU = 0x12
TECLAS = {
    "enter": 0x0D, "backspace": 0x08, "esc": 0x1B, "tab": 0x09, "win": 0x5B, "delete": 0x2E,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28, "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pagedown": 0x22, "space": 0x20, "f5": 0x74, "f11": 0x7A,
}

# Entrada simple: ("mouse", dx, dy, flags, datos) o ("tecla", vk, escaneo, flags).
Entrada = tuple


class EventoNoValido(ValueError):
    pass


def _entero(valor, limite: int) -> int:
    try:
        numero = int(round(float(valor)))
    except (TypeError, ValueError) as error:
        raise EventoNoValido("Número no válido en un evento del touchpad.") from error
    return max(-limite, min(limite, numero))


def _tecla(vk: int) -> list[Entrada]:
    return [("tecla", vk, 0, 0), ("tecla", vk, 0, KEYEVENTF_KEYUP)]


def eventos_a_entradas(eventos: list) -> list[Entrada]:
    if not isinstance(eventos, list) or len(eventos) > MAX_EVENTOS:
        raise EventoNoValido(f"Se esperaba una lista de como mucho {MAX_EVENTOS} eventos.")
    entradas: list[Entrada] = []
    for evento in eventos:
        if not isinstance(evento, dict):
            raise EventoNoValido("Evento del touchpad no válido.")
        tipo = evento.get("t")
        if tipo == "move":
            dx, dy = _entero(evento.get("dx", 0), MAX_MOVIMIENTO), _entero(evento.get("dy", 0), MAX_MOVIMIENTO)
            if dx or dy:
                entradas.append(("mouse", dx, dy, MOUSEEVENTF_MOVE, 0))
        elif tipo in ("click", "down", "up"):
            boton = _BOTONES.get(str(evento.get("b", "left")))
            if boton is None:
                raise EventoNoValido("Botón del mouse no válido.")
            if tipo in ("click", "down"):
                entradas.append(("mouse", 0, 0, boton[0], 0))
            if tipo in ("click", "up"):
                entradas.append(("mouse", 0, 0, boton[1], 0))
        elif tipo == "scroll":
            muescas = _entero(evento.get("dy", 0), MAX_SCROLL)
            if muescas:
                entradas.append(("mouse", 0, 0, MOUSEEVENTF_WHEEL, muescas * WHEEL_DELTA))
        elif tipo == "text":
            texto = str(evento.get("s", ""))[:MAX_TEXTO]
            for letra in texto.replace("\r\n", "\n"):
                if letra == "\n":
                    entradas += _tecla(TECLAS["enter"])
                    continue
                for unidad in _utf16(letra):
                    entradas += [("tecla", 0, unidad, KEYEVENTF_UNICODE),
                                 ("tecla", 0, unidad, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)]
        elif tipo == "key":
            nombre = str(evento.get("k", ""))
            if nombre == "alttab":
                entradas += [("tecla", VK_MENU, 0, 0), *_tecla(TECLAS["tab"]), ("tecla", VK_MENU, 0, KEYEVENTF_KEYUP)]
            elif nombre in TECLAS:
                entradas += _tecla(TECLAS[nombre])
            else:
                raise EventoNoValido(f"Tecla no válida: {nombre}.")
        else:
            raise EventoNoValido(f"Tipo de evento no válido: {tipo}.")
    return entradas


def _utf16(letra: str) -> list[int]:
    codificada = letra.encode("utf-16-le")
    return [int.from_bytes(codificada[i:i + 2], "little") for i in range(0, len(codificada), 2)]


def _send_input(entradas: list[Entrada]) -> int:
    if os.name != "nt":
        raise OSError("El control del mouse y el teclado solo está disponible en Windows.")
    import ctypes
    from ctypes import wintypes

    ULONG_PTR = ctypes.c_size_t

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]

    class _UNION(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", _UNION)]

    arreglo = (INPUT * len(entradas))()
    for i, entrada in enumerate(entradas):
        if entrada[0] == "mouse":
            _, dx, dy, flags, datos = entrada
            arreglo[i].type = 0
            arreglo[i].u.mi = MOUSEINPUT(dx, dy, ctypes.c_uint32(datos).value, flags, 0, 0)
        else:
            _, vk, escaneo, flags = entrada
            arreglo[i].type = 1
            arreglo[i].u.ki = KEYBDINPUT(vk, escaneo, flags, 0, 0)
    enviadas = ctypes.windll.user32.SendInput(len(entradas), arreglo, ctypes.sizeof(INPUT))
    if enviadas != len(entradas):
        raise OSError("Windows no aceptó todas las pulsaciones (¿pantalla de bloqueo o UAC?).")
    return enviadas


def aplicar(eventos: list, enviar: Callable[[list[Entrada]], int] = _send_input) -> int:
    """Convierte y envía los eventos; devuelve cuántas entradas se enviaron."""
    entradas = eventos_a_entradas(eventos)
    return enviar(entradas) if entradas else 0
