"""Fase 1: arreglo automático de la barra, vista de monitores y mover ventanas (sin tocar Windows)."""

import queue
import unittest

import jarvis_displays as d
import jarvis_monitor_view as mv
import jarvis_windows as w
from jarvis_commands import detectar_comando_monitores

MONITORES = [
    (1, (0, 0, 2560, 1440)),
    (3, (-1920, 0, 0, 1080)),
    (2, (-1264, -2160, 2576, 0)),  # la TV, encima
]


class MisplacedTaskbarTests(unittest.TestCase):
    def test_finds_the_monitor_of_a_floating_taskbar(self) -> None:
        bien = [(0, 1392, 2560, 1440), (-1920, 1032, 0, 1080), (-1264, -144, 2576, 0)]
        self.assertEqual(d.monitor_de_barra_mal(bien, MONITORES), (False, None))
        flotante = bien[:2] + [(-1264, -900, 2576, -756)]
        self.assertEqual(d.monitor_de_barra_mal(flotante, MONITORES), (True, 2))
        fuera = bien[:2] + [(5000, 5000, 6000, 5040)]
        self.assertEqual(d.monitor_de_barra_mal(fuera, MONITORES), (True, None))


class WatcherTests(unittest.TestCase):
    def _vigilante(self, lecturas, activo=True):
        eventos: queue.Queue = queue.Queue()
        reloj = {"t": 0.0}
        respuestas = iter(lecturas)
        vigilante = d.VigilanteBarras(
            eventos, lambda: activo, pausa=120, revisar=lambda: next(respuestas),
            reloj=lambda: reloj["t"],
        )
        return vigilante, eventos, reloj

    def test_needs_two_bad_readings_in_a_row(self) -> None:
        vigilante, eventos, _ = self._vigilante([(True, 2), (False, None), (True, 2), (True, 2)])
        for _ in range(4):
            vigilante.revisar_una_vez()
        self.assertEqual(eventos.get_nowait(), ("taskbar_misplaced", 2))
        self.assertTrue(eventos.empty())

    def test_waits_between_fixes_to_avoid_loops(self) -> None:
        vigilante, eventos, reloj = self._vigilante([(True, 2)] * 6)
        for _ in range(4):
            vigilante.revisar_una_vez()
        self.assertEqual(eventos.qsize(), 1)  # el segundo aviso cae dentro de la pausa
        reloj["t"] = 200
        vigilante.revisar_una_vez()
        vigilante.revisar_una_vez()
        self.assertEqual(eventos.qsize(), 2)

    def test_disabled_does_nothing(self) -> None:
        vigilante, eventos, _ = self._vigilante([(True, 2)] * 3, activo=False)
        for _ in range(3):
            vigilante.revisar_una_vez()
        self.assertTrue(eventos.empty())


class MonitorViewTests(unittest.TestCase):
    VISTAS = [
        mv.MonitorVista(1, "KG271U N3", True, (0, 0, 2560, 1440), 100, True),
        mv.MonitorVista(2, "VX2428-FHD", False, None, None, False),
        mv.MonitorVista(3, "Smart TV Pro", True, (-1264, -2160, 3840, 2160), 300, False),
    ]

    def test_layout_keeps_real_positions_and_puts_disabled_aside(self) -> None:
        cajas = mv.distribuir(self.VISTAS, 800, 230)
        kg, vx, tv = cajas[1], cajas[2], cajas[3]
        self.assertLess(tv[3], kg[1] + 1)  # la TV está encima del KG271U
        self.assertGreater(vx[0], max(kg[2], tv[2]))  # el apagado va en la columna derecha
        for x0, y0, x1, y1 in cajas.values():
            self.assertTrue(0 <= x0 < x1 <= 800 and 0 <= y0 < y1 <= 230)

    def test_menu_actions(self) -> None:
        acciones = {v.numero: [a[1:] for a in mv.acciones(v)] for v in self.VISTAS}
        self.assertEqual(acciones[2], [("configure_display", {"action": "extend", "monitor": 2})])
        self.assertIn(("configure_display", {"action": "set_primary", "monitor": 3}), acciones[3])
        self.assertNotIn("set_primary", str(acciones[1]))  # ya es el principal
        self.assertIn(("fix_display_scaling", {"monitor": 3}), acciones[3])

    def test_description(self) -> None:
        self.assertEqual(mv.describir(self.VISTAS[0]), "100 % · 2560×1440 · principal")
        self.assertEqual(mv.describir(self.VISTAS[0], compacto=True), "100 % · principal")
        self.assertEqual(mv.describir(self.VISTAS[1]), "APAGADO")


class MoveWindowTests(unittest.TestCase):
    VENTANAS = [
        w.Ventana(1, "YouTube - Brave", "brave", 10),
        w.Ventana(2, "General | Discord", "discord", 20),
        w.Ventana(3, "J.A.R.V.I.S. | Asistente local", "jarvis", 99),
        w.Ventana(4, "Presupuesto.xlsx - Excel", "excel", 30),
    ]

    def test_choose_by_program_alias_title_or_all(self) -> None:
        self.assertEqual([v.hwnd for v in w.elegir(self.VENTANAS, "Brave", 99)], [1])
        self.assertEqual([v.hwnd for v in w.elegir(self.VENTANAS, "navegador", 99)], [1])
        self.assertEqual([v.hwnd for v in w.elegir(self.VENTANAS, "presupuesto", 99)], [4])
        self.assertEqual([v.hwnd for v in w.elegir(self.VENTANAS, "todo", 99)], [1, 2, 4])  # sin Jarvis
        self.assertEqual(w.elegir(self.VENTANAS, "spotify", 99), [])
        # Nombrado, Jarvis sí se mueve («mueve Jarvis al monitor 2»): su ventana principal.
        propias = self.VENTANAS + [w.Ventana(5, "Comandos de voz", "jarvis", 99)]
        self.assertEqual([v.hwnd for v in w.elegir(propias, "Jarvis", 99)], [3])

    def test_new_rect_keeps_relative_position_and_fits(self) -> None:
        origen = (0, 0, 2560, 1392)
        destino = (-1920, 0, 0, 1032)
        # Ventana pegada a la derecha del origen → pegada a la derecha del destino.
        self.assertEqual(w.nuevo_rect((1560, 100, 2560, 700), origen, destino)[2], 0)
        # Ventana más grande que el destino: se ajusta al área de trabajo.
        x0, y0, x1, y1 = w.nuevo_rect((0, 0, 2560, 1392), origen, destino)
        self.assertEqual((x0, y0, x1, y1), (-1920, 0, 0, 1032))

    def test_phrases(self) -> None:
        casos = {
            "Manda Brave a la TV.": {"app": "brave", "screen_name": "tv"},
            "pon Discord en el monitor 2": {"app": "discord", "monitor": 2},
            "trae todo al monitor 1": {"app": "todo", "monitor": 1},
            "mueve la ventana de Spotify a la pantalla 3": {"app": "spotify", "monitor": 3},
            "manda esta ventana al monitor dos": {"app": "esta ventana", "monitor": 2},
        }
        for texto, argumentos in casos.items():
            with self.subTest(texto=texto):
                self.assertEqual(detectar_comando_monitores(texto), ("move_window", argumentos))
        self.assertEqual(detectar_comando_monitores("pon el monitor 3 como principal")[0], "configure_display")
        self.assertIsNone(detectar_comando_monitores("pon el escritorio en el monitor 2"))


if __name__ == "__main__":
    unittest.main()
