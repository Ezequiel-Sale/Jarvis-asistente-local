"""Vigila lo que dispara rutinas «al conectar un pendrive», «al abrir Spotify» o «al quedarme sin
internet», y publica ``("device_event", disparador, detalle)`` en la cola de la app (que ya ejecuta
las rutinas de los auriculares y de los juegos así).

- USB: unidades extraíbles (``GetDriveTypeW`` == DRIVE_REMOVABLE) o discos que aparecen o se van.
- Programas: solo los que tienen una rutina «al abrir / al cerrar» activa (se relee cada minuto).
- Internet: ``InternetGetConnectedState`` de Windows (no manda tráfico); dos lecturas iguales
  seguidas antes de avisar, para no disparar por un corte de un segundo.
La primera lectura de cada cosa es la base: lo que ya estaba al abrir Jarvis no dispara nada.
"""

from __future__ import annotations

import ctypes
import logging
import string
import threading
from collections.abc import Callable

import jarvis_routines as rutinas

INTERVALO = 3.0
CADA_INTERNET = 10      # vueltas: cada 30 s
CADA_PROGRAMAS_DB = 20  # vueltas: releer qué programas vigilar cada minuto
DRIVE_REMOVABLE = 2


def unidades_extraibles(kernel32=None) -> set[str]:
    kernel32 = kernel32 or ctypes.windll.kernel32
    mascara = kernel32.GetLogicalDrives()
    unidades = set()
    for i, letra in enumerate(string.ascii_uppercase):
        if mascara & (1 << i) and kernel32.GetDriveTypeW(f"{letra}:\\") == DRIVE_REMOVABLE:
            unidades.add(f"{letra}:")
    return unidades


def hay_internet(wininet=None) -> bool:
    wininet = wininet or ctypes.windll.wininet
    estado = ctypes.c_ulong(0)
    return bool(wininet.InternetGetConnectedState(ctypes.byref(estado), 0))


def procesos() -> set[str]:
    import psutil

    nombres = set()
    for proceso in psutil.process_iter(["name"]):
        nombre = (proceso.info.get("name") or "").casefold()
        if nombre:
            nombres.add(nombre.removesuffix(".exe"))
    return nombres


class VigilanteDisparadores(threading.Thread):
    def __init__(self, events, *, leer_usb: Callable[[], set[str]] = unidades_extraibles,
                 leer_internet: Callable[[], bool] = hay_internet, leer_procesos: Callable[[], set[str]] = procesos,
                 vigilados: Callable[[], set[str]] = rutinas.programas_vigilados, intervalo: float = INTERVALO) -> None:
        super().__init__(name="JarvisDisparadores", daemon=True)
        self.events = events
        self._leer_usb, self._leer_internet = leer_usb, leer_internet
        self._leer_procesos, self._vigilados = leer_procesos, vigilados
        self.intervalo = intervalo
        self._detenido = threading.Event()
        self._usb: set[str] | None = None
        self._internet: bool | None = None
        self._internet_candidato: bool | None = None
        self._abiertos: dict[str, bool] | None = None
        self._programas: set[str] = set()
        self._vuelta = 0

    def _publicar(self, disparador: str, detalle: str = "") -> None:
        logging.info("Disparador de rutinas: %s %s", disparador, detalle)
        self.events.put(("device_event", disparador, detalle))

    def revisar(self) -> None:
        """Una vuelta (los tests la llaman directo)."""
        self._revisar_usb()
        if self._vuelta % CADA_INTERNET == 0:
            self._revisar_internet()
        if self._vuelta % CADA_PROGRAMAS_DB == 0:
            try:
                self._programas = set(self._vigilados())
            except Exception:
                logging.debug("No se pudieron leer los programas con rutinas.", exc_info=True)
        self._revisar_programas()
        self._vuelta += 1

    def _revisar_usb(self) -> None:
        actuales = self._leer_usb()
        if self._usb is not None:
            for unidad in sorted(actuales - self._usb):
                self._publicar(rutinas.DISPARADOR_USB_CONECTADO, unidad)
            for unidad in sorted(self._usb - actuales):
                self._publicar(rutinas.DISPARADOR_USB_DESCONECTADO, unidad)
        self._usb = actuales

    def _revisar_internet(self) -> None:
        actual = self._leer_internet()
        if self._internet is None:
            self._internet = actual
            return
        if actual == self._internet:
            self._internet_candidato = None
            return
        if self._internet_candidato != actual:
            self._internet_candidato = actual  # espera una segunda lectura igual
            return
        self._internet, self._internet_candidato = actual, None
        self._publicar(rutinas.DISPARADOR_CON_INTERNET if actual else rutinas.DISPARADOR_SIN_INTERNET)

    def _revisar_programas(self) -> None:
        if not self._programas:
            self._abiertos = None
            return
        nombres = self._leer_procesos()
        actuales = {p: any(rutinas.programa_coincide(n, p) for n in nombres) for p in self._programas}
        if self._abiertos is not None:
            for programa, abierto in actuales.items():
                antes = self._abiertos.get(programa)
                if antes is None or antes == abierto:
                    continue
                self._publicar(rutinas.DISPARADOR_PROGRAMA_ABIERTO if abierto else rutinas.DISPARADOR_PROGRAMA_CERRADO,
                               programa)
        self._abiertos = actuales

    def run(self) -> None:
        while not self._detenido.is_set():
            try:
                self.revisar()
            except Exception:
                logging.debug("Falló una vuelta del vigilante de disparadores.", exc_info=True)
            self._detenido.wait(self.intervalo)

    def stop(self) -> None:
        self._detenido.set()
