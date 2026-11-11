"""Reproductores de otras webs (Crunchyroll, Twitch…): nombres de botones y pantalla completa con teclado."""

import unittest
from unittest.mock import patch

import jarvis_input
import jarvis_media as m
import jarvis_windows as w


class ButtonNameTests(unittest.TestCase):
    def test_names_with_and_without_shortcut(self) -> None:
        casos = {
            "Fullscreen (f)": "fullscreen", "Pantalla completa": "fullscreen", "Enter fullscreen": "fullscreen",
            "Entrar en pantalla completa": "fullscreen", "Exit Fullscreen (f)": "exit_fullscreen",
            "Salir de pantalla completa": "exit_fullscreen", "Exit full screen": "exit_fullscreen",
            "Pause (space/k)": "pause", "Pausar": "pause", "Reproducir": "play", "Play": "play",
            "Siguiente (Mayús+n)": "next",
        }
        for nombre, clave in casos.items():
            with self.subTest(nombre=nombre):
                self.assertEqual(m.clasificar_boton(nombre), clave)

    def test_other_buttons_are_not_players(self) -> None:
        for nombre in ("Mute (m)", "Play next", "Playlist", "Pantalla completa del vídeo",
                       "Unfollow Rubius"):
            with self.subTest(nombre=nombre):
                self.assertIsNone(m.clasificar_boton(nombre))


class PressTests(unittest.TestCase):
    """Cada web expone sus botones distinto: se prueba Invoke, Toggle y la acción por defecto."""

    def _boton(self, invoke=False, toggle=False, legacy=False):
        from unittest.mock import Mock

        boton = Mock()
        boton.GetInvokePattern.return_value = Mock() if invoke else None
        boton.GetTogglePattern.return_value = Mock() if toggle else None
        boton.GetLegacyIAccessiblePattern.return_value = Mock() if legacy else None
        return boton

    def test_twitch_style_button_uses_default_action(self) -> None:
        boton = self._boton(legacy=True)  # como «Fullscreen (f)» de Twitch
        self.assertTrue(m._pulsar(boton))
        boton.GetLegacyIAccessiblePattern.return_value.DoDefaultAction.assert_called_once()

    def test_order_and_failures(self) -> None:
        boton = self._boton(invoke=True, legacy=True)
        self.assertTrue(m._pulsar(boton))
        boton.GetInvokePattern.return_value.Invoke.assert_called_once()
        boton.GetLegacyIAccessiblePattern.return_value.DoDefaultAction.assert_not_called()
        solo_toggle = self._boton(toggle=True)
        self.assertTrue(m._pulsar(solo_toggle))
        solo_toggle.GetTogglePattern.return_value.Toggle.assert_called_once()
        falla = self._boton(invoke=True, legacy=True)
        falla.GetInvokePattern.return_value.Invoke.side_effect = OSError("no")
        self.assertTrue(m._pulsar(falla))  # si Invoke falla, prueba la siguiente vía
        self.assertFalse(m._pulsar(self._boton()))


class VerifiedFullscreenTests(unittest.TestCase):
    """Se comprueba que la ventana cambió; si no, se escala: botón → clic real → tecla.
    Todo simulado: ninguna prueba toca ventanas, ratón ni teclado reales."""

    def _ejecutar(self, enabled, estados, *, boton="fullscreen", pulsa=True):
        from unittest.mock import Mock

        auto = Mock()
        auto.UIAutomationInitializerInThread.return_value.__enter__ = Mock()
        auto.UIAutomationInitializerInThread.return_value.__exit__ = Mock(return_value=False)
        estados = iter(estados)
        actual = {"v": next(estados)}
        usados = []

        def via(nombre):
            def hacer(*_args):
                usados.append(nombre)
                actual["v"] = next(estados)
                return pulsa
            return hacer

        botones = {boton: Mock()} if boton else {}
        with patch.dict("sys.modules", {"uiautomation": auto}), \
                patch.object(m, "_botones_reproductor", return_value=[botones] if botones else []), \
                patch.object(m, "_ventana_navegador", return_value=w.Ventana(7, "Twitch - Brave", "brave", 1)), \
                patch.object(m, "_pulsar", side_effect=via("botón")), \
                patch.object(m, "_clic_real", side_effect=via("clic")), \
                patch.object(m, "_tecla_en", side_effect=via("tecla")), \
                patch("ctypes.windll", create=True):
            try:
                texto = m.pantalla_completa(enabled, espera=0, comprobar=lambda _h: actual["v"], esperar_cambio=0)
            except LookupError as error:
                texto = f"ERROR {error}"
        return texto, usados

    def test_youtube_style_button_is_enough(self) -> None:
        self.assertEqual(self._ejecutar(True, [False, True]), ("Puse el vídeo en pantalla completa.", ["botón"]))

    def test_twitch_needs_a_real_click(self) -> None:
        texto, usados = self._ejecutar(True, [False, False, True])
        self.assertEqual((texto, usados), ("Puse el vídeo en pantalla completa.", ["botón", "clic"]))

    def test_last_resort_key_and_honest_failure(self) -> None:
        self.assertEqual(self._ejecutar(True, [False, False, False, True])[1], ["botón", "clic", "tecla"])
        texto, _ = self._ejecutar(True, [False, False, False, False])
        self.assertIn("ERROR No pude poner el vídeo en pantalla completa", texto)

    def test_without_button_uses_the_key(self) -> None:
        self.assertEqual(self._ejecutar(True, [False, True], boton=None)[1], ["tecla"])

    def test_already_in_that_state_does_nothing(self) -> None:
        self.assertEqual(self._ejecutar(True, [True]), ("El vídeo ya está en pantalla completa.", []))
        self.assertEqual(self._ejecutar(False, [False]), ("El vídeo no está en pantalla completa.", []))

    def test_exit_uses_button_then_escape_without_clicking(self) -> None:
        texto, usados = self._ejecutar(False, [True, True, False], boton="exit_fullscreen")
        self.assertEqual((texto, usados), ("Saqué el vídeo de pantalla completa.", ["botón", "tecla"]))


class BrowserWindowTests(unittest.TestCase):
    def test_top_browser_and_never_other_chromium_apps(self) -> None:
        ventanas = [w.Ventana(1, "Claude", "claude", 5), w.Ventana(2, "Visual Studio Code", "code", 6),
                    w.Ventana(3, "Twitch - Brave", "brave", 7), w.Ventana(4, "Otra - Brave", "brave", 7)]
        with patch.object(w, "listar_ventanas", return_value=ventanas):
            self.assertEqual(m._ventana_navegador(None).hwnd, 3)
        with patch.object(w, "listar_ventanas", return_value=ventanas[:2]):
            self.assertIsNone(m._ventana_navegador(None))


if __name__ == "__main__":
    unittest.main()
