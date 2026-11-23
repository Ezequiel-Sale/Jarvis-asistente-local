"""Respuesta en vivo con formato desde el principio (2026-11-20)."""

import unittest
from pathlib import Path

import jarvis_markdown as md


class CerrarParcialTests(unittest.TestCase):
    def test_closes_what_is_still_open(self):
        self.assertEqual(md.cerrar_parcial("Hola **Bue"), "Hola **Bue**")
        self.assertEqual(md.cerrar_parcial("usa `pip"), "usa `pip`")
        self.assertEqual(md.cerrar_parcial("```python\nprint(1)"), "```python\nprint(1)\n```")
        self.assertEqual(md.cerrar_parcial("**listo** y `x`"), "**listo** y `x`")

    def test_partial_text_renders_without_symbols(self):
        visible = "".join(c for c, _e, _u in md.analizar(md.cerrar_parcial("## Título\n- **uno")))
        self.assertNotIn("*", visible)
        self.assertNotIn("#", visible)

    def test_live_bubble_uses_rich_text(self):
        fuente = (Path(__file__).resolve().parent.parent / "jarvis_app_voz.py").read_text("utf-8")
        self.assertIn("self._rich_text(burbuja, texto, PANEL_LIGHT)", fuente)
        self.assertIn("jarvis_markdown.cerrar_parcial", fuente)


class SeguirElFinalTests(unittest.TestCase):
    """2026-11-20: en un chat largo, subir un poco con la rueda no debe seguir «pegado» al final."""

    def seguir(self, abajo, alto_total):
        from jarvis_app import JarvisApp

        lienzo = type("Lienzo", (), {"yview": lambda _s: (abajo - 0.1, abajo),
                                     "cget": lambda _s, _k: f"0 0 800 {alto_total}"})()
        host = type("Host", (), {"chat_canvas": lienzo, "DISTANCIA_SEGUIR_PX": JarvisApp.DISTANCIA_SEGUIR_PX})()
        JarvisApp._update_chat_following(host)
        return host._chat_following

    def test_follow_by_pixels(self):
        self.assertTrue(self.seguir(1.0, 50000))
        self.assertFalse(self.seguir(0.995, 50000))  # 250 px más arriba: antes seguía (≥ 99 %)
        self.assertTrue(self.seguir(0.995, 2000))  # 10 px: sigue


if __name__ == "__main__":
    unittest.main()
