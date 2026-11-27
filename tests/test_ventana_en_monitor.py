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



class MoverDesdeMonitorTests(unittest.TestCase):
    """«Mueve Brave del monitor 2 al monitor 3» (2026-11-26): una ventana de Brave en el 1 y otra en el 2."""

    def test_phrases(self) -> None:
        from jarvis_commands import detectar_mover_ventana

        self.assertEqual(detectar_mover_ventana("Mueve Brave del monitor 2 al monitor 3"),
                         ("move_window", {"app": "brave", "monitor": 3, "from_monitor": 2}))
        self.assertEqual(detectar_mover_ventana("Mueve el navegador del monitor 2 al monitor 3"),
                         ("move_window", {"app": "brave", "monitor": 3, "from_monitor": 2}))
        self.assertEqual(detectar_mover_ventana("mueve brave al monitor 2"),
                         ("move_window", {"app": "brave", "monitor": 2}))

    def test_only_the_window_on_that_monitor_moves(self) -> None:
        from unittest.mock import patch

        en_1 = SimpleNamespace(hwnd=1, titulo="A - Brave", programa="brave", pid=10)
        en_2 = SimpleNamespace(hwnd=2, titulo="B - Brave", programa="brave", pid=10)
        user32 = SimpleNamespace(MonitorFromWindow=lambda h, _f: {1: "M1", 2: "M2"}[h])
        movidas = []
        with patch.object(w, "_elegir_o_de_la_bandeja", return_value=([], [en_1, en_2])),                 patch.object(w, "_user32", return_value=user32),                 patch.object(w, "_monitor_destino", return_value="M2"),                 patch.object(w, "mover", side_effect=lambda v, _m: movidas.append(v.hwnd) or True):
            self.assertEqual(w.mover_ventanas("brave", "destino", desde="monitor 2", desde_numero=2),
                             (1, 1, ["brave"]))
            self.assertEqual(movidas, [2])
            with self.assertRaises(w.ErrorVentana):
                with patch.object(w, "_monitor_destino", return_value="M3"):
                    w.mover_ventanas("brave", "destino", desde="monitor 3", desde_numero=3)

if __name__ == "__main__":
    unittest.main()
