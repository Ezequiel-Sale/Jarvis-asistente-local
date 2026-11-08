"""«Arregla el escalado»: baja la escala del monitor al 200 % y la devuelve (sin tocar Windows)."""

import unittest
from unittest.mock import patch

import jarvis_displays as d
from jarvis_commands import detectar_comando_monitores

ADAPTER = (1, 0)
KG = d.Pantalla("kg", "KG271U N3", ADAPTER, 1, True, 1, 0, gdi=1)
VX = d.Pantalla("vx", "VX2428-FHD", ADAPTER, 2, True, 2, 2, gdi=3)
TV = d.Pantalla("tv", "Smart TV Pro", ADAPTER, 3, True, 3, 1, gdi=2)
ESCALA_TV = d.escala_desde_relativa(-7, 0, 1)
ESCALA_100 = d.escala_desde_relativa(0, 0, 5)


class PhraseTests(unittest.TestCase):
    def test_phrases(self) -> None:
        casos = {
            "Arregla el escalado.": {},
            "arregla el escalado del monitor 3": {"monitor": 3},
            "corrige la barra de tareas de la pantalla dos": {"monitor": 2},
            "repara la escala en el monitor 3": {"monitor": 3},
            "reajusta el escalado": {},
        }
        for texto, argumentos in casos.items():
            with self.subTest(texto=texto):
                self.assertEqual(detectar_comando_monitores(texto), ("fix_display_scaling", argumentos))
        self.assertIsNone(detectar_comando_monitores("arregla el auto"))


class ScaleTests(unittest.TestCase):
    def test_relative_values_from_windows(self) -> None:
        # Valores reales de la Smart TV: mínimo -7, actual 0, máximo 1.
        self.assertEqual(ESCALA_TV.actual, 300)
        self.assertEqual(ESCALA_TV.recomendada, 300)
        self.assertEqual(ESCALA_TV.posibles[-1], 350)
        self.assertEqual(d.escala_desde_relativa(0, 0, 3).posibles, (100, 125, 150, 175))

    def test_intermediate_scale(self) -> None:
        self.assertEqual(d.escala_intermedia(ESCALA_TV), 200)
        en_200 = d.escala_desde_relativa(-4, 0, 4)
        self.assertEqual(d.escala_intermedia(en_200), 175)
        self.assertEqual(d.escala_intermedia(ESCALA_100), 200)
        hasta_175 = d.escala_desde_relativa(0, 0, 3)
        self.assertEqual(d.escala_intermedia(hasta_175), 125)

    def test_without_number_picks_highest_scale(self) -> None:
        escalas = {"kg": ESCALA_100, "vx": ESCALA_100, "tv": ESCALA_TV}
        elegido = d.elegir_monitor_escalado([KG, VX, TV], None, lambda p: escalas[p.clave])
        self.assertIs(elegido, TV)
        self.assertIs(d.elegir_monitor_escalado([KG, VX, TV], 2, lambda p: escalas[p.clave]), VX)

    @patch.object(d, "poner_escala")
    @patch.object(d, "leer_escala", return_value=ESCALA_TV)
    @patch.object(d, "listar_pantallas", return_value=([KG, VX, TV], {}))
    def test_goes_to_200_and_back(self, _listar, _leer, poner) -> None:
        texto = d.arreglar_escalado(3, {}, dormir=lambda _s: None)
        self.assertEqual([c.args[1] for c in poner.call_args_list], [200, 300])
        self.assertEqual(poner.call_args_list[0].args[0], TV)
        self.assertIn("monitor 3 (Smart TV Pro)", texto)
        self.assertIn("200 %", texto)

    @patch.object(d, "poner_escala")
    @patch.object(d, "leer_escala", return_value=ESCALA_TV)
    @patch.object(d, "listar_pantallas", return_value=([KG, VX, TV], {}))
    def test_always_restores_original_scale(self, _listar, _leer, poner) -> None:
        def cancelado(_s):
            raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            d.arreglar_escalado(3, {}, dormir=cancelado)
        self.assertEqual(poner.call_args_list[-1].args[1], 300)

    def test_disabled_monitor_is_rejected(self) -> None:
        apagado = d.Pantalla("vx", "VX2428-FHD", ADAPTER, 2, False, 2, None)
        with self.assertRaises(d.ErrorPantalla):
            d.leer_escala(apagado)


class TaskbarTests(unittest.TestCase):
    MONITORES = [(0, 0, 2560, 1440), (-1920, 0, 0, 1080), (-1264, -2160, 2576, 0)]

    def test_taskbar_at_monitor_edge_is_ok(self) -> None:
        self.assertTrue(d.barra_bien_ubicada((-1264, -144, 2576, 0), self.MONITORES))
        self.assertTrue(d.barra_bien_ubicada((0, 1392, 2560, 1440), self.MONITORES))

    def test_floating_taskbar_is_detected(self) -> None:
        self.assertFalse(d.barra_bien_ubicada((-1264, -700, 2576, -556), self.MONITORES))
        self.assertFalse(d.barra_bien_ubicada((-1264, -144, 3000, 0), self.MONITORES))


if __name__ == "__main__":
    unittest.main()
