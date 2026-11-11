"""Modo teatro / modo cine del reproductor (YouTube, Twitch…).

Todo simulado: ninguna prueba toca ventanas, ratón ni teclado reales."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import jarvis_media as m
import jarvis_windows as w
from jarvis_commands import detectar_comando_multimedia


def _boton(nombre, left=0):
    return SimpleNamespace(Name=nombre, BoundingRectangle=SimpleNamespace(left=left, top=0, right=left + 10, bottom=10))


class ButtonNameTests(unittest.TestCase):
    def test_real_button_names(self) -> None:
        self.assertEqual(m.clasificar_boton("Modo cine (t)"), "theater")
        self.assertEqual(m.clasificar_boton("Vista predeterminada (t)"), "exit_theater")
        self.assertEqual(m.clasificar_boton("Theater mode (t)"), "theater")
        self.assertEqual(m.clasificar_boton("Default view (t)"), "exit_theater")
        self.assertEqual(m.clasificar_boton("Theatre Mode (alt+t)"), "theater")
        self.assertEqual(m.clasificar_boton("Exit Theatre Mode (alt+t)"), "exit_theater")
        self.assertIsNone(m.clasificar_boton("Ver el modo cine de la película"))


class PhraseTests(unittest.TestCase):
    def test_enter_and_exit(self) -> None:
        casos = {
            "Modo teatro.": {"enabled": True},
            "modo cine": {"enabled": True},
            "pon el modo teatro": {"enabled": True},
            "pon el video en modo cine": {"enabled": True},
            "activa el modo teatro del monitor 2": {"enabled": True, "monitor": 2},
            "sal del modo teatro": {"enabled": False},
            "quita el modo cine": {"enabled": False},
            "desactiva el modo teatro": {"enabled": False},
            "saca el video del modo teatro": {"enabled": False},
        }
        for texto, argumentos in casos.items():
            with self.subTest(texto=texto):
                self.assertEqual(detectar_comando_multimedia(texto), ("video_theater", argumentos))
        # La pantalla completa sigue igual.
        self.assertEqual(detectar_comando_multimedia("pantalla completa"), ("video_fullscreen", {"enabled": True}))

    def test_inside_compound_orders(self) -> None:
        herramienta, argumentos = detectar_comando_multimedia("pausa el video y pon el modo teatro")
        self.assertEqual(herramienta, "media_sequence")
        self.assertEqual(argumentos["steps"][1], {"tool": "video_theater", "arguments": {"enabled": True}})


class ActionTests(unittest.TestCase):
    def _ejecutar(self, enabled, lecturas, *, pulsa=True, ventana=True):
        """``lecturas``: lo que ve cada consulta de botones, en orden."""
        auto = Mock()
        auto.UIAutomationInitializerInThread.return_value.__enter__ = Mock()
        auto.UIAutomationInitializerInThread.return_value.__exit__ = Mock(return_value=False)
        lecturas = iter(lecturas)
        usados = []

        def leer(*_args):
            return next(lecturas)

        def via(nombre):
            def hacer(*args):
                usados.append((nombre, *args[1:]) if nombre == "tecla" else nombre)
                return pulsa
            return hacer

        navegador = w.Ventana(7, "Twitch - Brave", "brave", 1) if ventana else None
        with patch.dict("sys.modules", {"uiautomation": auto}), \
                patch.object(m, "_botones_reproductor", side_effect=leer), \
                patch.object(m, "_ventana_navegador", return_value=navegador), \
                patch.object(m, "_pulsar", side_effect=via("botón")), \
                patch.object(m, "_clic_real", side_effect=via("clic")), \
                patch.object(m, "_tecla_en", side_effect=via("tecla")), \
                patch("ctypes.windll", create=True):
            try:
                texto = m.modo_teatro(enabled, espera=0, esperar_cambio=0)
            except LookupError as error:
                texto = f"ERROR {error}"
        return texto, usados

    def test_youtube_button_changes_name(self) -> None:
        antes = [{"theater": _boton("Modo cine (t)")}]
        despues = [{"exit_theater": _boton("Vista predeterminada (t)")}]
        self.assertEqual(self._ejecutar(True, [antes, despues]), ("Puse el vídeo en modo teatro.", ["botón"]))

    def test_twitch_change_seen_by_button_moving(self) -> None:
        antes = [{"theater": _boton("Theatre Mode (alt+t)", left=100)}]
        igual = [{"theater": _boton("Theatre Mode (alt+t)", left=100)}]
        movido = [{"theater": _boton("Theatre Mode (alt+t)", left=400)}]
        texto, usados = self._ejecutar(True, [antes, igual, movido])
        self.assertEqual((texto, usados), ("Puse el vídeo en modo teatro.", ["botón", "clic"]))

    def test_last_resort_key_uses_the_announced_shortcut(self) -> None:
        antes = [{"theater": _boton("Theatre Mode (alt+t)")}]
        movido = [{"theater": _boton("Theatre Mode (alt+t)", left=50)}]
        _, usados = self._ejecutar(True, [antes, antes, antes, movido])
        self.assertEqual(usados, ["botón", "clic", ("tecla", m.VK_T, True)])
        antes = [{"theater": _boton("Modo cine (t)")}]
        _, usados = self._ejecutar(True, [antes, antes, antes, [{"exit_theater": _boton("x")}]])
        self.assertEqual(usados[-1], ("tecla", m.VK_T, False))

    def test_honest_failure(self) -> None:
        antes = [{"theater": _boton("Modo cine (t)")}]
        texto, _ = self._ejecutar(True, [antes, antes, antes, antes])
        self.assertIn("ERROR No pude activar el modo teatro", texto)

    def test_already_in_that_state(self) -> None:
        texto, usados = self._ejecutar(True, [[{"exit_theater": _boton("Vista predeterminada (t)")}]])
        self.assertEqual((texto, usados), ("El vídeo ya está en modo teatro.", []))
        texto, _ = self._ejecutar(False, [[{"theater": _boton("Modo cine (t)")}]])
        self.assertEqual(texto, "El vídeo no está en modo teatro.")

    def test_exit(self) -> None:
        antes = [{"exit_theater": _boton("Vista predeterminada (t)")}]
        despues = [{"theater": _boton("Modo cine (t)")}]
        self.assertEqual(self._ejecutar(False, [antes, despues]), ("Quité el modo teatro del vídeo.", ["botón"]))

    def test_no_player(self) -> None:
        texto, usados = self._ejecutar(True, [[], []])
        self.assertIn("ERROR No encontré ningún reproductor", texto)
        self.assertEqual(usados, [])


if __name__ == "__main__":
    unittest.main()
