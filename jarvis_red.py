"""Antes de apagar o suspender, dejar la PC conectada por cable para poder encenderla desde el
celular (Wake-on-LAN, ver ``jarvis_encendido``).

2026-11-20, pedido del usuario: «cuando el equipo esté por apagarse, si está por Wi-Fi, pasalo a
Ethernet». Se hace en dos momentos:
- cuando Jarvis apaga o suspende (``jarvis_power.ejecutar_accion_energia``), y
- cuando Windows avisa que se apaga por otro lado (menú Inicio, botón…): una ventana invisible
  recibe WM_QUERYENDSESSION (``VigilanteApagado``).

Pasos (con ``netsh``, rápido y sin PowerShell): si la placa de cable está deshabilitada se
habilita; si el cable no está enchufado se avisa (eso no se arregla desde Windows); si el Wi-Fi
está conectado, se desconecta para que la conexión quede por cable. El Wi-Fi no se apaga: al
volver a encender, Windows lo reconecta solo si hace falta.
"""

from __future__ import annotations

import ctypes
import logging
import re
import subprocess
import threading
import time
from dataclasses import dataclass

CREATE_NO_WINDOW = 0x08000000
ESPERA_CABLE = 6.0
_WIFI = re.compile(r"wi-?fi|wlan|inal[aá]mbric|wireless", re.IGNORECASE)


@dataclass(frozen=True)
class Interfaz:
    nombre: str
    habilitada: bool
    conectada: bool


def _netsh(*argumentos: str) -> subprocess.CompletedProcess:
    return subprocess.run(["netsh", *argumentos], capture_output=True, text=True, encoding="cp850", errors="replace",
                          timeout=15, creationflags=CREATE_NO_WINDOW)


def interfaces(salida: str | None = None) -> list[Interfaz]:
    """Placas físicas según «netsh interface show interface» (columnas: admin, estado, tipo, nombre)."""
    import jarvis_encendido

    texto = salida if salida is not None else _netsh("interface", "show", "interface").stdout
    lista = []
    for linea in texto.splitlines():
        partes = re.split(r"\s{2,}", linea.strip(), maxsplit=3)
        if len(partes) < 4 or partes[0].startswith(("Estado", "Admin", "-")):
            continue
        admin, estado, _tipo, nombre = partes
        if jarvis_encendido._es_virtual(nombre):
            continue
        habilitada = admin.casefold().startswith(("habilit", "enabled"))
        conectada = estado.casefold().startswith(("conectado", "connected"))
        lista.append(Interfaz(nombre, habilitada, conectada))
    return lista


def preparar_para_encender(*, leer=interfaces, netsh=_netsh, dormir=time.sleep, espera: float = ESPERA_CABLE) -> str:
    """Deja la conexión por cable si se puede. Devuelve qué hizo (para la respuesta y el registro)."""
    placas = leer()
    cables = [p for p in placas if not _WIFI.search(p.nombre)]
    wifis = [p for p in placas if _WIFI.search(p.nombre)]
    if not cables:
        return "Esta PC no tiene placa de red por cable: solo se puede despertar desde «suspender»."
    cable = next((c for c in cables if c.conectada), cables[0])
    if not cable.habilitada:
        netsh("interface", "set", "interface", f"name={cable.nombre}", "admin=enabled")
        limite = time.monotonic() + espera
        while time.monotonic() < limite:
            dormir(0.5)
            cable = next((p for p in leer() if p.nombre == cable.nombre), cable)
            if cable.conectada:
                break
        logging.info("Red antes de apagar: habilité la placa de cable %s.", cable.nombre)
    if not cable.conectada:
        logging.info("Red antes de apagar: el cable no está conectado.")
        return ("Ojo: el cable de red no está conectado, así que no vas a poder encenderla desde el "
                "celular (por Wi-Fi solo despierta de «suspender»).")
    conectados = [w for w in wifis if w.conectada]
    for wifi in conectados:
        netsh("wlan", "disconnect", f"interface={wifi.nombre}")
    if conectados:
        logging.info("Red antes de apagar: desconecté el Wi-Fi; queda por cable.")
        return "Pasé la conexión del Wi-Fi al cable para que puedas encenderla desde el celular."
    return "Ya estaba conectada por cable: la vas a poder encender desde el celular."


