"""Pestañas de Brave por voz: «siguiente pestaña», «pestaña 3», «última pestaña», «cerrá la pestaña»,
«reabrí la pestaña», «andá a la pestaña de YouTube», «cerrá la pestaña de Gmail», «¿qué pestañas tengo?»,
«mueve la pestaña número 3 al monitor 2».

- Siguiente / anterior / por número / cerrar / reabrir: los atajos de Brave (Ctrl+Tab,
  Ctrl+Mayús+Tab, Ctrl+1…8, Ctrl+9 = la última, Ctrl+W, Ctrl+Mayús+T), con la ventana de Brave al
  frente (la del frente o, si no, la última usada, ``jarvis_windows.ventana_brave``).
- Por nombre y la lista: accesibilidad de Windows. Brave expone cada pestaña como ``TabItem`` con el
  patrón de selección (medido 2026-11-17: 10 pestañas leídas en 0,45 s): se elige sin mover el mouse.
- Mover a un monitor (2026-11-24, pedido del usuario): Brave no tiene atajo para sacar una pestaña a otra
  ventana; lo hace la extensión «Jarvis para Brave» (``chrome.windows.create({tabId})``) y Jarvis lleva la
  ventana nueva al monitor y la maximiza. Si era la única pestaña, se mueve la ventana entera.
"""

from __future__ import annotations

import logging
import re
import time
import unicodedata

ACCIONES = ("next", "previous", "number", "last", "close", "reopen", "go", "list", "move")
ESPERA_VENTANA_NUEVA = 3.0  # lo que puede tardar en aparecer la ventana de la pestaña movida

VK_CONTROL, VK_SHIFT, VK_TAB = 0x11, 0x10, 0x09
_SUBIR = 2  # KEYEVENTF_KEYUP
# Lo que Brave agrega al nombre accesible de la pestaña («Título: Reproducción de audio: …»).
_SUFIJOS = re.compile(r"\s*[:\-–]\s*(?:reproduccion de audio|reproduciendo audio|audio silenciado|audio playing|"
                      r"audio muted|uso de memoria|memory usage).*$", re.IGNORECASE)


