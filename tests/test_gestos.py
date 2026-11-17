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
            "pellizco": mano((), pellizco=True),
            "ok": mano(("medio", "anular", "menique"), pellizco=True),  # 👌 (fase 5)
            "cuernos": mano(("indice", "menique")),  # 🤘 (fase 5)
            "otro": mano(("medio", "anular")),
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
        self.assertGreaterEqual(len(envios), 8)  # continuo: ~20 tandas por segundo, no 5 a tirones
        self.assertLessEqual(len(envios), 20)  # pero en tandas, no una acción por cuadro
        total = sum(envios)
        self.assertLess(total, -600)  # ~0,4 de la imagen ≈ unas 8 muescas
        bajando = self._recorrido(list(reversed(subiendo)))
        self.assertTrue(all(c > 0 for c in bajando))
        self.assertTrue(all(c > 0 for c in self._recorrido(subiendo, invertir=True)))

    def test_slow_movement_still_scrolls(self) -> None:
        """2026-11-16: con la zona muerta por cuadro, mover la mano despacio no hacía nada."""
        lento = [0.7 - i * 0.0025 for i in range(40)]  # ~6 % de la imagen por segundo
        envios = self._recorrido(lento)
        self.assertTrue(envios)
        self.assertLess(sum(envios), -150)

    def test_odd_frame_does_not_cut_the_scroll(self) -> None:
        """Un cuadro borroso (otro gesto o sin mano) con «dos dedos» estable no corta el scroll."""
        eventos: queue.Queue = queue.Queue()
        envios: list[int] = []
        detector = g.DetectorGestos(eventos, lambda: True, Path("."), al_desplazar=envios.append)
        alturas = [0.8 - i * 0.02 for i in range(20)]
        reloj = iter(i * 0.04 for i in range(100))
        with patch.object(g.time, "monotonic", side_effect=lambda: next(reloj)):
            for i, y in enumerate(alturas):
                puntos = [(x, py + (y - 0.35)) for x, py in mano(("indice", "medio"))]
                raro = i in (7, 12)
                detector._desplazar("dos_dedos", "otro" if raro else "dos_dedos", None if i == 12 else puntos)
        self.assertEqual(detector.desplazamiento.salteados, 2)
        self.assertEqual(detector.desplazamiento.tandas, len(envios))
        self.assertLess(sum(envios), -600)  # siguió sumando de punta a punta

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


MONITOR_1 = SimpleNamespace(numero=1, x=0, y=0, ancho=1920, alto=1080)


def apuntando(x=0.5, y=0.45, pellizco=False):
    """Índice estirado con el nudillo (punto 5) en (x, y); con ``pellizco``, el pulgar toca la punta."""
    base = mano(("indice",))
    dx, dy = x - base[5][0], y - base[5][1]
    puntos = [(px + dx, py + dy) for px, py in base]
    if pellizco:
        puntos[4] = (puntos[8][0] - 0.01, puntos[8][1])
    return puntos