def placa_habilitada(name: str, *, leer=interfaces) -> bool:
    for placa in leer():
        if placa.nombre.casefold() == (name or "").casefold():
            return placa.habilitada
    raise ValueError(f"No encontré la placa de red «{name}».")


def cambiar_placa(name: str, state: str, *, leer=interfaces, netsh=_netsh, dormir=time.sleep) -> str:
    """Handler de ``network_adapter``: habilita (on) o deshabilita (off) una placa física (2026-11-20)."""
    if state not in ("on", "off"):
        raise ValueError("state debe ser on u off.")
    placa = next((p for p in leer() if p.nombre.casefold() == (name or "").casefold()), None)
    if placa is None:
        raise ValueError(f"No encontré la placa de red «{name}» (solo las físicas: Ethernet, Wi-Fi).")
    activar = state == "on"
    if placa.habilitada == activar:
        return f"La placa «{placa.nombre}» ya estaba {'habilitada' if activar else 'deshabilitada'}."
    resultado = netsh("interface", "set", "interface", f"name={placa.nombre}", f"admin={'enabled' if activar else 'disabled'}")
    for _ in range(10):
        dormir(0.5)
        ahora = next((p for p in leer() if p.nombre == placa.nombre), placa)
        if ahora.habilitada == activar:
            logging.info("Placa de red %s %s.", placa.nombre, "habilitada" if activar else "deshabilitada")
            return f"Listo: {'habilité' if activar else 'deshabilité'} la placa «{placa.nombre}»."
    detalle = (getattr(resultado, "stdout", "") or getattr(resultado, "stderr", "") or "").strip()
    raise RuntimeError(f"Windows no cambió la placa «{placa.nombre}». {detalle[:200]}")


def preparar_sin_fallar() -> str:
    try:
        return preparar_para_encender()
    except Exception:
        logging.exception("No se pudo revisar la red antes de apagar.")
        return ""


# --- Apagado desde Windows (menú Inicio, botón…) ---------------------------------------------

WM_QUERYENDSESSION = 0x0011
_WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t)


class VigilanteApagado(threading.Thread):
    """Ventana invisible (no solo de mensajes: esas no reciben el aviso de apagado) que, cuando
    Windows pregunta si puede cerrar la sesión, deja la red por cable y le dice que sí."""

    def __init__(self, preparar=preparar_sin_fallar) -> None:
        super().__init__(name="JarvisVigilanteApagado", daemon=True)
        self._preparar = preparar
        self._hecho = False

    def al_apagar(self) -> None:
        if not self._hecho:  # Windows puede preguntar más de una vez
            self._hecho = True
            logging.info("Windows avisó que se apaga: reviso la red. %s", self._preparar())

    def run(self) -> None:
        from ctypes import wintypes

        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        user32.DefWindowProcW.restype = ctypes.c_ssize_t
        user32.DefWindowProcW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE

        @_WNDPROC
        def procedimiento(hwnd, mensaje, wparam, lparam):
            if mensaje == WM_QUERYENDSESSION:
                self.al_apagar()
                return 1  # sí, puede apagarse
            return user32.DefWindowProcW(hwnd, mensaje, wparam, lparam)

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", _WNDPROC), ("cbClsExtra", ctypes.c_int),
                        ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                        ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]

        modulo = kernel32.GetModuleHandleW(None)
        clase = WNDCLASSW(0, procedimiento, 0, 0, modulo, None, None, None, None, "JarvisVigilanteApagado")
        if not user32.RegisterClassW(ctypes.byref(clase)):
            logging.warning("No pude registrar la ventana que vigila el apagado.")
            return
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                           ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                           wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p]
        hwnd = user32.CreateWindowExW(0, "JarvisVigilanteApagado", "Jarvis", 0, 0, 0, 0, 0, None, None, modulo, None)
        if not hwnd:
            logging.warning("No pude crear la ventana que vigila el apagado.")
            return
        mensaje = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(mensaje), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(mensaje))
            user32.DispatchMessageW(ctypes.byref(mensaje))
