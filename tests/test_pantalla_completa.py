"""Pantalla completa del vídeo (botón del reproductor de YouTube)."""

import unittest
from unittest.mock import Mock, patch

import jarvis_media as m
from jarvis_commands import detectar_comando_multimedia


class ButtonTests(unittest.TestCase):
    def test_real_button_names(self) -> None:
        self.assertEqual(m.clasificar_boton("Pantalla completa (f)"), "fullscreen")
        self.assertEqual(m.clasificar_boton("Salir de pantalla completa (f)"), "exit_fullscreen")
        self.assertEqual(m.clasificar_boton("Full screen (f)"), "fullscreen")
        self.assertEqual(m.clasificar_boton("Exit full screen (f)"), "exit_fullscreen")
        # Textos del chat u otros botones que mencionan la frase no cuentan.
        self.assertIsNone(m.clasificar_boton("Buscado pantalla completa|fullscreen Sin coincidencias"))
        self.assertIsNone(m.clasificar_boton("Modo cine (t)"))


class PhraseTests(unittest.TestCase):
    def test_enter_and_exit(self) -> None:
        casos = {
            "Pon el video en pantalla completa.": {"enabled": True},
            "pantalla completa": {"enabled": True},
            "agranda el video": {"enabled": True},
            "pon en pantalla completa el video del monitor 3": {"enabled": True, "monitor": 3},
            "Sal de pantalla completa.": {"enabled": False},
            "saca el video de pantalla completa": {"enabled": False},
            "vuelve el video a tamaño normal": {"enabled": False},
            "sal de la pantalla completa del monitor 3": {"enabled": False, "monitor": 3},
        }
        for texto, argumentos in casos.items():
            with self.subTest(texto=texto):
                self.assertEqual(detectar_comando_multimedia(texto), ("video_fullscreen", argumentos))
        for texto in ("minimiza jarvis", "pon el volumen al 20", "maximiza la ventana"):
            with self.subTest(texto=texto):
                self.assertNotEqual((detectar_comando_multimedia(texto) or ("",))[0], "video_fullscreen")

    def test_inside_compound_orders(self) -> None:
        herramienta, argumentos = detectar_comando_multimedia("pausa el video y pon pantalla completa")
        self.assertEqual(herramienta, "media_sequence")
        self.assertEqual(argumentos["steps"][1], {"tool": "video_fullscreen", "arguments": {"enabled": True}})


class ActionTests(unittest.TestCase):
    def _botones(self, *claves):
        return {clave: Mock() for clave in claves}

    def _ejecutar(self, enabled, lecturas):
        lector = Mock(side_effect=lecturas)
        auto = Mock()
        auto.UIAutomationInitializerInThread.return_value.__enter__ = Mock()
        auto.UIAutomationInitializerInThread.return_value.__exit__ = Mock(return_value=False)
        with patch.dict("sys.modules", {"uiautomation": auto}), \
                patch.object(m, "_botones_reproductor", lector), \
                patch("ctypes.windll", create=True):
            return m.pantalla_completa(enabled, espera=0), lector

    def test_enter_presses_the_button(self) -> None:
        botones = self._botones("fullscreen", "pause")
        texto, _ = self._ejecutar(True, [[botones]])
        botones["fullscreen"].GetInvokePattern.return_value.Invoke.assert_called_once()
        self.assertEqual(texto, "Puse el vídeo en pantalla completa.")

    def test_already_in_that_state(self) -> None:
        texto, _ = self._ejecutar(True, [[self._botones("exit_fullscreen")]])
        self.assertEqual(texto, "El vídeo ya está en pantalla completa.")
        texto, _ = self._ejecutar(False, [[self._botones("fullscreen")]])
        self.assertEqual(texto, "El vídeo no está en pantalla completa.")

    def test_retries_once_when_brave_had_not_exposed_the_page(self) -> None:
        botones = self._botones("exit_fullscreen")
        texto, lector = self._ejecutar(False, [[], [botones]])
        self.assertEqual(lector.call_count, 2)
        botones["exit_fullscreen"].GetInvokePattern.return_value.Invoke.assert_called_once()

    def test_no_player(self) -> None:
        with self.assertRaises(LookupError):
            self._ejecutar(True, [[], []])


if __name__ == "__main__":
    unittest.main()
