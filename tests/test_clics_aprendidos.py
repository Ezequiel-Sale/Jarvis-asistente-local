"""Clics aprendidos y frases de clic. No toca la pantalla, el ratón ni la base de datos real."""

import json
import queue
import unittest
from unittest.mock import Mock, patch

import jarvis_clics_aprendidos as ca
import jarvis_screen_actions as s
from jarvis_app import JarvisApp
from jarvis_vision import Monitor

MONITOR_3 = Monitor(3, 1920, 0, 3840, 2160, False)
VENTANA = (1920, 0, 5760, 2100)  # izquierda, arriba, derecha, abajo


class ClickPhraseTests(unittest.TestCase):
    def test_natural_phrases_reach_the_click(self) -> None:
        """2026-11-16: estas frases iban a la IA, que inventaba «No encontré…» sin buscar."""
        casos = {
            "Hacer clic en donde dice Crunchyroll": ("crunchyroll", None),
            "En el monitor 3 hacer clic en donde dice Crunchyroll": ("crunchyroll", 3),
            "Ahora haz clic en donde dice Crunchyroll": ("crunchyroll", None),
            "Analiza el monitor 3 y luego haz clic en donde dice Crunchyroll": ("crunchyroll", 3),
            "Por favor, ¿podrías hacer clic en Aceptar en el monitor 2?": ("aceptar", 2),
            "quiero que pulses el botón jugar": ("jugar", None),
        }
        for texto, (consulta, monitor) in casos.items():
            with self.subTest(texto=texto):
                pedido = s.detectar_comando(texto)
                self.assertIsNotNone(pedido)
                self.assertEqual((pedido["consulta"], pedido["monitor"]), (consulta, monitor))

    def test_other_phrases_are_not_clicks(self) -> None:
        for texto in ("dale", "mira el monitor 2", "analiza el monitor 3", "ahora sí"):
            with self.subTest(texto=texto):
                self.assertIsNone(s.detectar_comando(texto))


class RelativePositionTests(unittest.TestCase):
    def test_nearest_edges(self) -> None:
        logo = ca.posicion_relativa(1990, 70, VENTANA)  # arriba a la izquierda
        self.assertEqual(logo, {"borde_x": "izq", "dx": 70, "borde_y": "arr", "dy": 70})
        boton = ca.posicion_relativa(5700, 2050, VENTANA)  # abajo a la derecha
        self.assertEqual(boton, {"borde_x": "der", "dx": 60, "borde_y": "aba", "dy": 50})

    def test_position_follows_the_window(self) -> None:
        boton = ca.posicion_relativa(5700, 2050, VENTANA)
        self.assertEqual(ca.posicion_absoluta(boton, (0, 0, 1000, 800)), (940, 750))
        logo = ca.posicion_relativa(1990, 70, VENTANA)
        self.assertEqual(ca.posicion_absoluta(logo, (100, 50, 900, 700)), (170, 120))

    def test_window_too_small(self) -> None:
        lejos = {"borde_x": "izq", "dx": 900, "borde_y": "arr", "dy": 10}
        self.assertIsNone(ca.posicion_absoluta(lejos, (0, 0, 500, 500)))


class MemoryTests(unittest.TestCase):
    def test_remember_updates_and_forgets(self) -> None:
        entradas = ca.recordar([], "Crunchyroll", "brave", 1990, 70, VENTANA, "logo de Crunchyroll")
        entradas = ca.recordar(entradas, "crunchyroll", "brave", 1995, 72, VENTANA, "logo de Crunchyroll")
        self.assertEqual(len(entradas), 1)
        self.assertEqual(entradas[0]["veces"], 2)
        self.assertEqual(entradas[0]["dx"], 75)
        entradas = ca.recordar(entradas, "jugar", "launcher", 10, 10, (0, 0, 100, 100), "Jugar")
        self.assertEqual([e["consulta"] for e in entradas], ["jugar", "crunchyroll"])
        self.assertEqual(len(ca.candidatos(entradas, "CRUNCHYROLL")), 1)
        self.assertEqual(ca.olvidar(entradas, "crunchyroll", "brave")[0]["consulta"], "jugar")

    def test_limit_and_damaged_data(self) -> None:
        entradas = []
        for i in range(ca.MAXIMO + 5):
            entradas = ca.recordar(entradas, f"cosa {i}", "app", 5, 5, (0, 0, 10, 10), "x")
        self.assertEqual(len(entradas), ca.MAXIMO)
        self.assertEqual(ca.cargar(lambda _c: "{roto"), [])
        self.assertEqual(ca.cargar(lambda _c: None), [])

    def test_search_uses_the_visible_window(self) -> None:
        entradas = ca.recordar([], "crunchyroll", "brave", 1990, 70, VENTANA, "logo de Crunchyroll")
        ventana = Mock(hwnd=77, programa="brave", pid=5)
        user32 = Mock()
        with patch.object(ca, "_user32", return_value=user32), \
                patch.object(ca, "_rect", return_value=VENTANA), \
                patch.object(ca.jarvis_windows, "listar_ventanas", return_value=[ventana]), \
                patch.object(ca, "ventana_en", return_value=(77, VENTANA)), \
                patch.object(ca.jarvis_screen_actions, "_per_monitor_dpi"):
            objetivo, monitor, entrada = ca.buscar(entradas, "Crunchyroll", [MONITOR_3])
            self.assertEqual((objetivo.x, objetivo.y, monitor.numero), (1990, 70, 3))
            self.assertEqual(objetivo.origen, "lo aprendido la última vez")
            with patch.object(ca, "ventana_en", return_value=(99, VENTANA)):  # otra ventana encima
                self.assertIsNone(ca.buscar(entradas, "crunchyroll", [MONITOR_3]))
            self.assertIsNone(ca.buscar(entradas, "otra cosa", [MONITOR_3]))


