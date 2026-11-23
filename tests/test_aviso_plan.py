"""Plan con sus pasos en el aviso flotante cuando Jarvis está minimizado (2026-11-20)."""

import tkinter as tk
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import jarvis_floating
from jarvis_app import JarvisApp


class AvisoConPasosTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(f"Tk no disponible: {error}")
        self.root.withdraw()
        self.addCleanup(self.root.destroy)

    def textos(self, widget) -> list[str]:
        salida = []
        for hijo in widget.winfo_children():
            try:
                salida.append(str(hijo.cget("text")))
            except tk.TclError:
                pass
            salida += self.textos(hijo)
        return salida

    def test_steps_and_named_buttons(self) -> None:
        aviso = jarvis_floating.AvisoFlotante(self.root, Mock())
        si, no = Mock(), Mock()
        pasos = [(f"🪟 Controlar ventanas: paso {n}", "RIESGO BAJO", "MUTED") for n in range(1, 11)]
        aviso.pregunta("¿Ejecuto este plan? Ordenar ventanas", si, no, pasos=pasos,
                       si="EJECUTAR PLAN", no="NO EJECUTAR")
        textos = self.textos(aviso.ventana)
        self.assertIn("🪟 Controlar ventanas: paso 1", textos)
        self.assertIn("RIESGO BAJO", textos)
        self.assertNotIn("🪟 Controlar ventanas: paso 9", textos)  # tope de 8
        self.assertTrue(any("y 2 pasos más" in t for t in textos))
        botones = [b for b in aviso.ventana.winfo_children()[0].winfo_children()[-1].winfo_children()
                   if isinstance(b, tk.Button)]
        self.assertEqual([b.cget("text") for b in botones], ["EJECUTAR PLAN", "NO EJECUTAR"])
        botones[0].invoke()
        si.assert_called_once()
        aviso.quitar_pregunta("Aprobado")
        self.assertEqual(aviso._pasos, [])
        self.assertEqual(aviso._botones, ("SÍ", "NO"))

    def test_plain_question_keeps_si_no(self) -> None:
        aviso = jarvis_floating.AvisoFlotante(self.root, Mock())
        aviso.pregunta("¿Lo hago?", Mock(), Mock())
        self.assertIn("SÍ", self.textos(aviso.ventana))


class ResumenPasoTests(unittest.TestCase):
    def test_step_summary(self) -> None:
        herramienta = SimpleNamespace(name="window_control", display_name="Controlar ventanas",
                                      risk=SimpleNamespace(value="low"))
        texto, riesgo, color = JarvisApp._plan_step_summary(
            SimpleNamespace(tool=herramienta, arguments={"app": "WhatsApp", "action": "window_focus"}))
        self.assertIn("Controlar ventanas", texto)
        self.assertTrue(riesgo)
        self.assertTrue(color)


if __name__ == "__main__":
    unittest.main()
