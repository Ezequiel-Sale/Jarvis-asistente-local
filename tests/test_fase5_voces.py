"""Fase 5: varias voces de Piper (catálogo, elección y petición a Wyoming)."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import jarvis_core as core
import jarvis_voices as v
from jarvis_commands import detectar_comando_voz


class CatalogTests(unittest.TestCase):
    def test_keys_are_unique_and_default_exists(self) -> None:
        claves = [voz.clave for voz in v.VOCES]
        self.assertEqual(len(claves), len(set(claves)))
        self.assertEqual(v.por_clave(None).clave, v.POR_DEFECTO)
        self.assertEqual(v.por_clave("no_existe").modelo, "es_ES-davefx-medium")

    def test_installed_needs_both_files(self) -> None:
        with tempfile.TemporaryDirectory() as carpeta:
            base = Path(carpeta)
            daniela = v.por_clave("daniela")
            self.assertFalse(v.instalada(daniela, base))
            (base / "es_AR-daniela-high.onnx").write_bytes(b"x")
            self.assertFalse(v.instalada(daniela, base))
            (base / "es_AR-daniela-high.onnx.json").write_text("{}")
            self.assertTrue(v.instalada(daniela, base))
            self.assertEqual([x.clave for x in v.disponibles(base)], ["daniela"])

    def test_search_by_name_country_or_gender(self) -> None:
        self.assertEqual(v.buscar("la argentina").clave, "daniela")
        self.assertEqual(v.buscar("Daniela").clave, "daniela")
        self.assertEqual(v.buscar("sharvard mujer").clave, "sharvard_f")
        self.assertEqual(v.buscar("sharvard").clave, "sharvard_m")
        self.assertEqual(v.buscar("mexicana").clave, "ald")
        self.assertIsNone(v.buscar("Pepe"))

    def test_phrases(self) -> None:
        self.assertEqual(detectar_comando_voz("Cambia tu voz a la argentina."), ("set_voice", {"voice": "daniela"}))
        self.assertEqual(detectar_comando_voz("usa la voz de Daniela"), ("set_voice", {"voice": "daniela"}))
        self.assertIsNone(detectar_comando_voz("cambia la voz a Pepe"))
        self.assertIsNone(detectar_comando_voz("cambia el volumen"))


class WyomingTests(unittest.TestCase):
    def tearDown(self) -> None:
        v.aplicar(v.por_clave(v.POR_DEFECTO))

    def test_request_uses_chosen_voice_and_speaker(self) -> None:
        self.assertEqual(core._voz_wyoming(), {"name": "es_ES-davefx-medium"})
        v.aplicar(v.por_clave("sharvard_f"))
        self.assertEqual(core._voz_wyoming(), {"name": "es_ES-sharvard-medium", "speaker": "F"})
        # Una prueba con otra voz no hereda el hablante de la elegida.
        self.assertEqual(core._voz_wyoming("es_AR-daniela-high"), {"name": "es_AR-daniela-high"})

    @patch("jarvis_core.socket.create_connection", side_effect=ConnectionRefusedError("sin piper"))
    def test_explicit_voice_is_sent(self, conectar) -> None:
        with self.assertRaises(Exception):
            core.generar_audio_tts("hola", "prueba_unitaria", voz="es_MX-claude-high")


if __name__ == "__main__":
    unittest.main()
