"""Hacer clic o escribir en un elemento de la pantalla que el usuario nombra.

Flujo (lo orquesta la app; aquí están las piezas):
1. ``buscar_por_accesibilidad``: busca en las ventanas del monitor pedido un
   elemento cuyo nombre contenga el texto indicado, con UI Automation de
   Windows (los navegadores Chromium y la mayoría de programas lo exponen).
   Rápido y exacto; no envía nada a ningún modelo.
2. ``buscar_por_vision`` (solo si el usuario lo autoriza al ver la captura):
   pide al modelo de visión local la posición del elemento.
3. La app muestra la captura con una marca donde se hará clic y espera
   confirmación; solo entonces se llama a la herramienta ``screen_click``.

Privacidad: los textos de la pantalla no se escriben en los registros (solo
cantidades y de dónde salió el resultado). La captura vive solo en memoria.
"""

import base64
import ctypes
import json
import logging
import re
import time
import unicodedata
from dataclasses import dataclass

import requests

import jarvis_core as core

TIEMPO_BUSQUEDA = 6.0
# Elementos más pequeños que esto son invisibles en la práctica (YouTube pone
# botones de 1×2 px junto a los títulos): hacer clic ahí no sirve.
TAMANO_MINIMO = 6
PROFUNDIDAD = 150  # las páginas web (p. ej. la cuadrícula de YouTube) superan los 45 niveles
# Tipos de control que tiene sentido pulsar o en los que se puede escribir.
_PULSABLES = {
    "HyperlinkControl": 3, "ButtonControl": 3, "ListItemControl": 2, "MenuItemControl": 3,
    "TabItemControl": 3, "CheckBoxControl": 2, "RadioButtonControl": 2, "TreeItemControl": 2,
    "ImageControl": 1, "TextControl": 1, "GroupControl": 1, "DataItemControl": 2,
}
_EDITABLES = {"EditControl": 3, "ComboBoxControl": 2, "DocumentControl": 1}


@dataclass(frozen=True)
class Objetivo:
    x: int  # coordenadas físicas del escritorio de Windows
    y: int
    nombre: str  # texto del elemento encontrado (solo se muestra en la confirmación)
    origen: str  # "accesibilidad" o "visión"
    caja: tuple[int, int, int, int] | None = None  # izquierda, arriba, derecha, abajo


# Palabras que se dicen de una forma y se escriben de otra en los títulos.
_EQUIVALENTES = {"versus": "vs", "contra": "vs", "y": "", "and": "", "numero": "", "n": ""}