class PointerTests(unittest.TestCase):
    """Fase 3: índice → puntero; pellizco corto → clic; pellizco sostenido → arrastre."""

    def test_screen_mapping(self) -> None:
        izquierda, arriba, derecha, abajo = g.ZONA_PUNTERO
        self.assertEqual(g.a_pantalla(izquierda, arriba, MONITOR_1), (0, 0))
        self.assertEqual(g.a_pantalla(derecha, abajo, MONITOR_1), (1919, 1079))
        self.assertEqual(g.a_pantalla(0.0, 1.0, MONITOR_1), (0, 1079))  # fuera de la zona: al borde
        monitor_3 = SimpleNamespace(numero=3, x=1920, y=-200, ancho=3840, alto=2160)
        x, y = g.a_pantalla((izquierda + derecha) / 2, (arriba + abajo) / 2, monitor_3)
        self.assertAlmostEqual(x, 3840, delta=1)  # el centro de la zona, al centro del monitor 3
        self.assertAlmostEqual(y, 880, delta=1)

    def test_small_zone_reaches_the_edges(self) -> None:
        """2026-11-16: costaba llegar a los bordes; la zona es ahora ~30 % del ancho de la imagen."""
        izquierda, arriba, derecha, abajo = g.ZONA_PUNTERO
        self.assertLessEqual(derecha - izquierda, 0.32)
        self.assertLessEqual(abajo - arriba, 0.30)

    def test_trembling_fingertip_does_not_move_the_pointer(self) -> None:
        """Sigue el centro de la palma: el temblor de la punta del índice no lo mueve."""
        cuadros = []
        for i in range(30):
            puntos = apuntando()
            puntos[8] = (puntos[8][0] + (0.01 if i % 2 else -0.01), puntos[8][1])
            cuadros.append(("indice", puntos))
        acciones, _ = self._correr(cuadros)
        self.assertEqual(len([a for a in acciones if a[0] == "mover"]), 1)  # solo la posición inicial

    def test_pointer_freezes_while_the_thumb_approaches(self) -> None:
        acercando = []
        for i in range(8):
            puntos = apuntando(0.5 + i * 0.01)
            # Pulgar a 0,12 de la punta del índice (relación 0,40 con una mano de 0,30): casi tocando
            # (por debajo de ACERCA = 0,45) pero todavía no es pellizco (por encima de CIERRA = 0,35).
            puntos[4] = (puntos[8][0] - 0.12, puntos[8][1])
            acercando.append(("indice", puntos))
        acciones, p = self._correr([("indice", apuntando())] * 6 + acercando)
        movimientos = [a for a in acciones if a[0] == "mover"]
        self.assertEqual(len(movimientos), 1)  # la mano se corrió al acercar el pulgar: no se movió
        self.assertFalse(p._pellizco)  # y no fue un pellizco

    @staticmethod
    def _mundo(separacion_cm: float):
        """Puntos 3D en metros: solo importan el pulgar (4) y la punta del índice (8)."""
        m = [(0.0, 0.0, 0.0)] * 21
        m[8] = (0.0, -0.08, 0.0)
        m[4] = (0.0, -0.08, separacion_cm / 100)  # separados en profundidad
        return m

    def _con_mundo(self, cuadros, separacion_cm_por_cuadro):
        p = g.Puntero(MONITOR_1)
        acciones = []
        for i, ((estable, puntos), cm) in enumerate(zip(cuadros, separacion_cm_por_cuadro)):
            acciones += p.actualizar(estable, puntos, i * 0.04, self._mundo(cm))
        return acciones, p

    def test_overlap_in_the_image_but_apart_in_depth_is_not_a_pinch(self) -> None:
        """2026-11-16: clics sin pellizcar; en la imagen el pulgar quedaba «encima» del índice."""
        cuadros = [("indice", apuntando())] * 6 + [("indice", apuntando(pellizco=True))] * 4 + \
                  [("indice", apuntando())] * 4
        acciones, _ = self._con_mundo(cuadros, [8.0] * 14)  # en 3D, siempre a 8 cm
        self.assertNotIn("click", [a[0] for a in acciones])

    def test_real_pinch_in_3d_clicks(self) -> None:
        cuadros = [("indice", apuntando())] * 6 + [("indice", apuntando(pellizco=True))] * 4 + \
                  [("indice", apuntando())] * 4
        separacion = [8.0] * 6 + [1.5] * 4 + [8.0] * 4
        with self.assertLogs(level="INFO") as registro:
            acciones, _ = self._con_mundo(cuadros, separacion)
        self.assertEqual([a[0] for a in acciones].count("click"), 1)
        self.assertTrue(any("Clic con gestos" in linea and "1.5 cm" in linea for linea in registro.output))

    @staticmethod
    def _ele(puntos):
        """Apuntando con el índice y el pulgar abierto hacia el costado (L)."""
        p = list(puntos)
        base = p[2]
        p[3], p[4] = (base[0] - 0.08, base[1] - 0.01), (base[0] - 0.18, base[1] - 0.02)
        return p

    def test_l_shape_is_its_own_gesture(self) -> None:
        self.assertEqual(g.clasificar(self._ele(apuntando())), "ele")
        self.assertEqual(g.clasificar(apuntando()), "indice")  # el pulgar al costado de siempre, no
        self.assertEqual(g.clasificar(mano(("indice",))), "indice")

    def test_l_is_right_click_once(self) -> None:
        """2026-11-17: faltaba el clic derecho (tocar el medio con el pulgar salía como pellizco)."""
        p = g.Puntero(MONITOR_1)
        acciones = []
        cuadros = ([("indice", apuntando())] * 6 + [("ele", self._ele(apuntando()))] * 10
                   + [("indice", apuntando())] * 3 + [("ele", self._ele(apuntando()))] * 3)
        for i, (estable, puntos) in enumerate(cuadros):
            acciones += p.actualizar(estable, puntos, i * 0.04)
        botones = [a for a in acciones if a[0] != "mover"]
        self.assertEqual([a[0] for a in botones], ["right_click", "right_click"])  # una por cada L
        self.assertTrue(p.armado)  # la L no suelta el puntero

    def test_l_never_arms_the_pointer(self) -> None:
        self.assertEqual(g.Puntero(MONITOR_1).actualizar("ele", self._ele(apuntando()), 0.0), [])

    def test_right_click_button(self) -> None:
        import jarvis_screen_actions as acciones

        user32 = Mock()
        with patch("jarvis_screen_actions.mover_rapido"), patch("jarvis_screen_actions._enviar_raton") as enviar:
            self.assertEqual(acciones.boton_raton("right_click", 10, 20, user32=user32, pausa=0), "Hice clic derecho.")
        self.assertEqual([c.args[1] for c in enviar.call_args_list], [0x0008, 0x0010])

    def test_too_short_or_moving_pinch_is_not_a_click(self) -> None:
        # Dos cuadros de ruido: se detecta y se suelta enseguida (menos de CLIC_MINIMO).
        cuadros = [("indice", apuntando())] * 6 + [("indice", apuntando(pellizco=True))] * 2 + \
                  [("indice", apuntando())] * 2
        p = g.Puntero(MONITOR_1)
        acciones = []
        for i, (estable, puntos) in enumerate(cuadros):
            acciones += p.actualizar(estable, puntos, i * 0.02)  # cuadros más seguidos: ~40 ms en total
        self.assertNotIn("click", [a[0] for a in acciones])
        self.assertEqual(p.descartados, 1)
        # La mano cruzando la imagen mientras el pulgar «toca» el índice: no es un clic.
        moviendo = [("indice", apuntando())] * 6 + \
                   [("indice", apuntando(0.4 + i * 0.05, pellizco=True)) for i in range(4)] + \
                   [("indice", apuntando(0.6))] * 4
        acciones, p = self._correr(moviendo)
        self.assertNotIn("click", [a[0] for a in acciones])
        self.assertEqual(p.descartados, 1)

    def test_still_hand_with_camera_noise_does_not_tremble(self) -> None:
        """2026-11-16: con la mano quieta, ±1 píxel de cámara (≈10 px en pantalla) hacía temblar el puntero."""
        import random

        azar = random.Random(7)
        ruido = 1 / 640  # un píxel de la cámara
        # x = 0,42 deja el centro de la palma cerca del centro de la zona (y del monitor).
        quieta = [("indice", apuntando(0.42 + azar.uniform(-ruido, ruido), 0.45 + azar.uniform(-ruido, ruido)))
                  for _ in range(60)]
        acciones, _ = self._correr(quieta)
        movimientos = [a for a in acciones if a[0] == "mover"]
        self.assertLessEqual(len(movimientos), 2)  # la posición inicial y, a lo sumo, un ajuste
        # Pero moviendo la mano de verdad, el puntero la sigue sin atrasarse.
        moviendo = quieta[:10] + [("indice", apuntando(0.42 + i * 0.008, 0.45)) for i in range(15)]
        acciones, _ = self._correr(moviendo)
        xs = [a[1] for a in acciones if a[0] == "mover"]
        recorrido_mano = 14 * 0.008 / (g.ZONA_PUNTERO[2] - g.ZONA_PUNTERO[0]) * 1919
        self.assertGreater(xs[-1] - xs[0], recorrido_mano * 0.8)

    def test_tucked_thumb_does_not_freeze_the_pointer(self) -> None:
        """2026-11-16: con ACERCA = 0,60, el pulgar recogido al apuntar congelaba el puntero solo."""
        cuadros = []
        for i in range(10):
            puntos = apuntando(0.4 + i * 0.02)
            puntos[4] = (puntos[8][0] - 0.15, puntos[8][1])  # relación 0,50: recogido, no acercándose
            cuadros.append(("indice", puntos))
        acciones, _ = self._correr(cuadros)
        self.assertGreater(len([a for a in acciones if a[0] == "mover"]), 5)

    def test_frame_without_hand_does_not_release_or_jump(self) -> None:
        """2026-11-16: un cuadro sin mano soltaba el puntero; se frenaba y al siguiente saltaba."""
        def maximo_paso(cuadros):
            acciones, p = self._correr(cuadros)
            xs = [a[1] for a in acciones if a[0] == "mover"]
            return max(b - a for a, b in zip(xs, xs[1:])), p

        normal = [("indice", apuntando(0.45 + i * 0.005)) for i in range(10)]
        con_hueco = list(normal)
        con_hueco[5] = ("indice", None)  # la cámara perdió la mano un cuadro
        paso_normal, _ = maximo_paso(normal)
        paso_hueco, p = maximo_paso(con_hueco)
        self.assertTrue(p.armado)
        self.assertEqual(p._sin_mano, 1)
        # Al volver la mano recupera el movimiento de dos cuadros, nada más (antes: se reiniciaba
        # el filtro y el puntero arrancaba de cero, un salto).
        self.assertLessEqual(paso_hueco, 2 * paso_normal + 5)

    def test_other_gesture_keeps_it_but_fist_releases(self) -> None:
        p = self._correr([("indice", apuntando())] * 6)[1]
        p.actualizar("otro", apuntando(), 1.0)  # el dedo medio un poco abierto un momento
        self.assertTrue(p.armado)
        p.actualizar(None, None, 1.1)  # la mano se fue de verdad (gesto estable sin mano)
        self.assertFalse(p.armado)
        p = self._correr([("indice", apuntando())] * 6)[1]
        p.actualizar("puno", mano(()), 1.0)
        self.assertFalse(p.armado)
        self.assertEqual(g.Puntero(MONITOR_1).actualizar("otro", apuntando(), 0.0), [])  # «otro» no arma

    def test_jitter_is_measured_in_the_log(self) -> None:
        p = g.Puntero(MONITOR_1)
        self._correr([("indice", apuntando(0.4 + (i % 3) * 0.002)) for i in range(50)], puntero=p)
        with self.assertLogs(level="INFO") as registro:
            p.soltar()
        self.assertIn("salto por cuadro", registro.output[0])

    def test_one_euro_filter(self) -> None:
        f = g.FiltroUnEuro()
        self.assertEqual(f(100.0, 0.0), 100.0)
        quieto = [f(100.0 + (3 if i % 2 else -3), i * 0.04) for i in range(1, 30)]
        self.assertLess(max(quieto) - min(quieto), 3)  # el temblor de ±3 px casi no se ve
        rapido = g.FiltroUnEuro()
        rapido(0.0, 0.0)
        valores = [rapido(i * 80.0, i * 0.04) for i in range(1, 12)]
        self.assertGreater(valores[-1], 880 * 0.75)  # moviéndose rápido sigue de cerca

    def _correr(self, cuadros, inicio=0.0, paso=0.04, puntero=None):
        p = puntero or g.Puntero(MONITOR_1)
        acciones = []
        for i, (estable, puntos) in enumerate(cuadros):
            acciones += p.actualizar(estable, puntos, inicio + i * paso)
        return acciones, p

    def test_index_moves_the_pointer(self) -> None:
        acciones, p = self._correr([("indice", apuntando(0.3 + i * 0.02)) for i in range(10)])
        movimientos = [a for a in acciones if a[0] == "mover"]
        self.assertTrue(p.armado)
        self.assertGreater(len(movimientos), 5)
        self.assertLess(movimientos[0][1], movimientos[-1][1])  # mano a la derecha → puntero a la derecha
        self.assertEqual({a[0] for a in acciones}, {"mover"})

    def test_short_pinch_clicks_where_it_was_pointing(self) -> None:
        cuadros = [("indice", apuntando())] * 6 + [("indice", apuntando(pellizco=True))] * 4 + \
                  [("indice", apuntando())] * 4
        acciones, p = self._correr(cuadros)
        clics = [a for a in acciones if a[0] == "click"]
        self.assertEqual(len(clics), 1)
        ultimo_antes = [a for a in acciones[:acciones.index(clics[0])] if a[0] == "mover"][-1]
        self.assertEqual(clics[0][1:], ultimo_antes[1:])
        self.assertNotIn("down", {a[0] for a in acciones})
        self.assertFalse(p.arrastrando)

    def test_held_pinch_drags_and_releases(self) -> None:
        cuadros = ([("indice", apuntando())] * 6
                   + [("pellizco", apuntando(0.5 + i * 0.01, pellizco=True)) for i in range(20)]
                   + [("indice", apuntando(0.7))] * 4)
        acciones, p = self._correr(cuadros)
        tipos = [a[0] for a in acciones]
        self.assertIn("down", tipos)
        self.assertIn("up", tipos)
        self.assertLess(tipos.index("down"), tipos.index("up"))
        self.assertNotIn("click", tipos)
        entre = tipos[tipos.index("down"):tipos.index("up")]
        self.assertIn("mover", entre)  # arrastra con la mano
        self.assertFalse(p.arrastrando)

    def test_two_quick_pinches_are_a_double_click(self) -> None:
        abierta, cerrada = ("indice", apuntando()), ("indice", apuntando(pellizco=True))
        cuadros = [abierta] * 6 + [cerrada] * 3 + [abierta] * 3 + [cerrada] * 3 + [abierta] * 3
        acciones, _ = self._correr(cuadros)
        clics = [a for a in acciones if a[0] == "click"]
        self.assertEqual(len(clics), 2)
        self.assertEqual(clics[0][1:], clics[1][1:])  # en el mismo punto: Windows lo toma como doble clic

    def test_other_gesture_releases_the_button(self) -> None:
        cuadros = [("indice", apuntando())] * 6 + [("pellizco", apuntando(pellizco=True))] * 20
        acciones, p = self._correr(cuadros)
        self.assertTrue(p.arrastrando)
        soltar = p.actualizar("puno", mano(()), 10.0)
        self.assertEqual([a[0] for a in soltar], ["up"])
        self.assertFalse(p.armado)
        p2 = self._correr(cuadros)[1]
        self.assertEqual([a[0] for a in p2.soltar()], ["up"])  # al apagar los gestos

    def test_pinch_without_pointing_first_does_nothing(self) -> None:
        acciones, p = self._correr([("pellizco", apuntando(pellizco=True))] * 20)
        self.assertEqual(acciones, [])
        self.assertFalse(p.armado)


