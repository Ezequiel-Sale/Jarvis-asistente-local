import queue
import threading
import unittest
from unittest.mock import Mock, patch

import jarvis_displays as d
import jarvis_vision
from jarvis_app import JarvisApp
from jarvis_commands import detectar_comando_monitores

ADAPTER = (1234, 0)


def pantalla(numero, activa=True, source=None, nombre=None):
    return d.Pantalla(
        clave=f"ruta-{numero}",
        nombre=nombre or f"Monitor{numero}",
        adapter=ADAPTER,
        target_id=100 + numero,
        activa=activa,
        numero=numero,
        source_id=(numero - 1 if source is None else source) if activa else None,
    )


TRES = [pantalla(1), pantalla(2), pantalla(3)]


class PhraseTests(unittest.TestCase):
    def test_commands(self) -> None:
        casos = {
            "Desactiva el monitor 3": {"action": "disable", "monitor": 3},
            "apaga la pantalla tres": {"action": "disable", "monitor": 3},
            "extiende el monitor 3": {"action": "extend", "monitor": 3},
            "activa el monitor 2": {"action": "extend", "monitor": 2},
            "duplica los monitores 1 y 3": {"action": "duplicate", "monitor": 1, "second_monitor": 3},
            "duplica el monitor 1 en el 3": {"action": "duplicate", "monitor": 1, "second_monitor": 3},
            "clona la pantalla 2 con la 3": {"action": "duplicate", "monitor": 2, "second_monitor": 3},
        }
        for texto, argumentos in casos.items():
            with self.subTest(texto=texto):
                self.assertEqual(detectar_comando_monitores(texto), ("configure_display", argumentos))
        for texto in ("mira el monitor 3", "activa el audio", "apaga la luz", "duplica el archivo"):
            self.assertIsNone(detectar_comando_monitores(texto))


class PlanningTests(unittest.TestCase):
    def test_disable_removes_only_that_monitor(self) -> None:
        deseado, texto = d.planificar("disable", (3,), TRES)
        self.assertEqual(set(deseado), {(ADAPTER, 101), (ADAPTER, 102)})
        self.assertEqual(texto, "Desactivé el monitor 3 (Monitor3).")

    def test_never_disables_the_last_active_monitor(self) -> None:
        unica = [pantalla(1), pantalla(2, activa=False)]
        with self.assertRaises(d.ErrorPantalla):
            d.planificar("disable", (1,), unica)
        with self.assertRaises(d.ErrorPantalla):
            d.planificar("disable", (2,), unica)  # ya desactivado

    def test_extend_inactive_or_duplicated_monitor(self) -> None:
        apagado = [pantalla(1), pantalla(2), pantalla(3, activa=False)]
        deseado, _ = d.planificar("extend", (3,), apagado)
        self.assertIsNone(deseado[(ADAPTER, 103)])
        duplicado = [pantalla(1), pantalla(2), pantalla(3, source=0)]
        deseado, _ = d.planificar("extend", (3,), duplicado)
        self.assertIsNone(deseado[(ADAPTER, 103)])
        with self.assertRaises(d.ErrorPantalla):
            d.planificar("extend", (3,), TRES)  # ya extendido

    def test_duplicate_validations(self) -> None:
        with self.assertRaises(d.ErrorPantalla):
            d.planificar("duplicate", (1, 1), TRES)
        with self.assertRaises(d.ErrorPantalla):
            d.planificar("duplicate", (1, 3), [pantalla(1), pantalla(2), pantalla(3, source=0)])
        with self.assertRaises(d.ErrorPantalla) as context:
            d.planificar("duplicate", (1, 4), TRES)
        self.assertIn("Monitores conocidos: 1, 2, 3", str(context.exception))
        _deseado, texto = d.planificar("duplicate", (1, 3), TRES)
        self.assertEqual(texto, "Dupliqué el monitor 1 (Monitor1) y el monitor 3 (Monitor3).")


def _path(source, target):
    path = d.PATH_INFO()
    path.sourceInfo.adapterId = d.LUID(*ADAPTER)
    path.sourceInfo.id = source
    path.sourceInfo.modeInfoIdx = source * 2 + 1
    path.targetInfo.adapterId = d.LUID(*ADAPTER)
    path.targetInfo.id = target
    path.targetInfo.targetAvailable = True
    path.flags = d.DISPLAYCONFIG_PATH_ACTIVE
    return path


