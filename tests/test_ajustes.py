"""Ajustes de Windows (jarvis_ajustes): todo simulado, sin tocar el registro ni las radios."""

import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import jarvis_ajustes as a
import jarvis_deshacer
import jarvis_temas
import jarvis_verifiers
from jarvis_tools import crear_registro_herramientas

# Blob real de la luz nocturna apagada (Windows 11 LTSC 2024, 2026-11-19).
APAGADA = bytes.fromhex("43420100 0a020100 2a06d2a5 fdd7062a 2b0e1343 420100d0 0a02c614 c08682e1 f18bdeee 01000000 00")


class LuzNocturnaTests(unittest.TestCase):
    def test_turn_on_and_off_keeps_the_format(self):
        encendida = a.blob_luz(APAGADA, True)
        self.assertEqual(len(encendida), len(APAGADA) + 2)
        self.assertTrue(a.luz_activa(encendida))
        self.assertEqual(encendida[23:25], b"\x10\x00")
        self.assertNotEqual(encendida[10:15], APAGADA[10:15])  # la marca de tiempo avanza
        otra_vez = a.blob_luz(encendida, False)
        self.assertFalse(a.luz_activa(otra_vez))
        self.assertEqual(len(otra_vez), len(APAGADA))
        self.assertEqual(otra_vez[15:], APAGADA[15:])

    def test_same_state_does_not_change(self):
        self.assertEqual(a.blob_luz(APAGADA, False), APAGADA)

    def test_unknown_format_is_refused(self):
        with self.assertRaises(ValueError):
            a.luz_activa(b"\x00" * 30)


class CambiarTests(unittest.TestCase):
    def setUp(self):
        self.estado = {"night_light": False, "dark_mode": True, "wifi": True, "bluetooth": True}

        def lector(setting):
            def leer(activar):
                if activar is not None:
                    self.estado[setting] = activar
                return self.estado[setting]
            return leer

        self.parche = patch.dict(a._LECTORES, {s: lector(s) for s in a.AJUSTES})
        self.parche.start()
        self.addCleanup(self.parche.stop)

    def test_change_and_already_set(self):
        self.assertEqual(a.cambiar("night_light", "on"), "Listo: activé la luz nocturna.")
        self.assertTrue(self.estado["night_light"])
        self.assertIn("ya estaba activado", a.cambiar("dark_mode", "on"))
        self.assertEqual(a.cambiar("bluetooth", "off"), "Listo: desactivé el Bluetooth.")

    def test_bad_arguments(self):
        with self.assertRaises(ValueError):
            a.cambiar("airplane", "on")
        with self.assertRaises(ValueError):
            a.cambiar("wifi", "toggle")

    def test_undo_returns_to_the_previous_state(self):
        historial = jarvis_deshacer.Historial({"ajuste": a.estado})
        historial.anotar(historial.capturar("windows_setting", {"setting": "wifi", "state": "off"}, "directo"))
        deshacer = historial.sacar()
        self.assertEqual((deshacer.herramienta, deshacer.argumentos), ("windows_setting", {"setting": "wifi", "state": "on"}))
        self.assertIn("activar el Wi-Fi", deshacer.descripcion)
        self.assertIsNone(historial.capturar("windows_setting", {"setting": "wifi", "state": "on"}, "directo"))

    def test_verifier_reads_the_state_again(self):
        bien = jarvis_verifiers.verificar_ajuste({"setting": "wifi", "state": "on"}, None, leer=lambda _s: True)
        mal = jarvis_verifiers.verificar_ajuste({"setting": "wifi", "state": "off"}, None, leer=lambda _s: True)
        self.assertTrue(bien.ok)
        self.assertFalse(mal.ok)