def pulgar(arriba=True):
    """Puño con el pulgar estirado hacia arriba o hacia abajo."""
    p = list(mano(()))
    if arriba:
        p[2], p[3], p[4] = (0.4, 0.6), (0.38, 0.48), (0.37, 0.38)
    else:
        p[2], p[3], p[4] = (0.4, 0.75), (0.38, 0.85), (0.37, 0.95)
    return p


def abierta(x=0.5, y=0.45):
    base = mano(("indice", "medio", "anular", "menique"))
    dx, dy = x - g.centro_palma(base)[0], y - g.centro_palma(base)[1]
    return [(px + dx, py + dy) for px, py in base]


class MonitorEdgeTests(unittest.TestCase):
    """2026-11-17: el puntero pasa a otro monitor empujando el borde, sin decir «puntero en el monitor 2».
    Los monitores del usuario: el 1 (2560×1440, principal) y el 2 (1920×1080) a su izquierda."""

    M1 = SimpleNamespace(numero=1, x=0, y=0, ancho=2560, alto=1440)
    M2 = SimpleNamespace(numero=2, x=-1920, y=0, ancho=1920, alto=1080)

    def test_neighbours_share_an_edge(self) -> None:
        monitores = [self.M1, self.M2]
        self.assertIs(g.monitor_vecino(self.M1, monitores, "izquierda"), self.M2)
        self.assertIs(g.monitor_vecino(self.M2, monitores, "derecha"), self.M1)
        self.assertIsNone(g.monitor_vecino(self.M1, monitores, "derecha"))
        self.assertIsNone(g.monitor_vecino(self.M1, monitores, "arriba"))
        lejos = SimpleNamespace(numero=3, x=5000, y=0, ancho=1920, alto=1080)
        self.assertIsNone(g.monitor_vecino(self.M1, [self.M1, lejos], "derecha"))  # no se tocan

    def _puntero(self):
        p = g.Puntero(self.M1)
        p.listar_monitores = lambda: [self.M1, self.M2]
        return p

    def _mano_en(self, palma_x: float):
        """Índice estirado con el centro de la palma en ``palma_x`` (de la imagen)."""
        base = apuntando()
        dx = palma_x - g.centro_palma(base)[0]
        return [(x + dx, y) for x, y in base]

    def test_pushing_the_left_edge_jumps_to_the_left_monitor(self) -> None:
        p = self._puntero()
        izquierda = g.ZONA_PUNTERO[0]
        acciones = p.actualizar("indice", self._mano_en(0.5), 0.0)
        # Solo hacia donde hay otro monitor se achica la zona (para tener dónde empujar).
        self.assertEqual(p._zona_con_empuje()[0], izquierda + p.EMPUJE_MARGEN)
        self.assertEqual(p._zona_con_empuje()[2], g.ZONA_PUNTERO[2])
        # Un toque al borde no alcanza.
        afuera = self._mano_en(izquierda + p.EMPUJE_MARGEN / 2)
        for i in range(3):
            acciones += p.actualizar("indice", afuera, 0.1 + i * 0.1)
        self.assertNotIn("monitor", [a[0] for a in acciones])
        self.assertIs(p.monitor, self.M1)
        # Sostenido, salta al monitor 2 y sigue en sus coordenadas.
        acciones = []
        for i in range(3):
            acciones += p.actualizar("indice", afuera, 0.4 + i * 0.04)
        self.assertIn(("monitor", 2), acciones)
        self.assertIs(p.monitor, self.M2)
        ultimo = [a for a in acciones if a[0] == "mover"][-1]
        self.assertTrue(self.M2.x <= ultimo[1] < self.M2.x + 200)  # cerca de su borde izquierdo
        # Seguir empujando a la izquierda no hace nada más (no hay otro monitor ahí).
        mas = []
        for i in range(20):
            mas += p.actualizar("indice", afuera, 1.0 + i * 0.1)
        self.assertNotIn("monitor", [a[0] for a in mas])

    def test_back_to_the_right_and_not_while_pinching(self) -> None:
        p = self._puntero()
        p.actualizar("indice", self._mano_en(0.5), 0.0)
        p.monitor = self.M2
        derecha = self._mano_en(g.ZONA_PUNTERO[2] - p.EMPUJE_MARGEN / 2)
        acciones = []
        for i in range(12):
            acciones += p.actualizar("indice", derecha, 2.0 + i * 0.05)
        self.assertIn(("monitor", 1), acciones)
        # Con el pellizco (un clic en el borde) no se cambia de monitor.
        p2 = self._puntero()
        p2.actualizar("indice", self._mano_en(0.5), 0.0)
        p2._pellizco = True
        for i in range(12):
            p2._empujar(g.centro_palma(self._mano_en(g.ZONA_PUNTERO[0] + 0.01)), 0.1 + i * 0.1)
        self.assertIs(p2.monitor, self.M1)

    def test_single_monitor_keeps_the_whole_zone(self) -> None:
        p = g.Puntero(self.M1)
        p.listar_monitores = lambda: [self.M1]
        p.actualizar("indice", self._mano_en(0.5), 0.0)
        self.assertEqual(p._zona_con_empuje(), tuple(g.ZONA_PUNTERO))