def _modo_fuente(ancho, alto):
    modo = d.MODE_INFO()
    modo.infoType = 1
    fuente = d.SOURCE_MODE.from_buffer(modo.data)
    fuente.width, fuente.height = ancho, alto
    return modo


class TopologyTests(unittest.TestCase):
    def test_duplicate_shares_source_at_smallest_resolution(self) -> None:
        paths = [_path(0, 101), _path(1, 102), _path(2, 103)]
        modes = [d.MODE_INFO(), _modo_fuente(2560, 1440), d.MODE_INFO(), _modo_fuente(1920, 1080),
                 d.MODE_INFO(), _modo_fuente(3840, 2160)]
        with patch.object(d, "_consultar", return_value=(paths, modes)):
            nuevos_paths, nuevos_modes = d._config_duplicado(TRES[0], TRES[1])
        copia = next(p for p in nuevos_paths if p.targetInfo.id == 102)
        self.assertEqual(copia.sourceInfo.id, 0)
        fuente = d.SOURCE_MODE.from_buffer(nuevos_modes[copia.sourceInfo.modeInfoIdx].data)
        self.assertEqual((fuente.width, fuente.height), (1920, 1080))
        self.assertEqual(paths[1].sourceInfo.id, 1)  # el original no se modifica

    def test_topology_assigns_free_source_and_shared_source(self) -> None:
        todos = [_path(s, t) for t in (101, 102, 103) for s in (0, 1, 2)]
        resultado = d._topologia({(ADAPTER, 101): 0, (ADAPTER, 102): None, (ADAPTER, 103): 0}, todos)
        fuentes = {p.targetInfo.id: p.sourceInfo.id for p in resultado}
        self.assertEqual(fuentes[101], 0)
        self.assertEqual(fuentes[103], 0)
        self.assertNotEqual(fuentes[102], 0)
        self.assertTrue(all(p.sourceInfo.modeInfoIdx == d.MODE_IDX_INVALID for p in resultado))


def _modo_en(ancho, alto, x, y):
    modo = _modo_fuente(ancho, alto)
    fuente = d.SOURCE_MODE.from_buffer(modo.data)
    fuente.x, fuente.y = x, y
    return modo


class PrimaryMonitorTests(unittest.TestCase):
    def test_phrases(self) -> None:
        for texto, numero in (
            ("Pon el monitor 3 como principal", 3),
            ("haz principal el monitor 1", 1),
            ("cambia el monitor principal al 3", 3),
            ("que el monitor 3 sea el principal", 3),
        ):
            with self.subTest(texto=texto):
                self.assertEqual(
                    detectar_comando_monitores(texto),
                    ("configure_display", {"action": "set_primary", "monitor": numero}),
                )

    def test_only_monitors_1_and_3_and_must_be_active(self) -> None:
        _deseado, texto = d.planificar("set_primary", (3,), TRES)
        self.assertEqual(texto, "El monitor 3 (Monitor3) ahora es el monitor principal.")
        with self.assertRaises(d.ErrorPantalla) as context:
            d.planificar("set_primary", (2,), TRES)
        self.assertIn("1 y 3", str(context.exception))
        # Desactivado (p. ej. tras «modo gaming 2K»): se activa y después pasa a principal.
        deseado, texto = d.planificar("set_primary", (3,), [pantalla(1), pantalla(2), pantalla(3, activa=False)])
        self.assertIsNone(deseado[(ADAPTER, 103)])
        self.assertEqual(texto, "Activé el monitor 3 (Monitor3) y ahora es el monitor principal.")

    def test_inactive_primary_is_activated_then_moved_to_origin(self) -> None:
        apagado = [pantalla(1), pantalla(2), pantalla(3, activa=False)]
        pasos = []
        listas = iter([(apagado, {}), (TRES, {})])
        with patch.object(d, "listar_pantallas", side_effect=lambda mapa=None: next(listas)), \
                patch.object(d, "validar"), patch.object(d, "guardar_configuracion", return_value="previa"), \
                patch.object(d, "restaurar_configuracion"), \
                patch.object(d, "_aplicar", side_effect=lambda deseado, solo_validar=False: pasos.append(
                    ("activar", deseado[(ADAPTER, 103)]))), \
                patch.object(d, "_config_principal", side_effect=lambda p: pasos.append(("principal", p.activa))
                             or ([], [])), \
                patch.object(d, "_aplicar_explicito"):
            texto, previa, _mapa = d.aplicar("set_primary", (3,))
        self.assertEqual(pasos, [("activar", None), ("principal", True)])
        self.assertEqual((texto, previa), ("Activé el monitor 3 (Monitor3) y ahora es el monitor principal.", "previa"))

    def test_desktop_is_shifted_so_new_primary_is_at_origin(self) -> None:
        paths = [_path(0, 101), _path(1, 102), _path(2, 103)]
        modes = [d.MODE_INFO(), _modo_en(2560, 1440, 0, 0), d.MODE_INFO(),
                 _modo_en(1920, 1080, -1920, 0), d.MODE_INFO(), _modo_en(3840, 2160, -1264, -2160)]
        with patch.object(d, "_consultar", return_value=(paths, modes)):
            _nuevos_paths, nuevos = d._config_principal(TRES[2])
            posiciones = [
                (d.SOURCE_MODE.from_buffer(m.data).x, d.SOURCE_MODE.from_buffer(m.data).y)
                for m in nuevos if m.infoType == 1
            ]
            self.assertEqual(posiciones, [(1264, 2160), (-656, 2160), (0, 0)])
            with self.assertRaises(d.ErrorPantalla):
                d._config_principal(TRES[0])  # ya es el principal


