import logging
import os
import queue
import threading

GLOBAL_HOTKEY_ID = 1

WM_HOTKEY = 0x0312

WM_QUIT = 0x0012

VK_F9 = 0x78

MOD_NOREPEAT = 0x4000

class GlobalHotkeyListener(threading.Thread):
    def __init__(self, events: queue.Queue[tuple]) -> None:
        super().__init__(name="JarvisGlobalHotkey", daemon=True)
        self.events = events
        self._thread_id = 0
        self._stop_requested = threading.Event()

    def run(self) -> None:
        if os.name != "nt":
            self.events.put(
                ("global_hotkey_unavailable", "El atajo global solo está disponible en Windows.")
            )
            return

        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._thread_id = kernel32.GetCurrentThreadId()
        register_hotkey = user32.RegisterHotKey
        register_hotkey.argtypes = [
            wintypes.HWND,
            ctypes.c_int,
            wintypes.UINT,
            wintypes.UINT,
        ]
        register_hotkey.restype = wintypes.BOOL
        unregister_hotkey = user32.UnregisterHotKey
        unregister_hotkey.argtypes = [wintypes.HWND, ctypes.c_int]
        unregister_hotkey.restype = wintypes.BOOL
        post_thread_message = user32.PostThreadMessageW
        post_thread_message.argtypes = [
            wintypes.DWORD,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        post_thread_message.restype = wintypes.BOOL
        get_message = user32.GetMessageW
        get_message.argtypes = [
            ctypes.POINTER(wintypes.MSG),
            wintypes.HWND,
            wintypes.UINT,
            wintypes.UINT,
        ]
        get_message.restype = wintypes.BOOL
        kernel32.GetCurrentThreadId.restype = wintypes.DWORD

        if not register_hotkey(None, GLOBAL_HOTKEY_ID, MOD_NOREPEAT, VK_F9):
            error = ctypes.WinError(ctypes.get_last_error())
            self.events.put(("global_hotkey_unavailable", str(error)))
            return

        self.events.put(("global_hotkey_ready",))
        message = wintypes.MSG()
        try:
            if self._stop_requested.is_set():
                return
            while True:
                result = get_message(ctypes.byref(message), None, 0, 0)
                if result == -1:
                    raise ctypes.WinError(ctypes.get_last_error())
                if result == 0:
                    break
                if message.message == WM_HOTKEY and message.wParam == GLOBAL_HOTKEY_ID:
                    self.events.put(("global_microphone_hotkey",))
        finally:
            unregister_hotkey(None, GLOBAL_HOTKEY_ID)

    def stop(self) -> None:
        self._stop_requested.set()
        # Si el hilo ya terminó (p. ej. F9 estaba ocupado), no hay nada que detener.
        if not self._thread_id or os.name != "nt" or not self.is_alive():
            return

        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.PostThreadMessageW.argtypes = [
            wintypes.DWORD,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        user32.PostThreadMessageW.restype = wintypes.BOOL
        if not user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0):
            error = ctypes.WinError(ctypes.get_last_error())
            logging.error("No se pudo detener el listener global de F9: %s", error)
