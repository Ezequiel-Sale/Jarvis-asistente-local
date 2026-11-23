"""Ventana nueva de Brave (2026-11-20): frases y argumentos, sin abrir el navegador."""

import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import jarvis_app_herramientas
import jarvis_cards
from jarvis_commands import detectar_comando_nueva_pestana, detectar_comando_nueva_ventana


class FrasesTests(unittest.TestCase):
    def test_window_phrases(self):
        for texto in ("Abro una nueva ventana", "Abrí una ventana nueva de Brave", "abre otra ventana",
                      "Jarvis, abre una nueva ventana en Brave.", "nueva ventana", "ventana de Brave"):
            self.assertEqual(detectar_comando_nueva_ventana(texto), {"window": True}, texto)
        for texto in ("Abrí una ventana de incógnito", "abre una ventana privada de Brave",
                      "abrime una nueva ventana en modo incógnito"):
            self.assertEqual(detectar_comando_nueva_ventana(texto), {"window": True, "private": True}, texto)

    def test_not_a_new_window(self):
        for texto in ("ventana", "abre la ventana", "minimiza la ventana", "cierra esta ventana",
                      "abre una nueva pestaña", "abre una ventana del explorador"):
            self.assertIsNone(detectar_comando_nueva_ventana(texto), texto)
        self.assertTrue(detectar_comando_nueva_pestana("abre una nueva pestaña"))

    def test_routed_for_text_and_voice(self):
        fuente = (Path(__file__).resolve().parent.parent / "jarvis_app_ordenes.py").read_text("utf-8")
        self.assertIn("detectar_comando_nueva_ventana(text)", fuente)
        self.assertIn("detectar_comando_nueva_ventana(transcript)", fuente)


class HerramientaTests(unittest.TestCase):
    def abrir(self, **argumentos):
        host = Mock()
        with patch.object(jarvis_app_herramientas, "encontrar_brave", return_value=Path("brave.exe")), \
                patch.object(jarvis_app_herramientas.subprocess, "Popen") as popen, \
                patch.object(jarvis_app_herramientas, "abrir_nueva_pestana_brave", return_value=True):
            texto = jarvis_app_herramientas.HerramientasMixin._tool_open_new_browser_tab(host, **argumentos)
        return texto, [c.args[0] for c in popen.call_args_list]

    def test_new_window_and_incognito(self):
        self.assertEqual(self.abrir(window=True), ("Abrí una ventana nueva en Brave.", [["brave.exe", "--new-window"]]))
        self.assertEqual(self.abrir(window=True, private=True),
                         ("Abrí una ventana de incógnito en Brave.", [["brave.exe", "--incognito"]]))
        self.assertEqual(self.abrir(), ("Abrí una pestaña nueva en Brave.", []))

    def test_card(self):
        self.assertEqual(jarvis_cards.describir_paso("open_new_browser_tab", {"window": True}),
                         "Abrir una ventana nueva de Brave")
        self.assertEqual(jarvis_cards.describir_paso("open_new_browser_tab", {}),
                         "Abrir una pestaña nueva en el navegador")


if __name__ == "__main__":
    unittest.main()
