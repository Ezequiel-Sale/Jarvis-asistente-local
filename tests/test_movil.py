"""Servidor de la app del celular: vinculación, permisos y rutas (sin red ni base real)."""

import json
import os
import queue
import threading
import unittest

import jarvis_mobile as m

TS = "100.120.145.38"  # un cliente de Tailscale


def _servidor(ocupado=False):
    guardado: dict[str, str] = {}
    reloj = {"t": 0.0}
    vinculacion = m.Vinculacion(guardado.get, guardado.__setitem__, reloj=lambda: reloj["t"])
    eventos: queue.Queue = queue.Queue()
    servidor = m.ServidorMovil(eventos, vinculacion, ocupado=lambda: ocupado)
    return servidor, eventos, guardado, reloj


def _pedir(servidor, metodo, ruta, cuerpo=None, token=None, ip=TS):
    datos = cuerpo if isinstance(cuerpo, bytes) else json.dumps(cuerpo or {}).encode()
    cabeceras = {"Authorization": f"Bearer {token}"} if token else {}
    estado, tipo, respuesta = servidor.manejar(metodo, ruta, cabeceras, datos, ip)
    return estado, (json.loads(respuesta) if tipo == "application/json" else respuesta)


def _vincular(servidor):
    codigo = servidor.vinculacion.nuevo_codigo()
    return _pedir(servidor, "POST", "/api/pair", {"code": codigo, "device": "Motorola"})[1]["token"]


class ScreenTests(unittest.TestCase):
    """Ver la pantalla desde el celular: permiso, parámetros acotados y aviso en la PC."""

    def setUp(self) -> None:
        self.permitido = True
        self.pedidas: list[tuple[int, int, int]] = []

        def captura(monitor, ancho, calidad):
            self.pedidas.append((monitor, ancho, calidad))
            if monitor == 9:
                raise ValueError("No existe el monitor 9.")
            return b"jpeg"

        self.eventos: queue.Queue = queue.Queue()
        guardado: dict[str, str] = {}
        vinculacion = m.Vinculacion(guardado.get, guardado.__setitem__)
        self.servidor = m.ServidorMovil(
            self.eventos, vinculacion, captura=captura,
            pantallas=lambda: [{"number": 1, "width": 2560, "height": 1440, "primary": True}],
            pantalla_permitida=lambda: self.permitido,
        )
        self.token = _vincular(self.servidor)
        self.eventos.get_nowait()  # mobile_paired

    def test_requires_pairing(self) -> None:
        self.assertEqual(_pedir(self.servidor, "GET", "/api/screen")[0], 401)
        self.assertEqual(self.pedidas, [])

    def test_frames_with_clamped_parameters_and_watch_notice(self) -> None:
        self.assertFalse(self.servidor.mirando())
        estado, cuerpo = _pedir(self.servidor, "GET", "/api/screen?n=1&w=99999&q=5", token=self.token)
        self.assertEqual((estado, cuerpo), (200, b"jpeg"))
        _pedir(self.servidor, "GET", "/api/screen", token=self.token)
        self.assertEqual(self.pedidas, [(1, 2560, 25), (0, 1280, 60)])
        self.assertTrue(self.servidor.mirando())
        self.assertFalse(self.servidor.mirando(ahora=10**9))
        # La PC se entera una sola vez de que alguien empezó a mirar.
        self.assertEqual(self.eventos.get_nowait(), ("mobile_screen",))
        self.assertTrue(self.eventos.empty())

    def test_disabled_or_errors(self) -> None:
        self.assertEqual(_pedir(self.servidor, "GET", "/api/screen?n=9", token=self.token)[0], 404)
        self.assertEqual(_pedir(self.servidor, "GET", "/api/screen?n=x", token=self.token)[0], 400)
        self.assertEqual(_pedir(self.servidor, "GET", "/api/screens", token=self.token)[1]["screens"][0]["number"], 1)
        self.permitido = False
        estado, datos = _pedir(self.servidor, "GET", "/api/screen", token=self.token)
        self.assertEqual(estado, 403)
        self.assertIn("Ajustes", datos["error"])
        self.assertEqual(_pedir(self.servidor, "GET", "/api/screens", token=self.token)[1],
                         {"allowed": False, "screens": []})
        self.assertFalse(self.servidor.mirando())


