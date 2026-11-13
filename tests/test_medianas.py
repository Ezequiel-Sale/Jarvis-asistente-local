"""Modo juego, «deshaz lo último», avisos anticipados y búsqueda de archivos.

Todo simulado: no se cierra ningún programa, no se tocan el volumen, los monitores ni los
archivos reales, y la base de datos no se usa."""

import os
import queue
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import jarvis_buscar_archivos as ba
import jarvis_deshacer as dh
import jarvis_hardware as hw
import jarvis_modo_juego as mj
import jarvis_servicios as sv
from jarvis_app import JarvisApp
from jarvis_tool_registry import ToolRegistry


# --- «Deshaz lo último» -------------------------------------------------------------

def _lectores(**extra):
    base = {"volumen": lambda: 40, "volumen_app": lambda app: 25, "salida": lambda: "speakers",
            "tema": lambda: "cian", "monitores": lambda: "config-previa", "restaurar_monitores": Mock()}
    base.update(extra)
    return base


class UndoLogicTests(unittest.TestCase):
    def test_inverse_actions(self) -> None:
        casos = {
            ("set_system_volume", (("percentage", 80),)): ("set_system_volume", {"percentage": 40}),
            ("set_app_volume", (("app", "spotify"), ("percentage", 70))):
                ("set_app_volume", {"app": "spotify", "percentage": 25}),
            ("set_app_volume", (("app", "discord"), ("mute", True))): ("set_app_volume", {"app": "discord", "mute": False}),
            ("set_output_device", (("device", "headphones"),)): ("set_output_device", {"device": "speakers"}),
            ("mute_browser_tab", (("mute", True), ("monitor", 3))): ("mute_browser_tab", {"mute": False, "monitor": 3}),
            ("media_control", (("action", "pause"), ("monitor", 1))): ("media_control", {"action": "play", "monitor": 1}),
            ("video_fullscreen", (("enabled", True),)): ("video_fullscreen", {"enabled": False}),
            ("video_theater", (("enabled", False),)): ("video_theater", {"enabled": True}),
            ("set_theme", (("theme", "mark"),)): ("set_theme", {"theme": "cian"}),
            ("window_control", (("app", "discord"), ("action", "window_minimize"))):
                ("window_control", {"app": "discord", "action": "window_restore"}),
        }
        for (herramienta, args), esperado in casos.items():
            with self.subTest(herramienta=herramienta, args=args):
                d = dh.inverso(herramienta, dict(args), _lectores())
                self.assertEqual((d.herramienta, d.argumentos), esperado)
                self.assertTrue(d.descripcion)

    def test_nothing_to_undo(self) -> None:
        self.assertIsNone(dh.inverso("set_output_device", {"device": "speakers"}, _lectores()))  # ya estaba así
        self.assertIsNone(dh.inverso("media_control", {"action": "next"}, _lectores()))
        self.assertIsNone(dh.inverso("set_theme", {"theme": "cian"}, _lectores()))
        self.assertIsNone(dh.inverso("window_control", {"app": "todo", "action": "window_minimize"}, _lectores()))
        self.assertIsNone(dh.inverso("launch_game", {"name": "x"}, _lectores()))

    def test_monitors_restore_the_exact_previous_config(self) -> None:
        restaurar = Mock()
        d = dh.inverso("configure_display", {"action": "disable", "monitor": 2},
                       _lectores(restaurar_monitores=restaurar))
        d.accion()
        restaurar.assert_called_once_with("config-previa")

    def test_history_order_expiry_and_own_actions(self) -> None:
        reloj = [0.0]
        h = dh.Historial(_lectores(), reloj=lambda: reloj[0])
        h.anotar(h.capturar("set_system_volume", {"percentage": 80}, "directo"))
        reloj[0] = 10
        h.anotar(h.capturar("set_theme", {"theme": "mark"}, "agente"))
        self.assertIsNone(h.capturar("set_system_volume", {"percentage": 10}, "deshacer"))  # no se encadena
        self.assertEqual(h.sacar().herramienta, "set_theme")
        reloj[0] = dh.CADUCA + 5  # el volumen ya es viejo
        self.assertIsNone(h.sacar())

    def test_a_failing_reader_never_blocks_the_action(self) -> None:
        h = dh.Historial(_lectores(volumen=Mock(side_effect=OSError("COM"))))
        self.assertIsNone(h.capturar("set_system_volume", {"percentage": 80}, "directo"))

    def test_phrases(self) -> None:
        for texto in ("Deshaz lo último", "deshazlo", "Jarvis, deshacer", "revierte eso", "déjalo como estaba",
                      "vuelve a como estaba", "control z", "deshaz el último cambio por favor"):
            with self.subTest(texto=texto):
                self.assertEqual(dh.detectar(texto), ("undo_last_action", {}))
        for texto in ("revierte", "deshaz la maleta", "vuelve atrás"):
            with self.subTest(texto=texto):
                self.assertIsNone(dh.detectar(texto))


