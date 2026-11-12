"""Menú contextual de la pestaña (silenciar / activar sonido). Todo simulado."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import jarvis_media as m


def _item(nombre):
    return SimpleNamespace(ControlTypeName="MenuItemControl", Name=nombre)


def _ventana(nombre, *items):
    return SimpleNamespace(Name=nombre, ClassName="Chrome_WidgetWin_1", items=list(items))


class FakeAuto:
    def __init__(self, ventanas, aparece_en=1):
        self.ventanas = ventanas
        self.vueltas = 0
        self.aparece_en = aparece_en  # el menú aparece a partir de esta búsqueda
        self.recorridas = []

    def WalkControl(self, raiz, maxDepth):
        self.recorridas.append(raiz.Name)
        if raiz is self.ventanas[0]:
            self.vueltas += 1
        if self.vueltas < self.aparece_en:
            return []
        return [(c, 1) for c in raiz.items]

    def GetRootControl(self):
        return SimpleNamespace(GetChildren=lambda: self.ventanas)


class MenuSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        patcher = patch.object(m.time, "sleep")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_found_in_the_tabs_own_window_first(self) -> None:
        brave = _ventana("Padel - YouTube", _item("Duplicar"), _item("Activar sonido de la pestaña"))
        claude = _ventana("Claude")
        auto = FakeAuto([brave, claude])
        opcion = m._buscar_opcion_menu(auto, brave, mute=False)
        self.assertEqual(opcion.Name, "Activar sonido de la pestaña")
        self.assertEqual(auto.recorridas, ["Padel - YouTube"])  # no hizo falta recorrer Claude

    def test_waits_for_a_slow_menu(self) -> None:
        brave = _ventana("Padel - YouTube", _item("Silenciar pestaña"))
        auto = FakeAuto([brave], aparece_en=3)
        self.assertEqual(m._buscar_opcion_menu(auto, brave, mute=True).Name, "Silenciar pestaña")

    def test_searches_at_least_once_even_without_time(self) -> None:
        brave = _ventana("Padel - YouTube", _item("Silenciar pestaña"))
        self.assertIsNotNone(m._buscar_opcion_menu(FakeAuto([brave]), brave, mute=True, espera=0))

    def test_none_when_the_option_is_not_there(self) -> None:
        brave = _ventana("Padel - YouTube", _item("Duplicar"))
        with self.assertLogs(level="WARNING"):
            self.assertIsNone(m._buscar_opcion_menu(FakeAuto([brave]), brave, mute=False, espera=0))

    def test_menu_option_names(self) -> None:
        self.assertTrue(m._opcion_menu("Activar sonido de la pestaña", mute=False))
        self.assertTrue(m._opcion_menu("Silenciar pestaña", mute=True))
        self.assertFalse(m._opcion_menu("Silenciar sitio", mute=True))


class CompoundPhraseTests(unittest.TestCase):
    def test_mute_one_monitor_and_play_another(self) -> None:
        from jarvis_commands import detectar_comando_multimedia

        esperado = ("media_sequence", {"steps": [
            {"tool": "mute_browser_tab", "arguments": {"mute": True, "monitor": 3}},
            {"tool": "media_control", "arguments": {"action": "play", "monitor": 1}},
        ]})
        for texto in ("Mutea la pestaña del monitor 3 y reproduzca el video del monitor 1.",
                      "mutea la pestaña del monitor 3 y reproduce el video del monitor 1"):
            with self.subTest(texto=texto):
                self.assertEqual(detectar_comando_multimedia(texto), esperado)

    def test_monitor_without_saying_tab(self) -> None:
        from jarvis_commands import detectar_comando_multimedia

        casos = {
            "mutea el monitor 3": {"mute": True, "monitor": 3},
            "silencia la pantalla 2": {"mute": True, "monitor": 2},
            "silencia el segundo monitor": {"mute": True, "monitor": 2},
            "desmutea el monitor 3": {"mute": False, "monitor": 3},
            "quitale el silencio al monitor 3": {"mute": False, "monitor": 3},
        }
        for texto, argumentos in casos.items():
            with self.subTest(texto=texto):
                self.assertEqual(detectar_comando_multimedia(texto), ("mute_browser_tab", argumentos))
        # Antes iba al modelo, que eligió pantalla completa para «reproduce el vídeo».
        herramienta, argumentos = detectar_comando_multimedia(
            "Mutea el monitor 3 y reproduce el vídeo del monitor 1.")
        self.assertEqual(herramienta, "media_sequence")
        self.assertEqual(argumentos["steps"], [
            {"tool": "mute_browser_tab", "arguments": {"mute": True, "monitor": 3}},
            {"tool": "media_control", "arguments": {"action": "play", "monitor": 1}},
        ])

    def test_formal_forms(self) -> None:
        from jarvis_commands import detectar_comando_multimedia

        casos = {
            "reproduzca el video del monitor 1": ("media_control", {"action": "play", "monitor": 1}),
            "pause el video": ("media_control", {"action": "pause"}),
            "silencie la pestaña del monitor 2": ("mute_browser_tab", {"mute": True, "monitor": 2}),
            "desmutee la pestaña de twitch": ("mute_browser_tab", {"mute": False, "match": "twitch"}),
        }
        for texto, esperado in casos.items():
            with self.subTest(texto=texto):
                self.assertEqual(detectar_comando_multimedia(texto), esperado)

    def test_tab_name_never_swallows_another_order(self) -> None:
        from jarvis_commands import detectar_comando_multimedia

        # Antes: silenciar en el monitor 1 la pestaña «monitor 3 y reproduzca el video».
        self.assertIsNone(detectar_comando_multimedia("mutea la pestaña de x y z"))
        self.assertEqual(detectar_comando_multimedia("silencia la pestaña de yahoo"),
                         ("mute_browser_tab", {"mute": True, "match": "yahoo"}))


if __name__ == "__main__":
    unittest.main()
