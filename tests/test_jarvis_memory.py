import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import jarvis_core as core
import jarvis_memory


class JarvisMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path_patch = patch.object(
            core,
            "DB_PATH",
            Path(self.temp_dir.name) / "jarvis_memory.db",
        )
        self.db_path_patch.start()
        self.initialized_patch = patch.object(core, "_database_initialized", False)
        self.initialized_patch.start()
        self.addCleanup(self.temp_dir.cleanup)
        self.addCleanup(self.initialized_patch.stop)
        self.addCleanup(self.db_path_patch.stop)

    def test_explicit_memory_command_saves_and_lists_persistent_entry(self) -> None:
        response = jarvis_memory.procesar_comando(
            "Jarvis, recuerda que prefiero Brave como navegador"
        )
        self.assertEqual(response, "Lo recordaré. Guardé el recuerdo número 1.")
        self.assertEqual(
            jarvis_memory.procesar_comando("¿Qué recuerdas?"),
            "Estos son tus recuerdos guardados:\n1. prefiero Brave como navegador",
        )

    def test_voice_recognition_variant_saves_brave_preference_without_game_term(self) -> None:
        transcript = "Recuerdo que prefiero braiding."
        normalized = jarvis_memory.normalizar_transcripcion_memoria_voz(transcript)
        self.assertEqual(normalized, "Recuerdo que prefiero Brave.")
        response = jarvis_memory.procesar_comando(normalized)
        self.assertEqual(response, "Lo recordaré. Guardé el recuerdo número 1.")
        self.assertEqual(
            jarvis_memory.listar_recuerdos(),
            [(1, "prefiero Brave")],
        )

    def test_voice_normalization_only_corrects_brave_in_preference_context(self) -> None:
        self.assertEqual(
            jarvis_memory.normalizar_transcripcion_memoria_voz(
                "Recuerdo que el braiding es una técnica de peinado."
            ),
            "Recuerdo que el braiding es una técnica de peinado.",
        )
        self.assertEqual(
            jarvis_memory.normalizar_transcripcion_memoria_voz(
                "Abre el juego Braiding."
            ),
            "Abre el juego Braiding.",
        )

    def test_duplicate_memory_is_not_inserted_twice(self) -> None:
        first = jarvis_memory.procesar_comando("recuerda que uso Brave")
        second = jarvis_memory.procesar_comando("recuerda que uso brave")
        self.assertEqual(first, "Lo recordaré. Guardé el recuerdo número 1.")
        self.assertEqual(
            second,
            "Ya tenía guardado ese recuerdo (número 1).",
        )
        self.assertEqual(len(jarvis_memory.listar_recuerdos()), 1)

    def test_memory_search_returns_only_lexically_relevant_entries(self) -> None:
        jarvis_memory.guardar_recuerdo("Prefiero Brave como navegador")
        jarvis_memory.guardar_recuerdo("Mi juego favorito es Sekiro")
        self.assertEqual(
            jarvis_memory.buscar_recuerdos("¿Qué navegador prefiero?"),
            [(1, "Prefiero Brave como navegador")],
        )
        self.assertEqual(jarvis_memory.buscar_recuerdos("controlar volumen"), [])

    def test_recall_and_forget_commands_are_available(self) -> None:
        jarvis_memory.guardar_recuerdo("Mi editor favorito es VS Code")
        self.assertEqual(
            jarvis_memory.procesar_comando("¿Qué recuerdas sobre editor?"),
            "Estos son tus recuerdos guardados:\n1. Mi editor favorito es VS Code",
        )
        self.assertEqual(
            jarvis_memory.procesar_comando("olvida el recuerdo 1"),
            "Olvidé el recuerdo número 1.",
        )
        self.assertEqual(jarvis_memory.listar_recuerdos(), [])

    def test_memory_can_be_corrected_by_its_listed_number(self) -> None:
        jarvis_memory.guardar_recuerdo("Prefiero Chrome")
        self.assertEqual(
            jarvis_memory.procesar_comando(
                "actualiza el recuerdo 1 a Prefiero Brave"
            ),
            "Actualicé el recuerdo número 1.",
        )
        self.assertEqual(
            jarvis_memory.listar_recuerdos(),
            [(1, "Prefiero Brave")],
        )

    def test_memory_commands_reject_oversized_content_and_ignore_other_text(self) -> None:
        with self.assertRaises(ValueError):
            jarvis_memory.guardar_recuerdo("x" * 501)
        self.assertIsNone(jarvis_memory.procesar_comando("Hola, ¿cómo estás?"))


if __name__ == "__main__":
    unittest.main()