class InicioTests(unittest.TestCase):
    PROGRAMAS = [a.Inicio("Discord", "C:/Discord/Update.exe", True, "HKCU", "Run", "Discord"),
                 a.Inicio("Steam", "C:/Steam/steam.exe -silent", False, "HKCU", "Run", "Steam"),
                 a.Inicio("Spotify", "C:/Spotify/Spotify.exe", True, "HKCU", "StartupFolder", "Spotify.lnk")]

    def test_approved_mark(self):
        self.assertTrue(a.activo_segun(None))
        self.assertTrue(a.activo_segun(bytes([2]) + bytes(11)))
        self.assertTrue(a.activo_segun(bytes([6]) + bytes(11)))
        self.assertFalse(a.activo_segun(bytes([3]) + bytes(11)))

    def test_choose_by_name(self):
        self.assertEqual(a.elegir_inicio(self.PROGRAMAS, "discord").nombre, "Discord")
        self.assertEqual(a.elegir_inicio(self.PROGRAMAS, "spoti").nombre, "Spotify")
        with self.assertRaises(LookupError):
            a.elegir_inicio(self.PROGRAMAS, "whatsapp")

    def test_list_enable_disable(self):
        with patch.object(a, "listar_inicio", return_value=self.PROGRAMAS), patch.object(a, "marcar_inicio") as marcar:
            self.assertEqual(a.programas_de_inicio("list"),
                             "Arrancan con Windows: Discord, Spotify. Desactivados: Steam.")
            self.assertIn("ya no arranca", a.programas_de_inicio("disable", "Discord"))
            marcar.assert_called_once_with(self.PROGRAMAS[0], False)
            marcar.reset_mock()
            self.assertIn("ya no arrancaba", a.programas_de_inicio("disable", "Steam"))
            marcar.assert_not_called()

    def test_undo_and_verify(self):
        historial = jarvis_deshacer.Historial({"inicio": lambda nombre: nombre == "Discord"})
        historial.anotar(historial.capturar("startup_apps", {"action": "disable", "name": "Discord"}, "directo"))
        self.assertEqual(historial.sacar().argumentos, {"action": "enable", "name": "Discord"})
        self.assertTrue(jarvis_verifiers.verificar_inicio({"action": "disable", "name": "Discord"}, None,
                                                          leer=lambda _n: False).ok)


class FrasesTests(unittest.TestCase):
    def test_direct_phrases(self):
        casos = {
            "Desactivá la luz nocturna": ("windows_setting", {"setting": "night_light", "state": "off"}),
            "Sacá la luz nocturna.": ("windows_setting", {"setting": "night_light", "state": "off"}),
            "Prendé la luz nocturna": ("windows_setting", {"setting": "night_light", "state": "on"}),
            "Activá el modo oscuro de Windows": ("windows_setting", {"setting": "dark_mode", "state": "on"}),
            "Poné el modo claro": ("windows_setting", {"setting": "dark_mode", "state": "off"}),
            "Jarvis, apagá el wifi": ("windows_setting", {"setting": "wifi", "state": "off"}),
            "Desconectá el bluetooth": ("windows_setting", {"setting": "bluetooth", "state": "off"}),
            "¿Qué programas arrancan con Windows?": ("startup_apps", {"action": "list"}),
            "Sacá Discord del inicio": ("startup_apps", {"action": "disable", "name": "discord"}),
            "Que Steam no arranque con Windows": ("startup_apps", {"action": "disable", "name": "steam"}),
            "Agregá Discord al inicio": ("startup_apps", {"action": "enable", "name": "discord"}),
        }
        for texto, esperado in casos.items():
            with self.subTest(texto=texto):
                self.assertEqual(a.detectar(texto), esperado)
        for texto in ("Apagá la luz del living", "Activá la luz nocturna y abrí Spotify", "Poné música"):
            self.assertIsNone(a.detectar(texto), texto)

    def test_routed_for_text_and_voice(self):
        fuente = (Path(__file__).resolve().parent.parent / "jarvis_app_ordenes.py").read_text("utf-8")
        self.assertIn("jarvis_ajustes.detectar(text)", fuente)
        self.assertIn("jarvis_ajustes.detectar(transcript)", fuente)


class RegistroTests(unittest.TestCase):
    def test_tools_are_offered_only_for_settings(self):
        tools = crear_registro_herramientas(Mock()).ollama_tools()
        nombres = lambda t: {x["function"]["name"] for x in t}  # noqa: E731
        self.assertIn("windows_setting", nombres(jarvis_temas.elegir(tools, "apagá el bluetooth")))
        self.assertNotIn("windows_setting", nombres(jarvis_temas.elegir(tools, "¿qué hora es?")))

    def test_settings_pages_are_fixed_uris(self):
        self.assertTrue(all(uri.startswith("ms-settings:") for uri in a.PAGINAS.values()))
        self.assertEqual(set(a.PAGINAS), set(a.NOMBRES_PAGINAS))
        with patch("os.startfile") as abrir:
            self.assertIn("Notificaciones", a.abrir_pagina("notifications"))
        abrir.assert_called_once_with("ms-settings:notifications")


if __name__ == "__main__":
    unittest.main()
