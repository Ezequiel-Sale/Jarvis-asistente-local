"""Gestos con la mano (fase 1). Sin cámara real: manos sintéticas, cámara y modelo falsos."""

import math
import queue
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

import jarvis_gestos as g
from jarvis_app import JarvisApp
from jarvis_commands import detectar_comando_gestos

MUNECA = (0.5, 0.9)
COLUMNAS = {"indice": 0.42, "medio": 0.5, "anular": 0.58, "menique": 0.66}
BASES = {"indice": 5, "medio": 9, "anular": 13, "menique": 17}


def mano(estirados=(), pellizco=False, girar=0.0):
    """21 puntos de una mano de frente; ``estirados``: dedos (sin pulgar) estirados."""
    p = [(0.0, 0.0)] * 21
    p[0] = MUNECA
    for dedo, x in COLUMNAS.items():
        base = BASES[dedo]
        p[base] = (x, 0.6)
        if dedo in estirados:
            p[base + 1], p[base + 2], p[base + 3] = (x, 0.5), (x, 0.42), (x, 0.35)
        else:  # enroscado: la punta vuelve hacia la palma
            p[base + 1], p[base + 2], p[base + 3] = (x, 0.52), (x, 0.58), (x, 0.66)
    p[1], p[2], p[3], p[4] = (0.42, 0.82), (0.36, 0.75), (0.31, 0.7), (0.27, 0.66)  # pulgar afuera
    if pellizco:  # índice a medio cerrar con el pulgar tocándolo
        x = COLUMNAS["indice"]
        p[6], p[7], p[8] = (x, 0.5), (x - 0.02, 0.46), (x - 0.05, 0.45)
        p[4] = (x - 0.06, 0.46)
    if girar:  # rotar toda la mano alrededor de la muñeca
        c, s = math.cos(girar), math.sin(girar)
        p = [(MUNECA[0] + (x - MUNECA[0]) * c - (y - MUNECA[1]) * s,
              MUNECA[1] + (x - MUNECA[0]) * s + (y - MUNECA[1]) * c) for x, y in p]
    return p


def puno_con_pulgar_encima():
    """Puño: todo enroscado y el pulgar sobre el índice (cerca de su punta)."""
    p = mano(())
    p[4] = (COLUMNAS["indice"] - 0.01, 0.64)
    return p


class ClassifierTests(unittest.TestCase):
    def test_gestures(self) -> None:
        casos = {
            "indice": mano(("indice",)),
            "dos_dedos": mano(("indice", "medio")),
            "mano_abierta": mano(("indice", "medio", "anular", "menique")),
            "puno": mano(()),
            "pellizco": mano(("medio", "anular", "menique"), pellizco=True),
            "otro": mano(("indice", "menique")),
        }
        for esperado, puntos in casos.items():
            with self.subTest(gesto=esperado):
                self.assertEqual(g.clasificar(puntos), esperado)

    def test_fist_with_thumb_over_index_is_not_a_pinch(self) -> None:
        """En la fase 0 el puño salió poco: el pulgar encima del índice parecía un pellizco."""
        self.assertEqual(g.clasificar(puno_con_pulgar_encima()), "puno")

    def test_turned_hand(self) -> None:
        for angulo in (0.6, -0.9, math.pi / 2):
            with self.subTest(angulo=angulo):
                self.assertEqual(g.clasificar(mano(("indice", "medio"), girar=angulo)), "dos_dedos")
                self.assertEqual(g.clasificar(mano(("indice",), girar=angulo)), "indice")

    def test_incomplete_hand(self) -> None:
        self.assertEqual(g.clasificar([(0.5, 0.5)] * 5), "otro")


class StabilizerTests(unittest.TestCase):
    def test_needs_consecutive_frames(self) -> None:
        e = g.Estabilizador(cuadros=3)
        self.assertIs(e.agregar("indice"), False)
        self.assertIs(e.agregar("indice"), False)
        self.assertEqual(e.agregar("indice"), "indice")
        self.assertIs(e.agregar("indice"), False)  # sin cambios: nada que avisar
        self.assertIs(e.agregar("puno"), False)  # un cuadro suelto no cuenta
        self.assertIs(e.agregar("indice"), False)
        for _ in range(2):
            self.assertIs(e.agregar(None), False)
        self.assertIsNone(e.agregar(None))  # la mano se fue (None, no False)


