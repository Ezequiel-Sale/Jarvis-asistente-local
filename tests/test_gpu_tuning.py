"""Undervolt de la GPU: perfil de Adrenalin, comparación y vigilante (sin tocar el driver)."""

import queue
import tempfile
import unittest
from pathlib import Path

import jarvis_gpu_tuning as g
from jarvis_commands import detectar_comando_gpu

XML = """<?xml version="1.0" encoding="UTF-8"?>
<SYSTEM>
  <GPU DevID="7550" RevID="C0">
    <FEATURE ID="3" Enabled="True"><STATES>
      <STATE ID="0" Enabled="True" Value="-10"/><STATE ID="1" Enabled="True" Value="0"/></STATES></FEATURE>
    <FEATURE ID="12" Enabled="False"><STATES><STATE ID="0" Enabled="False" Value="-80"/></STATES></FEATURE>
    <FEATURE ID="5" Enabled="True"><STATES>
      <STATE ID="0" Enabled="False" Value="2718"/><STATE ID="1" Enabled="False" Value="0"/></STATES></FEATURE>
    <FEATURE ID="22" Enabled="True"><STATES>
      <STATE ID="0" Value="30"/><STATE ID="1" Value="37"/><STATE ID="2" Value="37"/><STATE ID="3" Value="45"/>
      <STATE ID="4" Value="43"/><STATE ID="5" Value="52"/><STATE ID="6" Value="49"/><STATE ID="7" Value="61"/>
      <STATE ID="8" Value="59"/><STATE ID="9" Value="74"/><STATE ID="10" Value="0"/></STATES></FEATURE>
  </GPU>
</SYSTEM>"""
CURVA = [30, 37, 37, 45, 43, 52, 49, 61, 59, 74]


def _estado(voltaje=-80, vram=2718, energia=-10, curva=CURVA, cero_rpm=0):
    """Estado como lo informa el driver (límites reales de la RX 9070 XT)."""
    estado = {i: g.Ajuste(0, 0, 0, 0) for i in range(g.OD8_COUNT)}
    estado[g.OD8_OD_VOLTAGE] = g.Ajuste(-200, 0, 0, voltaje)
    estado[g.OD8_UCLK_FMAX] = g.Ajuste(2518, 3000, 2518, vram)
    estado[g.OD8_POWER_PERCENTAGE] = g.Ajuste(-30, 10, 0, energia)
    estado[g.OD8_FAN_ZERORPM] = g.Ajuste(0, 1, 1, cero_rpm)
    for n, valor in enumerate(curva):
        estado[g.OD8_FAN_CURVE_INICIO + n] = g.Ajuste(25, 100, 50, valor)
    return estado


FABRICA = dict(voltaje=0, vram=2518, energia=0, curva=[30, 30, 50, 44, 59, 58, 68, 72, 76, 100], cero_rpm=1)


class ProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        carpeta = tempfile.TemporaryDirectory()
        self.addCleanup(carpeta.cleanup)
        self.ruta = Path(carpeta.name) / "undervolt.xml"
        self.ruta.write_text(XML, encoding="utf-8")

    def test_xml_maps_to_driver_settings(self) -> None:
        perfil, dev = g.perfil_desde_xml(self.ruta)
        self.assertEqual(dev, "7550")
        self.assertEqual(perfil[g.OD8_OD_VOLTAGE], -80)
        self.assertEqual(perfil[g.OD8_UCLK_FMAX], 2718)
        self.assertEqual(perfil[g.OD8_POWER_PERCENTAGE], -10)
        self.assertEqual([perfil[g.OD8_FAN_CURVE_INICIO + i] for i in range(10)], CURVA)
        self.assertEqual(len(perfil), 13)
        self.assertEqual(g.describir(perfil), "voltaje -80 mV, VRAM 2718 MHz, energía -10 %, curva de ventiladores")

    def test_bad_files_are_rejected(self) -> None:
        with self.assertRaises(g.ErrorGPU):
            g.perfil_desde_xml(self.ruta.with_name("no_existe.xml"))
        self.ruta.write_text("<SYSTEM><CPU/></SYSTEM>", encoding="utf-8")
        with self.assertRaises(g.ErrorGPU):
            g.perfil_desde_xml(self.ruta)
        self.ruta.write_text("esto no es xml", encoding="utf-8")
        with self.assertRaises(g.ErrorGPU):
            g.perfil_desde_xml(self.ruta)

    def test_differences_and_range_validation(self) -> None:
        perfil, _ = g.perfil_desde_xml(self.ruta)
        self.assertEqual(g.diferencias(perfil, _estado()), {})
        perdidas = g.diferencias(perfil, _estado(**FABRICA))
        self.assertEqual(perdidas[g.OD8_OD_VOLTAGE], (0, -80))
        g.validar(perfil, _estado(**FABRICA))
        with self.assertRaises(g.ErrorGPU):
            g.validar({g.OD8_OD_VOLTAGE: -250}, _estado())  # fuera del límite del driver
        with self.assertRaises(g.ErrorGPU):
            g.validar({70: 1}, {})  # ajuste que la GPU no informa

    def test_fmin_only_sent_when_the_gpu_has_it(self) -> None:
        perfil = {g.OD8_UCLK_FMAX: 2718}
        self.assertNotIn(g.OD8_UCLK_FMIN, g.pedido_completo(perfil, _estado()))
        estado = _estado()
        estado[g.OD8_UCLK_FMIN] = g.Ajuste(500, 2000, 500, 700)
        self.assertEqual(g.pedido_completo(perfil, estado)[g.OD8_UCLK_FMIN], 700)

    def test_struct_sizes_match_the_sdk(self) -> None:
        import ctypes

        self.assertEqual(g.OD8_COUNT, 77)
        self.assertEqual(ctypes.sizeof(g._Pedidos), 4 + 77 * 12)
        self.assertEqual(ctypes.sizeof(g._Actuales), 4 + 77 * 4)