class ShortcutTests(unittest.TestCase):
    """Fase 4: pausa con la mano quieta, deslizar, pulgares."""

    def test_thumbs(self) -> None:
        self.assertEqual(g.clasificar(pulgar(True)), "pulgar_arriba")
        self.assertEqual(g.clasificar(pulgar(False)), "pulgar_abajo")
        self.assertEqual(g.clasificar(mano(())), "puno")
        self.assertEqual(g.clasificar(puno_con_pulgar_encima()), "puno")

    def _correr(self, cuadros, paso=0.04):
        a = g.Atajos()
        salida = []
        for i, (estable, puntos) in enumerate(cuadros):
            salida += a.actualizar(estable, puntos, i * paso)
        return salida

    def test_still_open_hand_pauses_once(self) -> None:
        salida = self._correr([("mano_abierta", abierta())] * 60)  # 2,4 s quieta
        self.assertEqual(salida, [("pausa", False)])
        self.assertEqual(self._correr([("mano_abierta", abierta())] * 20), [])  # 0,8 s: todavía no

    def test_moving_open_hand_does_not_pause(self) -> None:
        cuadros = [("mano_abierta", abierta(0.45 + 0.03 * math.sin(i / 3))) for i in range(60)]
        self.assertNotIn(("pausa", False), self._correr(cuadros))

    def test_swipes(self) -> None:
        derecha = [("mano_abierta", abierta(0.3 + i * 0.04)) for i in range(10)]  # cruza en 0,4 s
        self.assertEqual(self._correr(derecha), [("atras", False)])
        izquierda = [("mano_abierta", abierta(0.7 - i * 0.04)) for i in range(10)]
        self.assertEqual(self._correr(izquierda), [("adelante", False)])
        lento = [("mano_abierta", abierta(0.3 + i * 0.005)) for i in range(60)]  # 2,4 s: no es deslizar
        self.assertFalse(any(a[0] in ("atras", "adelante") for a in self._correr(lento)))
        diagonal = [("mano_abierta", abierta(0.3 + i * 0.04, 0.2 + i * 0.05)) for i in range(10)]
        self.assertFalse(any(a[0] in ("atras", "adelante") for a in self._correr(diagonal)))

    def test_real_swipes_with_blur_and_late_stable_gesture(self) -> None:
        """2026-11-16: el deslizar no se detectaba nunca con la mano de verdad."""
        def correr(cuadros, paso=0.04):
            a = g.Atajos()
            salida = []
            for i, (estable, puntos, gesto) in enumerate(cuadros):
                salida += a.actualizar(estable, puntos, i * paso, gesto)
            return salida

        # Empieza antes de que el gesto sea estable (los primeros 4 cuadros, el estable es otro).
        temprano = [("indice" if i < 4 else "mano_abierta", abierta(0.3 + i * 0.03), "mano_abierta")
                    for i in range(12)]
        self.assertEqual(correr(temprano), [("atras", False)])
        # Borroso a mitad de camino: el estable pasa a «otro», un cuadro sin mano; igual cuenta.
        borroso = []
        for i in range(14):
            estable = "otro" if 5 <= i <= 9 else "mano_abierta"
            gesto = "otro" if i in (6, 7) else "mano_abierta"
            puntos = None if i == 8 else abierta(0.75 - i * 0.03)
            borroso.append((estable, puntos, gesto))
        self.assertEqual(correr(borroso), [("adelante", False)])
        # Un poco más lento (0,2 de la imagen en ~0,55 s): también.
        tranquilo = [("mano_abierta", abierta(0.4 + i * 0.0145), "mano_abierta") for i in range(15)]
        self.assertEqual(correr(tranquilo), [("atras", False)])

    def test_short_lateral_move_logs_how_far_it_went(self) -> None:
        a = g.Atajos()
        for i in range(10):
            a.actualizar("mano_abierta", abierta(0.4 + i * 0.012), i * 0.04, "mano_abierta")  # 0,11 en total
        with self.assertLogs(level="INFO") as registro:
            for i in range(10, 40):
                a.actualizar("puno", None, i * 0.04, None)
        self.assertTrue(any("Deslizar no alcanzó" in linea for linea in registro.output))

    def test_thumb_held_fires_then_repeats(self) -> None:
        salida = self._correr([("pulgar_arriba", pulgar())] * 42)  # hasta 1,64 s
        self.assertEqual(salida[0], ("pulgar_arriba", False))
        self.assertTrue(all(a == ("pulgar_arriba", True) for a in salida[1:]))
        self.assertEqual(len(salida), 3)  # a los 0,4 s y repetido a los 1,0 y 1,6 s
        self.assertEqual(self._correr([("pulgar_abajo", pulgar(False))] * 8), [])  # 0,32 s: muy corto