def _normal(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", str(texto).casefold())
    return " ".join("".join(c for c in texto if not unicodedata.combining(c)).replace("-", " ").split())


def titulo_limpio(nombre: str) -> str:
    """El título como se ve, sin lo que Brave agrega para la accesibilidad."""
    # Letra por letra (sin tildes ni mayúsculas) para que el corte caiga en el mismo lugar del original.
    sin_tildes = "".join(
        ("".join(c for c in unicodedata.normalize("NFKD", letra) if not unicodedata.combining(c)) or letra)[0].lower()
        for letra in nombre)
    corte = _SUFIJOS.search(sin_tildes)
    return nombre[:corte.start()].strip() if corte else nombre.strip()


def elegir(titulos: list[str], buscado: str) -> int | None:
    """Índice de la pestaña cuyo título contiene ``buscado`` (o todas sus palabras); si hay varias,
    la primera. None si ninguna."""
    clave = _normal(buscado)
    if not clave:
        return None
    normales = [_normal(t) for t in titulos]
    for i, t in enumerate(normales):
        if clave in t:
            return i
    palabras = [p for p in clave.split() if len(p) > 2]
    for i, t in enumerate(normales):
        if palabras and all(p in t for p in palabras):
            return i
    sin_espacios = clave.replace(" ", "")
    for i, t in enumerate(normales):
        if sin_espacios and sin_espacios in t.replace(" ", ""):  # «you tube» → YouTube
            return i
    return None


def _teclas(*vks: int) -> list[tuple]:
    """Apretar en orden y soltar al revés (formato de ``jarvis_input._send_input``)."""
    return [("tecla", vk, 0, 0) for vk in vks] + [("tecla", vk, 0, _SUBIR) for vk in reversed(vks)]


ATAJOS = {
    "next": _teclas(VK_CONTROL, VK_TAB),
    "previous": _teclas(VK_CONTROL, VK_SHIFT, VK_TAB),
    "last": _teclas(VK_CONTROL, ord("9")),
    "close": _teclas(VK_CONTROL, ord("W")),
    "reopen": _teclas(VK_CONTROL, VK_SHIFT, ord("T")),
}


def _brave_al_frente():
    """La ventana de Brave elegida, ya al frente (o ErrorVentana)."""
    import jarvis_windows

    user32 = jarvis_windows._user32()
    ventana = jarvis_windows.ventana_brave(jarvis_windows.listar_ventanas(), user32.GetForegroundWindow())
    if ventana is None:
        raise jarvis_windows.ErrorVentana("No encontré ninguna ventana abierta de Brave.")
    if ventana.hwnd != user32.GetForegroundWindow():
        jarvis_windows.enfocar(ventana.hwnd, user32)
        time.sleep(0.25)  # que Brave tenga el foco antes de las teclas
    return ventana


def _pestanas_de(hwnd: int, auto) -> list:
    for ventana in auto.GetRootControl().GetChildren():
        if ventana.NativeWindowHandle == hwnd:
            return [c for c, _ in auto.WalkControl(ventana, maxDepth=12) if c.ControlTypeName == "TabItemControl"]
    return []


def _enviar(teclas: list[tuple]) -> None:
    import jarvis_input

    jarvis_input._send_input(list(teclas))


def pestana(action: str, number: int | None = None, title: str = "", monitor: int | None = None,
            screen_name: str | None = None) -> str:
    """La herramienta ``browser_tab``."""
    if action not in ACCIONES:
        raise ValueError(f"Acción de pestaña no válida: {action}.")
    if action == "move":
        return mover_pestana(number, title, monitor, screen_name)
    ventana = _brave_al_frente()
    if action in ATAJOS and not (action == "close" and title):
        _enviar(ATAJOS[action])
        return {"next": "Pasé a la pestaña siguiente.", "previous": "Volví a la pestaña anterior.",
                "last": "Fui a la última pestaña.", "close": "Cerré la pestaña.",
                "reopen": "Volví a abrir la última pestaña que cerraste."}[action]
    if action == "number" and number is not None and 1 <= number <= 8:
        _enviar(_teclas(VK_CONTROL, ord(str(number))))
        return f"Fui a la pestaña {number}."
    import ctypes

    import uiautomation as auto

    ctypes.windll.user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    with auto.UIAutomationInitializerInThread():
        pestanas = _pestanas_de(ventana.hwnd, auto)
        if not pestanas:
            raise RuntimeError("No pude leer las pestañas de Brave.")
        titulos = [titulo_limpio(p.Name) for p in pestanas]
        if action == "list":
            activa = next((i for i, p in enumerate(pestanas) if _seleccionada(p)), None)
            partes = [f"{i + 1}) {t}" + (" (la que estás viendo)" if i == activa else "")
                      for i, t in enumerate(titulos)]
            return f"Tenés {len(titulos)} pestañas en Brave: " + "; ".join(partes) + "."
        if action == "number":
            if number is None or not 1 <= number <= len(pestanas):
                raise RuntimeError(f"Brave tiene {len(pestanas)} pestañas: no hay una número {number}.")
            indice = number - 1
        else:
            indice = elegir(titulos, title)
            if indice is None:
                raise RuntimeError(f"No encontré ninguna pestaña de Brave que diga «{title}».")
        pestanas[indice].GetSelectionItemPattern().Select()
    logging.info("Pestaña de Brave elegida por %s (%d de %d).", action, indice + 1, len(titulos))
    if action == "close":
        time.sleep(0.15)
        _enviar(ATAJOS["close"])
        return f"Cerré la pestaña «{titulos[indice]}»."
    return f"Fui a la pestaña «{titulos[indice]}»."


def _monitor_por_nombre(nombre: str) -> int:
    """«tv» / «la tele» → el número del monitor activo con ese nombre (la «Smart TV Pro»)."""
    import jarvis_core as core
    import jarvis_displays
    import jarvis_windows

    clave = jarvis_windows.normalizar(nombre)
    if clave in ("tele", "televisor", "television", "smart tv"):
        clave = "tv"
    pantallas, _ = jarvis_displays.listar_pantallas(jarvis_displays.cargar_mapa(core.obtener_preferencia))
    for pantalla in pantallas:
        if pantalla.numero and clave in jarvis_windows.normalizar(pantalla.nombre).split():
            return pantalla.numero
    raise ValueError(f"No encontré ningún monitor llamado «{nombre}».")


def _numero_por_titulo(hwnd: int, title: str) -> int:
    import ctypes

    import uiautomation as auto

    ctypes.windll.user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    with auto.UIAutomationInitializerInThread():
        titulos = [titulo_limpio(c.Name) for c in _pestanas_de(hwnd, auto)]
    indice = elegir(titulos, title)
    if indice is None:
        raise RuntimeError(f"No encontré ninguna pestaña de Brave que diga «{title}».")
    return indice + 1


def _ventana_nueva(antes: set[int], *, listar=None, dormir=time.sleep, reloj=time.monotonic):
    """La ventana de Brave que apareció después de ``antes`` (espera hasta ``ESPERA_VENTANA_NUEVA``)."""
    import jarvis_windows

    listar = listar or jarvis_windows.listar_ventanas
    limite = reloj() + ESPERA_VENTANA_NUEVA
    while True:
        nuevas = [v for v in listar() if v.programa == "brave" and v.hwnd not in antes]
        if nuevas or reloj() >= limite:
            return nuevas[0] if nuevas else None
        dormir(0.1)


def mover_pestana(number: int | None, title: str = "", monitor: int | None = None,
                  screen_name: str | None = None) -> str:
    """Saca la pestaña (por número, título o la actual) a una ventana nueva en el monitor pedido."""
    import jarvis_brave
    import jarvis_vision
    import jarvis_windows

    if monitor is None and screen_name:
        monitor = _monitor_por_nombre(screen_name)
    if monitor is None:
        raise ValueError("Decime a qué monitor mover la pestaña (por ejemplo «al monitor 2»).")
    destino = jarvis_vision.elegir_monitor(jarvis_vision.listar_monitores(), monitor)  # antes de tocar nada
    ventana = _brave_al_frente()
    if title:
        number = _numero_por_titulo(ventana.hwnd, title)
    antes = {v.hwnd for v in jarvis_windows.listar_ventanas() if v.programa == "brave"}
    datos = jarvis_brave._activo().pedir("mover_pestana", numero=number)
    titulo = str(datos.get("titulo") or "").strip()
    if datos.get("sola"):
        movida = ventana  # era la única pestaña: va la ventana entera
    else:
        movida = _ventana_nueva(antes)
        if movida is None:
            raise RuntimeError("Saqué la pestaña a otra ventana, pero no la encontré para moverla.")
    if not jarvis_windows.mover(movida, destino):
        raise RuntimeError(f"No pude llevar la pestaña al monitor {monitor}.")
    jarvis_windows._user32().ShowWindow(movida.hwnd, 3)  # SW_MAXIMIZE
    logging.info("Pestaña %s de Brave movida al monitor %s.", number or "actual", monitor)
    cual = f"«{titulo}»" if titulo else (f"número {number}" if number else "actual")
    return f"Moví la pestaña {cual} al monitor {monitor}."


def titulos_actuales() -> list[str]:
    """Los títulos de las pestañas de la ventana de Brave, en orden (para saber cuál se abrió)."""
    import ctypes

    import uiautomation as auto

    import jarvis_windows

    ventana = jarvis_windows.ventana_brave(jarvis_windows.listar_ventanas(),
                                           jarvis_windows._user32().GetForegroundWindow())
    if ventana is None:
        return []
    ctypes.windll.user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    with auto.UIAutomationInitializerInThread():
        return [titulo_limpio(p.Name) for p in _pestanas_de(ventana.hwnd, auto)]


def pestana_nueva(antes: list[str], despues: list[str]) -> int | None:
    """Número (desde 1) de la pestaña que apareció entre dos listas de títulos, o None."""
    if len(despues) <= len(antes):
        return None
    for i, titulo in enumerate(despues):
        if i >= len(antes) or titulo != antes[i]:
            return i + 1
    return None


def _seleccionada(control) -> bool:
    try:
        return bool(control.GetSelectionItemPattern().IsSelected)
    except Exception:
        return False


# --- Frases ------------------------------------------------------------------------------------

_P = r"(?:pestana|tab)"
_ORDINALES = {"primera": 1, "segunda": 2, "tercera": 3, "cuarta": 4, "quinta": 5, "sexta": 6, "septima": 7,
              "octava": 8, "novena": 9, "decima": 10}
_NUMEROS = {"una": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7, "ocho": 8,
            "nueve": 9, "diez": 10}
# «vea / vean»: así transcribe la voz «ve a» (2026-11-24, iba a la IA).
_IR = r"(?:(?:anda|ve|vea|vean|vamos|ir|pasa|pasate|cambia|cambiate|salta|volve|vuelve|abre|abri|mostrame|muestrame|trae|traeme)\s+)?(?:a\s+)?"
_SIGUIENTE = re.compile(rf"^{_IR}(?:la\s+)?(?:siguiente|proxima|otra)\s+{_P}$|^{_IR}(?:la\s+)?{_P}\s+(?:siguiente|de\s+la\s+derecha)$")
_ANTERIOR = re.compile(rf"^{_IR}(?:la\s+)?{_P}\s+(?:anterior|de\s+la\s+izquierda)$|^{_IR}(?:la\s+)?anterior\s+{_P}$")
_ORDINAL = re.compile(rf"^{_IR}(?:la\s+)?(?P<ord>" + "|".join(_ORDINALES) + rf"|ultima)\s+{_P}$")
_NUMERO = re.compile(rf"^{_IR}(?:la\s+)?{_P}\s+(?:numero\s+)?(?P<n>\d{{1,2}}|" + "|".join(_NUMEROS) + r")$")
_CERRAR = re.compile(rf"^(?:cierra|cerra)\s+(?:la|esta|esa)\s+{_P}(?:\s+(?:de|del)\s+(?P<titulo>.+))?$")
_REABRIR = re.compile(rf"^(?:(?:reabre|reabri|recupera|recuperame|volve\s+a\s+abrir|vuelve\s+a\s+abrir|abre\s+de\s+nuevo)"
                      rf"\s+la\s+(?:ultima\s+)?{_P}(?:\s+que\s+cerre)?|deshace\s+(?:el\s+)?cerrar\s+{_P})$")
_POR_NOMBRE = re.compile(rf"^{_IR}(?:la\s+)?{_P}\s+(?:de|del)\s+(?P<titulo>.+)$")
_LISTA = re.compile(rf"^(?:que|cuales)\s+{_P}s\s+(?:tengo|hay)(?:\s+abiertas)?(?:\s+en\s+brave)?$"
                    rf"|^(?:deci|decime|dime|lista|listame|nombra|nombrame)\s+(?:las\s+|mis\s+)?{_P}s(?:\s+abiertas)?$")
# «la pestaña de Ajustes» es una sección de Jarvis (``detectar_navegacion`` va antes); estas tampoco.
_NO_TITULO = {"atras", "adelante", "jarvis", "rutinas"}


# Lo que queda como «app» en «mueve la pestaña número 3 al monitor 2» (``detectar_mover_ventana``).
_PESTANA_NUMERO = re.compile(rf"^{_P}\s+(?:numero\s+)?(?P<n>\d{{1,2}}|" + "|".join(_NUMEROS) + r")$")
_PESTANA_ORDINAL = re.compile(r"^(?P<ord>" + "|".join(_ORDINALES) + rf")\s+{_P}$")
_PESTANA_TITULO = re.compile(rf"^{_P}\s+(?:de|del)\s+(?P<titulo>.+)$")
_PESTANA_ACTUAL = re.compile(rf"^(?:(?:esta|esa)\s+)?{_P}(?:\s+(?:actual|abierta))?$")


def pestana_a_mover(app: str) -> dict | None:
    """Argumentos de «move» si lo que se manda a un monitor es una pestaña; si no, None."""
    if (m := _PESTANA_NUMERO.fullmatch(app)) is not None:
        valor = m.group("n")
        return {"action": "move", "number": int(valor) if valor.isdigit() else _NUMEROS[valor]}
    if (m := _PESTANA_ORDINAL.fullmatch(app)) is not None:
        return {"action": "move", "number": _ORDINALES[m.group("ord")]}
    if (m := _PESTANA_TITULO.fullmatch(app)) is not None:
        return {"action": "move", "title": m.group("titulo").strip()}
    if _PESTANA_ACTUAL.fullmatch(app):
        return {"action": "move"}
    return None


def detectar(texto: str) -> tuple[str, dict] | None:
    """``("browser_tab", argumentos)`` o None."""
    from jarvis_commands import _normalizar_consulta

    consulta = _normalizar_consulta(texto)
    consulta = re.sub(r"\s+en\s+(?:brave|el\s+navegador)$", "", consulta)
    if _SIGUIENTE.fullmatch(consulta):
        return "browser_tab", {"action": "next"}
    if _ANTERIOR.fullmatch(consulta):
        return "browser_tab", {"action": "previous"}
    if (m := _ORDINAL.fullmatch(consulta)) is not None:
        if m.group("ord") == "ultima":
            return "browser_tab", {"action": "last"}
        return "browser_tab", {"action": "number", "number": _ORDINALES[m.group("ord")]}
    if (m := _NUMERO.fullmatch(consulta)) is not None:
        valor = m.group("n")
        return "browser_tab", {"action": "number", "number": int(valor) if valor.isdigit() else _NUMEROS[valor]}
    if (m := _CERRAR.fullmatch(consulta)) is not None:
        titulo = (m.group("titulo") or "").strip()
        return "browser_tab", ({"action": "close", "title": titulo} if titulo else {"action": "close"})
    if _REABRIR.fullmatch(consulta):
        return "browser_tab", {"action": "reopen"}
    if _LISTA.fullmatch(consulta):
        return "browser_tab", {"action": "list"}
    if (m := _POR_NOMBRE.fullmatch(consulta)) is not None:
        titulo = m.group("titulo").strip()
        if titulo and titulo not in _NO_TITULO:
            return "browser_tab", {"action": "go", "title": titulo}
    return None