class VideoTests(unittest.TestCase):
    """/api/video: flujo con marco de 5 bytes, errores de arranque y un vídeo a la vez."""

    def setUp(self) -> None:
        self.permitido = True
        self.pedidos: list[tuple] = []

        def video(monitor, fps, ancho, kbps, detener):
            self.pedidos.append((monitor, fps, ancho, kbps))
            self.detener = detener
            if monitor == 9:
                raise ValueError("No existe el monitor 9.")
            if monitor == 8:
                raise OSError("No hay codificador H.264 disponible.")
            yield b"\x00\x00\x00\x01clave", True
            yield b"\x00\x00\x00\x01p", False

        self.eventos: queue.Queue = queue.Queue()
        guardado: dict[str, str] = {}
        self.servidor = m.ServidorMovil(self.eventos, m.Vinculacion(guardado.get, guardado.__setitem__),
                                        video=video, pantalla_permitida=lambda: self.permitido)
        self.servidor._http = object()  # como si escuchara
        self.token = _vincular(self.servidor)

    def _abrir(self, consulta):
        cabeceras = {"Authorization": f"Bearer {self.token}"}
        return self.servidor.manejar("GET", "/api/video" + consulta, cabeceras, b"", TS)

    def test_stream_is_framed_and_marks_watching(self) -> None:
        import io
        import struct

        estado, _tipo, escribir = self._abrir("?n=1&fps=999&w=10&kbps=1")
        self.assertEqual(estado, 200)
        self.assertEqual(self.pedidos, [(1, 60, 640, 1000)])
        salida = io.BytesIO()
        escribir(salida)
        datos = salida.getvalue()
        largo, banderas = struct.unpack(">IB", datos[:5])
        self.assertEqual((datos[5:5 + largo], banderas), (b"\x00\x00\x00\x01clave", 1))
        self.assertEqual(datos[5 + largo + 4], 0)
        self.assertTrue(self.servidor.mirando())
        self.assertTrue(self.detener.is_set())

    def test_start_errors_and_permission(self) -> None:
        self.assertEqual(self.servidor.manejar("GET", "/api/video?n=1", {}, b"", TS)[0], 401)
        self.assertEqual(self._abrir("?n=0")[0], 400)
        self.assertEqual(self._abrir("?n=9")[0], 404)
        estado, _tipo, cuerpo = self._abrir("?n=8")
        self.assertEqual(estado, 501)
        self.assertIn("codificador", json.loads(cuerpo)["error"])
        self.permitido = False
        self.assertEqual(self._abrir("?n=1")[0], 403)

    def test_new_stream_stops_previous(self) -> None:
        self._abrir("?n=1")
        primero = self.detener
        self._abrir("?n=2")
        self.assertTrue(primero.is_set())
        self.assertFalse(self.detener.is_set())

    def test_output_size_is_even_and_never_upscaled(self) -> None:
        import jarvis_video_movil as jv

        self.assertEqual(jv.tamano_salida(2560, 1440, 1920), (1920, 1080))
        self.assertEqual(jv.tamano_salida(1920, 1080, 2560), (1920, 1080))
        self.assertEqual(jv.tamano_salida(1366, 768, 1000), (1000, 562))


class AppUpdateTests(unittest.TestCase):
    """/api/app y /api/app/apk: la app se actualiza con el APK que deja compilar.ps1."""

    def setUp(self) -> None:
        import tempfile
        from pathlib import Path

        self.carpeta = Path(tempfile.mkdtemp())
        guardado: dict[str, str] = {}
        self.servidor = m.ServidorMovil(queue.Queue(), m.Vinculacion(guardado.get, guardado.__setitem__),
                                        carpeta_app=self.carpeta)
        self.token = _vincular(self.servidor)

    def _escribir(self, apk: bytes, codigo: int) -> None:
        (self.carpeta / "Jarvis.apk").write_bytes(apk)
        # Con BOM, como lo escribe PowerShell 5.
        (self.carpeta / "Jarvis.apk.json").write_text(
            json.dumps({"versionCode": codigo, "versionName": f"1.{codigo}"}), encoding="utf-8-sig")

    def test_version_and_download(self) -> None:
        import hashlib

        self.assertEqual(_pedir(self.servidor, "GET", "/api/app", token=self.token)[0], 404)
        self._escribir(b"PK-apk-1", 10)
        estado, info = _pedir(self.servidor, "GET", "/api/app", token=self.token)
        self.assertEqual(estado, 200)
        self.assertEqual(info, {"versionCode": 10, "versionName": "1.10", "size": 8,
                                "sha256": hashlib.sha256(b"PK-apk-1").hexdigest()})
        self.assertEqual(_pedir(self.servidor, "GET", "/api/app/apk", token=self.token)[1], b"PK-apk-1")
        self._escribir(b"PK-apk-dos", 11)  # compilación nueva: se recalcula
        self.assertEqual(_pedir(self.servidor, "GET", "/api/app", token=self.token)[1]["versionCode"], 11)
        self.assertEqual(_pedir(self.servidor, "GET", "/api/app/apk")[0], 401)

    def test_broken_version_file(self) -> None:
        (self.carpeta / "Jarvis.apk").write_bytes(b"PK")
        (self.carpeta / "Jarvis.apk.json").write_text("{roto", encoding="utf-8")
        self.assertEqual(_pedir(self.servidor, "GET", "/api/app", token=self.token)[0], 404)

    def test_high_risk_questions_are_marked(self) -> None:
        self.servidor.pregunta("q1", "¿Apago el equipo?", "SÍ", "NO", alto_riesgo=True)
        self.servidor.pregunta("q2", "¿Ejecuto el plan?", "SÍ", "NO")
        eventos = [e for e in self.servidor.registro.desde(-1) if e["type"] == "question"]
        self.assertEqual([e["high"] for e in eventos], [True, False])