class ScrollTests(unittest.TestCase):
    """Fase 2: dos dedos que suben o bajan → rueda del mouse."""

    def _recorrido(self, alturas, invertir=False, paso=0.04):
        d = g.Desplazamiento(invertir)
        envios = [c for i, y in enumerate(alturas) if (c := d.mover(y, i * paso)) is not None]
        final = d.terminar(len(alturas) * paso)
        return envios + ([final] if final else [])

    def test_raising_the_hand_scrolls_the_page_down(self) -> None:
        subiendo = [0.8 - i * 0.02 for i in range(20)]  # de abajo hacia arriba, ~0,8 s
        envios = self._recorrido(subiendo)
        self.assertTrue(envios)
        self.assertTrue(all(c < 0 for c in envios))  # rueda negativa: la página baja
        self.assertLess(len(envios), 8)  # en tandas, no una acción por cuadro
        total = sum(envios)
        self.assertLess(total, -600)  # ~0,4 de la imagen ≈ unas 8 muescas
        bajando = self._recorrido(list(reversed(subiendo)))
        self.assertTrue(all(c > 0 for c in bajando))
        self.assertTrue(all(c > 0 for c in self._recorrido(subiendo, invertir=True)))

    def test_still_hand_does_not_scroll(self) -> None:
        temblor = [0.5 + (0.002 if i % 2 else -0.002) for i in range(40)]
        self.assertEqual(self._recorrido(temblor), [])

    def test_first_frame_is_only_a_reference(self) -> None:
        d = g.Desplazamiento()
        self.assertIsNone(d.mover(0.9, 0.0))  # meter la mano en la imagen no salta
        self.assertIsNone(d.terminar())

    def test_fingertip_height(self) -> None:
        self.assertAlmostEqual(g.altura_dos_dedos(mano(("indice", "medio"))), 0.35)

    def test_wheel_input(self) -> None:
        import jarvis_screen_actions as s

        user32 = Mock()
        user32.SendInput.return_value = 1
        with patch.object(s.time, "sleep"):
            texto = s.girar_rueda(-360, user32=user32)
        self.assertEqual(user32.SendInput.call_count, 3)
        mouse_datos = [llamada.args[1]._obj.u.mi.mouseData for llamada in user32.SendInput.call_args_list]
        self.assertEqual(mouse_datos, [(-120) & 0xFFFFFFFF] * 3)  # negativo, como DWORD con signo
        self.assertIn("hacia abajo", texto)
        self.assertEqual(s.girar_rueda(0, user32=user32), "Nada que desplazar.")
        with patch.object(s.time, "sleep"):
            s.girar_rueda(99999, user32=user32)  # se limita a RUEDA_MAXIMA

    def test_tool_is_hidden_from_the_model(self) -> None:
        from jarvis_tools import crear_registro_herramientas

        registro = crear_registro_herramientas(Mock())
        self.assertEqual(registro.get("gesture_scroll").label, "Desplazar con gestos")
        self.assertNotIn("gesture_scroll", {t["function"]["name"] for t in registro.ollama_tools()})


class ImageTests(unittest.TestCase):
    def test_dark_frames_are_brightened(self) -> None:
        import cv2

        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        oscura = np.full((48, 64, 3), 20, dtype=np.uint8)
        resultado, brillo = g.aclarar(oscura, cv2, clahe)
        self.assertEqual(brillo, 20.0)
        self.assertGreater(float(resultado.mean()), 60)
        clara = np.full((48, 64, 3), 150, dtype=np.uint8)
        self.assertIs(g.aclarar(clara, cv2, clahe)[0], clara)

    def test_what_is_missing(self) -> None:
        carpeta = Path(tempfile.mkdtemp())
        with patch("importlib.util.find_spec", return_value=None):
            self.assertIn("MediaPipe", g.que_falta(carpeta))
        with patch("importlib.util.find_spec", return_value=object()):
            self.assertIn("gestos-modelo", g.que_falta(carpeta))
            (carpeta / g.CARPETA_MODELO).mkdir()
            (carpeta / g.CARPETA_MODELO / g.MODELO).write_bytes(b"x")
            self.assertIsNone(g.que_falta(carpeta))


