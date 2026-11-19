"""Programas con winget (jarvis_winget): salidas de winget simuladas, sin correrlo."""

import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import jarvis_temas
import jarvis_winget as w
from jarvis_tool_registry import ToolRisk
from jarvis_tools import crear_registro_herramientas

BUSQUEDA = """   -    \\    |
Nombre                     Id                         Versión     Coincidencia Origen
-------------------------------------------------------------------------------------
VLC media player           VideoLAN.VLC               3.0.24      Moniker: vlc winget
VLC media player (Nightly) VideoLAN.VLC.Nightly       4.0.0       Tag: vlc     winget
pympress                   Cimbali.pympress           1.8.6       Tag: vlc     winget
"""

ACTUALIZACIONES = """Nombre        Id               Versión   Disponible Origen
----------------------------------------------------------------
Git           Git.Git          2.55.0.5  2.56.0.4   winget
Radmin VPN    Famatech.Radmin  2.0.4899  2.1.4952   winget
2 actualizaciones disponibles.
"""


class TablaTests(unittest.TestCase):
    def test_parse_search_ignoring_progress(self):
        paquetes = w._paquetes(w.tabla(w.limpiar(BUSQUEDA)))
        self.assertEqual([p.id for p in paquetes], ["VideoLAN.VLC", "VideoLAN.VLC.Nightly", "Cimbali.pympress"])
        self.assertEqual(paquetes[0].coincidencia, "Moniker: vlc")

    def test_parse_upgrades(self):
        paquetes = w._paquetes(w.tabla(ACTUALIZACIONES))
        self.assertEqual([(p.nombre, p.version, p.disponible) for p in paquetes],
                         [("Git", "2.55.0.5", "2.56.0.4"), ("Radmin VPN", "2.0.4899", "2.1.4952")])

    def test_choose_moniker_or_stable(self):
        paquetes = w._paquetes(w.tabla(BUSQUEDA))
        self.assertEqual(w.elegir(paquetes, "vlc").id, "VideoLAN.VLC")
        self.assertEqual(w.elegir(paquetes, "VideoLAN.VLC.Nightly").id, "VideoLAN.VLC.Nightly")
        sin_moniker = [w.Paquete("Foo", "A.Foo"), w.Paquete("Foo Beta", "A.Foo.Beta")]
        self.assertEqual(w.elegir(sin_moniker, "fo").id, "A.Foo")
        with self.assertRaisesRegex(LookupError, "varios parecidos"):
            w.elegir([w.Paquete("Bar uno", "X.A"), w.Paquete("Bar dos", "X.B")], "bar")
        with self.assertRaisesRegex(LookupError, "No encontré"):
            w.elegir([], "nada")


class AccionesTests(unittest.TestCase):
    def test_install_uses_only_the_winget_source(self):
        with patch.object(w, "_correr", side_effect=[(0, BUSQUEDA), (0, "Instalado correctamente")]) as correr:
            self.assertEqual(w.instalar("vlc"), "Instalé VLC media player (3.0.24).")
        argumentos = correr.call_args_list[1].args[0]
        self.assertEqual(argumentos[:4], ["install", "--id", "VideoLAN.VLC", "--exact"])
        self.assertIn("winget", argumentos[argumentos.index("--source") + 1])
        self.assertNotIn("msstore", argumentos)
        self.assertNotIn("--accept-source-agreements", argumentos)

    def test_install_failure_is_reported(self):
        with patch.object(w, "_correr", side_effect=[(0, BUSQUEDA), (1, "algo\nEl instalador falló con el código 1603")]):
            with self.assertRaisesRegex(RuntimeError, "1603"):
                w.instalar("vlc")

    def test_update_all_reports_failures(self):
        with patch.object(w, "_correr", side_effect=[(0, ACTUALIZACIONES), (0, ""), (1, "")]):
            texto = w.actualizar("all")
        self.assertIn("Actualicé 1: Git", texto)
        self.assertIn("Fallaron: Radmin VPN", texto)


class FrasesYRegistroTests(unittest.TestCase):
    def test_phrases(self):
        casos = {
            "Instalá VLC": ("install_program", {"name": "vlc"}),
            "Desinstalá el programa VLC": ("uninstall_program", {"name": "vlc"}),
            "¿Qué programas tienen actualizaciones?": ("list_program_updates", {}),
            "Actualizá todos los programas": ("update_programs", {"name": "all"}),
            "Actualizá Discord": ("update_programs", {"name": "discord"}),
        }
        for texto, esperado in casos.items():
            with self.subTest(texto=texto):
                self.assertEqual(w.detectar(texto), esperado)
        for texto in ("Instalá las actualizaciones de Windows", "Actualizá la página", "Poné música"):
            self.assertIsNone(w.detectar(texto), texto)

    def test_risky_tools_always_ask_and_are_offered_by_topic(self):
        registry = crear_registro_herramientas(Mock())
        for nombre in ("install_program", "uninstall_program", "update_programs"):
            self.assertIs(registry.get(nombre).risk, ToolRisk.HIGH, nombre)
        nombres = lambda t: {x["function"]["name"] for x in t}  # noqa: E731
        self.assertIn("install_program", nombres(jarvis_temas.elegir(registry.ollama_tools(), "instalame el 7zip")))
        self.assertNotIn("install_program", nombres(jarvis_temas.elegir(registry.ollama_tools(), "¿qué hora es?")))

    def test_routed_for_text_and_voice(self):
        fuente = (Path(__file__).resolve().parent.parent / "jarvis_app_ordenes.py").read_text("utf-8")
        self.assertIn("jarvis_winget.detectar(text)", fuente)
        self.assertIn("jarvis_winget.detectar(transcript)", fuente)


if __name__ == "__main__":
    unittest.main()