class NoticeAndVoiceTests(unittest.TestCase):
    """Avisos marcados para notificar y voz de Piper para el celular."""

    def setUp(self) -> None:
        self.textos: list[str] = []

        def tts(texto):
            self.textos.append(texto)
            if texto == "falla":
                raise ConnectionError("Piper caído")
            return b"RIFF-wav"

        guardado: dict[str, str] = {}
        self.servidor = m.ServidorMovil(queue.Queue(), m.Vinculacion(guardado.get, guardado.__setitem__), tts=tts)
        self.token = _vincular(self.servidor)

    def test_alerts_are_marked_and_replies_are_not(self) -> None:
        self.servidor.mensaje("assistant", "Recordatorio: llamar a mamá.", "alarm")
        self.servidor.mensaje("assistant", "Listo.")
        eventos = [e for e in self.servidor.registro.desde(-1) if e["type"] == "assistant"]
        self.assertEqual([e.get("alert") for e in eventos], ["alarm", None])

    def test_tts_returns_wav(self) -> None:
        estado, cuerpo = _pedir(self.servidor, "POST", "/api/tts", {"text": "  Hola   mundo "}, token=self.token)
        self.assertEqual((estado, cuerpo), (200, b"RIFF-wav"))
        self.assertEqual(self.textos, ["Hola mundo"])
        self.assertEqual(_pedir(self.servidor, "POST", "/api/tts", {"text": " "}, token=self.token)[0], 400)
        self.assertEqual(_pedir(self.servidor, "POST", "/api/tts", {"text": "falla"}, token=self.token)[0], 503)
        self.assertEqual(_pedir(self.servidor, "POST", "/api/tts", {"text": "x"})[0], 401)


class MediaAndStatusTests(unittest.TestCase):
    """Pestañas Música y Equipo: solo herramientas de riesgo bajo, por la función inyectada."""

    def setUp(self) -> None:
        self.acciones: list[tuple] = []

        def accion(herramienta, argumentos):
            self.acciones.append((herramienta, argumentos))
            if argumentos.get("app") == "nada":
                raise RuntimeError("No encuentro audio de «nada».")
            return "Hecho."

        guardado: dict[str, str] = {}
        self.servidor = m.ServidorMovil(
            queue.Queue(), m.Vinculacion(guardado.get, guardado.__setitem__),
            musica=lambda: {"playing": {"app": "Spotify"}, "apps": []},
            portada=lambda: b"\x89PNG-imagen", accion=accion,
            estado_equipo=lambda fps: {"gpu": 50.0, "fps": {"fps": 144} if fps else None},
        )
        self.token = _vincular(self.servidor)

    def test_media_reads_and_cover(self) -> None:
        self.assertEqual(_pedir(self.servidor, "GET", "/api/media", token=self.token)[1]["playing"]["app"], "Spotify")
        estado, tipo, cuerpo = self.servidor.manejar("GET", "/api/media/cover", {"Authorization": f"Bearer {self.token}"},
                                                      b"", TS)
        self.assertEqual((estado, tipo, cuerpo), (200, "image/png", b"\x89PNG-imagen"))

    def test_only_whitelisted_tools_run(self) -> None:
        pedir = lambda datos: _pedir(self.servidor, "POST", "/api/media", datos, token=self.token)
        self.assertEqual(pedir({"tool": "media_control", "args": {"action": "next"}}), (200, {"ok": True, "text": "Hecho."}))
        self.assertEqual(pedir({"tool": "power_control", "args": {"action": "shutdown"}})[0], 400)
        self.assertEqual(pedir({"tool": "set_app_volume", "args": "x"})[0], 400)
        estado, datos = pedir({"tool": "set_app_volume", "args": {"app": "nada", "mute": True}})
        self.assertEqual((estado, datos["error"]), (409, "No encuentro audio de «nada»."))
        self.assertEqual(self.acciones, [("media_control", {"action": "next"}),
                                         ("set_app_volume", {"app": "nada", "mute": True})])
        self.assertEqual(_pedir(self.servidor, "POST", "/api/media", {"tool": "media_control", "args": {}})[0], 401)

    def test_status_with_and_without_fps(self) -> None:
        self.assertIsNone(_pedir(self.servidor, "GET", "/api/status", token=self.token)[1]["fps"])
        self.assertEqual(_pedir(self.servidor, "GET", "/api/status?fps=1", token=self.token)[1]["fps"], {"fps": 144})


