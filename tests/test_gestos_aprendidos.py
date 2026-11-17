"""Fase 6: «enseñá tu gesto» (huella de la mano y vecino más cercano). Sin cámara ni archivos reales."""

import math
import queue
import random
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import jarvis_gestos as g
import jarvis_gestos_aprendidos as a
from jarvis_app import JarvisApp
from test_gestos import _CamaraFalsa, _ModeloFalso, mano


def tres_dedos(**k):
    return mano(("indice", "medio", "anular"), **k)  # «otro» para clasificar


def sin_indice(**k):
    return mano(("medio", "anular", "menique"), **k)  # «otro» también, pero otra forma


def mover(puntos, dx=0.0, dy=0.0, escala=1.0):
    cx, cy = puntos[0]
    return [(cx + (x - cx) * escala + dx, cy + (y - cy) * escala + dy) for x, y in puntos]


def temblor(puntos, rng, cuanto=0.004):
    return [(x + rng.uniform(-cuanto, cuanto), y + rng.uniform(-cuanto, cuanto)) for x, y in puntos]


def grabacion(forma, n=30, semilla=1):
    rng = random.Random(semilla)
    muestras, clases = [], []
    for i in range(n):
        puntos = temblor(mover(forma(girar=0.15 * math.sin(i)), dx=0.02 * math.cos(i), escala=1 + 0.1 * math.sin(i / 3)),
                         rng)
        muestras.append(a.huella_mano(puntos))
        clases.append(g.clasificar(puntos))
    return muestras, clases


def cuatro_pulgar_escondido(**k):
    p = list(mano(("indice", "medio", "anular", "menique"), **k))
    if not k:
        p[2], p[3], p[4] = (0.44, 0.76), (0.48, 0.7), (0.52, 0.66)  # el pulgar cruzado sobre la palma
    return p


def vulcano():
    p = list(mano(("indice", "medio", "anular", "menique")))
    for punta, x in ((6, 0.45), (7, 0.46), (8, 0.47), (10, 0.49), (11, 0.5), (12, 0.51),
                     (14, 0.61), (15, 0.62), (16, 0.63), (18, 0.65), (19, 0.66), (20, 0.67)):
        p[punta] = (x, p[punta][1])
    return p


class FourFingersTests(unittest.TestCase):
    """2026-11-17: 🖖 y cuatro dedos con el pulgar escondido salían como «mano abierta» (play/pausa)
    y por eso no se podían enseñar."""

    def test_open_hand_still_is_open_hand(self) -> None:
        self.assertEqual(g.clasificar(mano(("indice", "medio", "anular", "menique"))), "mano_abierta")

    def test_thumb_tucked_is_four_fingers(self) -> None:
        self.assertEqual(g.clasificar(cuatro_pulgar_escondido()), "cuatro_dedos")

    def test_vulcan_salute(self) -> None:
        self.assertEqual(g.clasificar(vulcano()), "vulcano")

    def test_real_measurements(self) -> None:
        """Medianas del log del 2026-11-17: cuatro dedos juntos (no es 🖖); un 🖖 con el hueco al medio."""
        juntos = {"pulgar_medio": 0.37, "pulgar_anular": 0.35, "pulgar_indice": 0.39,
                  "indice_medio": 0.24, "medio_anular": 0.22, "anular_menique": 0.30}
        with patch("jarvis_gestos.medidas_cuatro_dedos", return_value=juntos):
            self.assertFalse(g.es_vulcano(None, 1.0))
            self.assertTrue(g.pulgar_escondido(None, 1.0))
        with patch("jarvis_gestos.medidas_cuatro_dedos", return_value=dict(juntos, medio_anular=0.42)):
            self.assertTrue(g.es_vulcano(None, 1.0))

    def test_no_shortcuts_while_teaching(self) -> None:
        host = Mock()
        host._teach_window.winfo_exists.return_value = True
        JarvisApp._on_gesture_shortcut(host, "propio:aprendido:g1", False)
        host._run_custom_gesture.assert_not_called()
        host._gesture_queue.assert_not_called()

    def test_now_they_can_be_taught(self) -> None:
        nuevo = a.aprender("Cuatro", "", *grabacion(lambda **k: cuatro_pulgar_escondido()), [])
        conocidos = a.Aprendidos([nuevo])
        self.assertEqual(conocidos.reconocer(a.huella_mano(cuatro_pulgar_escondido())), "g1")
        with self.assertRaisesRegex(ValueError, "mano abierta"):
            a.aprender("Abierta", "", *grabacion(lambda **k: mano(("indice", "medio", "anular", "menique"), **k)), [])


