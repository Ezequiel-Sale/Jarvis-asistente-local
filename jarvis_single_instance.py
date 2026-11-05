"""Garantiza que solo haya una instancia de Jarvis abierta.

Usa un mutex con nombre de Windows. Si ya existe, la segunda instancia activa
un evento con nombre que la primera escucha para mostrarse (aunque estuviera
oculta en la bandeja) y luego termina.
"""

import logging
import os
import threading
from collections.abc import Callable

MUTEX_NAME = "Local\\JarvisAsistenteLocal_Instancia"
EVENT_NAME = "Local\\JarvisAsistenteLocal_Mostrar"
ERROR_ALREADY_EXISTS = 183
WAIT_OBJECT_0 = 0
EVENT_MODIFY_STATE = 0x0002


class SingleInstance:
    def __init__(self, mutex_name: str = MUTEX_NAME, event_name: str = EVENT_NAME) -> None:
        self.mutex_name = mutex_name
        self.event_name = event_name
        self._mutex = None
        self._event = None
        self._stop = threading.Event()
        self._listener: threading.Thread | None = None
        self._kernel32 = None
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
            kernel32.CreateMutexW.restype = wintypes.HANDLE
            kernel32.CreateEventW.argtypes = [
                ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR,
            ]
            kernel32.CreateEventW.restype = wintypes.HANDLE
            kernel32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
            kernel32.OpenEventW.restype = wintypes.HANDLE
            kernel32.SetEvent.argtypes = [wintypes.HANDLE]
            kernel32.SetEvent.restype = wintypes.BOOL
            kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            kernel32.WaitForSingleObject.restype = wintypes.DWORD
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            self._kernel32 = kernel32
            self._ctypes = ctypes

    def acquire(self) -> bool:
        """True si esta es la primera instancia. Fuera de Windows siempre True."""
        if self._kernel32 is None:
            return True
        mutex = self._kernel32.CreateMutexW(None, False, self.mutex_name)
        error = self._ctypes.get_last_error()
        if not mutex:
            # Si no se puede crear el mutex, no bloquear el arranque.
            logging.error("No se pudo crear el mutex de instancia única (error %s).", error)
            return True
        if error == ERROR_ALREADY_EXISTS:
            self._kernel32.CloseHandle(mutex)
            return False
        self._mutex = mutex
        # Evento de reinicio automático: cada aviso despierta una vez al oyente.
        self._event = self._kernel32.CreateEventW(None, False, False, self.event_name)
        return True

    def notify_existing(self) -> bool:
        """Pide a la instancia ya abierta que se muestre."""
        if self._kernel32 is None:
            return False
        event = self._kernel32.OpenEventW(EVENT_MODIFY_STATE, False, self.event_name)
        if not event:
            return False
        try:
            return bool(self._kernel32.SetEvent(event))
        finally:
            self._kernel32.CloseHandle(event)

    def start_listener(self, on_show_requested: Callable[[], None]) -> None:
        """Llama a ``on_show_requested`` (desde un hilo) cada vez que otra instancia avisa."""
        if self._kernel32 is None or not self._event:
            return

        def listen() -> None:
            while not self._stop.is_set():
                if self._kernel32.WaitForSingleObject(self._event, 500) == WAIT_OBJECT_0:
                    if self._stop.is_set():
                        break
                    try:
                        on_show_requested()
                    except Exception:
                        logging.exception("No se pudo mostrar Jarvis a pedido de otra instancia.")

        self._listener = threading.Thread(target=listen, name="JarvisSingleInstance", daemon=True)
        self._listener.start()

    def release(self) -> None:
        self._stop.set()
        if self._listener is not None:
            self._listener.join(timeout=1)
        if self._kernel32 is not None:
            for handle in (self._event, self._mutex):
                if handle:
                    self._kernel32.CloseHandle(handle)
        self._event = self._mutex = None
