"""«Omitir intro/créditos» automático en la extensión de Brave (2026-11-23, pedido para Crunchyroll)."""

import json
import re
import unittest
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


class OmitirIntroTests(unittest.TestCase):
    def test_content_script_is_declared_and_packaged(self):
        manifiesto = json.loads((RAIZ / "brave_extension" / "manifest.json").read_text("utf-8"))
        script = manifiesto["content_scripts"][0]
        self.assertEqual(script["js"], ["saltar.js"])
        self.assertTrue(script["all_frames"])  # el reproductor puede ir en un iframe
        self.assertIn("https://*.crunchyroll.com/*", script["matches"])
        self.assertIn("'brave_extension/saltar.js'", (RAIZ / "Jarvis.spec").read_text("utf-8"))

    def test_only_skip_intro_and_credits(self):
        fuente = (RAIZ / "brave_extension" / "saltar.js").read_text("utf-8")
        patron = re.search(r"const OMITIR = /(.+)/i;", fuente).group(1)
        omitir = re.compile(patron, re.IGNORECASE)
        for texto in ("Omitir intro", "Omitir créditos", "Skip Intro", "Saltar introducción", "Skip Recap",
                      "Omitir resumen"):
            self.assertTrue(omitir.match(texto), texto)
        for texto in ("Omitir anuncio", "Siguiente episodio", "Omitir", "Omitir intro y ver más tarde",
                      "Cancelar suscripción"):
            self.assertFalse(omitir.match(texto), texto)

    def test_does_not_talk_to_any_server(self):
        fuente = (RAIZ / "brave_extension" / "saltar.js").read_text("utf-8")
        for prohibido in ("fetch(", "XMLHttpRequest", "sendMessage", "WebSocket"):
            self.assertNotIn(prohibido, fuente)


if __name__ == "__main__":
    unittest.main()
