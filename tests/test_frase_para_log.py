"""Las órdenes que van a la IA quedan en el registro, sin datos personales."""

import unittest

from jarvis_agent import frase_para_log


class FraseParaLogTests(unittest.TestCase):
    def test_masks_personal_data(self) -> None:
        self.assertEqual(frase_para_log("  manda un mail a ana.perez@correo.com  "), "manda un mail a [correo]")
        self.assertEqual(frase_para_log("llama al +54 9 11 5555-1234"), "llama al [número]")
        self.assertEqual(frase_para_log("abre https://ejemplo.com/a?token=abc123"), "abre https://ejemplo.com/a?…")
        self.assertEqual(frase_para_log("pon el volumen al 20 en el monitor 3"), "pon el volumen al 20 en el monitor 3")

    def test_long_phrases_are_cut(self) -> None:
        frase = frase_para_log("a" * 500)
        self.assertEqual(len(frase), 160)
        self.assertTrue(frase.endswith("…"))


if __name__ == "__main__":
    unittest.main()
