"""Frases de rutinas reconocidas aunque el STT las escriba mal («Modulgaming DOSCA»)."""

import unittest

import jarvis_routines as r

CANDIDATAS = [
    ("2k", ("Modo gaming 2K",)),
    ("4k", ("Modo Gaming 4K",)),
    ("noche", ("modo noche", "buenas noches")),
]


class SoundKeyTests(unittest.TestCase):
    def test_numbers_letters_and_spaces(self) -> None:
        self.assertEqual(r.clave_sonido("Modo gaming 2K"), r.clave_sonido("modo gaming dos ka"))
        self.assertEqual(r.clave_sonido("modo gaming 4K"), r.clave_sonido("Modo gaming cuatro ca"))
        self.assertEqual(r.clave_sonido("Hey Jarvis, modo noche."), "modonoke")


class MatchTests(unittest.TestCase):
    def test_exact_phrase_still_wins(self) -> None:
        self.assertEqual(r.elegir_por_frase("Modo gaming 2K.", CANDIDATAS), "2k")
        self.assertEqual(r.elegir_por_frase("buenas noches", CANDIDATAS), "noche")

    def test_what_the_stt_wrote(self) -> None:
        # Caso real (12/11/2026): dijo «modo gaming 2K» y el STT escribió esto.
        self.assertEqual(r.elegir_por_frase("Modulgaming DOSCA.", CANDIDATAS), "2k")
        self.assertEqual(r.elegir_por_frase("modo gaming dos k", CANDIDATAS), "2k")
        self.assertEqual(r.elegir_por_frase("modo gaming cuatro ka", CANDIDATAS), "4k")

    def test_never_guesses(self) -> None:
        for texto in ("modo gaming", "modo nube", "mutea el monitor 3", "ok", "pon música"):
            with self.subTest(texto=texto):
                self.assertIsNone(r.elegir_por_frase(texto, CANDIDATAS))

    def test_ambiguous_between_two_routines(self) -> None:
        parecidas = [("a", ("modo gaming uno",)), ("b", ("modo gaming unos",))]
        self.assertIsNone(r.elegir_por_frase("modo gamin uno", parecidas))


if __name__ == "__main__":
    unittest.main()