class _CamaraFalsa:
    def __init__(self, cuadros: int, detector) -> None:
        self.quedan = cuadros
        self.detector = detector
        self.cerrada = False

    def isOpened(self):
        return True

    def read(self):
        if self.quedan <= 0:
            self.detector.stop()
            return False, None
        self.quedan -= 1
        return True, np.full((48, 64, 3), 120, dtype=np.uint8)

    def release(self):
        self.cerrada = True


class _ModeloFalso:
    def __init__(self, puntos) -> None:
        self.puntos = puntos
        self.cerrado = False

    def detect_for_video(self, _imagen, _ms):
        if not self.puntos:
            return SimpleNamespace(hand_landmarks=[])
        return SimpleNamespace(hand_landmarks=[[SimpleNamespace(x=x, y=y) for x, y in self.puntos]])

    def close(self):
        self.cerrado = True


class DetectorThreadTests(unittest.TestCase):
    def _correr(self, puntos, cuadros=8, puede_usar=lambda: True):
        eventos: queue.Queue = queue.Queue()
        camaras = []
        modelo = _ModeloFalso(puntos)
        detector = g.DetectorGestos(eventos, puede_usar, Path("."), crear_modelo=lambda: modelo,
                                    abrir_camara=lambda: camaras.append(_CamaraFalsa(cuadros, detector))
                                    or camaras[-1])
        detector.vista = True
        hilo = threading.Thread(target=detector._bucle)
        hilo.start()
        hilo.join(5)
        return [eventos.get_nowait() for _ in range(eventos.qsize())], camaras, modelo, detector

    def test_stable_gesture_is_published(self) -> None:
        eventos, camaras, modelo, detector = self._correr(mano(("indice", "medio")))
        self.assertIn(("gesture", "dos_dedos"), eventos)
        self.assertEqual([e for e in eventos if e[0] == "gesture"], [("gesture", "dos_dedos")])  # una vez
        self.assertTrue(camaras[0].cerrada)
        self.assertTrue(modelo.cerrado)
        self.assertEqual(detector.ultima_imagen.shape, (48, 64, 3))  # para la vista, en memoria

    def test_two_fingers_moving_up_scroll(self) -> None:
        eventos: queue.Queue = queue.Queue()
        desplazamientos: list[int] = []
        alturas = iter([0.0] * 4 + [-0.02 * i for i in range(1, 30)])

        class ModeloQueSube(_ModeloFalso):
            def detect_for_video(self, imagen, ms):
                d = next(alturas, -0.6)
                self.puntos = [(x, y + d) for x, y in mano(("indice", "medio"))]
                return super().detect_for_video(imagen, ms)

        detector = g.DetectorGestos(eventos, lambda: True, Path("."), crear_modelo=lambda: ModeloQueSube(None),
                                    abrir_camara=lambda: _CamaraFalsa(34, detector),
                                    al_desplazar=desplazamientos.append)
        reloj = iter(range(10_000))
        with patch.object(g.time, "monotonic", side_effect=lambda: next(reloj) * 0.04):
            hilo = threading.Thread(target=detector._bucle)
            hilo.start()
            hilo.join(5)
        self.assertTrue(desplazamientos)
        self.assertTrue(all(c < 0 for c in desplazamientos))

    def test_no_hand_no_events(self) -> None:
        eventos, camaras, modelo, detector = self._correr(None)
        self.assertEqual([e for e in eventos if e[0] == "gesture"], [])
        self.assertTrue(camaras[0].cerrada and modelo.cerrado)  # terminó bien, sin caerse
        self.assertEqual(detector.ultima_imagen.shape, (48, 64, 3))

    def test_paused_for_game_keeps_the_camera_closed(self) -> None:
        eventos: queue.Queue = queue.Queue()
        abiertas = []
        detector = g.DetectorGestos(eventos, lambda: False, Path("."), crear_modelo=lambda: _ModeloFalso(None),
                                    abrir_camara=lambda: abiertas.append(1))
        hilo = threading.Thread(target=detector._bucle)
        hilo.start()
        threading.Timer(0.3, detector.stop).start()
        hilo.join(5)
        self.assertEqual(abiertas, [])

    def test_camera_that_does_not_open_reports_an_error(self) -> None:
        eventos: queue.Queue = queue.Queue()
        camara = Mock()
        camara.isOpened.return_value = False
        detector = g.DetectorGestos(eventos, lambda: True, Path("."), crear_modelo=lambda: _ModeloFalso(None),
                                    abrir_camara=lambda: camara)
        detector.run()
        evento = eventos.get_nowait()
        self.assertEqual(evento[0], "gestures_error")
        self.assertIn("cámara", evento[1])


