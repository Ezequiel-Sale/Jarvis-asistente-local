"""Batería del G533. No abre el receptor USB."""

import queue
import unittest

import jarvis_headset as headset
from jarvis_commands import detectar_consulta_bateria
from jarvis_events import HeadsetBatteryWatcher


def _estado(mv: int | None, *, encendido: bool = True, cargando: bool = False):
    return headset.EstadoAuricular(encendido, mv, cargando)


class PercentageTests(unittest.TestCase):
    def test_curve(self) -> None:
        self.assertEqual(headset.porcentaje_bateria(4300), 100)
        self.assertEqual(headset.porcentaje_bateria(4186), 100)
        self.assertEqual(headset.porcentaje_bateria(4099), 95)
        self.assertEqual(headset.porcentaje_bateria(3765), 50)
        self.assertEqual(headset.porcentaje_bateria(3680), 20)
        self.assertEqual(headset.porcentaje_bateria(3400), 0)

    def test_always_multiple_of_five_and_monotonic(self) -> None:
        anteriores = [headset.porcentaje_bateria(v) for v in range(3450, 4250, 7)]
        self.assertTrue(all(p % 5 == 0 for p in anteriores))
        self.assertEqual(anteriores, sorted(anteriores))

    def test_descriptions(self) -> None:
        self.assertIn("95 %", headset.describir_bateria(_estado(4099)))
        self.assertIn("se están cargando", headset.describir_bateria(_estado(3900, cargando=True)))
        self.assertIn("apagados", headset.describir_bateria(_estado(None, encendido=False)))
        self.assertIn("No encuentro", headset.describir_bateria(None))


class PhraseTests(unittest.TestCase):
    def test_phrases(self) -> None:
        for texto in (
            "¿Cuánta batería tienen los auriculares?",
            "batería de los auriculares",
            "cómo está la batería del headset",
            "qué batería les queda a los auriculares",
        ):
            with self.subTest(texto=texto):
                self.assertEqual(detectar_consulta_bateria(texto), ("get_headset_battery", {}))
        self.assertIsNone(detectar_consulta_bateria("cuánta batería le queda al portátil"))


class LowBatteryWatcherTests(unittest.TestCase):
    def _watcher(self, lecturas: list):
        events: queue.Queue[tuple] = queue.Queue()
        datos = iter(lecturas)
        watcher = HeadsetBatteryWatcher(events, leer=lambda: next(datos))
        for _ in lecturas:
            watcher.comprobar()
        avisos = []
        while not events.empty():
            avisos.append(events.get_nowait())
        return avisos

    def test_warns_once_when_low(self) -> None:
        avisos = self._watcher([_estado(3900), _estado(3660), _estado(3650), _estado(3640)])
        self.assertEqual(avisos, [("headset_battery_low", 15)])

    def test_rearms_after_charging(self) -> None:
        avisos = self._watcher(
            [_estado(3660), _estado(3700, cargando=True), _estado(3660)]
        )
        self.assertEqual(len(avisos), 2)

    def test_ignores_off_or_charging(self) -> None:
        avisos = self._watcher([None, _estado(None, encendido=False), _estado(3600, cargando=True)])
        self.assertEqual(avisos, [])


if __name__ == "__main__":
    unittest.main()