class ShortcutAppTests(unittest.TestCase):
    def _host(self, **estado):
        valores = {"_gesture_actions": queue.Queue(), "PASO_VOLUMEN": JarvisApp.PASO_VOLUMEN,
                   "_pending_plan_reply": None, "_pending_approval_kind": None, **estado}
        host = _host(**valores)
        host._gesture_queue = lambda: host._gesture_actions
        return host

    def test_thumbs_confirm_or_cancel(self) -> None:
        host = self._host(_pending_plan_reply=object(), _pending_approval_kind="plan")
        JarvisApp._on_gesture_shortcut(host, "pulgar_arriba", False)
        host._resolve_plan_approval.assert_called_once_with(True)
        self.assertIn("👍", host._add_message.call_args.args[1])
        host = self._host(_pending_plan_reply=object(), _pending_approval_kind="screen")
        JarvisApp._on_gesture_shortcut(host, "pulgar_abajo", False)
        host._resolve_plan_approval.assert_called_once_with(False)
        host = self._host(_pending_plan_reply=object(), _pending_approval_kind="plan")
        JarvisApp._on_gesture_shortcut(host, "pulgar_arriba", True)  # repetido: no confirma otra cosa
        host._resolve_plan_approval.assert_not_called()

    def test_shutdown_is_not_confirmed_with_a_thumb(self) -> None:
        host = self._host(_pending_plan_reply=object(), _pending_approval_kind="power")
        JarvisApp._on_gesture_shortcut(host, "pulgar_arriba", False)
        host._resolve_plan_approval.assert_not_called()
        self.assertIn("CONFIRMA CON LA VOZ", host._set_status.call_args.args[0])
        JarvisApp._on_gesture_shortcut(host, "pulgar_abajo", False)  # cancelar, sí
        host._resolve_plan_approval.assert_called_once_with(False)

    def test_actions_without_a_question(self) -> None:
        host = self._host()
        for accion in ("pulgar_arriba", "pulgar_abajo", "pausa", "atras", "adelante"):
            JarvisApp._on_gesture_shortcut(host, accion, False)
        cola = [host._gesture_actions.get_nowait() for _ in range(host._gesture_actions.qsize())]
        self.assertEqual(cola, [("volumen", 5), ("volumen", -5), ("media", "toggle"), ("browser", "back"),
                                ("browser", "forward")])

    def test_worker_runs_them_through_the_registry(self) -> None:
        host = self._host()
        host._gesture_action_worker = lambda cola: JarvisApp._gesture_action_worker(host, cola)
        llamadas = []
        hecho = threading.Event()

        def ejecutar(nombre, argumentos, **kwargs):
            llamadas.append((nombre, argumentos, kwargs["origin"]))
            if len(llamadas) == 3:
                hecho.set()
            return "Volumen al 50 %."  # lo que devuelve la herramienta (no un ResultadoAccion)

        host.tool_registry.execute.side_effect = ejecutar
        cola = queue.Queue()
        for item in (("volumen", 5), ("volumen", 5), ("media", "toggle"), ("browser", "back")):
            cola.put(item)
        host.events = queue.Queue()
        with patch("jarvis_audio_devices.obtener_volumen_sistema", return_value=40):
            hilo = threading.Thread(target=JarvisApp._gesture_action_worker, args=(host, cola))
            hilo.start()
            self.assertTrue(hecho.wait(3))
            host._closing = True
            hilo.join(3)
        self.assertEqual(host.events.get_nowait(), ("volume_bar", 50))  # la barra con el volumen nuevo
        self.assertEqual(llamadas, [("set_system_volume", {"percentage": 50}, "gestos"),  # 5 + 5 juntos
                                    ("media_control", {"action": "toggle"}, "gestos"),
                                    ("browser_history", {"action": "back"}, "gestos")])


class VolumeBarTests(unittest.TestCase):
    """2026-11-17: con el pulgar se cambiaba el volumen pero no se veía ninguna barra."""

    def test_bar_shows_the_value_and_hides(self) -> None:
        import tkinter as tk

        try:
            root = tk.Tk()
        except tk.TclError:
            self.skipTest("sin pantalla")
        root.withdraw()
        try:
            host = Mock()
            host.root = root
            host.BARRA_VOLUMEN_MS = 50
            host._TRANSPARENTE = JarvisApp._TRANSPARENTE
            host._volume_bar = None
            host._volume_bar_hide = None
            host._rounded_rect = JarvisApp._rounded_rect
            JarvisApp._show_volume_bar(host, 35)
            barra, lienzo = host._volume_bar, host._volume_bar_canvas
            self.assertEqual(lienzo.itemcget(host._volume_bar_text, "text"), "35")
            self.assertEqual(lienzo.itemcget(host._volume_bar_icon, "text"), "🔉")
            JarvisApp._show_volume_bar(host, 120)  # la misma ventana, recortado a 100
            self.assertIs(host._volume_bar, barra)
            self.assertEqual(lienzo.itemcget(host._volume_bar_text, "text"), "100")
            self.assertEqual(barra.state(), "normal")
            root.after(150, root.quit)
            root.mainloop()
            self.assertEqual(barra.state(), "withdrawn")  # se fue sola
        finally:
            root.destroy()