class ThumbDownTests(unittest.TestCase):
    def test_thumb_down_with_pinky_slightly_out_is_not_call_me(self) -> None:
        """2026-11-17: sosteniendo 👎 salía «llamame» con el meñique pegado."""
        from test_gestos import llamame, pulgar

        p = pulgar(arriba=False)
        p[18], p[19], p[20] = (0.66, 0.56), (0.66, 0.5), (0.66, 0.47)  # apenas despegado
        self.assertEqual(g.dedos_estirados(p), {"menique"})  # la regla vieja lo daba por estirado
        self.assertEqual(g.clasificar(p), "pulgar_abajo")
        self.assertEqual(g.clasificar(llamame()), "llamame")  # el 🤙 de verdad sigue andando


class FingerprintTests(unittest.TestCase):
    def test_same_shape_anywhere_is_close(self) -> None:
        """Ni la posición, ni la distancia a la cámara, ni el giro cambian la huella."""
        base = a.huella_mano(tres_dedos())
        for otra in (mover(tres_dedos(), dx=0.2, dy=-0.1), mover(tres_dedos(), escala=0.6), tres_dedos(girar=0.6)):
            self.assertLess(a.distancia(base, a.huella_mano(otra)), 0.01)
        self.assertGreater(a.distancia(base, a.huella_mano(sin_indice())), a.UMBRAL_MAXIMO)

    def test_uses_the_3d_points_when_there_are(self) -> None:
        mundo = [(x * 0.2, y * 0.2, 0.01 * i) for i, (x, y) in enumerate(tres_dedos())]
        self.assertEqual(len(a.huella_mano(tres_dedos(), mundo)), 60)
        self.assertIsNone(a.huella_mano([(0.5, 0.5)] * 21))  # mano degenerada
        self.assertIsNone(a.huella_mano([(0.5, 0.5)] * 5))


