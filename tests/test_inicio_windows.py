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


class TaskTests(unittest.TestCase):
    def test_task_starts_hidden_at_logon_without_admin(self) -> None:
        xml = autostart.xml_tarea(Path(r"C:\Mis cosas & más\Jarvis.exe"), "PC\\Ezequiel")
        self.assertIn("<LogonTrigger>", xml)
        self.assertIn("<UserId>PC\\Ezequiel</UserId>", xml)
        self.assertIn("<RunLevel>LeastPrivilege</RunLevel>", xml)
        self.assertIn("<Command>C:\\Mis cosas &amp; más\\Jarvis.exe</Command>", xml)
        self.assertIn("<Arguments>--autostart</Arguments>", xml)
        self.assertIn("<ExecutionTimeLimit>PT0S</ExecutionTimeLimit>", xml)
        self.assertTrue(autostart.arrancado_con_windows(["Jarvis.exe", "--autostart"]))
        self.assertFalse(autostart.arrancado_con_windows(["Jarvis.exe"]))

    def test_cannot_enable_from_source(self) -> None:
        with patch.object(autostart, "ejecutable", return_value=None):
            with self.assertRaises(OSError):
                autostart.activar()

    def test_sync_follows_preference_and_fixes_moved_path(self) -> None:
        casos = (
            (True, None, "activar"),
            (True, r"D:\Viejo\Jarvis.exe", "activar"),
            (True, str(EXE), None),
            (False, str(EXE), "desactivar"),
            (False, None, None),
        )
        for preferido, registrado, esperado in casos:
            with self.subTest(preferido=preferido, registrado=registrado), \
                    patch.object(autostart, "es_administrador", return_value=False), \
                    patch.object(autostart, "ejecutable", return_value=EXE), \
                    patch.object(autostart, "comando_registrado", return_value=registrado), \
                    patch.object(autostart, "_borrar_run_antiguo"), \
                    patch.object(autostart, "activar") as activar, \
                    patch.object(autostart, "desactivar") as desactivar:
                autostart.sincronizar(preferido)
                self.assertEqual(activar.called, esperado == "activar")
                self.assertEqual(desactivar.called, esperado == "desactivar")

    def test_sync_does_nothing_from_source(self) -> None:
        with patch.object(autostart, "ejecutable", return_value=None), \
                patch.object(autostart, "comando_registrado") as leer:
            autostart.sincronizar(True)
        leer.assert_not_called()

    def test_create_failure_is_reported(self) -> None:
        fallo = Mock(returncode=1, stderr="Acceso denegado.", stdout="")
        with patch.object(autostart, "_schtasks", return_value=fallo), \
                patch.object(autostart, "_borrar_run_antiguo") as borrar:
            with self.assertRaises(OSError) as error:
                autostart.activar(EXE)
        self.assertIn("Acceso denegado", str(error.exception))
        borrar.assert_not_called()  # sin tarea nueva, no se quita lo que había


class AdminTests(unittest.TestCase):
    def test_admin_tasks_run_with_highest_privileges(self) -> None:
        inicio = autostart.xml_tarea(EXE, "PC\\Ezequiel", administrador=True)
        self.assertIn("<RunLevel>HighestAvailable</RunLevel>", inicio)
        self.assertIn("<LogonTrigger>", inicio)
        abrir = autostart.xml_tarea(EXE, "PC\\Ezequiel", administrador=True, al_iniciar_sesion=False)
        self.assertIn("<RunLevel>HighestAvailable</RunLevel>", abrir)
        self.assertNotIn("<LogonTrigger>", abrir)
        self.assertNotIn("--autostart", abrir)
        self.assertIn("<MultipleInstancesPolicy>Parallel</MultipleInstancesPolicy>", abrir)

    def test_when_to_relaunch(self) -> None:
        casos = (
            (EXE, False, ["Jarvis.exe"], True),
            (EXE, True, ["Jarvis.exe"], False),  # ya es administrador
            (None, False, ["jarvis_app.py"], False),  # desde el código fuente
            (EXE, False, ["Jarvis.exe", "--autostart"], False),  # sin UAC al encender
            (EXE, False, ["Jarvis.exe", "--sin-admin"], False),
        )
        for ruta, admin, argv, esperado in casos:
            with self.subTest(argv=argv, admin=admin), \
                    patch.object(autostart, "ejecutable", return_value=ruta), \
                    patch.object(autostart, "es_administrador", return_value=admin):
                self.assertEqual(autostart.debe_relanzar_como_administrador(argv), esperado)

    def test_relaunch_prefers_task_without_uac(self) -> None:
        ok = Mock(returncode=0, stdout="", stderr="")
        with patch.object(autostart, "ejecutable", return_value=EXE), \
                patch.object(autostart, "comando_registrado", return_value=str(EXE)), \
                patch.object(autostart, "_schtasks", return_value=ok) as schtasks, \
                patch("ctypes.windll.shell32.ShellExecuteW") as uac:
            self.assertTrue(autostart.relanzar_como_administrador())
        schtasks.assert_called_once_with("/Run", "/TN", autostart.TAREA_ADMIN)
        uac.assert_not_called()

    def test_relaunch_asks_uac_without_task_and_continues_if_rejected(self) -> None:
        with patch.object(autostart, "ejecutable", return_value=EXE), \
                patch.object(autostart, "comando_registrado", return_value=None), \
                patch("ctypes.windll.shell32.ShellExecuteW", return_value=5) as uac, \
                self.assertLogs(level="WARNING"):
            self.assertFalse(autostart.relanzar_como_administrador())
        self.assertEqual(uac.call_args.args[1], "runas")

    def test_elevated_sync_creates_admin_task_and_upgrades_logon_task(self) -> None:
        with patch.object(autostart, "es_administrador", return_value=True), \
                patch.object(autostart, "ejecutable", return_value=EXE), \
                patch.object(autostart, "comando_registrado", return_value=str(EXE)), \
                patch.object(autostart, "tarea_elevada", return_value=False), \
                patch.object(autostart, "_crear_tarea") as crear, \
                patch.object(autostart, "activar") as activar:
            autostart.sincronizar(True)
        self.assertEqual(crear.call_args.args[0], autostart.TAREA_ADMIN)
        activar.assert_called_once_with(EXE)


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