def llamame():
    p = list(mano(("menique",)))
    p[4] = (0.15, 0.5)  # pulgar bien afuera
    return p


def paz(x=0.5):
    """Índice y medio estirados y separados en V."""
    p = list(mano(("indice", "medio")))
    p[6], p[7], p[8] = (0.38, 0.5), (0.34, 0.42), (0.30, 0.35)
    p[10], p[11], p[12] = (0.54, 0.5), (0.58, 0.42), (0.62, 0.35)
    dx = x - 0.5
    return [(px + dx, py) for px, py in p]


class CustomGestureTests(unittest.TestCase):
    """Fase 5: 🤘 🤙 👌 ✌ asignables a una rutina, una frase o un atajo de teclado."""

    def test_new_shapes(self) -> None:
        self.assertEqual(g.clasificar(mano(("indice", "menique"))), "cuernos")
        self.assertEqual(g.clasificar(llamame()), "llamame")
        self.assertEqual(g.clasificar(mano(("menique",))), "otro")  # meñique solo, sin el pulgar
        self.assertEqual(g.clasificar(mano(("medio", "anular", "menique"), pellizco=True)), "ok")
        self.assertEqual(g.clasificar(paz()), "dos_dedos")  # ✌ es «dos dedos»…
        self.assertTrue(g.en_v(paz()))  # …pero en V
        self.assertFalse(g.en_v(mano(("indice", "medio"))))  # juntos: el scroll

    def _correr(self, cuadros, apuntando=False):
        a = g.Atajos()
        a.apuntando = apuntando
        salida, progresos = [], []
        for i, (estable, puntos) in enumerate(cuadros):
            salida += a.actualizar(estable, puntos, i * 0.04, estable)
            progresos.append(a.progreso)
        return salida, progresos

    def test_held_custom_gesture_fires_once_with_progress(self) -> None:
        salida, progresos = self._correr([("cuernos", mano(("indice", "menique")))] * 30)
        self.assertEqual(salida, [("propio:cuernos", False)])  # una sola vez, aunque siga
        fracciones = [p[1] for p in progresos if p]
        self.assertEqual(fracciones, sorted(fracciones))  # el anillo se llena
        self.assertEqual(self._correr([("ok", mano())] * 10)[0], [])  # 0,4 s: todavía no

    def test_still_v_is_peace_but_moving_v_is_scroll(self) -> None:
        self.assertEqual(self._correr([("dos_dedos", paz())] * 20)[0], [("propio:paz", False)])
        moviendo = [("dos_dedos", paz(0.4 + i * 0.02)) for i in range(20)]
        self.assertEqual(self._correr(moviendo)[0], [])
        juntos = [("dos_dedos", mano(("indice", "medio")))] * 20
        self.assertEqual(self._correr(juntos)[0], [])

    def test_not_while_pointing(self) -> None:
        salida, _ = self._correr([("ok", mano(("medio", "anular", "menique"), pellizco=True))] * 30,
                                 apuntando=True)
        self.assertEqual(salida, [])

    def test_keyboard_shortcuts(self) -> None:
        import jarvis_screen_actions as s

        self.assertEqual(s.parsear_atajo("Ctrl + Shift + T"), [0x11, 0x10, 0x54])
        self.assertEqual(s.parsear_atajo("win+d"), [0x5B, 0x44])
        for malo in ("", "ctrl", "ctrl+xyz"):
            with self.subTest(malo=malo), self.assertRaises(ValueError):
                s.parsear_atajo(malo)
        user32 = Mock()
        user32.SendInput.return_value = 4
        s.pulsar_atajo("alt+tab", user32=user32)
        cantidad, entradas, _tam = user32.SendInput.call_args.args
        self.assertEqual(cantidad, 4)  # apretar alt, tab; soltar tab, alt
        orden = [(entradas[i].u.ki.wVk, bool(entradas[i].u.ki.dwFlags & 0x2)) for i in range(4)]
        self.assertEqual(orden, [(0x12, False), (0x09, False), (0x09, True), (0x12, True)])


class CustomGestureAppTests(unittest.TestCase):
    def _host(self, asignados: dict, **estado):
        valores = {"_gesture_actions": queue.Queue(), "busy": False, "recorder": None,
                   "_pending_plan_reply": None, "TIPOS_PROPIOS": JarvisApp.TIPOS_PROPIOS,
                   "HISTORIAL_GESTOS": JarvisApp.HISTORIAL_GESTOS, **estado}
        host = _host(**valores)
        host._custom_gestures = lambda: asignados
        host._gesture_queue = lambda: host._gesture_actions
        host._describe_custom_gesture = lambda a: JarvisApp._describe_custom_gesture(host, a)
        host._gesture_history_add = lambda gesto, texto: JarvisApp._gesture_history_add(host, gesto, texto)
        host._run_custom_gesture = lambda gesto: JarvisApp._run_custom_gesture(host, gesto)
        host._gesture_history = []
        return host

    def test_keys(self) -> None:
        host = self._host({"cuernos": {"tipo": "teclas", "valor": "ctrl+w"}})
        JarvisApp._on_gesture_shortcut(host, "propio:cuernos", False)
        self.assertEqual(host._gesture_actions.get_nowait(), ("teclas", "ctrl+w"))
        self.assertIn("teclas ctrl+w", host._gesture_history[0])

    def test_routine_and_phrase(self) -> None:
        host = self._host({"llamame": {"tipo": "rutina", "valor": 7}, "ok": {"tipo": "frase", "valor": "pausa"}})
        with patch("jarvis_routines.listar_automatizaciones", return_value=[SimpleNamespace(identifier=7,
                                                                                             name="Cine")]):
            JarvisApp._on_gesture_shortcut(host, "propio:llamame", False)
        host._run_routine_from_mobile.assert_called_once_with(7, desde="con un gesto")
        self.assertIn("rutina «Cine»", host._gesture_history[0])
        JarvisApp._on_gesture_shortcut(host, "propio:ok", False)
        host._send_text_as_typed.assert_called_once_with("pausa")

    def test_unassigned_and_busy(self) -> None:
        host = self._host({})
        JarvisApp._on_gesture_shortcut(host, "propio:paz", False)
        self.assertIn("SIN ASIGNAR", host._set_status.call_args.args[0])
        host = self._host({"ok": {"tipo": "frase", "valor": "pausa"}}, busy=True)
        JarvisApp._on_gesture_shortcut(host, "propio:ok", False)
        host._send_text_as_typed.assert_not_called()
        self.assertIn("OCUPADO", host._set_status.call_args.args[0])

    def test_saved_assignments_are_validated(self) -> None:
        host = _host(_PREFERENCIA_PROPIOS="gestos_propios", TIPOS_PROPIOS=JarvisApp.TIPOS_PROPIOS)
        guardado = {"gestos_propios": '{"cuernos": {"tipo": "teclas", "valor": "ctrl+w"}, '
                                      '"pulgar_arriba": {"tipo": "teclas", "valor": "x"}, '
                                      '"ok": {"tipo": "raro", "valor": "x"}}'}
        with patch("jarvis_core.obtener_preferencia", side_effect=guardado.get):
            self.assertEqual(JarvisApp._custom_gestures(host), {"cuernos": {"tipo": "teclas", "valor": "ctrl+w"}})


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
    def __init__(self, puntos, lado="Left") -> None:  # «Left» = la mano derecha del usuario
        self.puntos = puntos
        self.lado = lado  # qué mano dice MediaPipe que es
        self.cerrado = False

    def detect_for_video(self, _imagen, _ms):
        if not self.puntos:
            return SimpleNamespace(hand_landmarks=[], handedness=[])
        return SimpleNamespace(hand_landmarks=[[SimpleNamespace(x=x, y=y) for x, y in self.puntos]],
                               handedness=[[SimpleNamespace(category_name=self.lado, score=0.95)]])

    def close(self):
        self.cerrado = True


