"""Responder a las confirmaciones sin «Hey Jarvis». Sin micrófono real."""

import os
import queue
import struct
import unittest
from unittest.mock import Mock, patch

import jarvis_wakeword as ww
from jarvis_app import JarvisApp


def _bloque(valor: int) -> bytes:
    return struct.pack(f"<{ww.BLOQUE}h", *([valor] * ww.BLOQUE))


class _Microfono:
    def __init__(self, energias: list[int]) -> None:
        self.bloques = [_bloque(e) for e in energias]

    def read(self, _n, exception_on_overflow=False):
        return self.bloques.pop(0) if self.bloques else _bloque(0)


class ShortAnswerRecordingTests(unittest.TestCase):
    def _grabar(self, energias: list[int]):
        detector = ww.DetectorPalabraActivacion(queue.Queue(), lambda: True)
        ruta = detector._grabar_respuesta(_Microfono(energias), 50, [_bloque(2000)])
        if ruta:
            self.addCleanup(os.remove, ruta)
        return ruta

    def test_short_answer_is_recorded(self) -> None:
        self.assertIsNotNone(self._grabar([2000] * 5 + [10] * 15))  # «sí» ≈ 0,5 s

    def test_long_speech_is_ignored(self) -> None:
        self.assertIsNone(self._grabar([2000] * 60))  # una conversación de fondo

    def test_click_or_noise_is_ignored(self) -> None:
        detector = ww.DetectorPalabraActivacion(queue.Queue(), lambda: True)
        self.assertIsNone(detector._grabar_respuesta(_Microfono([10] * 15), 50, []))

    def test_voice_threshold(self) -> None:
        self.assertEqual(ww.umbral_voz(50), 300)
        self.assertEqual(ww.umbral_voz(200), 600)


def _host(**estado) -> Mock:
    host = Mock()
    host._pending_plan_reply = object()
    host._pending_approval_kind = "power"
    host._pending_power_action = "shutdown"
    host.recorder = None
    host._free_approval_busy = False
    host.events = queue.Queue()
    for clave, valor in estado.items():
        setattr(host, clave, valor)
    host._approval_decision = lambda kind, texto: JarvisApp._approval_decision(host, kind, texto)
    return host


class DecisionTests(unittest.TestCase):
    def test_answers_with_or_without_wake_word(self) -> None:
        host = _host()
        for texto, esperado in (("Sí.", True), ("Hey Jarvis, sí", True), ("no", False),
                                ("Oye Jarvis, cancela", False), ("qué rico está esto", None)):
            with self.subTest(texto=texto):
                self.assertEqual(JarvisApp._approval_decision(host, "power", texto), esperado)

    def test_plan_accepts_its_phrase_or_a_plain_yes_no(self) -> None:
        host = _host()
        for kind in ("plan", "routine_save", "vision"):
            for texto, esperado in (("ejecutar plan" if kind == "plan" else "sí", True), ("Sí, dale", True),
                                    ("Hey Jarvis, sí", True), ("no", False), ("cancela", False),
                                    ("qué rico está esto", None)):
                with self.subTest(kind=kind, texto=texto):
                    self.assertEqual(JarvisApp._approval_decision(host, kind, texto), esperado)
        self.assertFalse(JarvisApp._approval_decision(host, "plan", "no ejecutar plan"))


class FreeListeningTests(unittest.TestCase):
    def test_only_with_an_open_confirmation(self) -> None:
        self.assertTrue(JarvisApp._free_approval_listening(_host()))
        for estado in ({"_pending_plan_reply": None}, {"recorder": object()}, {"_free_approval_busy": True}):
            with self.subTest(estado=list(estado)):
                self.assertFalse(JarvisApp._free_approval_listening(_host(**estado)))

    @patch("jarvis_app.limpiar_audio")
    @patch("jarvis_app.threading.Thread")
    def test_utterance_is_ignored_while_another_is_being_read(self, thread: Mock, limpiar: Mock) -> None:
        JarvisApp._on_approval_utterance(_host(_free_approval_busy=True), "r.wav")
        thread.assert_not_called()
        limpiar.assert_called_once_with("r.wav")

    @patch("jarvis_app.limpiar_audio")
    @patch("jarvis_app.core.procesar_stt", return_value="Sí")
    def test_worker_sends_decision_for_that_confirmation(self, _stt: Mock, _limpiar: Mock) -> None:
        host = _host()
        reply = host._pending_plan_reply
        JarvisApp._free_approval_worker(host, "r.wav", "power", reply)
        self.assertEqual(host.events.get_nowait(), ("free_approval_result", True, "Sí", reply))


if __name__ == "__main__":
    unittest.main()