class LearnTests(unittest.TestCase):
    def test_learn_and_recognize(self) -> None:
        muestras, clases = grabacion(tres_dedos)
        nuevo = a.aprender(" Tres  dedos ", "🖖", muestras, clases, [])
        self.assertEqual((nuevo["id"], nuevo["nombre"], nuevo["emoji"]), ("g1", "Tres dedos", "🖖"))
        self.assertTrue(a.UMBRAL_MINIMO <= nuevo["umbral"] <= a.UMBRAL_MAXIMO)
        conocidos = a.Aprendidos([nuevo])
        otra, _ = grabacion(tres_dedos, n=10, semilla=7)
        self.assertTrue(all(conocidos.reconocer(m) == "g1" for m in otra))
        self.assertIsNone(conocidos.reconocer(a.huella_mano(sin_indice())))
        self.assertIsNone(conocidos.reconocer(a.huella_mano(mano(()))))  # puño

    def test_two_learned_gestures(self) -> None:
        primero = a.aprender("Tres", "", *grabacion(tres_dedos), [])
        segundo = a.aprender("Sin índice", "", *grabacion(sin_indice), [primero])
        self.assertEqual((segundo["id"], segundo["emoji"]), ("g2", "✋"))
        conocidos = a.Aprendidos([primero, segundo])
        self.assertEqual(conocidos.reconocer(a.huella_mano(tres_dedos(girar=0.3))), "g1")
        self.assertEqual(conocidos.reconocer(a.huella_mano(mover(sin_indice(), dx=0.1))), "g2")

    def test_rejections(self) -> None:
        muestras, clases = grabacion(tres_dedos)
        with self.assertRaisesRegex(ValueError, "nombre"):
            a.aprender("  ", "", muestras, clases, [])
        with self.assertRaisesRegex(ValueError, "Casi no vi la mano"):
            a.aprender("X", "", muestras[:5], clases[:5], [])
        with self.assertRaisesRegex(ValueError, "índice"):  # una forma que Jarvis ya usa
            a.aprender("X", "", *grabacion(lambda **k: mano(("indice",), **k)), [])
        primero = a.aprender("Tres", "", muestras, clases, [])
        with self.assertRaisesRegex(ValueError, "Ya tenés"):
            a.aprender("tres", "", muestras, clases, [primero])
        with self.assertRaisesRegex(ValueError, "Se parece mucho a «Tres»"):
            a.aprender("Otra", "", *grabacion(tres_dedos, semilla=3), [primero])
        llenos = [dict(primero, id=f"g{i}", nombre=f"n{i}") for i in range(a.MAXIMO_GESTOS)]
        with self.assertRaisesRegex(ValueError, "borrá alguno"):
            a.aprender("Nueva", "", *grabacion(sin_indice), llenos)

    def test_keeps_at_most_max_samples(self) -> None:
        nuevo = a.aprender("Tres", "", *grabacion(tres_dedos, n=90), [])
        self.assertEqual(len(nuevo["muestras"]), a.MAXIMO_MUESTRAS)

    def test_save_and_load(self) -> None:
        nuevo = a.aprender("Tres", "", *grabacion(tres_dedos), [])
        with tempfile.TemporaryDirectory() as carpeta:
            self.assertEqual(a.cargar(Path(carpeta)), [])
            a.guardar(Path(carpeta), [nuevo])
            self.assertEqual(a.cargar(Path(carpeta)), [nuevo])
            (Path(carpeta) / a.ARCHIVO).write_text("{roto", encoding="utf-8")
            self.assertEqual(a.cargar(Path(carpeta)), [])


class DetectorTests(unittest.TestCase):
    def _detector(self, puntos, cuadros):
        eventos = queue.Queue()
        detector = g.DetectorGestos(eventos, lambda: True, Path("."), al_atajo=Mock(), al_apuntar=Mock(),
                                    monitor=Mock(x=0, y=0, ancho=1920, alto=1080),
                                    crear_modelo=lambda: _ModeloFalso(puntos),
                                    abrir_camara=lambda: _CamaraFalsa(cuadros, detector))
        return detector, eventos

    def _correr(self, detector, eventos):
        hilo = threading.Thread(target=detector._bucle)
        hilo.start()
        hilo.join(5)
        return [eventos.get_nowait() for _ in range(eventos.qsize())]

    def test_recognized_as_its_own_gesture(self) -> None:
        detector, eventos = self._detector(tres_dedos(), 8)
        detector.aprendidos = a.Aprendidos([a.aprender("Tres", "", *grabacion(tres_dedos), [])])
        publicados = self._correr(detector, eventos)
        self.assertIn(("gesture", "aprendido:g1"), publicados)

    def test_core_gestures_are_never_replaced(self) -> None:
        """Un gesto enseñado nunca tapa el puntero (el índice) aunque se parezca."""
        indice = lambda **k: mano(("indice",), **k)  # noqa: E731
        muestras, _ = grabacion(indice)
        enseñado = a.aprender("Truco", "", muestras, ["otro"] * len(muestras), [])  # forzado
        detector, eventos = self._detector(indice(), 8)
        detector.aprendidos = a.Aprendidos([enseñado])
        publicados = self._correr(detector, eventos)
        self.assertIn(("gesture", "indice"), publicados)
        self.assertNotIn(("gesture", "aprendido:g1"), publicados)

    def test_recording_publishes_samples_and_does_nothing_else(self) -> None:
        detector, eventos = self._detector(tres_dedos(), 6)
        with patch("jarvis_gestos.time.monotonic", side_effect=[0.0] * 3 + [0.1 * i for i in range(400)]):
            detector._grabacion = {"hasta": 0.25, "muestras": [], "clases": []}
            publicados = self._correr(detector, eventos)
        grabados = [e for e in publicados if e[0] == "gesture_recorded"]
        self.assertEqual(len(grabados), 1)
        self.assertGreaterEqual(len(grabados[0][1]), 1)
        self.assertEqual(set(grabados[0][2]), {"otro"})
        detector.al_atajo.assert_not_called()

    def test_learned_gesture_held_fires_its_action(self) -> None:
        atajos = g.Atajos()
        acciones = []
        for i in range(20):
            acciones += atajos.actualizar("aprendido:g1", tres_dedos(), i * 0.05)
        self.assertEqual(acciones, [("propio:aprendido:g1", False)])


