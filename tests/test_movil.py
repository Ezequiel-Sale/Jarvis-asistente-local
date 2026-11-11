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
