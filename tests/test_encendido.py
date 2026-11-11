"""Encender la PC desde el celular: datos de la red de casa y diagnóstico (sin red ni powercfg reales)."""

import json
import queue
import socket
import unittest
from types import SimpleNamespace

import psutil

import jarvis_encendido as e
import jarvis_mobile as m

TS = "100.120.145.38"


def _dir(familia, direccion, mascara=None):
    return SimpleNamespace(family=familia, address=direccion, netmask=mascara)


class NetworkTests(unittest.TestCase):
    def test_home_network_skips_virtual_adapters(self) -> None:
        direcciones = {
            "Wi-Fi": [_dir(psutil.AF_LINK, "50-EE-32-8C-88-0B"), _dir(socket.AF_INET, "192.168.1.104", "255.255.255.0")],
            "vEthernet (WSL)": [_dir(psutil.AF_LINK, "00-15-5D-DF-32-80"), _dir(socket.AF_INET, "172.20.0.1", "255.255.240.0")],
            "Tailscale": [_dir(socket.AF_INET, "100.104.121.13", "255.192.0.0")],
            "Conexión de área local* 1": [_dir(psutil.AF_LINK, "52-EE-32-8C-88-0B")],
        }
        datos = e.placas(direcciones, ip="192.168.1.104")
        self.assertEqual(datos, {"ip": "192.168.1.104", "broadcast": "192.168.1.255", "adapter": "Wi-Fi",
                                 "wifi": True, "macs": ["50:EE:32:8C:88:0B"]})

    def test_wired_network(self) -> None:
        direcciones = {"Ethernet": [_dir(psutil.AF_LINK, "10-FF-E0-C4-DC-5F"), _dir(socket.AF_INET, "10.0.0.7", "255.255.0.0")]}
        datos = e.placas(direcciones, ip="10.0.0.7")
        self.assertEqual((datos["broadcast"], datos["wifi"]), ("10.0.255.255", False))


class TipsTests(unittest.TestCase):
    def test_wifi_not_armed(self) -> None:
        consejos = e.consejos({"macs": ["50:EE:32:8C:88:0B"], "broadcast": "192.168.1.255", "wifi": True,
                               "armed": False, "device": "Realtek 8852CE", "fastStartup": True})
        self.assertIn('powercfg /deviceenablewake "Realtek 8852CE"', consejos[0])
        self.assertIn("suspender", consejos[1])
        self.assertEqual(len(consejos), 2)  # el inicio rápido no importa por Wi-Fi

    def test_wired_ready_but_fast_startup(self) -> None:
        consejos = e.consejos({"macs": ["10:FF:E0:C4:DC:5F"], "broadcast": "10.0.255.255", "wifi": False,
                               "armed": True, "fastStartup": True})
        self.assertTrue(any("BIOS" in c for c in consejos))
        self.assertTrue(any("inicio rápido" in c for c in consejos))
        self.assertFalse(any("powercfg" in c for c in consejos))

    def test_no_home_network(self) -> None:
        self.assertIn("No encontré la placa", e.consejos({"macs": [], "broadcast": None})[0])


class ServerTests(unittest.TestCase):
    def test_wol_endpoint(self) -> None:
        guardado: dict[str, str] = {}
        servidor = m.ServidorMovil(queue.Queue(), m.Vinculacion(guardado.get, guardado.__setitem__),
                                   encendido=lambda: {"macs": ["50:EE:32:8C:88:0B"], "broadcast": "192.168.1.255"})
        codigo = servidor.vinculacion.nuevo_codigo()
        token = json.loads(servidor.manejar("POST", "/api/pair", {}, json.dumps({"code": codigo}).encode(), TS)[2])["token"]
        estado, _tipo, cuerpo = servidor.manejar("GET", "/api/wol", {"Authorization": f"Bearer {token}"}, b"", TS)[:3]
        self.assertEqual((estado, json.loads(cuerpo)["broadcast"]), (200, "192.168.1.255"))
        self.assertEqual(servidor.manejar("GET", "/api/wol", {}, b"", TS)[0], 401)


if __name__ == "__main__":
    unittest.main()
