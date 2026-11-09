"""Fase 6: modo juego automático (detección de juegos y disparadores de rutinas)."""

import queue
import unittest.mock
import tempfile
import unittest
from pathlib import Path

import jarvis_games_watch as g
import jarvis_routine_form as form
import jarvis_routines as r

RAICES = [Path(r"C:\Program Files (x86)\Steam\steamapps\common"), Path(r"C:\Games")]


class GameDetectionTests(unittest.TestCase):
    def test_game_is_the_folder_under_a_game_root(self) -> None:
        self.assertEqual(g.juego_de(r"C:\Program Files (x86)\Steam\steamapps\common\ProjectZomboid\ProjectZomboid64.exe", RAICES),
                         "ProjectZomboid")
        self.assertEqual(g.juego_de(r"c:\games\Sekiro\sekiro.exe", RAICES), "Sekiro")

    def test_non_games_are_ignored(self) -> None:
        for ruta in (
            r"C:\Program Files (x86)\Steam\steamapps\common\wallpaper_engine\wallpaper64.exe",
            r"C:\Program Files (x86)\Steam\steamapps\common\ProjectZomboid\UnityCrashHandler64.exe",
            r"C:\Games\Elden Ring\EasyAntiCheat\EasyAntiCheat_EOS_Setup.exe",
            r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
            r"C:\Games\suelto.exe",  # directamente en la raíz: no es la carpeta de un juego
            "",
        ):
            with self.subTest(ruta=ruta):
                self.assertIsNone(g.juego_de(ruta, RAICES))

    def test_steam_libraries_are_read(self) -> None:
        with tempfile.TemporaryDirectory() as carpeta:
            steam = Path(carpeta)
            (steam / "steamapps").mkdir()
            (steam / "steamapps" / "libraryfolders.vdf").write_text(
                '"libraryfolders" { "0" { "path" "D:\\\\SteamLibrary" } }', encoding="utf-8")
            rutas = {str(p) for p in g.bibliotecas_steam(steam)}
        self.assertIn(str(Path(r"D:\SteamLibrary") / "steamapps" / "common"), rutas)
        self.assertIn(str(steam / "steamapps" / "common"), rutas)


class WatcherTests(unittest.TestCase):
    def _vigilante(self, lecturas):
        eventos: queue.Queue = queue.Queue()
        datos = iter(lecturas)
        return g.VigilanteJuegos(eventos, leer=lambda: next(datos)), eventos

    def test_open_and_close_need_two_readings(self) -> None:
        vigilante, eventos = self._vigilante([set(), {"Sekiro"}, {"Sekiro"}, {"Sekiro"}, set(), set()])
        for _ in range(6):
            vigilante.revisar_una_vez()
        self.assertEqual(eventos.get_nowait(), ("game_started", "Sekiro"))
        self.assertEqual(eventos.get_nowait(), ("game_stopped", "Sekiro"))
        self.assertTrue(eventos.empty())

    def test_short_lived_process_does_not_fire(self) -> None:
        vigilante, eventos = self._vigilante([set(), {"Launcher"}, set(), set()])
        for _ in range(4):
            vigilante.revisar_una_vez()
        self.assertTrue(eventos.empty())

    def test_game_already_open_at_startup_does_not_fire(self) -> None:
        vigilante, eventos = self._vigilante([{"Sekiro"}, {"Sekiro"}, {"Sekiro", "GTA"}])
        for _ in range(3):
            vigilante.revisar_una_vez()
        self.assertTrue(eventos.empty())
        self.assertEqual(vigilante.jugando, {"Sekiro", "GTA"})


class RoutineTriggerTests(unittest.TestCase):
    def test_triggers_are_known_everywhere(self) -> None:
        self.assertEqual(r._validate_trigger("game_started"), r.DISPARADOR_JUEGO_ABIERTO)
        self.assertEqual(r._validate_trigger("juego_cerrado"), r.DISPARADOR_JUEGO_CERRADO)
        self.assertIn(r.DISPARADOR_JUEGO_ABIERTO, r.DISPARADORES_EVENTO)
        self.assertEqual(r.describir_disparador(r.DISPARADOR_JUEGO_ABIERTO, "", ()), "al abrir un juego")
        self.assertEqual(form._DB_A_FORM[r.DISPARADOR_JUEGO_CERRADO], "game_stopped")
        self.assertIn("game_started", dict(form.DISPARADORES))

    def test_voice_phrases_create_game_routines(self) -> None:
        casos = {
            "cuando abra un juego, cambia la salida a auriculares": ("game_started", "set_output_device"),
            "cuando cierre el juego cambia la salida a altavoces": ("game_stopped", "set_output_device"),
            "cuando deje de jugar reanuda la música": ("game_stopped", "media_control"),
        }
        with unittest.mock.patch.object(r, "listar_rutinas", return_value=[]):
            for texto, (disparador, herramienta) in casos.items():
                with self.subTest(texto=texto):
                    pedido = r.parsear_solicitud_creacion(texto)
                    self.assertEqual(pedido.trigger, disparador)
                    self.assertIn(herramienta, pedido.steps_json)
            self.assertIsNone(r.parsear_solicitud_creacion("cuando abra Brave pon el volumen al 20"))


if __name__ == "__main__":
    unittest.main()
