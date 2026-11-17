"""Fase 7 de gestos: «Jarvis, esto» (sin tocar ventanas, mouse ni pantalla reales)."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image

import jarvis_apuntar as a
import jarvis_windows as w
from jarvis_commands import _normalizar_consulta, detectar_acomodar_ventanas, detectar_mover_ventana

PROPIO = 999
VENTANAS = [
    w.Ventana(1, "Discord", "discord", 10),
    w.Ventana(2, "J.A.R.V.I.S.", "jarvis", PROPIO),
    w.Ventana(3, "Spotify Premium", "spotify", 20),
]


class PhraseTests(unittest.TestCase):
    def test_look_click_and_close(self) -> None:
        mirar = ("¿Qué es esto?", "Jarvis, ¿qué dice acá?", "leé esto", "explicame esto", "¿Para qué sirve esto?",
                 "mirá esto y decime qué error es")
        for frase in mirar:
            with self.subTest(frase=frase):
                self.assertEqual(a.detectar(frase)["tipo"], "mirar")
        self.assertEqual(a.detectar("Jarvis, ¿qué dice acá?")["pregunta"], "qué dice acá")
        clics = {"hacé clic acá": "click", "dale clic acá": "click", "clic derecho ahí": "right_click",
                 "doble clic acá": "double_click", "tocá acá": "click"}
        for frase, accion in clics.items():
            with self.subTest(frase=frase):
                self.assertEqual(a.detectar(frase), {"tipo": "clic", "accion": accion})
        for frase in ("cerrá esto", "cierra eso", "cerrá esa ventana"):
            with self.subTest(frase=frase):
                self.assertEqual(a.detectar(frase), {"tipo": "cerrar"})

    def test_other_orders_are_not_taken(self) -> None:
        # «cerralo» suelto puede ser lo que se nombró antes; «clic en Guardar» es la búsqueda de siempre.
        for frase in ("qué es la fotosíntesis", "cerrá Discord", "cerralo", "cierra", "hacé clic en Guardar",
                      "¿qué es esto de la inflación?", "leé el correo"):
            with self.subTest(frase=frase):
                self.assertIsNone(a.detectar(frase))

    def test_voseo_window_orders_no_longer_go_to_the_ai(self) -> None:
        """2026-11-17: «mové Discord al monitor 2» o «poné Brave a la izquierda» iban a la IA."""
        self.assertEqual(detectar_mover_ventana("mové Discord al monitor 2"),
                         ("move_window", {"app": "discord", "monitor": 2}))
        self.assertEqual(detectar_mover_ventana("mové esto al monitor 2"),
                         ("move_window", {"app": "esto", "monitor": 2}))
        self.assertEqual(detectar_acomodar_ventanas("poné esto a la izquierda"),
                         ("arrange_windows", {"windows": [{"app": "esto", "position": "left"}]}))
        self.assertEqual(_normalizar_consulta("Abrí Spotify y poné el volumen al 30"),
                         "abre spotify y pon el volumen al 30")
        self.assertEqual(_normalizar_consulta("pasá este tema"), "pasa este tema")  # los regulares, igual


class PointedTests(unittest.TestCase):
    def tearDown(self) -> None:
        a.olvidar()

    def test_pointing_counts_only_for_a_while(self) -> None:
        self.assertIsNone(a.apuntado(posicion=lambda: (5, 5)))
        a.marcar_apuntado(ahora=100.0)
        self.assertEqual(a.apuntado(ahora=110.0, posicion=lambda: (5, 5)), (5, 5))
        self.assertIsNone(a.apuntado(ahora=100.0 + a.SEGUNDOS_APUNTADO + 1, posicion=lambda: (5, 5)))

    def _activa(self, punto, raiz):
        user32 = Mock()
        user32.GetForegroundWindow.return_value = 3  # Spotify activa
        with patch.object(w, "_user32", return_value=user32), \
                patch.object(a, "apuntado", return_value=punto), \
                patch.object(w, "_ventana_en_punto", return_value=raiz):
            return w.ventana_activa(VENTANAS, PROPIO)

    def test_this_is_the_window_under_the_finger(self) -> None:
        self.assertEqual(self._activa((10, 10), 1), [VENTANAS[0]])  # Discord bajo el dedo
        self.assertEqual(self._activa((10, 10), 2), [VENTANAS[1]])  # Jarvis, si se lo señala
        self.assertEqual(self._activa(None, None), [VENTANAS[2]])  # sin apuntar: la activa, como antes
        self.assertEqual(self._activa((10, 10), 77), [VENTANAS[2]])  # el escritorio: la activa

    def test_close_only_the_pointed_window_and_never_jarvis(self) -> None:
        hechas = []

        def cerrar(app, ventana_apuntada):
            with patch.object(w, "ventana_activa", return_value=[ventana_apuntada]):
                return w.cambiar_estado(app, "window_close", ventanas=VENTANAS, propio_pid=PROPIO,
                                        mostrar=lambda v, comando: hechas.append((v.hwnd, comando)) or True)

        self.assertEqual(cerrar("esto", VENTANAS[0]), (1, ["discord"]))
        self.assertEqual(hechas, [(1, "cerrar")])
        with self.assertRaisesRegex(w.ErrorVentana, "A mí no me cierro"):
            cerrar("esto", VENTANAS[1])
        with self.assertRaisesRegex(w.ErrorVentana, "cerrá Discord"):
            cerrar("discord", VENTANAS[0])  # por nombre, close_program (el programa entero)
        self.assertEqual(len(hechas), 1)


class LookTests(unittest.TestCase):
    MONITOR = SimpleNamespace(x=-2560, y=0, ancho=2560, alto=1440)

    def test_zone_is_centered_and_stays_inside_the_monitor(self) -> None:
        self.assertEqual(a.zona_alrededor((-1280, 720), self.MONITOR), (-1280 - 550, 360, 1100, 720))
        self.assertEqual(a.zona_alrededor((-2550, 10), self.MONITOR), (-2560, 0, 1100, 720))  # esquina
        self.assertEqual(a.zona_alrededor((-5, 1435), self.MONITOR), (-1100, 720, 1100, 720))

    def test_ring_marks_where_it_points(self) -> None:
        imagen = Image.new("RGB", (200, 200), (0, 0, 0))
        marcada = a.marcar(imagen, (100, 100))
        self.assertEqual(imagen.getpixel((126, 100)), (0, 0, 0))  # la original no se toca
        self.assertEqual(marcada.getpixel((126, 100)), (255, 40, 40))
        self.assertEqual(marcada.getpixel((100, 100)), (0, 0, 0))  # el centro queda a la vista
        self.assertIn("círculo rojo", a.pregunta_para_el_modelo("¿qué es esto?"))


class ClickTests(unittest.TestCase):
    def test_double_click_button(self) -> None:
        import jarvis_screen_actions as acciones

        with patch("jarvis_screen_actions.mover_rapido"), patch("jarvis_screen_actions._enviar_raton") as enviar:
            self.assertEqual(acciones.boton_raton("double_click", 10, 20, user32=Mock(), pausa=0), "Hice doble clic.")
        self.assertEqual([c.args[1] for c in enviar.call_args_list], [0x0002, 0x0004, 0x0002, 0x0004])

    def test_app_uses_the_pointer_position(self) -> None:
        from jarvis_app import JarvisApp

        host = Mock()
        with patch.object(a, "cursor", return_value=(300, 400)):
            JarvisApp._start_pointed(host, {"tipo": "clic", "accion": "right_click"}, "clic derecho acá")
        host._start_direct_tool_action.assert_called_once_with(
            "gesture_click", {"action": "right_click", "x": 300, "y": 400}, "clic derecho acá")
        host.reset_mock()
        JarvisApp._start_pointed(host, {"tipo": "cerrar"}, "cerrá esto")
        host._start_direct_tool_action.assert_called_once_with(
            "window_control", {"app": "esto", "action": "window_close"}, "cerrá esto")


if __name__ == "__main__":
    unittest.main()
