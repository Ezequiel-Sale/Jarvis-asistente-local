import logging
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

import jarvis_core as core
import jarvis_health as health
import jarvis_logging
from jarvis_actions import CancellationToken
from jarvis_app import JarvisApp
from jarvis_commands import detectar_borrado_historial, detectar_consulta_sistema


class LoggingTests(unittest.TestCase):
    def test_log_file_is_created_and_receives_records(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        root = logging.getLogger()
        handlers_before = list(root.handlers)
        original_hooks = (jarvis_logging.sys.excepthook, jarvis_logging.threading.excepthook)

        def restore() -> None:
            for handler in list(root.handlers):
                if handler not in handlers_before:
                    root.removeHandler(handler)
                    handler.close()
            jarvis_logging._file_handler = None
            jarvis_logging.sys.excepthook, jarvis_logging.threading.excepthook = original_hooks

        self.addCleanup(restore)
        with patch.object(jarvis_logging, "_file_handler", None):
            path = jarvis_logging.configurar_registro(Path(temp_dir.name))
            self.assertEqual(jarvis_logging.configurar_registro(Path(temp_dir.name)), path)
            logging.getLogger().info("mensaje de prueba de registro")
            jarvis_logging._file_handler.flush()
            self.assertIn("mensaje de prueba de registro", path.read_text(encoding="utf-8"))
            self.assertEqual(path.parent.name, "logs")


class LatencyTests(unittest.TestCase):
    def test_summary_by_stage(self) -> None:
        with patch.object(core, "_latencias_recientes", {}):
            core.registrar_latencia("llm", 2.0)
            core.registrar_latencia("llm", 4.0, ok=False)
            self.assertEqual(core.resumen_latencias()["llm"], (2, 3.0, 4.0, 1))


class HealthTests(unittest.TestCase):
    def _response(self, payload: dict) -> Mock:
        response = Mock()
        response.json.return_value = payload
        response.raise_for_status.return_value = None
        return response

    @patch("jarvis_health.requests.get", side_effect=requests.ConnectionError("rechazada"))
    def test_ollama_down(self, _get: Mock) -> None:
        result = health.comprobar_ollama()
        self.assertFalse(result.ok)
        self.assertIn("abre la aplicación de Ollama", result.detalle)

    @patch("jarvis_health.requests.get")
    def test_ollama_without_model(self, get: Mock) -> None:
        get.return_value = self._response({"models": [{"name": "otro:7b"}]})
        result = health.comprobar_ollama()
        self.assertFalse(result.ok)
        self.assertIn(core.MODEL_LLM, result.detalle)

    @patch("jarvis_health.requests.get")
    def test_ollama_ready_and_loaded(self, get: Mock) -> None:
        get.return_value = self._response({"models": [{"name": core.MODEL_LLM}]})
        result = health.comprobar_ollama()
        self.assertTrue(result.ok)
        self.assertIn("cargado en memoria", result.detalle)

    def test_service_urls_avoid_localhost_ipv6_delay(self) -> None:
        self.assertIn("127.0.0.1", core.LLM_URL)
        self.assertIn("127.0.0.1", core.STT_URL)

    def test_closed_port_is_reported(self) -> None:
        result = health.comprobar_puerto("Prueba", "127.0.0.1", 1, timeout=0.2)
        self.assertFalse(result.ok)

    def test_report_format(self) -> None:
        texto = health.formatear_diagnostico(
            [health.Comprobacion("Ollama", True, "funciona."),
             health.Comprobacion("Texto a voz (TTS)", False, "no responde.")],
            {"llm": (3, 2.5, 4.0, 1)},
            {"tiempo_agotado": 2},
            2048,
            "C:/Jarvis/logs/jarvis.log",
        )
        self.assertIn("- ✓ Ollama: funciona.", texto)
        self.assertIn("- ✗ Texto a voz (TTS): no responde.", texto)
        self.assertIn("- Ollama: media 2.5 s, máximo 4.0 s (3 mediciones, 1 con error)", texto)
        self.assertIn("2 tiempo agotado", texto)
        self.assertIn("Registro de errores: C:/Jarvis/logs/jarvis.log", texto)

    def test_diagnostic_phrases(self) -> None:
        for texto in ("diagnóstico", "Haz un diagnóstico", "estado de Jarvis", "¿funciona Ollama?"):
            with self.subTest(texto=texto):
                self.assertEqual(detectar_consulta_sistema(texto), ("get_jarvis_diagnostics", {}))
        self.assertEqual(detectar_consulta_sistema("estado del sistema"), ("get_system_status", {}))


class RetentionTests(unittest.TestCase):
    def setUp(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.dir = Path(temp_dir.name) / "responses"
        self.dir.mkdir()
        patcher = patch.object(core, "RESPONSES_DIR", self.dir)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _file(self, name: str, age: int) -> Path:
        path = self.dir / name
        path.write_bytes(b"x")
        stamp = time.time() - age
        os.utime(path, (stamp, stamp))
        return path

    def test_no_text_file_is_written(self) -> None:
        identifier = core.guardar_respuesta("texto privado")
        self.assertTrue(identifier.startswith("respuesta_"))
        self.assertEqual(list(self.dir.iterdir()), [])

    def test_keeps_newest_audios_and_removes_text_files(self) -> None:
        for index in range(5):
            self._file(f"respuesta_2026110{index}_000000_000000.wav", age=100 - index)
        self._file("respuesta_20261101_000000_000000.txt", age=1)
        other = self._file("notas_personales.txt", age=1)
        self.assertEqual(core.limpiar_respuestas(max_audios=2), 4)
        remaining = sorted(p.name for p in self.dir.iterdir())
        self.assertEqual(
            remaining,
            ["notas_personales.txt", "respuesta_20261103_000000_000000.wav",
             "respuesta_20261104_000000_000000.wav"],
        )
        self.assertTrue(other.exists())


class HistoryDeletionTests(unittest.TestCase):
    def setUp(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        for patcher in (
            patch.object(core, "DB_PATH", Path(temp_dir.name) / "jarvis_memory.db"),
            patch.object(core, "_database_initialized", False),
            patch.object(core, "RESPONSES_DIR", Path(temp_dir.name) / "responses"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_history_is_deleted_but_memories_and_routines_are_kept(self) -> None:
        conversation = core.obtener_conversacion_actual()
        core.guardar_turno(conversation, "hola", "qué tal")
        with core.conexion_db() as connection:
            connection.execute("INSERT INTO memorias (contenido) VALUES ('prefiero Brave')")
        new_id = core.borrar_historial_conversaciones()
        self.assertNotEqual(new_id, conversation)
        self.assertEqual(core.obtener_historial(new_id), [])
        with core.conexion_db() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM mensajes").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM memorias").fetchone()[0], 1)
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM conversaciones").fetchone()[0], 1
            )

    def test_phrases(self) -> None:
        self.assertTrue(detectar_borrado_historial("Borra el historial de conversaciones"))
        self.assertTrue(detectar_borrado_historial("Jarvis, borrar historial"))
        self.assertFalse(detectar_borrado_historial("borra el historial de acciones"))
        self.assertFalse(detectar_borrado_historial("borra la rutina 2"))


class VoiceCancellationTests(unittest.TestCase):
    @patch("audio_capture.limpiar_audio")
    @patch("jarvis_core.procesar_stt", side_effect=lambda _path: time.sleep(2) or "hola")
    def test_cancel_during_transcription(self, _stt: Mock, _clean: Mock) -> None:
        host = Mock()
        host.events = __import__("queue").Queue()
        token = CancellationToken()
        __import__("threading").Timer(0.2, token.cancel).start()
        started = time.monotonic()
        JarvisApp._voice_worker(host, "audio.wav", 0.0, "conv-1", False, "plan", token)
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertEqual(host.events.get_nowait()[0], "cancelled")


if __name__ == "__main__":
    unittest.main()