class UndoRegistryTests(unittest.TestCase):
    def _registro(self):
        registro = ToolRegistry(recorder=lambda r: None)
        volumen = {"v": 40}
        registro.register("set_system_volume", "V.", {"type": "object", "properties": {"percentage": {"type": "integer"}},
                                                       "required": ["percentage"], "additionalProperties": False},
                          lambda percentage: volumen.__setitem__("v", percentage) or "ok")
        historial = dh.Historial(_lectores(volumen=lambda: volumen["v"]))
        registro.antes_de_ejecutar = historial.capturar
        registro.tras_exito = historial.anotar
        return registro, historial, volumen

    def test_registry_records_and_undo_goes_back(self) -> None:
        registro, historial, volumen = self._registro()
        registro.execute("set_system_volume", {"percentage": 90}, origin="directo")
        host = SimpleNamespace(deshacer=historial, tool_registry=registro)
        texto = JarvisApp._tool_undo_last_action(host)
        self.assertEqual(volumen["v"], 40)
        self.assertIn("volver el volumen del equipo al 40 %", texto)
        # El deshacer no se anota a sí mismo: no hay nada más que deshacer.
        self.assertIn("No tengo nada reciente", JarvisApp._tool_undo_last_action(host))

    def test_failed_action_is_not_recorded(self) -> None:
        registro, historial, _ = self._registro()
        registro.register("set_theme", "T.", {"type": "object", "properties": {"theme": {"type": "string"}},
                                              "required": ["theme"], "additionalProperties": False},
                          Mock(side_effect=RuntimeError("falló")))
        with self.assertRaises(RuntimeError):
            registro.execute("set_theme", {"theme": "mark"})
        self.assertIsNone(historial.sacar())


# --- Modo juego ---------------------------------------------------------------------

class _Proceso:
    def __init__(self, pid, nombre, exe, vivo=True):
        self.info = {"pid": pid, "name": nombre, "exe": exe}
        self.vivo = vivo
        self.terminado = False

    def is_running(self):
        return self.vivo

    def terminate(self):
        self.terminado = True


class GameModeTests(unittest.TestCase):
    def test_names_and_protected_programs(self) -> None:
        self.assertEqual(mj.nombres_de("Discord, brave.exe ; Spotify, explorer, jarvis, Discord"),
                         ["discord", "brave", "spotify"])
        self.assertTrue(mj.coincide("Discord.exe", "discord"))
        self.assertTrue(mj.coincide("brave.exe", "brave"))
        self.assertFalse(mj.coincide("steam.exe", "steam"))
        self.assertFalse(mj.coincide("braveupdate.exe", "bra"))

    def test_close_politely_then_force_and_reopen(self) -> None:
        discord = _Proceso(1, "Discord.exe", r"C:\Discord\Discord.exe", vivo=True)   # se esconde en la bandeja
        brave = [_Proceso(2, "brave.exe", r"C:\Brave\brave.exe", vivo=False), _Proceso(3, "brave.exe", r"C:\Brave\brave.exe", vivo=False)]
        otro = _Proceso(4, "notepad.exe", r"C:\notepad.exe")
        ventanas = []
        cerrados = mj.cerrar(["discord", "brave"], procesos=lambda: [discord, *brave, otro],
                             cerrar_ventanas=ventanas.append, dormir=lambda s: None)
        self.assertEqual(sorted(c.nombre for c in cerrados), ["brave", "discord"])
        self.assertEqual(sorted(ventanas), [1, 2, 3])
        self.assertTrue(discord.terminado)
        self.assertFalse(any(p.terminado for p in brave))
        self.assertFalse(otro.terminado)
        lanzados = []
        self.assertEqual(mj.reabrir(cerrados, lanzar=lanzados.append), [])
        self.assertEqual(sorted(lanzados), [r"C:\Brave\brave.exe", r"C:\Discord\Discord.exe"])
        self.assertEqual(mj.describir(cerrados[:1]), cerrados[0].nombre.capitalize())

    def test_quiet_announcements_are_kept_for_later(self) -> None:
        host = Mock(_game_quiet=True, _quiet_backlog=[])
        JarvisApp._announce(host, "El disco D está al 95 %.")
        host._quiet_message.assert_called_once()
        host._add_message.assert_not_called()
        self.assertEqual(host._quiet_backlog, ["El disco D está al 95 %."])
        # Las alarmas suenan igual.
        host.audio_enabled = False
        JarvisApp._announce(host, "Alarma: sacar la ropa.", alarm=True)
        host._add_message.assert_called_once()

    @patch("threading.Thread")
    def test_game_stop_summarizes_and_reopens(self, hilo) -> None:
        host = Mock(_quiet_backlog=["Aviso 1.", "Aviso 2."], _closed_for_game=[mj.Cerrado("discord", "d.exe")])
        JarvisApp._on_game_stopped(host)
        host._restore_gpu_after_game.assert_called_once()
        self.assertFalse(host._game_quiet)
        host._announce.assert_called_once_with("Mientras jugabas: Aviso 1. Aviso 2.")
        self.assertIs(hilo.call_args.kwargs["target"], mj.reabrir)