class WatcherTests(unittest.TestCase):
    def setUp(self) -> None:
        carpeta = tempfile.TemporaryDirectory()
        self.addCleanup(carpeta.cleanup)
        ruta = Path(carpeta.name) / "undervolt.xml"
        ruta.write_text(XML, encoding="utf-8")
        self.prefs = {g.PREFERENCIA_RUTA: str(ruta)}
        self.eventos: queue.Queue = queue.Queue()
        self.estado = _estado()
        self.escrituras: list[dict] = []
        self.reloj = 0.0
        self.activo = True

        def escribir(perfil, dev):
            self.escrituras.append(dict(perfil))
            self.estado = _estado()  # el driver acepta el perfil
            return {}

        self.vigilante = g.VigilanteGPU(
            self.eventos, lambda: self.activo, self.prefs.get, self.prefs.__setitem__,
            leer=lambda: (self.estado, "7550", "AMD Radeon RX 9070 XT"), escribir=escribir,
            reloj=lambda: self.reloj,
        )

    def test_applied_profile_is_left_alone_and_zero_rpm_is_remembered(self) -> None:
        self.vigilante.revisar_una_vez()
        self.assertEqual(self.vigilante.ultimo_estado, "aplicado")
        self.assertEqual(self.escrituras, [])
        self.assertEqual(self.prefs[g.PREFERENCIA_EXTRAS], '{"15": 0}')

    def test_lost_profile_is_reapplied_after_two_readings(self) -> None:
        self.vigilante.revisar_una_vez()  # aplicado: recuerda cero RPM = 0
        self.estado = _estado(**FABRICA)  # pantallazo: el driver volvió a fábrica
        self.vigilante.revisar_una_vez()
        self.assertEqual(self.escrituras, [])  # primera lectura: solo sospecha
        self.vigilante.revisar_una_vez()
        self.assertEqual(len(self.escrituras), 1)
        self.assertEqual(self.escrituras[0][g.OD8_OD_VOLTAGE], -80)
        self.assertEqual(self.escrituras[0][g.OD8_FAN_ZERORPM], 0)  # el extra recordado
        self.assertEqual(self.eventos.get_nowait()[0], "gpu_profile_applied")
        self.assertEqual(self.vigilante.ultimo_estado, "aplicado")

    def test_stops_after_three_losses_in_an_hour(self) -> None:
        for _ in range(g.MAX_REAPLICACIONES):
            self.estado = _estado(**FABRICA)
            self.vigilante.revisar_una_vez()
            self.vigilante.revisar_una_vez()
            self.reloj += 300
        self.assertEqual(len(self.escrituras), 3)
        self.estado = _estado(**FABRICA)  # cuarta pérdida
        self.vigilante.revisar_una_vez()
        self.vigilante.revisar_una_vez()
        self.vigilante.revisar_una_vez()
        self.assertEqual(len(self.escrituras), 3)  # ya no reaplica
        self.assertTrue(self.vigilante.bloqueado)
        tipos = [self.eventos.get_nowait()[0] for _ in range(self.eventos.qsize())]
        self.assertEqual(tipos.count("gpu_profile_warning"), 1)
        self.vigilante.desbloquear()  # «aplica el undervolt» o reactivar en Ajustes
        self.vigilante.revisar_una_vez()
        self.vigilante.revisar_una_vez()
        self.assertEqual(len(self.escrituras), 4)

    def test_old_losses_do_not_count(self) -> None:
        for _ in range(5):
            self.estado = _estado(**FABRICA)
            self.vigilante.revisar_una_vez()
            self.vigilante.revisar_una_vez()
            self.reloj += g.VENTANA_REAPLICACIONES / 2 + 1  # dos por hora como mucho
        self.assertEqual(len(self.escrituras), 5)
        self.assertFalse(self.vigilante.bloqueado)

    def test_disabled_or_unreadable_does_nothing(self) -> None:
        self.activo = False
        self.estado = _estado(**FABRICA)
        self.vigilante.revisar_una_vez()
        self.vigilante.revisar_una_vez()
        self.assertEqual(self.escrituras, [])
        self.activo = True
        self.prefs[g.PREFERENCIA_RUTA] = "Z:/no/existe.xml"
        self.vigilante.revisar_una_vez()
        self.vigilante.revisar_una_vez()
        self.assertEqual(self.escrituras, [])
        self.assertIn("No pude leer el perfil", self.vigilante.ultimo_estado)

    def test_driver_rejection_warns_and_stops(self) -> None:
        def falla(perfil, dev):
            raise g.ErrorGPU("El driver de AMD rechazó el ajuste (código -1).")

        self.vigilante._escribir = falla
        self.estado = _estado(**FABRICA)
        self.vigilante.revisar_una_vez()
        self.vigilante.revisar_una_vez()
        self.assertEqual(self.eventos.get_nowait()[0], "gpu_profile_warning")
        self.assertTrue(self.vigilante.bloqueado)


class PhraseTests(unittest.TestCase):
    def test_phrases(self) -> None:
        self.assertEqual(detectar_comando_gpu("Aplica el undervolt."), ("apply_gpu_profile", {}))
        self.assertEqual(detectar_comando_gpu("reaplica el undervolt de la gpu"), ("apply_gpu_profile", {}))
        self.assertEqual(detectar_comando_gpu("¿Está aplicado el undervolt?"), ("get_gpu_profile_status", {}))
        self.assertEqual(detectar_comando_gpu("revisa el undervolt"), ("get_gpu_profile_status", {}))
        self.assertIsNone(detectar_comando_gpu("aplica el tema rojo"))


if __name__ == "__main__":
    unittest.main()