class PhraseTests(unittest.TestCase):
    def test_phrases(self) -> None:
        casos = {
            "activa los gestos": "on", "Jarvis, activa el modo gestos": "on", "usa los gestos con la mano": "on",
            "desactiva los gestos": "off", "apaga la cámara de gestos": "off",
            "muéstrame la cámara": "vista", "muéstrame la cámara de gestos": "vista",
            "activa la escucha continua": None, "muestra la tele": None, "pon la tele": None,
        }
        for texto, esperado in casos.items():
            with self.subTest(texto=texto):
                self.assertEqual(detectar_comando_gestos(texto), esperado)


def _host(**estado) -> Mock:
    host = Mock()
    host._closing = False
    host.gesture_detector = None
    host._gesture = None
    host._gestures_paused_for_game = False
    host._PREFERENCIA_GESTOS = JarvisApp._PREFERENCIA_GESTOS
    host._gestures_active = lambda: JarvisApp._gestures_active(host)
    host._start_gestures = lambda: JarvisApp._start_gestures(host)
    host._stop_gestures = lambda: JarvisApp._stop_gestures(host)
    for clave, valor in estado.items():
        setattr(host, clave, valor)
    return host


class AppTests(unittest.TestCase):
    @patch("jarvis_gestos.DetectorGestos")
    @patch("jarvis_gestos.que_falta", return_value=None)
    def test_toggle_on_and_off(self, _falta: Mock, detector_clase: Mock) -> None:
        host = _host()
        detector_clase.return_value.is_alive.return_value = True
        with patch("jarvis_core.guardar_preferencia") as guardar, patch("jarvis_core.guardar_turno"):
            JarvisApp._execute_gestures_toggle(host, True, "activa los gestos")
            detector_clase.return_value.start.assert_called_once()
            guardar.assert_called_with("gestos", "1")
            self.assertIn("Gestos activados", host._add_message.call_args.args[1])
            JarvisApp._execute_gestures_toggle(host, False, "desactiva los gestos")
        detector_clase.return_value.stop.assert_called_once()
        self.assertIsNone(host.gesture_detector)
        guardar.assert_called_with("gestos", "0")

    @patch("jarvis_gestos.que_falta", return_value="falta el modelo de la mano (gestos-modelo\\hand_landmarker.task)")
    def test_missing_model_is_explained(self, _falta: Mock) -> None:
        host = _host()
        with patch("jarvis_core.guardar_preferencia"), patch("jarvis_core.guardar_turno"):
            JarvisApp._execute_gestures_toggle(host, True, "activa los gestos")
        self.assertIn("falta el modelo de la mano", host._add_message.call_args.args[1])
        self.assertIsNone(host.gesture_detector)

    def test_camera_is_not_used_while_gaming(self) -> None:
        host = _host()
        self.assertTrue(JarvisApp._can_use_camera(host))
        JarvisApp._pause_gestures_for_game(host, True)
        self.assertFalse(JarvisApp._can_use_camera(host))
        JarvisApp._pause_gestures_for_game(host, False)
        self.assertTrue(JarvisApp._can_use_camera(host))

    def test_indicator(self) -> None:
        detector = Mock()
        detector.is_alive.return_value = True
        host = _host(gesture_detector=detector, _gesture="pellizco")
        host.gesture_indicator.winfo_ismapped.return_value = True
        JarvisApp._refresh_gesture_indicator(host)
        self.assertEqual(host.gesture_indicator.configure.call_args.kwargs["text"], "✋ GESTOS · PELLIZCO")
        self.assertEqual(host.gestures_button.configure.call_args.kwargs["text"], "GESTOS ON")
        host._gestures_paused_for_game = True
        JarvisApp._refresh_gesture_indicator(host)
        self.assertEqual(host.gesture_indicator.configure.call_args.kwargs["text"], "✋ GESTOS EN PAUSA (JUEGO)")
        host.gesture_detector = None
        JarvisApp._refresh_gesture_indicator(host)
        host.gesture_indicator.pack_forget.assert_called()
        self.assertEqual(host.gestures_button.configure.call_args.kwargs["text"], "GESTOS OFF")

    def test_error_stops_and_explains(self) -> None:
        host = _host(gesture_detector=Mock())
        JarvisApp._on_gestures_error(host, "la cámara no entrega imágenes")
        self.assertIsNone(host.gesture_detector)
        self.assertIn("la cámara no entrega imágenes", host._add_message.call_args.args[1])

    def test_scroll_goes_through_the_tool_registry_without_blocking(self) -> None:
        host = _host(_gesture_actions=None)
        hecho = threading.Event()
        host.tool_registry.execute.side_effect = lambda *a, **k: hecho.set()
        host._gesture_action_worker = lambda cola: JarvisApp._gesture_action_worker(host, cola)
        JarvisApp._queue_gesture_scroll(host, -240)
        self.assertTrue(hecho.wait(2))
        host._closing = True
        args, kwargs = host.tool_registry.execute.call_args
        self.assertEqual(args, ("gesture_scroll", {"amount": -240}))
        self.assertEqual(kwargs, {"confirmed": True, "origin": "gestos"})

    def test_scroll_direction_toggle(self) -> None:
        guardado = {}
        detector = Mock()
        host = _host(gesture_detector=detector, _PREFERENCIA_SCROLL_INVERTIDO="gestos_scroll_invertido")
        host._gesture_scroll_inverted = lambda: guardado.get("gestos_scroll_invertido") == "1"
        with patch("jarvis_core.guardar_preferencia", side_effect=guardado.__setitem__):
            JarvisApp._toggle_gesture_scroll_direction(host)
        self.assertEqual(guardado, {"gestos_scroll_invertido": "1"})
        self.assertEqual(detector.desplazamiento.signo, -1.0)

    def test_successful_gesture_actions_do_not_fill_the_database(self) -> None:
        import jarvis_actions
        from jarvis_actions import EstadoAccion, ResultadoAccion

        def resultado(origen, estado=EstadoAccion.EXITO):
            return ResultadoAccion(herramienta="gesture_scroll", etiqueta="Desplazar con gestos", origen=origen,
                                   estado=estado, duracion=0.01, argumentos={"amount": -120})

        with patch.object(jarvis_actions, "guardar_accion") as guardar:
            jarvis_actions.registrar_accion(resultado("gestos"))
            guardar.assert_not_called()
            jarvis_actions.registrar_accion(resultado("gestos", EstadoAccion.ERROR))
            guardar.assert_called_once()  # los errores sí quedan

    def test_voice_routes_gestures(self) -> None:
        host = Mock()
        host.events = queue.Queue()
        host._find_phrase_routine.return_value = None
        with patch("jarvis_core.procesar_stt"):
            JarvisApp._voice_worker(host, None, 0.0, "c", transcript="activa los gestos")
        eventos = [host.events.get_nowait() for _ in range(host.events.qsize())]
        self.assertIn(("gestures_toggle", "on", "activa los gestos"), eventos)


if __name__ == "__main__":
    unittest.main()
