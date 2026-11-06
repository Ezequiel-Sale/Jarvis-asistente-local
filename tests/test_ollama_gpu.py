"""Comprobación de GPU de Ollama. Nunca reinicia Ollama de verdad."""

import unittest
from unittest.mock import Mock

import jarvis_ollama_gpu as gpu

ARRANQUE = 'time=1 level=INFO source=routes.go msg="Listening on 127.0.0.1:11434 (version 0.40.15)"\n'
CPU = 'time=2 level=INFO source=types.go msg="inference compute" id=cpu library=cpu compute=""\n'
ROCM = (
    'time=2 level=INFO source=types.go msg="inference compute" id=0 filter_id=0 '
    'library=ROCm compute=gfx1201 name=ROCm0 description="AMD Radeon RX 9070 XT"\n'
)


class LogParsingTests(unittest.TestCase):
    def test_gpu_detected(self) -> None:
        self.assertEqual(gpu.dispositivos_detectados(ARRANQUE + ROCM), ["ROCm"])
        self.assertFalse(gpu.usa_solo_cpu(ARRANQUE + ROCM))

    def test_cpu_only(self) -> None:
        self.assertTrue(gpu.usa_solo_cpu(ARRANQUE + CPU))

    def test_only_the_last_start_counts(self) -> None:
        self.assertTrue(gpu.usa_solo_cpu(ARRANQUE + ROCM + ARRANQUE + CPU))
        self.assertFalse(gpu.usa_solo_cpu(ARRANQUE + CPU + ARRANQUE + ROCM))

    def test_incomplete_start_is_unknown(self) -> None:
        self.assertIsNone(gpu.usa_solo_cpu(""))
        self.assertIsNone(gpu.usa_solo_cpu(ARRANQUE))  # aún detectando dispositivos
        self.assertIsNone(gpu.usa_solo_cpu(CPU))  # sin línea de arranque


class EnsureGpuTests(unittest.TestCase):
    def _asegurar(self, registros: list[str], *, reinicio_ok: bool = True, **extra):
        lecturas = iter(registros)
        reiniciar = Mock(return_value=reinicio_ok)
        aviso = gpu.asegurar_gpu(
            leer=lambda: next(lecturas),
            reiniciar=reiniciar,
            responde=extra.pop("responde", lambda: True),
            dormir=lambda _s: None,
            instalada=extra.pop("instalada", lambda: True),
            **extra,
        )
        return aviso, reiniciar

    def test_nothing_to_do_when_gpu_is_used(self) -> None:
        aviso, reiniciar = self._asegurar([ARRANQUE + ROCM])
        self.assertIsNone(aviso)
        reiniciar.assert_not_called()

    def test_restarts_once_when_cpu_only(self) -> None:
        aviso, reiniciar = self._asegurar([ARRANQUE + CPU, ARRANQUE + ROCM])
        reiniciar.assert_called_once()
        self.assertIn("ya la usa", aviso)

    def test_gives_up_after_the_attempts(self) -> None:
        aviso, reiniciar = self._asegurar([ARRANQUE + CPU] * 4, intentos=3)
        self.assertEqual(reiniciar.call_count, 3)
        self.assertIn("no detecta la tarjeta gráfica", aviso)

    def test_reports_when_ollama_does_not_come_back(self) -> None:
        aviso, reiniciar = self._asegurar([ARRANQUE + CPU], reinicio_ok=False)
        reiniciar.assert_called_once()
        self.assertIn("no volvió a responder", aviso)

    def test_does_nothing_if_not_installed_or_not_running(self) -> None:
        for extra in ({"instalada": lambda: False}, {"responde": lambda: False}):
            with self.subTest(extra=list(extra)):
                aviso, reiniciar = self._asegurar([ARRANQUE + CPU], **extra)
                self.assertIsNone(aviso)
                reiniciar.assert_not_called()

    def test_unknown_state_does_not_restart(self) -> None:
        aviso, reiniciar = self._asegurar([ARRANQUE])
        self.assertIsNone(aviso)
        reiniciar.assert_not_called()


if __name__ == "__main__":
    unittest.main()
