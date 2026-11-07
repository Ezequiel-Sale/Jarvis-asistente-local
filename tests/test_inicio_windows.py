"""Inicio con Windows. No toca el registro real: se simulan sus funciones."""

import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import jarvis_autostart as autostart
from jarvis_app import JarvisApp
from jarvis_commands import detectar_comando_inicio_windows

EXE = Path(r"C:\Jarvis\Jarvis.exe")


class PhraseTests(unittest.TestCase):
    def test_phrases(self) -> None:
        casos = {
            "Activa el inicio con Windows": True,
            "Inicia con Windows.": True,
            "quiero que Jarvis se inicie al encender la PC": True,
            "Desactiva el inicio automático con Windows": False,
            "no te inicies con Windows": False,
            "quita el arranque con Windows": False,
            "apaga el equipo": None,
            "inicia Spotify": None,
        }
        for texto, esperado in casos.items():
            with self.subTest(texto=texto):
                self.assertEqual(detectar_comando_inicio_windows(texto), esperado)


class RegistryTests(unittest.TestCase):
    def test_command_starts_hidden(self) -> None:
        self.assertEqual(autostart.comando(EXE), f'"{EXE}" --autostart')
        self.assertTrue(autostart.arrancado_con_windows(["Jarvis.exe", "--autostart"]))
        self.assertFalse(autostart.arrancado_con_windows(["Jarvis.exe"]))

    def test_cannot_enable_from_source(self) -> None:
        with patch.object(autostart, "ejecutable", return_value=None):
            with self.assertRaises(OSError):
                autostart.activar()

    def test_sync_follows_preference_and_fixes_moved_path(self) -> None:
        casos = (
            (True, None, "activar"),
            (True, '"D:\\Viejo\\Jarvis.exe" --autostart', "activar"),
            (True, autostart.comando(EXE), None),
            (False, autostart.comando(EXE), "desactivar"),
            (False, None, None),
        )
        for preferido, registrado, esperado in casos:
            with self.subTest(preferido=preferido, registrado=registrado), \
                    patch.object(autostart, "ejecutable", return_value=EXE), \
                    patch.object(autostart, "valor_registrado", return_value=registrado), \
                    patch.object(autostart, "activar") as activar, \
                    patch.object(autostart, "desactivar") as desactivar:
                autostart.sincronizar(preferido)
                self.assertEqual(activar.called, esperado == "activar")
                self.assertEqual(desactivar.called, esperado == "desactivar")

    def test_sync_does_nothing_from_source(self) -> None:
        with patch.object(autostart, "ejecutable", return_value=None), \
                patch.object(autostart, "valor_registrado") as leer:
            autostart.sincronizar(True)
        leer.assert_not_called()


class ToggleTests(unittest.TestCase):
    def _host(self, deseado: bool) -> Mock:
        host = Mock()
        host._autostart_wanted = deseado
        host._PREFERENCIA_INICIO_WINDOWS = JarvisApp._PREFERENCIA_INICIO_WINDOWS
        return host

    @patch("jarvis_app.core")
    @patch("jarvis_app.jarvis_autostart")
    def test_disable_saves_preference(self, autostart_mock, core_mock) -> None:
        autostart_mock.desactivar.return_value = "Listo: Jarvis ya no se iniciará con Windows."
        host = self._host(True)
        JarvisApp._execute_autostart_toggle(host, False, "desactiva el inicio con windows")
        self.assertFalse(host._autostart_wanted)
        core_mock.guardar_preferencia.assert_called_once_with("inicio_windows", "0")
        host._add_message.assert_called_once_with("assistant", "Listo: Jarvis ya no se iniciará con Windows.")

    @patch("jarvis_app.core")
    @patch("jarvis_app.jarvis_autostart")
    def test_failure_keeps_state(self, autostart_mock, core_mock) -> None:
        autostart_mock.activar.side_effect = OSError("solo desde Jarvis.exe")
        host = self._host(False)
        JarvisApp._execute_autostart_toggle(host, True, "inicia con windows")
        self.assertFalse(host._autostart_wanted)
        core_mock.guardar_preferencia.assert_not_called()
        self.assertIn("No pude", host._add_message.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
