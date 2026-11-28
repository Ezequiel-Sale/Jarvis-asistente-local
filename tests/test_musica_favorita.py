"""«Mi música favorita» se resuelve al buscar (2026-11-28). Recuerdos e historial de prueba, nunca la base real."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import jarvis_core as core
import jarvis_musica_favorita as m


class ResolverTests(unittest.TestCase):
    def test_from_memory_history_or_ask(self) -> None:
        recuerdos = ["Me gusta el café", "Mi música favorita es Lo-fi para trabajar."]
        self.assertEqual(m.resolver("mi música favorita", recuerdos, []), ("Lo-fi para trabajar", "tus recuerdos"))
        self.assertEqual(m.resolver("mis canciones favoritas", [], ["daft punk", "lofi", "daft punk"]),
                         ("daft punk", "lo que más buscaste"))
        self.assertEqual(m.resolver("daft punk", [], []), ("daft punk", ""))  # una búsqueda normal no cambia
        with self.assertRaises(LookupError):
            m.resolver("música favorita", [], ["mi música favorita"])  # su propia búsqueda no cuenta

    def test_latest_memory_wins(self) -> None:
        self.assertEqual(m.de_los_recuerdos(["Mi música favorita es rock", "Mis canciones favoritas son de Soda"]),
                         "Soda")


class HistorialTests(unittest.TestCase):
    def test_searches_from_the_action_log(self) -> None:
        carpeta = tempfile.TemporaryDirectory()
        self.addCleanup(carpeta.cleanup)
        with patch.object(core, "DB_PATH", Path(carpeta.name) / "jarvis_memory.db"), \
                patch.object(core, "_database_initialized", False):
            core.inicializar_base_datos()
            with core.conexion_db() as conexion:
                for consulta, estado in (("lofi", "exito"), ("lofi", "exito"), ("jazz", "exito"), ("rock", "error")):
                    conexion.execute(
                        "INSERT INTO acciones (herramienta, etiqueta, origen, estado, argumentos, duracion_ms) "
                        "VALUES ('search_youtube', 'YouTube', 'directo', ?, ?, 1)",
                        (estado, json.dumps({"query": consulta})))
            self.assertEqual(sorted(m._busquedas_reales()), ["jazz", "lofi", "lofi"])



class RecordarConVoseoTests(unittest.TestCase):
    def test_voseo_saves_a_memory(self) -> None:
        """2026-11-28: «Recordá que mi música favorita es el cuarteto» iba a la IA y no se guardaba."""
        import jarvis_memory

        guardados = []
        with patch.object(jarvis_memory, "guardar_recuerdo", side_effect=lambda c: guardados.append(c) or (7, True)):
            for frase in ("Recordá que mi música favorita es el cuarteto", "Acordate de que uso Brave",
                          "anotá que prefiero el modo oscuro", "Recordar que mi mano es la izquierda"):
                self.assertIn("Guardé el recuerdo", jarvis_memory.procesar_comando(frase))
            self.assertIsNone(jarvis_memory.procesar_comando("guardá la rutina"))
        self.assertEqual(guardados, ["mi música favorita es el cuarteto", "uso Brave", "prefiero el modo oscuro",
                                     "mi mano es la izquierda"])
        self.assertEqual(m.de_los_recuerdos(guardados[:1]), "el cuarteto")

if __name__ == "__main__":
    unittest.main()