class WorkerTests(unittest.TestCase):
    def _host(self, aprobaciones, hallado) -> Mock:
        host = Mock()
        host.events = queue.Queue()
        host._request_screen_approval.side_effect = aprobaciones
        host._find_learned_click.return_value = hallado
        return host

    def _eventos(self, host) -> list[tuple]:
        return [host.events.get_nowait() for _ in range(host.events.qsize())]

    @patch.object(s, "buscar_por_vision")
    @patch.object(s, "buscar_por_accesibilidad", return_value=None)
    def test_learned_spot_skips_the_vision_model(self, _acc: Mock, vision: Mock) -> None:
        aprendido = s.Objetivo(1990, 70, "logo de Crunchyroll", "lo aprendido la última vez")
        host = self._host([True], (aprendido, MONITOR_3, {"programa": "brave"}))
        pedido = {"accion": "click", "consulta": "crunchyroll", "texto": "", "monitor": 3}
        JarvisApp._screen_action_worker(host, pedido, MONITOR_3, Mock(), "haz clic", "c1")
        vision.assert_not_called()
        self.assertEqual(self._eventos(host), [("screen_execute", aprendido, "", "haz clic", "c1", "crunchyroll")])
        self.assertEqual(host._request_screen_approval.call_args.args[0], "click")  # siempre con confirmación

    @patch.object(s, "buscar_por_vision")
    @patch.object(s, "buscar_por_accesibilidad", return_value=None)
    def test_rejected_learned_spot_is_forgotten_and_vision_offered(self, _acc: Mock, vision: Mock) -> None:
        aprendido = s.Objetivo(1990, 70, "logo", "lo aprendido la última vez")
        host = self._host([False, False], (aprendido, MONITOR_3, {"programa": "brave"}))
        pedido = {"accion": "click", "consulta": "crunchyroll", "texto": "", "monitor": 3}
        JarvisApp._screen_action_worker(host, pedido, MONITOR_3, Mock(), "haz clic", "c1")
        host._forget_learned_click.assert_called_once_with("crunchyroll", "brave")
        self.assertEqual([c.args[0] for c in host._request_screen_approval.call_args_list], ["click", "vision"])
        vision.assert_not_called()  # tampoco quiso la visión

    def test_successful_click_is_remembered(self) -> None:
        guardado = {}
        host = Mock()
        with patch.object(ca, "programa_de", return_value="brave"), \
                patch("jarvis_core.obtener_preferencia", side_effect=lambda c: guardado.get(c)), \
                patch("jarvis_core.guardar_preferencia", side_effect=guardado.__setitem__):
            objetivo = s.Objetivo(1990, 70, "logo de Crunchyroll", "visión")
            JarvisApp._remember_click(host, "Crunchyroll", objetivo, (77, VENTANA))
            JarvisApp._remember_click(host, "", objetivo, (77, VENTANA))  # sin consulta: nada
        entradas = json.loads(guardado[ca.PREFERENCIA])
        self.assertEqual(len(entradas), 1)
        self.assertEqual((entradas[0]["programa"], entradas[0]["dx"], entradas[0]["dy"]), ("brave", 70, 70))

    def test_jarvis_own_window_is_not_learned(self) -> None:
        with patch.object(ca, "programa_de", return_value="jarvis"), \
                patch("jarvis_core.guardar_preferencia") as guardar:
            JarvisApp._remember_click(Mock(), "x", s.Objetivo(1, 1, "x", "visión"), (1, (0, 0, 9, 9)))
        guardar.assert_not_called()


if __name__ == "__main__":
    unittest.main()
