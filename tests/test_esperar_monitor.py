"""Esperar a que la tele aparezca como monitor antes de configurarla (2026-11-23, «modo gaming 4K»)."""

import unittest
from pathlib import Path
from types import SimpleNamespace

import jarvis_displays as d

MAPA = {"monitor-1": 1, "monitor-2": 2, "tele": 3}


def pantallas(*numeros):
    return [SimpleNamespace(numero=n) for n in numeros]


class EsperarMonitoresTests(unittest.TestCase):
    def correr(self, lecturas, numeros=(3,), mapa=MAPA, espera=20.0):
        lecturas = list(lecturas)
        ahora = {"t": 0.0}
        dormidas = []

        def listar(_mapa):
            return (lecturas.pop(0) if len(lecturas) > 1 else lecturas[0]), _mapa

        def dormir(s):
            dormidas.append(s)
            ahora["t"] += s

        hecho = d.esperar_monitores(numeros, mapa, espera=espera, listar=listar, dormir=dormir,
                                    reloj=lambda: ahora["t"])
        return hecho, len(dormidas)

    def test_waits_until_the_tv_shows_up(self):
        self.assertEqual(self.correr([pantallas(1, 2), pantallas(1, 2), pantallas(1, 2, 3)]), (True, 2))

    def test_already_there_does_not_wait(self):
        self.assertEqual(self.correr([pantallas(1, 2, 3)]), (True, 0))

    def test_unknown_number_fails_at_once(self):
        self.assertEqual(self.correr([pantallas(1, 2)], numeros=(7,)), (False, 0))

    def test_gives_up_after_the_limit(self):
        hecho, dormidas = self.correr([pantallas(1, 2)], espera=5.0)
        self.assertFalse(hecho)
        self.assertEqual(dormidas, 5)

    def test_wired_before_applying(self):
        fuente = (Path(__file__).resolve().parent.parent / "jarvis_app_monitores.py").read_text("utf-8")
        self.assertLess(fuente.index("jarvis_displays.esperar_monitores(numeros, mapa)"),
                        fuente.index("jarvis_displays.aplicar(action, numeros, mapa)"))


if __name__ == "__main__":
    unittest.main()


class ReintentoTests(unittest.TestCase):
    """2026-11-23: «salir de modo gaming 4K» falló al desactivar la tele con el código 31."""

    def test_retries_transient_error(self):
        from unittest.mock import patch

        intentos = []

        def una_vez(*_a):
            intentos.append(1)
            if len(intentos) < 3:
                raise d._error_de(31)
            return "Desactivé el monitor 3.", None, {}

        dormidas = []
        with patch.object(d, "_aplicar_una_vez", side_effect=una_vez):
            self.assertEqual(d.aplicar("disable", (3,), {}, dormir=dormidas.append)[0], "Desactivé el monitor 3.")
        self.assertEqual(dormidas, [1.0, 2.0])

    def test_other_errors_and_already_done_are_not_retried(self):
        from unittest.mock import patch

        for error in (d._error_de(87), d.YaEstaAsi("El monitor 3 ya está desactivado.")):
            with self.subTest(error=str(error)), patch.object(d, "_aplicar_una_vez", side_effect=error) as una:
                with self.assertRaises(d.ErrorPantalla):
                    d.aplicar("disable", (3,), {}, dormir=lambda _s: None)
                self.assertEqual(una.call_count, 1)

    def test_gives_up_after_the_last_wait(self):
        from unittest.mock import patch

        with patch.object(d, "_aplicar_una_vez", side_effect=d._error_de(31)) as una:
            with self.assertRaisesRegex(d.ErrorPantalla, "código 31"):
                d.aplicar("disable", (3,), {}, dormir=lambda _s: None)
        self.assertEqual(una.call_count, len(d.ESPERAS_REINTENTO) + 1)