class FlagTests(unittest.TestCase):
    """Regresión: Windows devolvía 87 porque se validaba con flags distintos a los reales."""

    def test_topology_never_saves_to_database(self) -> None:
        self.assertFalse(d.FLAGS_TOPOLOGIA & d.SDC_SAVE_TO_DATABASE)
        self.assertTrue(d.FLAGS_TOPOLOGIA & d.SDC_TOPOLOGY_SUPPLIED)
        self.assertTrue(d.FLAGS_EXPLICITO & d.SDC_USE_SUPPLIED_DISPLAY_CONFIG)

    def test_validation_uses_the_same_flags_except_apply(self) -> None:
        for reales in (d.FLAGS_TOPOLOGIA, d.FLAGS_EXPLICITO):
            validacion = d.flags_para(reales, solo_validar=True)
            self.assertFalse(validacion & d.SDC_APPLY)
            self.assertTrue(validacion & d.SDC_VALIDATE)
            self.assertEqual(validacion & ~d.SDC_VALIDATE, reales & ~d.SDC_APPLY)
            self.assertEqual(d.flags_para(reales, solo_validar=False), reales)

    def test_apply_validates_change_and_restore_before_touching_anything(self) -> None:
        llamadas: list[int] = []
        user32 = Mock()
        user32.SetDisplayConfig.side_effect = lambda *args: llamadas.append(args[-1]) or 0
        todos = [_path(s, t) for t in (101, 102, 103) for s in (0, 1, 2)]
        guardada = d.ConfiguracionGuardada(b"".join(bytes(_path(i, 101 + i)) for i in range(3)), b"", 3, 0)
        with patch.object(d, "listar_pantallas", return_value=(TRES, {})), \
                patch.object(d, "_consultar", return_value=(todos, [])), \
                patch.object(d, "guardar_configuracion", return_value=guardada), \
                patch.object(d, "_user32", return_value=user32):
            d.aplicar("disable", (3,))
        self.assertEqual(
            llamadas,
            [d.flags_para(d.FLAGS_TOPOLOGIA, True), d.flags_para(d.FLAGS_EXPLICITO, True), d.FLAGS_TOPOLOGIA],
        )


class MapTests(unittest.TestCase):
    def test_map_round_trip_and_bad_values(self) -> None:
        guardado = {}
        d.guardar_mapa(guardado.__setitem__, {"ruta-3": 3})
        self.assertEqual(d.cargar_mapa(guardado.get), {"ruta-3": 3})
        self.assertEqual(d.cargar_mapa(lambda _k: "no es json"), {})
        self.assertEqual(d.cargar_mapa(lambda _k: None), {})


