"""Pestaña «Atajos» y guía de comandos del celular (servidor y app, sin la base real)."""

import queue
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import jarvis_mobile as m
import jarvis_routines
from jarvis_app import JarvisApp
from test_movil import _pedir, _vincular


class EndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.pedidos = []

        def accion(pedido):
            self.pedidos.append(pedido)
            if pedido.get("game") == "Roto":
                raise RuntimeError("No encontré «Roto».")
            return "hecho"

        guardado = {}
        self.servidor = m.ServidorMovil(
            queue.Queue(), m.Vinculacion(guardado.get, guardado.__setitem__),
            atajos=lambda: {"routines": [{"id": 1, "name": "Cine"}], "games": [{"name": "Hades", "actions": 0}]},
            atajo_accion=accion,
            comandos=lambda: {"sections": [{"icon": "📺", "title": "Tele", "items": []}], "aiHelp": "…"},
        )
        self.token = _vincular(self.servidor)

    def pedir(self, metodo, ruta, cuerpo=None, token="si"):
        return _pedir(self.servidor, metodo, ruta, cuerpo, token=self.token if token == "si" else token)

    def test_list_run_and_validate(self) -> None:
        estado, datos = self.pedir("GET", "/api/shortcuts")
        self.assertEqual((estado, datos["games"][0]["name"]), (200, "Hades"))
        estado, datos = self.pedir("POST", "/api/shortcuts", {"routine": 1, "action": "run"})
        self.assertEqual((estado, datos["text"], datos["shortcuts"]["routines"][0]["name"]), (200, "hecho", "Cine"))
        self.assertEqual(self.pedir("POST", "/api/shortcuts", {"routine": 1, "action": "disable"})[0], 200)
        self.assertEqual(self.pedir("POST", "/api/shortcuts", {"game": "Hades"})[0], 200)
        estado, datos = self.pedir("POST", "/api/shortcuts", {"game": "Roto"})
        self.assertEqual((estado, datos["error"]), (409, "No encontré «Roto»."))
        for malo in ({"routine": "1; drop"}, {"routine": 1, "action": "delete"}, {"game": ""}, {}):
            with self.subTest(malo=malo):
                self.assertEqual(self.pedir("POST", "/api/shortcuts", malo)[0], 400)
        self.assertEqual(self.pedir("GET", "/api/shortcuts", token=None)[0], 401)
        self.assertEqual(self.pedidos, [{"routine": 1, "action": "run"}, {"routine": 1, "action": "disable"},
                                        {"game": "Hades"}, {"game": "Roto"}])

    def test_commands(self) -> None:
        estado, datos = self.pedir("GET", "/api/commands")
        self.assertEqual((estado, datos["sections"][0]["title"]), (200, "Tele"))


class AppTests(unittest.TestCase):
    RUTINA = jarvis_routines.RoutineInfo(
        7, "Modo cine", "", (), True, jarvis_routines.DISPARADOR_FRASE, "preguntar",
        (jarvis_routines.RoutineStep("tv_control", {"action": "power_on"}),), ("modo cine",))

    def _host(self):
        host = Mock()
        host.events = queue.Queue()
        host.busy = False
        host._game_actions = lambda nombre: [{}] if nombre == "Hades" else []
        return host

    def test_shortcuts_data(self) -> None:
        import jarvis_system

        juegos = [SimpleNamespace(nombre="Hades", ejecutable=Path("h.exe")), SimpleNamespace(nombre="Roto", ejecutable=None)]
        with patch.object(jarvis_routines, "listar_automatizaciones", return_value=[self.RUTINA]), \
                patch.object(jarvis_system, "listar_juegos", return_value=juegos):
            host = self._host()
            host._CACHE_JUEGOS_MOVIL = 60.0
            host._mobile_games_cache = (0.0, [])
            datos = JarvisApp._shortcuts_for_mobile(host)
        self.assertEqual(datos["routines"][0]["when"], "al decir «modo cine»")
        self.assertEqual(datos["routines"][0]["steps"], ["Encender la tele"])
        self.assertEqual(datos["games"], [{"name": "Hades", "actions": 1}])  # «Roto» no se puede abrir

    def test_run_toggle_and_play(self) -> None:
        host = self._host()
        with patch.object(jarvis_routines, "listar_automatizaciones", return_value=[self.RUTINA]), \
                patch.object(jarvis_routines, "cambiar_estado_rutina") as cambiar:
            self.assertEqual(JarvisApp._shortcut_from_mobile(host, {"routine": 7, "action": "run"}), "Ejecuto «Modo cine».")
            self.assertEqual(host.events.get_nowait(), ("mobile_run_routine", 7))
            self.assertEqual(JarvisApp._shortcut_from_mobile(host, {"routine": 7, "action": "disable"}),
                             "«Modo cine» pausada.")
            cambiar.assert_called_once_with(7, False)
            host.busy = True
            with self.assertRaises(Exception):
                JarvisApp._shortcut_from_mobile(host, {"routine": 7, "action": "run"})
            with self.assertRaises(Exception):
                JarvisApp._shortcut_from_mobile(host, {"routine": 99, "action": "run"})
        host.tool_registry.execute.return_value = "Inicié Hades."
        self.assertEqual(JarvisApp._shortcut_from_mobile(host, {"game": "Hades"}), "Inicié Hades.")
        host.tool_registry.execute.assert_called_once_with("launch_game", {"name": "Hades"},
                                                           confirmed=True, origin="celular")

    def test_commands_put_your_phrases_first(self) -> None:
        with patch.object(jarvis_routines, "listar_automatizaciones", return_value=[self.RUTINA]):
            datos = JarvisApp._commands_for_mobile(Mock())
        self.assertEqual(datos["sections"][0]["items"][0]["phrase"], "modo cine")
        self.assertGreater(len(datos["sections"]), 5)


if __name__ == "__main__":
    unittest.main()