def normalizar(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", (texto or "").casefold())
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", texto).split())


def normalizar_busqueda(texto: str) -> str:
    """Para comparar con los nombres de la pantalla: además unifica «versus»/«vs»
    y quita palabras que suelen faltar en los títulos («y»)."""
    palabras = normalizar(texto).split()
    return " ".join(p for p in (_EQUIVALENTES.get(p, p) for p in palabras) if p)


def _per_monitor_dpi() -> None:
    """Coordenadas físicas en este hilo (como ``jarvis_vision.listar_monitores``)."""
    try:
        ctypes.windll.user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        pass


_SINONIMOS = {
    "busqueda": ("busc", "search"),
    "buscar": ("busc", "search"),
    "buscador": ("busc", "search"),
    "mensaje": ("mensaje", "message", "escrib"),
    "direccion": ("direcc", "address", "url"),
}


def variantes(consulta: str) -> list[str]:
    """La consulta y sus equivalentes habituales en los nombres de los controles."""
    normal = normalizar(consulta)
    return [normal, *_SINONIMOS.get(normal, ())]


def _palabras_parecidas(consulta_n: str, nombre_n: str) -> bool:
    """Todas las palabras pedidas aparecen en el nombre, aunque con pequeñas
    diferencias (errores de transcripción: «enamore»/«enamoré», «subscribirse»)."""
    from difflib import SequenceMatcher

    palabras_nombre = nombre_n.split()
    pedidas = [p for p in consulta_n.split() if len(p) >= 2]
    if not pedidas or not palabras_nombre:
        return False
    faltan = 0
    for pedida in pedidas:
        if not any(
            palabra == pedida
            or (len(pedida) >= 4 and palabra.startswith(pedida))
            or (len(pedida) >= 4 and SequenceMatcher(None, pedida, palabra).ratio() >= 0.8)
            for palabra in palabras_nombre
        ):
            faltan += 1
    # En frases largas (4+ palabras) se tolera que falte una: Whisper a veces
    # escribe una palabra distinta a la del título.
    return faltan == 0 or (len(pedidas) >= 4 and faltan == 1)


def fraccion_palabras(consulta: str, nombre: str) -> float:
    """Proporción de palabras pedidas presentes en el nombre (para diagnosticar)."""
    pedidas = [p for p in normalizar_busqueda(consulta).split() if len(p) >= 2]
    if not pedidas:
        return 0.0
    presentes = set(normalizar_busqueda(nombre).split())
    return sum(1 for p in pedidas if p in presentes) / len(pedidas)


def puntuar(nombre: str, consulta: str, tipo: str, escribir: bool) -> int:
    """Cuánto se parece un elemento a lo pedido (0 = no sirve)."""
    nombre_n, consulta_n = normalizar_busqueda(nombre), normalizar_busqueda(consulta)
    if not nombre_n or not consulta_n:
        return 0
    pesos = _EDITABLES if escribir else _PULSABLES
    if tipo not in pesos:
        return 0
    if nombre_n == consulta_n:
        afinidad = 30
    elif nombre_n.startswith(consulta_n):
        afinidad = 20
    elif consulta_n in nombre_n:
        afinidad = 10
    elif _palabras_parecidas(consulta_n, nombre_n):
        afinidad = 5
    else:
        return 0
    # Preferir nombres cortos: el título del vídeo antes que su descripción larga.
    return afinidad + pesos[tipo] * 5 - min(len(nombre_n) // 40, 8)


def _dentro(monitor, x: int, y: int) -> bool:
    return monitor.x <= x < monitor.x + monitor.ancho and monitor.y <= y < monitor.y + monitor.alto


ESPERA_CHROMIUM = 1.5
CLASE_CHROMIUM = "Chrome_WidgetWin_1"  # Brave, Chrome, Edge, Discord, VS Code…


def buscar_por_accesibilidad(
    monitor, consulta: str, *, escribir: bool = False, excluir_pid: int | None = None
) -> Objetivo | None:
    """Mejor elemento visible del monitor cuyo nombre contiene ``consulta``.

    Brave y demás Chromium solo exponen el contenido de la página después de que
    un cliente de accesibilidad lo pide: la primera búsqueda ve la ventana casi
    vacía. Si no hay coincidencias y en el monitor hay una ventana Chromium, se
    espera un momento y se busca otra vez (medido 2026-11-08: 0 % → 100 %).
    """
    objetivo, hay_chromium = _buscar_una_vez(monitor, consulta, escribir, excluir_pid)
    if objetivo is None and hay_chromium:
        time.sleep(ESPERA_CHROMIUM)
        objetivo, _ = _buscar_una_vez(monitor, consulta, escribir, excluir_pid)
    return objetivo


def _buscar_una_vez(
    monitor, consulta: str, escribir: bool, excluir_pid: int | None
) -> tuple[Objetivo | None, bool]:
    import uiautomation as auto

    _per_monitor_dpi()
    limite = time.monotonic() + TIEMPO_BUSQUEDA
    mejor: tuple[int, Objetivo] | None = None
    revisados = 0
    mejor_parcial = 0.0  # diagnóstico: no se registra ningún texto
    con_nombre = 0
    hay_chromium = False
    with auto.UIAutomationInitializerInThread():
        for ventana in auto.GetRootControl().GetChildren():
            try:
                r = ventana.BoundingRectangle
                if r.width() <= 0 or r.height() <= 0 or ventana.IsOffscreen:
                    continue
                if excluir_pid is not None and ventana.ProcessId == excluir_pid:
                    continue  # las ventanas de Jarvis (el chat repite lo que pediste)
                if not _dentro(monitor, (r.left + r.right) // 2, (r.top + r.bottom) // 2):
                    continue
                hay_chromium = hay_chromium or getattr(ventana, "ClassName", "") == CLASE_CHROMIUM
            except Exception:
                continue
            for control, _profundidad in auto.WalkControl(ventana, maxDepth=PROFUNDIDAD):
                revisados += 1
                if time.monotonic() > limite:
                    break
                try:
                    if control.Name:
                        con_nombre += 1
                        mejor_parcial = max(mejor_parcial, fraccion_palabras(consulta, control.Name))
                    puntos = max(
                        puntuar(control.Name, v, control.ControlTypeName, escribir)
                        for v in variantes(consulta)
                    )
                    if not puntos:
                        continue
                    caja = control.BoundingRectangle
                    if (
                        caja.width() < TAMANO_MINIMO
                        or caja.height() < TAMANO_MINIMO
                        or control.IsOffscreen
                    ):
                        continue
                    cx, cy = (caja.left + caja.right) // 2, (caja.top + caja.bottom) // 2
                    if not _dentro(monitor, cx, cy):
                        continue
                except Exception:
                    continue
                if mejor is None or puntos > mejor[0]:
                    mejor = (
                        puntos,
                        Objetivo(cx, cy, control.Name, "accesibilidad",
                                 (caja.left, caja.top, caja.right, caja.bottom)),
                    )
    logging.info(
        "Búsqueda por accesibilidad en el monitor %s: %s elementos revisados (%s con nombre), "
        "%s; consulta de %s palabra(s), mejor coincidencia parcial %.0f %%.",
        monitor.numero, revisados, con_nombre, "encontrado" if mejor else "sin coincidencias",
        len(normalizar(consulta).split()), mejor_parcial * 100,
    )
    return (mejor[1] if mejor else None), hay_chromium


def interpretar_vision(respuesta: str, monitor, ancho: int, alto: int) -> Objetivo | None:
    """Convierte la respuesta JSON del modelo (x, y en 0–1000) a coordenadas físicas."""
    respuesta = respuesta or ""
    coincidencia = re.search(r"\{.*\}", respuesta, re.DOTALL)
    try:
        datos = json.loads(coincidencia.group(0)) if coincidencia else {}
    except json.JSONDecodeError:
        datos = {}
    if not datos:
        # Respuesta cortada (p. ej. un «texto» largo): leer los campos sueltos.
        x = re.search(r'"x"\s*:\s*([\d.]+)', respuesta)
        y = re.search(r'"y"\s*:\s*([\d.]+)', respuesta)
        if x and y and re.search(r'"encontrado"\s*:\s*true', respuesta):
            datos = {"encontrado": True, "x": x.group(1), "y": y.group(1)}
    if not datos.get("encontrado"):
        return None
    def numero(valor):
        # A veces el modelo devuelve [x1, x2] (una caja) en vez de un número.
        if isinstance(valor, list) and valor:
            return sum(float(v) for v in valor) / len(valor)
        return float(valor)

    try:
        x, y = numero(datos["x"]), numero(datos["y"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (0 <= x <= 1000 and 0 <= y <= 1000):
        return None
    px = monitor.x + round(x / 1000 * monitor.ancho)
    py = monitor.y + round(y / 1000 * monitor.alto)
    return Objetivo(px, py, str(datos.get("texto") or ""), "visión")


def buscar_por_vision(imagen, monitor, consulta: str, *, escribir: bool = False) -> Objetivo | None:
    """Pide al modelo de visión local dónde está el elemento (solo con permiso del usuario)."""
    import jarvis_vision

    jpeg = jarvis_vision.preparar_imagen(imagen)
    que = "el campo de texto" if escribir else "el elemento en el que hay que hacer clic"
    instruccion = (
        f"En esta captura de pantalla, localiza {que} que corresponde a: «{consulta}». "
        "Responde SOLO con JSON: {\"encontrado\": true|false, \"x\": número, \"y\": número, "
        "\"texto\": \"texto visible del elemento\"}, donde x e y son el centro del elemento "
        "en una escala de 0 a 1000 (0,0 arriba a la izquierda; 1000,1000 abajo a la derecha). "
        "Si no lo ves con claridad, responde {\"encontrado\": false}."
    )
    respuesta = requests.post(
        core.LLM_URL.rsplit("/", 1)[0] + "/chat",
        json={
            "model": core.MODEL_VISION,
            "messages": [{
                "role": "user", "content": instruccion,
                "images": [base64.b64encode(jpeg).decode("ascii")],
            }],
            "stream": False,
            "think": False,
            "keep_alive": "5m",
            # Esquema JSON: obliga a devolver números (sin él a veces daba listas).
            "format": {
                "type": "object",
                "properties": {
                    "encontrado": {"type": "boolean"},
                    "x": {"type": "number"},
                    "y": {"type": "number"},
                    "texto": {"type": "string"},
                },
                "required": ["encontrado", "x", "y"],
            },
            "options": {"num_ctx": 8192, "num_predict": 250, "temperature": 0},
        },
        timeout=300,
    )
    respuesta.raise_for_status()
    contenido = (respuesta.json().get("message") or {}).get("content", "")
    objetivo = interpretar_vision(contenido, monitor, imagen.width, imagen.height)
    logging.info("Búsqueda por visión en el monitor %s: %s.", monitor.numero,
                 "encontrado" if objetivo else "sin resultado")
    return objetivo


# --- Ratón y teclado -------------------------------------------------------------

_INPUT_MOUSE, _INPUT_KEYBOARD = 0, 1
_MOUSEEVENTF_MOVE, _MOUSEEVENTF_LEFTDOWN, _MOUSEEVENTF_LEFTUP = 0x0001, 0x0002, 0x0004
_MOUSEEVENTF_VIRTUALDESK, _MOUSEEVENTF_ABSOLUTE = 0x4000, 0x8000
_SM_XVIRTUALSCREEN, _SM_YVIRTUALSCREEN, _SM_CXVIRTUALSCREEN, _SM_CYVIRTUALSCREEN = 76, 77, 78, 79
_KEYEVENTF_UNICODE, _KEYEVENTF_KEYUP = 0x0004, 0x0002
_TOLERANCIA_PUNTERO = 2  # px: el redondeo de SendInput absoluto puede desviar 1 px


def _estructuras_entrada():
    from ctypes import wintypes

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", ctypes.c_size_t)]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class _UNION(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("relleno", ctypes.c_byte * 32)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", _UNION)]

    return MOUSEINPUT, KEYBDINPUT, INPUT


def _enviar_raton(user32, flags: int, dx: int = 0, dy: int = 0) -> bool:
    MOUSEINPUT, _KEYBDINPUT, INPUT = _estructuras_entrada()
    entrada = INPUT(type=_INPUT_MOUSE)
    entrada.u.mi = MOUSEINPUT(dx, dy, 0, flags, 0, 0)
    return user32.SendInput(1, ctypes.byref(entrada), ctypes.sizeof(INPUT)) == 1


def coordenadas_absolutas(x: int, y: int, izquierda: int, arriba: int,
                          ancho: int, alto: int) -> tuple[int, int]:
    """Punto físico del escritorio virtual → escala 0–65535 de SendInput (VIRTUALDESK)."""
    ancho, alto = max(ancho - 1, 1), max(alto - 1, 1)
    ax = round((x - izquierda) * 65535 / ancho)
    ay = round((y - arriba) * 65535 / alto)
    return min(max(ax, 0), 65535), min(max(ay, 0), 65535)


def _posicion_puntero(user32) -> tuple[int, int] | None:
    from ctypes import wintypes

    punto = wintypes.POINT()
    if not user32.GetCursorPos(ctypes.byref(punto)):
        return None
    return punto.x, punto.y


def mover_puntero(x: int, y: int) -> None:
    """Lleva el puntero a (x, y) en coordenadas físicas, en cualquier monitor.

    ``SetCursorPos`` a veces falla (p. ej. con el puntero en otro monitor o
    según qué ventana tenga el foco); entonces se mueve con ``SendInput``
    absoluto sobre todo el escritorio virtual y se comprueba que llegó.
    """
    _per_monitor_dpi()
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    x, y = int(x), int(y)

    def llego() -> bool:
        actual = _posicion_puntero(user32)
        return actual is not None and (
            abs(actual[0] - x) <= _TOLERANCIA_PUNTERO and abs(actual[1] - y) <= _TOLERANCIA_PUNTERO
        )

    if user32.SetCursorPos(x, y) and llego():
        return
    codigo = ctypes.get_last_error()
    izquierda = user32.GetSystemMetrics(_SM_XVIRTUALSCREEN)
    arriba = user32.GetSystemMetrics(_SM_YVIRTUALSCREEN)
    ancho = user32.GetSystemMetrics(_SM_CXVIRTUALSCREEN)
    alto = user32.GetSystemMetrics(_SM_CYVIRTUALSCREEN)
    logging.warning(
        "SetCursorPos no llevó el puntero a (%s, %s) (código %s); pruebo con SendInput "
        "(escritorio virtual %s,%s %s×%s).", x, y, codigo, izquierda, arriba, ancho, alto,
    )
    ax, ay = coordenadas_absolutas(x, y, izquierda, arriba, ancho, alto)
    flags = _MOUSEEVENTF_MOVE | _MOUSEEVENTF_ABSOLUTE | _MOUSEEVENTF_VIRTUALDESK
    for _intento in range(3):
        _enviar_raton(user32, flags, ax, ay)
        time.sleep(0.05)
        if llego():
            return
    actual = _posicion_puntero(user32)
    logging.error("El puntero quedó en %s en vez de (%s, %s) (último código %s).",
                  actual, x, y, ctypes.get_last_error())
    raise OSError(
        "Windows no pudo mover el puntero a esa posición. Si la ventana de destino "
        "se ejecuta como administrador, Jarvis también necesita ejecutarse como administrador."
    )


def hacer_clic(x: int, y: int) -> None:
    mover_puntero(x, y)
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    time.sleep(0.05)
    _enviar_raton(user32, _MOUSEEVENTF_LEFTDOWN)
    time.sleep(0.03)
    _enviar_raton(user32, _MOUSEEVENTF_LEFTUP)


def escribir_texto(texto: str) -> None:
    """Escribe texto como si se tecleara (caracteres Unicode, incluidas tildes)."""
    _MOUSEINPUT, KEYBDINPUT, INPUT = _estructuras_entrada()
    user32 = ctypes.windll.user32
    for caracter in texto:
        for flags in (_KEYEVENTF_UNICODE, _KEYEVENTF_UNICODE | _KEYEVENTF_KEYUP):
            entrada = INPUT(type=_INPUT_KEYBOARD)
            entrada.u.ki = KEYBDINPUT(0, ord(caracter), flags, 0, 0)
            user32.SendInput(1, ctypes.byref(entrada), ctypes.sizeof(INPUT))
        time.sleep(0.005)


def ejecutar_en_pantalla(x: int, y: int, text: str = "") -> str:
    """Handler de ``screen_click``: clic y, si hay texto, escribirlo."""
    hacer_clic(x, y)
    if text:
        time.sleep(0.25)
        escribir_texto(text)
        return "Hice clic y escribí el texto."
    return "Hice clic."


# --- Frases ------------------------------------------------------------------------

_MONITOR = r"(?:\s+(?:en|del|de)\s+(?:el\s+|la\s+)?(?:monitor|pantalla)\s+(?P<n>\d|uno|dos|tres|cuatro))?"
_NUMEROS = {"uno": 1, "dos": 2, "tres": 3, "cuatro": 4}
# «analiza el monitor 3 y haz clic…», «en el monitor 3 haz clic…», «en la pantalla 2, pulsa…»
_PREFIJO = (
    r"^(?:(?:(?:analiza|mira|revisa)\s+|en\s+)(?:el\s+|la\s+)?(?:monitor|pantalla)\s+"
    r"(?P<n0>\d|uno|dos|tres|cuatro)\s+(?:y\s+)?)?"
)
_CLIC = re.compile(
    _PREFIJO
    + r"(?:haz|hace|hacé|dale|da)\s+(?:un\s+)?(?:click|clic|clik|clickeo)\s+(?:(?:en|sobre|a)\s+|(?=donde\s))"
    r"(?P<obj>.+?)" + _MONITOR + r"$"
    r"|" + _PREFIJO.replace("n0", "n1")
    + r"(?:pulsa|presiona|toca|aprieta|clickea|cliquea)\s+(?P<obj2>.+?)"
    + _MONITOR.replace("<n>", "<n2>") + r"$"
)
_ESCRIBIR = re.compile(
    _PREFIJO
    + r"(?:escribe|escribi|teclea)\s+(?P<texto>.+?)\s+en\s+(?P<obj>.+?)" + _MONITOR + r"$"
)
# Palabras de relleno para quedarse con el texto que hay que buscar.
_RELLENO = re.compile(
    r"^(?:el|la|los|las|un|una)\s+|"
    r"^(?:video|enlace|link|boton|opcion|pestana|elemento|texto|resultado|cuadro|campo|casilla)"
    r"(?:\s+de\s+(?:busqueda|texto))?\s+|"
    r"^(?:que\s+(?:dice|pone|se\s+llama)|llamado|llamada|con\s+el\s+texto|de)\s+|"
    # «haz clic donde dice …», «en donde pone …», «en lo que dice …»
    # Solo junto a «donde»/«lo que», para no recortar títulos que empiezan por «Sale…».
    r"^(?:donde|lo\s+que)\s+(?:dice|pone|aparece|sale)\s+"
)


_CAMPO = re.compile(
    r"^(?:el\s+|la\s+)?(?:cuadro|campo|barra|buscador|caja|casilla|chat|busqueda|"
    r"campo\s+de\s+texto)\b"
)


def _limpiar_objeto(texto: str) -> str:
    texto = normalizar(texto)
    anterior = None
    while anterior != texto:
        anterior = texto
        texto = _RELLENO.sub("", texto).strip()
    return texto


def _numero(valor: str | None) -> int | None:
    if not valor:
        return None
    return int(valor) if valor.isdigit() else _NUMEROS.get(valor)


def detectar_comando(texto: str) -> dict | None:
    """``{"accion": "click"|"type", "consulta", "monitor", "texto"}`` o None."""
    consulta = normalizar(texto)
    consulta = re.sub(r"^(?:(?:hey|oye)\s+)?jarvis\s+", "", consulta)
    if (m := _ESCRIBIR.fullmatch(consulta)) is not None and (
        _CAMPO.match(m.group("obj")) or m.group("n0") or m.group("n")
    ):
        # Solo si nombra un campo («el cuadro de búsqueda») o un monitor: así
        # «escribe un poema en inglés» sigue siendo una consulta para el modelo.
        objeto = _limpiar_objeto(m.group("obj"))
        escrito = _texto_original(texto, m.group("texto"))
        if objeto and escrito:
            return {"accion": "type", "consulta": objeto, "texto": escrito,
                    "monitor": _numero(m.group("n0") or m.group("n"))}
    if (m := _CLIC.fullmatch(consulta)) is not None:
        objeto = _limpiar_objeto(m.group("obj") or m.group("obj2") or "")
        numero = _numero(m.group("n0") or m.group("n") or m.group("n1") or m.group("n2"))
        if objeto:
            return {"accion": "click", "consulta": objeto, "texto": "", "monitor": numero}
    return None


def _texto_original(original: str, normalizado: str) -> str:
    """Recupera del texto original lo que hay que escribir (con mayúsculas y tildes)."""
    entre_comillas = re.search(r"[«\"“']([^»\"”']+)[»\"”']", original)
    if entre_comillas:
        return entre_comillas.group(1).strip()
    return normalizado.strip()