class KeepOrRevertTests(unittest.TestCase):
    def _host(self, respuesta: bool | None):
        host = Mock()
        host.DISPLAY_KEEP_SECONDS = 0
        host.events = queue.Queue()
        host._display_batch = None
        host._ask_display_keep = lambda descripcion: JarvisApp._ask_display_keep(host, descripcion)

        def responder() -> None:
            evento = host.events.get(timeout=2)
            if respuesta is not None:
                evento[2].put(respuesta)

        threading.Thread(target=responder, daemon=True).start()
        return host

    @patch("jarvis_core.guardar_preferencia")
    @patch("jarvis_core.obtener_preferencia", return_value=None)
    @patch("jarvis_displays.listar_pantallas", return_value=([], {}))
    @patch("jarvis_displays.restaurar_configuracion")
    @patch("jarvis_displays.aplicar", return_value=("Desactivé el monitor 3.", "previa", {}))
    def test_keep_and_revert(self, aplicar, restaurar, *_mocks) -> None:
        self.assertEqual(
            JarvisApp._tool_configure_display(self._host(True), "disable", 3), "Desactivé el monitor 3."
        )
        restaurar.assert_not_called()
        aplicar.assert_called_with("disable", (3,), {})

        with self.assertRaises(RuntimeError) as context:
            JarvisApp._tool_configure_display(self._host(False), "duplicate", 1, 3)
        restaurar.assert_called_once_with("previa")
        self.assertIn("restauré la configuración anterior", str(context.exception))

    @patch("jarvis_core.guardar_preferencia")
    @patch("jarvis_core.obtener_preferencia", return_value=None)
    @patch("jarvis_displays.listar_pantallas", return_value=([], {}))
    @patch("jarvis_displays.restaurar_configuracion")
    @patch("jarvis_displays.aplicar", return_value=("Cambio.", "previa", {}))
    def test_no_answer_reverts(self, _aplicar, restaurar, *_mocks) -> None:
        host = self._host(None)  # nadie responde al diálogo
        host.DISPLAY_KEEP_SECONDS = -9.9  # espera total de 0,1 s
        with self.assertRaises(RuntimeError):
            JarvisApp._tool_configure_display(host, "extend", 3)
        restaurar.assert_called_once_with("previa")


class RoutineBatchTests(unittest.TestCase):
    """En una rutina, los cambios de monitores se confirman una sola vez al final."""

    def _host(self, respuestas):
        host = Mock()
        host._display_batch = None
        preguntas = []

        def preguntar(descripcion):
            preguntas.append(descripcion)
            return respuestas.pop(0)

        host._ask_display_keep = preguntar
        return host, preguntas

    def _rutina(self, host, nombre="Modo gaming 4K"):
        lote = JarvisApp._start_display_batch(host)
        pasos = [("Monitor 3 principal.", "previa-original"), ("Desactivé el 2.", "previa-2"),
                 ("Desactivé el 1.", "previa-3")]
        with patch("jarvis_core.guardar_preferencia"),                 patch("jarvis_core.obtener_preferencia", return_value=None),                 patch("jarvis_displays.listar_pantallas", return_value=([], {})),                 patch("jarvis_displays.aplicar", side_effect=[(d, p, {}) for d, p in pasos]):
            for _ in pasos:
                JarvisApp._tool_configure_display(host, "disable", 1)
        with patch("jarvis_displays.restaurar_configuracion") as restaurar:
            resultado = JarvisApp._confirm_display_batch(host, lote, nombre)
        return resultado, restaurar

    def test_one_question_for_the_whole_routine(self) -> None:
        host, preguntas = self._host([True])
        resultado, restaurar = self._rutina(host)
        self.assertIsNone(resultado)
        self.assertEqual(preguntas, ["Modo gaming 4K: Monitor 3 principal. Desactivé el 2. Desactivé el 1."])
        restaurar.assert_not_called()
        self.assertIsNone(host._display_batch)

    def test_not_keeping_restores_the_config_before_the_first_change(self) -> None:
        host, _ = self._host([False])
        resultado, restaurar = self._rutina(host)
        restaurar.assert_called_once_with("previa-original")
        self.assertIn("restauré la configuración anterior", resultado)

    def test_routine_without_display_changes_asks_nothing(self) -> None:
        host, preguntas = self._host([])
        lote = JarvisApp._start_display_batch(host)
        self.assertIsNone(JarvisApp._confirm_display_batch(host, lote, "Otra"))
        self.assertEqual(preguntas, [])