class ScreenCaptureTests(unittest.TestCase):
    """captura_para_movil: recorte de cada monitor, «todos» y reducción (sin capturar de verdad)."""

    def test_crops_monitor_or_all_and_scales(self) -> None:
        import io
        from unittest.mock import patch

        from PIL import Image

        import jarvis_vision as v

        monitores = [v.Monitor(1, 0, 0, 2560, 1440, True), v.Monitor(2, -1920, 0, 1920, 1080, False)]
        pedidas: list[tuple[int, int, int, int]] = []

        def captura(x, y, ancho, alto):
            pedidas.append((x, y, ancho, alto))
            return Image.new("RGB", (ancho, alto))

        with patch.object(v, "listar_monitores", return_value=monitores),                 patch.object(v, "_captura_rapida", side_effect=captura):
            uno = Image.open(io.BytesIO(v.captura_para_movil(1, 1600, 55)))
            todos = Image.open(io.BytesIO(v.captura_para_movil(0, 2560, 55)))
            with self.assertRaises(ValueError):
                v.captura_para_movil(7, 1600, 55)
        self.assertEqual(pedidas, [(0, 0, 2560, 1440), (-1920, 0, 4480, 1440)])
        self.assertEqual((uno.format, uno.size), ("JPEG", (1600, 900)))
        self.assertEqual(todos.size, (2560, 823))


class NetworkTests(unittest.TestCase):
    def test_only_tailscale_addresses(self) -> None:
        self.assertTrue(m.es_tailscale("100.104.121.13"))
        for ip in ("192.168.1.102", "127.0.0.1", "26.137.25.213", "100.63.0.1", "no-es-ip"):
            self.assertFalse(m.es_tailscale(ip))

    def test_refuses_to_listen_outside_tailscale(self) -> None:
        servidor, *_ = _servidor()
        with self.assertRaises(ValueError):
            servidor.iniciar("0.0.0.0")
        with self.assertRaises(ValueError):
            servidor.iniciar("192.168.1.102")

    def test_rejects_clients_outside_tailscale_even_with_token(self) -> None:
        servidor, *_ = _servidor()
        token = _vincular(servidor)
        self.assertEqual(_pedir(servidor, "GET", "/api/events", token=token, ip="192.168.1.50")[0], 403)