class HandSideTests(unittest.TestCase):
    """Solo la mano elegida (la derecha por defecto)."""

    @staticmethod
    def _resultado(*lados):
        return SimpleNamespace(
            hand_landmarks=[[SimpleNamespace(x=0.5, y=0.5)] * 21 for _ in lados],
            handedness=[[SimpleNamespace(category_name=lado, score=score)] for lado, score in lados])

    def test_pick_the_chosen_hand(self) -> None:
        dos = self._resultado(("Left", 0.9), ("Right", 0.8))
        self.assertEqual(g.elegir_mano(dos, "Right"), 1)
        self.assertEqual(g.elegir_mano(dos, "Left"), 0)
        self.assertEqual(g.elegir_mano(dos, None), 0)
        self.assertIsNone(g.elegir_mano(self._resultado(("Left", 0.9)), "Right"))  # solo la izquierda
        self.assertIsNone(g.elegir_mano(self._resultado(), "Right"))
        # Dos «derechas» (un error de MediaPipe): la de más confianza.
        self.assertEqual(g.elegir_mano(self._resultado(("Right", 0.6), ("Right", 0.9)), "Right"), 1)

    def test_left_hand_is_ignored_by_the_thread(self) -> None:
        eventos: queue.Queue = queue.Queue()
        detector = g.DetectorGestos(eventos, lambda: True, Path("."),
                                    crear_modelo=lambda: _ModeloFalso(mano(("indice", "medio")), lado="Right"),
                                    abrir_camara=lambda: _CamaraFalsa(8, detector))
        hilo = threading.Thread(target=detector._bucle)
        hilo.start()
        hilo.join(5)
        publicados = [eventos.get_nowait() for _ in range(eventos.qsize())]
        self.assertEqual([e for e in publicados if e[0] == "gesture"], [])  # «Right» = la izquierda: no cuenta

    def test_hand_setting_cycles(self) -> None:
        guardado = {}
        detector = Mock()
        host = _host(gesture_detector=detector, _PREFERENCIA_MANO="gestos_mano")
        host._gesture_hand = lambda: guardado.get("gestos_mano", "derecha")
        with patch("jarvis_core.guardar_preferencia", side_effect=guardado.__setitem__):
            JarvisApp._cycle_gesture_hand(host)
            self.assertEqual(detector.mano, "Right")  # izquierda (MediaPipe la etiqueta al revés)
            JarvisApp._cycle_gesture_hand(host)
            self.assertIsNone(detector.mano)  # cualquiera
            JarvisApp._cycle_gesture_hand(host)
        self.assertEqual(detector.mano, "Left")  # derecha

    def test_sensitivity_slider_applies_live(self) -> None:
        guardado = {}
        detector = Mock()
        host = _host(gesture_detector=detector, _PREFERENCIA_SENSIBILIDAD="gestos_sensibilidad")
        with patch("jarvis_core.guardar_preferencia", side_effect=guardado.__setitem__) as guardar:
            JarvisApp._set_gesture_sensitivity(host, "10")  # tk.Scale manda el valor como texto
            self.assertEqual(detector.puntero.zona, g.ZONA_PUNTERO)  # 10: la zona de la fase 3
            JarvisApp._set_gesture_sensitivity(host, "3")
            JarvisApp._set_gesture_sensitivity(host, "3")  # el mismo valor no se vuelve a guardar
        self.assertEqual(guardar.call_count, 2)
        self.assertEqual(guardado["gestos_sensibilidad"], "3")
        self.assertEqual(detector.puntero.zona, g.zona_con_sensibilidad(3))

    def test_saved_levels(self) -> None:
        self.assertEqual(g.nivel_sensibilidad(None), g.SENSIBILIDAD_POR_DEFECTO)
        self.assertEqual(g.nivel_sensibilidad("7"), 7)
        self.assertEqual(g.nivel_sensibilidad("99"), 10)
        self.assertEqual(g.nivel_sensibilidad("0"), 1)
        self.assertEqual(g.nivel_sensibilidad("raro"), g.SENSIBILIDAD_POR_DEFECTO)
        self.assertEqual(g.nivel_sensibilidad("media"), 8)  # los tres niveles de antes
        self.assertEqual(g.nivel_sensibilidad("alta"), 10)

    def test_lower_sensitivity_moves_less(self) -> None:
        """2026-11-17: «el puntero se mueve mucho al mínimo movimiento»."""
        izquierda, arriba, derecha, abajo = g.ZONA_PUNTERO
        cx, cy = (izquierda + derecha) / 2, (arriba + abajo) / 2

        def recorrido(nivel: int) -> int:
            zona = g.zona_con_sensibilidad(nivel)
            return g.a_pantalla(cx + 0.01, cy, MONITOR_1, zona)[0] - g.a_pantalla(cx, cy, MONITOR_1, zona)[0]

        recorridos = [recorrido(n) for n in range(1, 11)]
        self.assertEqual(recorridos, sorted(recorridos))  # más sensibilidad, más se mueve
        self.assertLess(recorridos[0], recorridos[-1] / 2)  # en 1 se mueve menos de la mitad que en 10
        for nivel in range(1, 11):
            with self.subTest(nivel=nivel):
                zona = g.zona_con_sensibilidad(nivel)
                x, _y = g.a_pantalla(cx, cy, MONITOR_1, zona)
                self.assertLessEqual(abs(x - 960), 2)  # la mano de frente: al medio a lo ancho
                self.assertTrue(0.0 <= zona[0] < zona[2] <= 1.0 and 0.0 <= zona[1] < zona[3] <= 1.0)
                # 2026-11-17: con sensibilidad 1 no se llegaba al tope del monitor (la zona empezaba en
                # 0,14 y la palma no sube de ~0,30 sin que el dedo salga de la imagen).
                self.assertGreaterEqual(zona[1], g.ALCANCE_PALMA[1])
                self.assertEqual(g.a_pantalla(cx, g.ALCANCE_PALMA[1], MONITOR_1, zona)[1], 0)

    def test_right_hand_is_what_mediapipe_calls_left(self) -> None:
        """2026-11-16: con «derecha» → «Right» no seguía la mano derecha (el log: «se sigue Left»)."""
        self.assertEqual(g.MANOS["derecha"], "Left")
        self.assertEqual(g.MANOS[g.MANO_POR_DEFECTO], "Left")


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
    host._gesture_queue = lambda: JarvisApp._gesture_queue(host)
    host._gesture_hand = lambda: "derecha"
    host._gesture_sensitivity = lambda: 5
    host._learned_gestures = lambda: []  # fase 6: ninguno enseñado (no lee archivos)
    host._teach_window = None  # la ventana de «Enseñá tu gesto» cerrada
    host._gesture_label = lambda gesto: JarvisApp._gesture_label(host, gesto)
    host._gesture_progress = None
    host._merge_gesture_actions = JarvisApp._merge_gesture_actions
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

    def test_actions_are_merged_in_order(self) -> None:
        merge = JarvisApp._merge_gesture_actions
        self.assertEqual(merge([("scroll", -120), ("scroll", -60), ("mover", 1, 1), ("mover", 5, 5),
                                ("click", 5, 5), ("mover", 6, 6), ("down", 6, 6), ("up", 6, 6)]),
                         [("scroll", -180), ("mover", 5, 5), ("click", 5, 5), ("mover", 6, 6),
                          ("down", 6, 6), ("up", 6, 6)])

    def test_pointer_glides_and_clicks_through_the_registry(self) -> None:
        host = _host(_gesture_actions=None)
        hecho = threading.Event()
        host.tool_registry.execute.side_effect = lambda *a, **k: hecho.set()
        host._gesture_action_worker = lambda cola: JarvisApp._gesture_action_worker(host, cola)
        host._merge_gesture_actions = JarvisApp._merge_gesture_actions
        JarvisApp._queue_gesture_pointer(host, [("mover", 10, 20), ("click", 10, 20)])
        self.assertTrue(hecho.wait(2))
        host._closing = True
        self.assertEqual(host._set_pointer_target.call_args_list[0].args, (10, 20))  # deslizar
        self.assertEqual(host._set_pointer_target.call_args_list[1].kwargs, {"saltar": True})  # clic: directo
        self.assertEqual(host.tool_registry.execute.call_args.args,
                         ("gesture_click", {"action": "click", "x": 10, "y": 20}))

    def test_glide_step(self) -> None:
        """2026-11-16: a 24 cuadros/s el puntero iba a saltos; ahora avanza de a poco entre cuadros."""
        actual = (0.0, 0.0)
        pasos = []
        for _ in range(30):
            actual = g.deslizar(actual, (300, -120))
            pasos.append(actual)
        self.assertLess(pasos[0][0], 120)  # el primer paso no salta de golpe
        self.assertEqual(pasos[-1], (300.0, -120.0))  # y llega exacto
        tiempo_en_llegar = next(i for i, p in enumerate(pasos) if abs(p[0] - 300) < 10) * g.PASO_DESLIZAR
        self.assertLess(tiempo_en_llegar, 0.08)  # sin sentirse atrasado

    def test_glide_worker_moves_smoothly_and_stops_when_released(self) -> None:
        host = _host(_pointer_target=None)
        movidas: list[tuple[int, int]] = []

        def mover(x, y):
            movidas.append((x, y))
            if len(movidas) == 1:
                host._pointer_target = (200, 100)  # la mano se movió
            elif (x, y) == (200, 100):
                host._pointer_target = None  # se soltó el puntero
                host._closing = True

        host._pointer_target = (0, 0)
        with patch("jarvis_screen_actions.mover_rapido", side_effect=mover), patch.object(g, "PASO_DESLIZAR", 0):
            hilo = threading.Thread(target=JarvisApp._pointer_glide_worker, args=(host,))
            hilo.start()
            hilo.join(3)
        self.assertGreater(len(movidas), 4)  # varios pasos intermedios, no un salto
        self.assertEqual(movidas[-1], (200, 100))
        self.assertTrue(all(movidas[i][0] <= movidas[i + 1][0] for i in range(len(movidas) - 1)))

    @patch("jarvis_vision.listar_monitores")
    def test_pointer_monitor_by_voice(self, monitores: Mock) -> None:
        monitores.return_value = [SimpleNamespace(numero=1, descripcion="Monitor 1"),
                                  SimpleNamespace(numero=3, descripcion="Monitor 3 (3840×2160)")]
        detector = Mock()
        host = _host(gesture_detector=detector, _PREFERENCIA_MONITOR="gestos_monitor")
        with patch("jarvis_vision.elegir_monitor", side_effect=lambda ms, n: next(m for m in ms if m.numero == n)), \
                patch("jarvis_core.guardar_preferencia") as guardar, patch("jarvis_core.guardar_turno"):
            JarvisApp._set_gesture_monitor(host, 3, "puntero en el monitor 3")
        guardar.assert_called_with("gestos_monitor", "3")
        self.assertEqual(detector.puntero.monitor.numero, 3)
        self.assertIn("monitor 3", host._add_message.call_args.args[1])
        self.assertEqual(detectar_comando_gestos("puntero en el monitor 3"), 3)
        self.assertEqual(detectar_comando_gestos("Jarvis, pon el puntero en el monitor dos"), 2)
        self.assertIsNone(detectar_comando_gestos("mueve Brave al monitor 3"))

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
        host._split_orders.return_value = None  # una sola orden
        with patch("jarvis_core.procesar_stt"):
            JarvisApp._voice_worker(host, None, 0.0, "c", transcript="activa los gestos")
        eventos = [host.events.get_nowait() for _ in range(host.events.qsize())]
        self.assertIn(("gestures_toggle", "on", "activa los gestos"), eventos)


if __name__ == "__main__":
    unittest.main()
