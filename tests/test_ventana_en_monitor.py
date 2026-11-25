"""«Cierra el navegador del monitor 3» (2026-11-24: la IA minimizó Brave). Sin ventanas reales."""

import unittest
from types import SimpleNamespace

import jarvis_windows as w
from jarvis_commands import detectar_comando_ventanas


class FrasesTests(unittest.TestCase):
    def test_phrases(self) -> None:
        casos = {
            "cierra el navegador del monitor 3": {"app": "brave", "action": "window_close", "monitor": 3},
            "cerrá Brave del monitor 1": {"app": "brave", "action": "window_close", "monitor": 1},
            "cierra la ventana de Discord en el monitor 2": {"app": "discord", "action": "window_close", "monitor": 2},
            "minimiza Spotify del monitor dos": {"app": "spotify", "action": "window_minimize", "monitor": 2},
        }
        for frase, argumentos in casos.items():
            with self.subTest(frase=frase):
                self.assertEqual(detectar_comando_ventanas(frase), ("window_control", argumentos))
        self.assertEqual(detectar_comando_ventanas("minimiza Discord"),
                         ("window_control", {"app": "discord", "action": "window_minimize"}))


class CerrarEnMonitorTests(unittest.TestCase):
    def test_closes_only_the_windows_on_that_monitor(self) -> None:
        ventanas = [SimpleNamespace(hwnd=1, titulo="A - Brave", programa="brave", pid=10),
                    SimpleNamespace(hwnd=2, titulo="B - Brave", programa="brave", pid=10)]
        hechas = []
        n, _ = w.cambiar_estado("brave", "window_close", ventanas=ventanas, propio_pid=99, monitor=3,
                                en_monitor=lambda v: v.hwnd == 2,
                                mostrar=lambda v, c: hechas.append((v.hwnd, c)) or True)
        self.assertEqual((n, hechas), (1, [(2, "cerrar")]))
        with self.assertRaises(w.ErrorVentana):
            w.cambiar_estado("brave", "window_close", ventanas=ventanas, propio_pid=99, monitor=1,
                             en_monitor=lambda v: False, mostrar=lambda v, c: True)


if __name__ == "__main__":
    unittest.main()