class StableNumberingTests(unittest.TestCase):
    def test_new_monitors_take_their_windows_number(self) -> None:
        mapa = d.asignar_numeros([("kg", 1), ("vx", 2), ("tv", 3)], {})
        self.assertEqual(mapa, {"kg": 1, "vx": 2, "tv": 3})

    def test_remembered_number_survives_windows_renumbering(self) -> None:
        # Caso real: al volver a extender, Windows dio DISPLAY2 a la Smart TV.
        recordado = {"kg": 1, "vx": 2, "tv": 3}
        mapa = d.asignar_numeros([("kg", 1), ("tv", 2), ("vx", None)], recordado)
        self.assertEqual(mapa, recordado)

    def test_new_monitor_never_steals_a_remembered_number(self) -> None:
        mapa = d.asignar_numeros([("kg", 1), ("nuevo", 2), ("vx", None)], {"kg": 1, "vx": 2})
        self.assertEqual(mapa["vx"], 2)
        self.assertEqual(mapa["nuevo"], 3)

    def test_duplicated_numbers_from_old_maps_are_repaired(self) -> None:
        mapa = d.asignar_numeros([("a", 2), ("b", None)], {"a": 2, "b": 2})
        self.assertEqual(sorted(mapa.values()), [1, 2])

    def test_gdi_translation_for_vision(self) -> None:
        pantallas = [
            d.Pantalla("kg", "KG", ADAPTER, 1, True, 1, 0, gdi=1),
            d.Pantalla("vx", "VX", ADAPTER, 2, True, 2, 2, gdi=3),
            d.Pantalla("tv", "TV", ADAPTER, 3, True, 3, 1, gdi=2),
        ]
        with patch.object(d, "listar_pantallas", return_value=(pantallas, {})):
            equivalencias, _ = d.numeros_por_gdi({})
        self.assertEqual(equivalencias, {1: 1, 3: 2, 2: 3})

    def test_extend_targets_the_physical_monitor(self) -> None:
        pantallas = [
            d.Pantalla("kg", "KG271U N3", ADAPTER, 1, True, 1, 0, gdi=1),
            d.Pantalla("vx", "VX2428-FHD", ADAPTER, 2, False, 2, None),
            d.Pantalla("tv", "Smart TV Pro", ADAPTER, 3, True, 3, 1, gdi=2),
        ]
        _deseado, texto = d.planificar("extend", (2,), pantallas)
        self.assertEqual(texto, "Extendí el escritorio al monitor 2 (VX2428-FHD).")


class VisionNumberingTests(unittest.TestCase):
    def tearDown(self) -> None:
        jarvis_vision.configurar_numeracion(None)

    def test_vision_uses_stable_numbers(self) -> None:
        monitores = [jarvis_vision.Monitor(1, 0, 0, 10, 10, True), jarvis_vision.Monitor(2, 10, 0, 10, 10, False)]
        jarvis_vision.configurar_numeracion(lambda: {2: 3})
        self.assertEqual([m.numero for m in jarvis_vision.renumerar(monitores)], [1, 3])

    def test_falls_back_to_windows_numbers_on_error_or_clash(self) -> None:
        monitores = [jarvis_vision.Monitor(1, 0, 0, 10, 10, True), jarvis_vision.Monitor(2, 10, 0, 10, 10, False)]
        jarvis_vision.configurar_numeracion(lambda: {2: 1})
        with self.assertLogs(level="WARNING"):
            self.assertEqual([m.numero for m in jarvis_vision.renumerar(monitores)], [1, 2])

        def falla():
            raise OSError("sin API")

        jarvis_vision.configurar_numeracion(falla)
        with self.assertLogs(level="ERROR"):
            self.assertEqual([m.numero for m in jarvis_vision.renumerar(monitores)], [1, 2])


if __name__ == "__main__":
    unittest.main()
