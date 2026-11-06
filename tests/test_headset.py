import builtins
import unittest
from unittest.mock import patch

import jarvis_headset as headset
from jarvis_events import auriculares_en_uso


def _rellenar(*datos: int) -> list[int]:
    return list(datos) + [0] * (20 - len(datos))


class HidppParsingTests(unittest.TestCase):
    def test_query_packet(self) -> None:
        paquete = headset.construir_consulta_bateria()
        self.assertEqual(len(paquete), 20)
        self.assertEqual(paquete[:4], [0x11, 0xFF, 0x07, 0x01])
        self.assertTrue(all(b == 0 for b in paquete[4:]))

    def test_headset_on(self) -> None:
        # Respuesta real del receptor con el auricular encendido (4099 mV).
        estado = headset.interpretar_respuesta(_rellenar(0x11, 0xFF, 0x07, 0x01, 0x10, 0x03, 0x07))
        self.assertEqual(estado, headset.EstadoAuricular(True, 4099, False))

    def test_headset_charging(self) -> None:
        estado = headset.interpretar_respuesta(_rellenar(0x11, 0xFF, 0x07, 0x01, 0x0F, 0xA0, 0x03))
        self.assertTrue(estado.encendido)
        self.assertTrue(estado.cargando)

    def test_hidpp_error_means_off(self) -> None:
        estado = headset.interpretar_respuesta(_rellenar(0x11, 0xFF, 0xFF, 0x07, 0x01, 0x08))
        self.assertEqual(estado, headset.EstadoAuricular(False))

    def test_zero_voltage_means_off(self) -> None:
        estado = headset.interpretar_respuesta(_rellenar(0x11, 0xFF, 0x07, 0x01, 0x00, 0x00, 0x00))
        self.assertFalse(estado.encendido)

    def test_unrelated_reports_are_skipped(self) -> None:
        for informe in (
            _rellenar(0x10, 0xFF, 0x07, 0x01),  # informe corto
            _rellenar(0x11, 0x01, 0x07, 0x01),  # otro dispositivo
            _rellenar(0x11, 0xFF, 0x04, 0x0E),  # otra función
            _rellenar(0x11, 0xFF, 0xFF, 0x04, 0x0E, 0x02),  # error de otra función
            [],
        ):
            self.assertIsNone(headset.interpretar_respuesta(informe))

    def test_without_hidapi_returns_none(self) -> None:
        importar = builtins.__import__

        def sin_hid(name, *args, **kwargs):
            if name == "hid":
                raise ImportError(name)
            return importar(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=sin_hid):
            self.assertIsNone(headset.consultar_g533())


class HeadphonesInUseTests(unittest.TestCase):
    def test_receiver_unplugged(self) -> None:
        self.assertFalse(auriculares_en_uso(lambda: False, lambda: True))

    def test_receiver_plugged_headset_on(self) -> None:
        self.assertTrue(auriculares_en_uso(lambda: True, lambda: True))

    def test_receiver_plugged_headset_off(self) -> None:
        self.assertFalse(auriculares_en_uso(lambda: True, lambda: False))

    def test_unknown_link_falls_back_to_audio_output(self) -> None:
        self.assertTrue(auriculares_en_uso(lambda: True, lambda: None))

    def test_link_error_falls_back_to_audio_output(self) -> None:
        def falla() -> bool:
            raise OSError("HID ocupado")

        self.assertTrue(auriculares_en_uso(lambda: True, falla))


if __name__ == "__main__":
    unittest.main()