class PairingTests(unittest.TestCase):
    def test_code_gives_token_once_and_only_hash_is_stored(self) -> None:
        servidor, eventos, guardado, _ = _servidor()
        codigo = servidor.vinculacion.nuevo_codigo()
        self.assertRegex(codigo, r"^\d{6}$")
        estado, datos = _pedir(servidor, "POST", "/api/pair", {"code": codigo, "device": "Motorola Edge 40"})
        self.assertEqual(estado, 200)
        token = datos["token"]
        self.assertNotIn(token, guardado[m.PREFERENCIA_DISPOSITIVOS])
        self.assertTrue(servidor.vinculacion.autorizado(token))
        self.assertEqual(eventos.get_nowait(), ("mobile_paired",))
        # El mismo código no sirve para otro celular.
        self.assertEqual(_pedir(servidor, "POST", "/api/pair", {"code": codigo})[0], 403)
        self.assertIn("Motorola Edge 40", servidor.vinculacion.dispositivos()[0])

    def test_wrong_codes_expire_the_code(self) -> None:
        servidor, *_ = _servidor()
        codigo = servidor.vinculacion.nuevo_codigo()
        malo = "000000" if codigo != "000000" else "111111"
        for _ in range(m.MAX_INTENTOS):
            self.assertEqual(_pedir(servidor, "POST", "/api/pair", {"code": malo})[0], 403)
        self.assertEqual(_pedir(servidor, "POST", "/api/pair", {"code": codigo})[0], 403)

    def test_code_expires_after_five_minutes(self) -> None:
        servidor, _, _, reloj = _servidor()
        codigo = servidor.vinculacion.nuevo_codigo()
        reloj["t"] = m.VIGENCIA_CODIGO + 1
        self.assertEqual(_pedir(servidor, "POST", "/api/pair", {"code": codigo})[0], 403)

    def test_without_code_nobody_pairs(self) -> None:
        servidor, *_ = _servidor()
        self.assertEqual(_pedir(servidor, "POST", "/api/pair", {"code": ""})[0], 403)

    def test_unpair_revokes_tokens(self) -> None:
        servidor, *_ = _servidor()
        token = _vincular(servidor)
        servidor.vinculacion.desvincular_todos()
        self.assertEqual(_pedir(servidor, "GET", "/api/events", token=token)[0], 401)


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.servidor, self.eventos, _, _ = _servidor()
        self.token = _vincular(self.servidor)
        self.eventos.get_nowait()  # mobile_paired

    def test_everything_needs_a_valid_token(self) -> None:
        for metodo, ruta in (("GET", "/api/events"), ("POST", "/api/message"), ("POST", "/api/audio"),
                             ("POST", "/api/answer"), ("POST", "/api/cancel"), ("GET", "/api/question/q1/image")):
            with self.subTest(ruta=ruta):
                self.assertEqual(_pedir(self.servidor, metodo, ruta)[0], 401)
                self.assertEqual(_pedir(self.servidor, metodo, ruta, token="falso")[0], 401)
        self.assertTrue(self.eventos.empty())

    def test_message_goes_to_the_app_queue(self) -> None:
        estado, _ = _pedir(self.servidor, "POST", "/api/message", {"text": "  sube   el volumen "}, self.token)
        self.assertEqual(estado, 200)
        self.assertEqual(self.eventos.get_nowait(), ("mobile_text", "sube el volumen"))
        self.assertEqual(_pedir(self.servidor, "POST", "/api/message", {"text": " "}, self.token)[0], 400)
        largo = {"text": "x" * (m.MAX_TEXTO + 1)}
        self.assertEqual(_pedir(self.servidor, "POST", "/api/message", largo, self.token)[0], 413)
        self.assertEqual(_pedir(self.servidor, "POST", "/api/message", b"no json", self.token)[0], 400)

    def test_busy_rejects_new_orders(self) -> None:
        servidor, eventos, _, _ = _servidor(ocupado=True)
        token = _vincular(servidor)
        eventos.get_nowait()
        self.assertEqual(_pedir(servidor, "POST", "/api/message", {"text": "hola"}, token)[0], 409)
        self.assertTrue(eventos.empty())

    def test_audio_is_saved_as_wav_and_queued(self) -> None:
        wav = b"RIFF" + b"\x00" * 2000
        estado, _ = _pedir(self.servidor, "POST", "/api/audio", wav, self.token)
        self.assertEqual(estado, 200)
        tipo, ruta = self.eventos.get_nowait()
        self.addCleanup(os.remove, ruta)
        self.assertEqual(tipo, "mobile_audio")
        with open(ruta, "rb") as archivo:
            self.assertEqual(archivo.read(), wav)
        self.assertEqual(_pedir(self.servidor, "POST", "/api/audio", b"no es wav" * 200, self.token)[0], 400)

    def test_events_questions_and_answers(self) -> None:
        self.servidor.mensaje("user", "apaga el equipo")
        self.servidor.pregunta("q1", "¿Apago el equipo?", "SÍ", "NO", imagen=b"\xff\xd8jpeg")
        estado, datos = _pedir(self.servidor, "GET", "/api/events?since=-1", token=self.token)
        self.assertEqual([e["type"] for e in datos["events"]], ["user", "question"])
        self.assertEqual(datos["pending"], "q1")
        self.assertTrue(datos["events"][1]["image"])
        self.assertEqual(_pedir(self.servidor, "GET", "/api/question/q1/image", token=self.token)[1], b"\xff\xd8jpeg")
        # Solo se puede responder la confirmación pendiente.
        self.assertEqual(_pedir(self.servidor, "POST", "/api/answer", {"qid": "q0", "approve": True}, self.token)[0], 409)
        self.assertEqual(_pedir(self.servidor, "POST", "/api/answer", {"qid": "q1", "approve": False}, self.token)[0], 200)
        self.assertEqual(self.eventos.get_nowait(), ("mobile_answer", "q1", False))
        self.servidor.pregunta_resuelta("q1", "✕ NO")
        ultimo = datos["last"]
        _, datos = _pedir(self.servidor, "GET", f"/api/events?since={ultimo}", token=self.token)
        self.assertEqual([e["type"] for e in datos["events"]], ["resolved"])
        self.assertIsNone(datos["pending"])
        self.assertEqual(_pedir(self.servidor, "GET", "/api/question/q1/image", token=self.token)[0], 404)

    def test_long_poll_wakes_up_on_new_event(self) -> None:
        resultado = {}

        def esperar() -> None:
            resultado["r"] = _pedir(self.servidor, "GET", "/api/events?since=0&wait=5", token=self.token)

        hilo = threading.Thread(target=esperar)
        hilo.start()
        self.servidor.mensaje("assistant", "Listo.")
        hilo.join(3)
        self.assertFalse(hilo.is_alive())
        self.assertEqual(resultado["r"][1]["events"][0]["text"], "Listo.")

    def test_stale_id_after_restart_returns_everything(self) -> None:
        self.servidor.mensaje("assistant", "Hola")
        _, datos = _pedir(self.servidor, "GET", "/api/events?since=999", token=self.token)
        self.assertEqual(len(datos["events"]), 1)

    def test_live_answer_replaces_itself_and_final_message_removes_it(self) -> None:
        reloj = [0.0]
        ahora = lambda: reloj[0]  # noqa: E731
        self.servidor.mensaje("user", "¿qué es Python?")
        self.assertTrue(self.servidor.en_vivo("Python es", reloj=ahora))
        self.assertFalse(self.servidor.en_vivo("Python es un", reloj=ahora))  # muy seguido
        reloj[0] += m.INTERVALO_EN_VIVO
        self.assertTrue(self.servidor.en_vivo("Python es un lenguaje", reloj=ahora))
        _, datos = _pedir(self.servidor, "GET", "/api/events?since=-1", token=self.token)
        self.assertEqual([(e["type"], e["text"]) for e in datos["events"]],
                         [("user", "¿qué es Python?"), ("live", "Python es un lenguaje")])
        # El celular que ya tenía el primer borrador recibe el texto nuevo.
        _, nuevos = _pedir(self.servidor, "GET", f"/api/events?since={datos['last'] - 1}", token=self.token)
        self.assertEqual(nuevos["events"][-1]["text"], "Python es un lenguaje")
        self.servidor.mensaje("assistant", "Python es un lenguaje de programación.")
        _, datos = _pedir(self.servidor, "GET", "/api/events?since=-1", token=self.token)
        self.assertEqual([e["type"] for e in datos["events"]], ["user", "assistant"])

    def test_discarded_live_answer_tells_the_phone_to_remove_it(self) -> None:
        self.servidor.fin_en_vivo()  # nada abierto: no publica
        self.assertEqual(self.servidor.registro.ultimo, 0)
        self.servidor.en_vivo("Voy a")
        self.servidor.en_vivo("")  # nueva ronda del modelo
        self.servidor.fin_en_vivo()  # ya cerrada: no repite
        _, datos = _pedir(self.servidor, "GET", "/api/events?since=-1", token=self.token)
        self.assertEqual([(e["type"], e["text"]) for e in datos["events"]], [("live", "")])
        self.servidor.en_vivo("Otra")  # tras cerrar, la siguiente sale enseguida
        self.assertEqual(self.servidor.registro.desde(-1)[-1]["text"], "Otra")

    def test_cancel_and_unknown_route(self) -> None:
        self.assertEqual(_pedir(self.servidor, "POST", "/api/cancel", token=self.token)[0], 200)
        self.assertEqual(self.eventos.get_nowait(), ("mobile_cancel",))
        self.assertEqual(_pedir(self.servidor, "GET", "/api/otra", token=self.token)[0], 404)


if __name__ == "__main__":
    unittest.main()
