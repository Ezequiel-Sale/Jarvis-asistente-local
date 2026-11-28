"""«Dame una lista de las rutinas que tengo» (2026-11-27: la IA intentó un script de PowerShell). Base temporal."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import jarvis_core as core
import jarvis_routines as r


class ListaRutinasTests(unittest.TestCase):
    def setUp(self) -> None:
        carpeta = tempfile.TemporaryDirectory()
        self.addCleanup(carpeta.cleanup)
        for parche in (patch.object(core, "DB_PATH", Path(carpeta.name) / "jarvis_memory.db"),
                       patch.object(core, "_database_initialized", False)):
            parche.start()
            self.addCleanup(parche.stop)
        core.inicializar_base_datos()

    def _crear(self, nombre, hora, dias, disparador, frases="[]", activa=1) -> None:
        with core.conexion_db() as conexion:
            conexion.execute(
                "INSERT INTO rutinas (nombre, hora, accion, activa, dias_semana, disparador, frases) "
                "VALUES (?, ?, '[]', ?, ?, ?, ?)", (nombre, hora, activa, dias, disparador, frases))

    def test_summary(self) -> None:
        self.assertIn("No tenés rutinas", r.resumen_rutinas())
        self._crear("Publicación LinkedIn", "17:00", "3,5", "hora")
        self._crear("Modo juego", "", "1,2,3,4,5,6,7", "frase", '["modo juego"]')
        self._crear("Al conectar auriculares", "", "1,2,3,4,5,6,7", "auriculares_conectados", activa=0)
        texto = r.resumen_rutinas()
        self.assertTrue(texto.startswith("Tenés 3 rutinas (2 activas)"))
        self.assertIn("«Publicación LinkedIn»: a las 17:00, mié, vie", texto)
        self.assertIn("«Modo juego»: con la frase «modo juego»", texto)
        self.assertIn("al conectar los auriculares (pausada)", texto)

    def test_phrases(self) -> None:
        for frase in ("Dame una lista de las rutinas que tengo guardada", "qué rutinas tengo",
                      "cuáles son mis rutinas", "listá mis rutinas", "cuántas rutinas tengo"):
            self.assertEqual(r.detectar_lista_rutinas(frase), ("list_routines", {}), frase)
        for frase in ("Jarvis rutinas", "abrí las rutinas", "pausa la rutina Inicio"):
            self.assertIsNone(r.detectar_lista_rutinas(frase), frase)


if __name__ == "__main__":
    unittest.main()