# --- Avisos anticipados ---------------------------------------------------------

class EarlyWarningTests(unittest.TestCase):
    def _historial(self, gpu, ram, segundos=130, paso=3):
        return [(t, hw.Temperaturas(cpu=60, gpu=gpu), ram) for t in range(0, segundos, paso)]

    def test_sustained_heat_and_memory(self) -> None:
        avisos = hw.alertas_sostenidas(self._historial(gpu=87, ram=95), ahora=129)
        self.assertEqual(len(avisos), 2)
        self.assertIn("la GPU lleva 2 minutos por encima de 85 °C", avisos[0])
        self.assertIn("la memoria lleva 2 minutos al 95 %", avisos[1])

    def test_a_peak_is_not_sustained(self) -> None:
        historial = self._historial(gpu=87, ram=50)
        historial[10] = (historial[10][0], hw.Temperaturas(cpu=60, gpu=70), 50)
        self.assertEqual(hw.alertas_sostenidas(historial, ahora=129), [])
        self.assertEqual(hw.alertas_sostenidas(self._historial(gpu=87, ram=50, segundos=60), ahora=59), [])

    def test_disk_alert_carries_its_level(self) -> None:
        eventos = queue.Queue()
        disco = SimpleNamespace(unidad="D:", titulo="D:", libre=20 * 1024**3, usado_pct=98.0)
        vigilante = hw.VigilanteSalud(eventos, leer=lambda: hw.Temperaturas(), discos=lambda: [disco])
        vigilante.revisar_discos()
        tipo, texto, clave = eventos.get_nowait()
        self.assertEqual((tipo, clave), ("disk_alert", "D::critico"))
        self.assertIn("casi sin espacio", texto)

    def test_app_remembers_disk_alerts_across_restarts(self) -> None:
        guardado = {}
        host = SimpleNamespace(_PREFERENCIA_AVISOS_DISCO="avisos_disco")
        with patch("jarvis_core.obtener_preferencia", side_effect=guardado.get), \
                patch("jarvis_core.guardar_preferencia", side_effect=guardado.__setitem__):
            self.assertTrue(JarvisApp._first_disk_alert_today(host, "D::lleno"))
            self.assertFalse(JarvisApp._first_disk_alert_today(host, "D::lleno"))  # otro arranque, mismo día
            self.assertTrue(JarvisApp._first_disk_alert_today(host, "D::critico"))  # empeoró


class ServiceWatcherTests(unittest.TestCase):
    def test_alerts_after_two_failures_repairs_and_recovers(self) -> None:
        eventos = queue.Queue()
        estado = {"ok": False}
        reparar = Mock()
        servicio = sv.Servicio("la voz de Jarvis", lambda: estado["ok"], "Revisa Docker.", reparar)
        vigilante = sv.VigilanteServicios(eventos, [servicio])
        vigilante.revisar_una_vez()
        self.assertTrue(eventos.empty())  # un fallo suelto no avisa
        vigilante.revisar_una_vez()
        reparar.assert_called_once()
        tipo, texto = eventos.get_nowait()
        self.assertEqual(tipo, "service_alert")
        self.assertIn("Revisa Docker.", texto)
        vigilante.revisar_una_vez()
        self.assertTrue(eventos.empty())  # no repite el aviso
        estado["ok"] = True
        vigilante.revisar_una_vez()
        self.assertEqual(eventos.get_nowait()[0], "service_recovered")

    def test_repair_that_works_only_says_it_recovered(self) -> None:
        eventos = queue.Queue()
        estado = {"ok": False}
        servicio = sv.Servicio("la voz a texto", lambda: estado["ok"], reparar=lambda: estado.update(ok=True))
        vigilante = sv.VigilanteServicios(eventos, [servicio])
        vigilante.revisar_una_vez()
        vigilante.revisar_una_vez()
        tipo, texto = eventos.get_nowait()
        self.assertEqual(tipo, "service_recovered")
        self.assertIn("lo reinicié", texto)


