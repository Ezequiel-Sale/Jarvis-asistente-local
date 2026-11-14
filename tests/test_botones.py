"""Pulsar un botón por su texto en una ventana (sin UI Automation real: ventanas falsas)."""

import unittest
from pathlib import Path

import jarvis_botones as b
from jarvis_system import carpetas_juegos


class ButtonTests(unittest.TestCase):
    def test_matching(self) -> None:
        self.assertEqual(b.coincidencia("JUGAR", "Jugar"), 2)
        self.assertEqual(b.coincidencia("Jugar ahora", "jugar"), 1)
        self.assertEqual(b.coincidencia("Conjugar", "jugar"), 0)
        self.assertTrue(b.es_del_programa("Netmarble Launcher",
                                          r"C:\Program Files\Netmarble\Netmarble Launcher\Netmarble Launcher.exe", ""))
        self.assertTrue(b.es_del_programa("netmarble launcher.exe", "", "Netmarble Launcher"))
        self.assertFalse(b.es_del_programa("Netmarble Launcher", r"C:\x\brave.exe", "YouTube"))

    def test_prefers_exact_button(self) -> None:
        controles = [("TextControl", "Jugar ahora mismo", "texto"), ("ButtonControl", "Jugar", "boton"),
                     ("ButtonControl", "Ajustes", "otro"), ("WindowControl", "Jugar", "ventana")]
        self.assertEqual(b.elegir(controles, "jugar"), "boton")
        self.assertIsNone(b.elegir(controles, "Salir"))

    def test_waits_until_the_button_appears(self) -> None:
        """El launcher tarda: primero no hay ventana, después no hay botón, al final sí."""
        vistas = iter([[], [("Netmarble Launcher", [("ButtonControl", "Actualizando", "x")])],
                       [("Netmarble Launcher", [("ButtonControl", "JUGAR", "jugar")])]])
        pulsados, pausas = [], []
        texto = b.pulsar_boton("Netmarble Launcher", "Jugar", 60, buscar=lambda p: next(vistas),
                               pulsar=lambda c: pulsados.append(c) or True, dormir=pausas.append)
        self.assertEqual(texto, "Pulsé «Jugar» en Netmarble Launcher.")
        self.assertEqual((pulsados, len(pausas)), (["jugar"], 2))

    def test_clear_errors(self) -> None:
        with self.assertRaises(RuntimeError) as sin_ventana:
            b.pulsar_boton("Netmarble Launcher", "Jugar", 1, buscar=lambda p: [], pulsar=lambda c: True,
                           dormir=lambda s: None)
        self.assertIn("No apareció la ventana", str(sin_ventana.exception))
        with self.assertRaises(RuntimeError) as sin_boton:
            b.pulsar_boton("Netmarble Launcher", "Jugar", 1, buscar=lambda p: [("L", [])], pulsar=lambda c: True,
                           dormir=lambda s: None)
        self.assertIn("No encontré el botón", str(sin_boton.exception))

    def test_netmarble_games_count_for_game_mode(self) -> None:
        """Los juegos de Netmarble (7DS) activan el modo juego: así se libera la memoria de video."""
        carpetas = carpetas_juegos(discos=[], steam=[])
        esperada = Path(r"C:\Program Files\Netmarble\Netmarble Game")
        if esperada.is_dir():
            self.assertIn(esperada, carpetas)


if __name__ == "__main__":
    unittest.main()