def _host(carpeta: Path) -> Mock:
    host = Mock()
    host.gesture_detector = None
    host._PREFERENCIA_PROPIOS = JarvisApp._PREFERENCIA_PROPIOS
    host.TIPOS_PROPIOS = JarvisApp.TIPOS_PROPIOS
    host._learned_cache = None
    for nombre in ("_learned_gestures", "_save_learned_gestures", "_custom_gestures", "_gesture_label",
                   "_save_custom_gesture", "_on_gesture_recorded"):
        setattr(host, nombre, getattr(JarvisApp, nombre).__get__(host))
    return host


class AppTests(unittest.TestCase):
    def test_recorded_gesture_is_saved_and_opens_the_assignment(self) -> None:
        with tempfile.TemporaryDirectory() as carpeta, patch("jarvis_core.APP_DIR", Path(carpeta)):
            host = _host(Path(carpeta))
            host.gesture_detector = Mock()
            ventana = Mock()
            host._teach_state = {"ventana": ventana, "estado": Mock(), "nombre": Mock(get=lambda: "Tres"),
                                 "emoji": Mock(get=lambda: "🖖"), "grabando": True}
            JarvisApp._on_gesture_recorded(host, *grabacion(tres_dedos))
            ventana.destroy.assert_called_once()
            host._assign_custom_gesture.assert_called_once_with("aprendido:g1")
            self.assertEqual([x["nombre"] for x in a.cargar(Path(carpeta))], ["Tres"])
            self.assertEqual(host.gesture_detector.aprendidos.reconocer(a.huella_mano(tres_dedos())), "g1")
            self.assertEqual(host._gesture_label("aprendido:g1"), ("🖖", "Tres"))
            self.assertEqual(host._gesture_label("cuernos"), ("🤘", "cuernos"))

    def test_bad_recording_shows_why(self) -> None:
        with tempfile.TemporaryDirectory() as carpeta, patch("jarvis_core.APP_DIR", Path(carpeta)):
            host = _host(Path(carpeta))
            estado = Mock()
            host._teach_state = {"ventana": Mock(), "estado": estado, "nombre": Mock(get=lambda: "Tres"),
                                 "emoji": Mock(get=lambda: ""), "grabando": True}
            JarvisApp._on_gesture_recorded(host, [], [])
            self.assertIn("Casi no vi la mano", estado.configure.call_args.kwargs["text"])
            host._assign_custom_gesture.assert_not_called()
            self.assertEqual(a.cargar(Path(carpeta)), [])

    def test_assignments_accept_learned_gestures(self) -> None:
        with tempfile.TemporaryDirectory() as carpeta, patch("jarvis_core.APP_DIR", Path(carpeta)):
            a.guardar(Path(carpeta), [a.aprender("Tres", "", *grabacion(tres_dedos), [])])
            host = _host(Path(carpeta))
            datos = ('{"aprendido:g1": {"tipo": "frase", "valor": "pausa"}, '
                     '"aprendido:g9": {"tipo": "frase", "valor": "x"}}')
            with patch("jarvis_core.obtener_preferencia", return_value=datos):
                self.assertEqual(host._custom_gestures(), {"aprendido:g1": {"tipo": "frase", "valor": "pausa"}})


if __name__ == "__main__":
    unittest.main()