# --- Archivos --------------------------------------------------------------------

class FileSearchTests(unittest.TestCase):
    def test_phrases(self) -> None:
        self.assertEqual(ba.detectar("Abre el PDF de la factura de la luz"),
                         ("find_files", {"query": "la factura de la luz", "kind": "pdf", "open_best": True}))
        self.assertEqual(ba.detectar("busca las fotos de ayer"), ("find_files", {"query": "", "kind": "image", "days": 2}))
        self.assertEqual(ba.detectar("¿Dónde está el excel del presupuesto?"),
                         ("find_files", {"query": "presupuesto", "kind": "spreadsheet"}))
        for texto in ("abre el video del monitor 2", "abre el archivo", "abre Discord", "busca en youtube música"):
            with self.subTest(texto=texto):
                self.assertIsNone(ba.detectar(texto))
        self.assertEqual(ba.detectar_orden("abre el segundo"), 2)
        self.assertEqual(ba.detectar_orden("abre el número 3"), 3)

    def test_search_in_folders(self) -> None:
        with tempfile.TemporaryDirectory() as carpeta:
            raiz = Path(carpeta)
            (raiz / "Facturas").mkdir()
            (raiz / "Facturas" / "Factura_Luz_Octubre.pdf").write_text("x")
            (raiz / "factura agua.pdf").write_text("x")
            (raiz / "luz.txt").write_text("x")
            (raiz / "node_modules").mkdir()
            (raiz / "node_modules" / "factura luz.pdf").write_text("x")
            viejo = raiz / "Factura luz 2020.pdf"
            viejo.write_text("x")
            os.utime(viejo, (0, 0))
            resultados = ba.buscar_en_carpetas(ba.palabras_de("la factura de la luz"), ba.TIPOS["pdf"], None, [raiz])
            self.assertEqual(sorted(e.nombre for e in resultados), ["Factura luz 2020.pdf", "Factura_Luz_Octubre.pdf"])
            recientes = ba.buscar_en_carpetas(["factura"], ba.TIPOS["pdf"], 1_000_000, [raiz])
            self.assertNotIn("Factura luz 2020.pdf", [e.nombre for e in recientes])

    def test_choose_and_never_open_programs(self) -> None:
        a = ba.Encontrado("a/factura luz.pdf", 2)
        b = ba.Encontrado("b/factura.pdf", 1)
        self.assertIs(ba.elegir_para_abrir([a, b], ["factura", "luz"]), a)
        self.assertIsNone(ba.elegir_para_abrir([b, ba.Encontrado("c/factura 2.pdf", 0)], ["factura"]))
        for ruta in ("x.exe", "x.bat", "x.ps1", "x.lnk", "x.vbs"):
            self.assertFalse(ba.es_abrible(ruta))
        self.assertTrue(ba.es_abrible("x.pdf"))
        with tempfile.TemporaryDirectory() as carpeta:
            programa = Path(carpeta) / "virus.exe"
            programa.write_text("x")
            with self.assertRaises(ValueError), patch("os.startfile", create=True) as abrir:
                ba.abrir(str(programa))
            abrir.assert_not_called()

    def test_list_text(self) -> None:
        ahora = datetime(2026, 11, 13, 12, 0)
        e = ba.Encontrado(str(Path("Descargas") / "dni.pdf"), datetime(2026, 11, 12, 9, 0).timestamp())
        self.assertEqual(ba.describir([e], ahora), "1. dni.pdf — Descargas, ayer")

    def test_app_opens_the_clear_best_and_remembers_the_list(self) -> None:
        encontrados = [ba.Encontrado("a/factura luz.pdf", 2), ba.Encontrado("b/factura.pdf", 1)]
        host = Mock(_FILE_RESULTS_TTL=600)
        with patch("jarvis_buscar_archivos.buscar", return_value=encontrados):
            texto = JarvisApp._tool_find_files(host, "factura luz", "pdf", 0, True)
        host.tool_registry.execute.assert_called_once_with("open_file", {"path": "a/factura luz.pdf"},
                                                           confirmed=True, origin="directo")
        self.assertIn("Abrí «factura luz.pdf»", texto)
        host._last_file_results = (__import__("time").monotonic(), encontrados)
        self.assertEqual(JarvisApp._file_choice(host, "abre el segundo"), ("open_file", {"path": "b/factura.pdf"}))
        self.assertIsNone(JarvisApp._file_choice(host, "abre el quinto"))


if __name__ == "__main__":
    unittest.main()
