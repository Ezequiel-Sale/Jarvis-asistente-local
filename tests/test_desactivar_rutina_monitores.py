"""«Desactiva el modo gaming 2K»: volver los monitores a como estaban antes de la rutina,
cambios ya aplicados que no frenan un plan y la tarjeta del plan en lenguaje llano.

Todo simulado: ninguna prueba toca la configuración real de monitores ni la base de datos."""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import jarvis_cards as c
import jarvis_displays as d
import jarvis_routines as r
from jarvis_app import JarvisApp

PREVIA = d.ConfiguracionGuardada(paths=b"\x01\x02", modes=b"\x03", num_paths=1, num_modes=1)


class UndoPhraseTests(unittest.TestCase):
    def test_undo_phrases(self) -> None:
        self.assertEqual(r.frase_a_deshacer("Desactivar modo gaming 2K."), "modo gaming 2k")
        self.assertEqual(r.frase_a_deshacer("sal del modo gaming 4k"), "modo gaming 4k")
        self.assertEqual(r.frase_a_deshacer("quita el modo noche"), "modo noche")
        self.assertIsNone(r.frase_a_deshacer("desactiva"))
        self.assertIsNone(r.frase_a_deshacer("modo gaming 2k"))

    def test_undo_phrase_is_not_the_routine_itself(self) -> None:
        candidatas = [("2k", ("Modo gaming 2K",))]
        self.assertIsNone(r.elegir_por_frase("Desactivar modo gaming 2K", candidatas))


class SavedBaseTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        datos = d.base_desde_texto(d.base_a_texto(PREVIA, "Modo gaming 4K", [3, 1, 2], 1))
        self.assertEqual(datos["configuracion"], PREVIA)
        self.assertEqual((datos["rutina"], datos["activos"], datos["principal"]), ("Modo gaming 4K", [1, 2, 3], 1))
        self.assertIsNone(d.base_desde_texto(""))
        self.assertIsNone(d.base_desde_texto("no es json"))

    def test_exact_copy_first(self) -> None:
        datos = d.base_desde_texto(d.base_a_texto(PREVIA, "Modo gaming 2K", [1, 2, 3], 1))
        with patch.object(d, "guardar_configuracion", return_value="ahora"), \
                patch.object(d, "restaurar_configuracion") as restaurar, \
                patch.object(d, "aplicar") as aplicar:
            descripcion, previa = d.volver_a_base(datos)
        self.assertEqual(previa, "ahora")
        restaurar.assert_called_with(PREVIA)
        aplicar.assert_not_called()
        self.assertIn("antes de «Modo gaming 2K»", descripcion)

    def test_after_a_reboot_reactivates_monitor_by_monitor(self) -> None:
        datos = d.base_desde_texto(d.base_a_texto(PREVIA, "Modo gaming 2K", [1, 2, 3], 3))

        def restaurar(conf, solo_validar=False):
            if conf is PREVIA or conf == PREVIA:
                raise d.ErrorPantalla("Windows no aceptó la configuración (código 87).")

        pantallas = [SimpleNamespace(numero=1, activa=True), SimpleNamespace(numero=2, activa=False),
                     SimpleNamespace(numero=3, activa=False)]
        with patch.object(d, "guardar_configuracion", return_value="ahora"), \
                patch.object(d, "restaurar_configuracion", side_effect=restaurar), \
                patch.object(d, "listar_pantallas", return_value=(pantallas, {})), \
                patch.object(d, "aplicar") as aplicar:
            d.volver_a_base(datos)
        self.assertEqual([llamada.args[:2] for llamada in aplicar.call_args_list],
                         [("extend", (2,)), ("extend", (3,)), ("set_primary", (3,))])


class AlreadyAppliedTests(unittest.TestCase):
    def test_disable_an_already_disabled_monitor_is_not_a_failure(self) -> None:
        pantallas = [d.Pantalla("a", "KG", (0, 1), 1, True, 1, 0),
                     d.Pantalla("b", "VX", (0, 1), 2, False, 2, None)]
        with self.assertRaises(d.YaEstaAsi):
            d.planificar("disable", (2,), pantallas)

    @patch("jarvis_app.core.obtener_preferencia", return_value=None)
    @patch("jarvis_app.jarvis_displays.aplicar", side_effect=d.YaEstaAsi("El monitor 2 (VX) ya está desactivado."))
    def test_tool_returns_the_message_without_asking(self, _aplicar, _pref) -> None:
        host = Mock()
        host._display_batch = None
        texto = JarvisApp._tool_configure_display(host, "disable", 2)
        self.assertEqual(texto, "El monitor 2 (VX) ya está desactivado.")
        host._ask_display_keep.assert_not_called()


class RememberBaseTests(unittest.TestCase):
    def _host(self, guardado: dict, despues=([1], 1)):
        host = Mock()
        host._estado_monitores.return_value = despues
        return host

    def _recordar(self, guardado: dict, lote: dict, nombre: str, despues=([1], 1)) -> dict:
        host = self._host(guardado, despues)
        with patch("jarvis_app.core.obtener_preferencia", side_effect=guardado.get), \
                patch("jarvis_app.core.guardar_preferencia", side_effect=guardado.__setitem__):
            JarvisApp._recordar_base_monitores(host, lote, nombre)
        return json.loads(guardado[d.PREFERENCIA_BASE])

    def test_first_routine_saves_the_config_before_it(self) -> None:
        guardado = {}
        datos = self._recordar(guardado, {"previa": PREVIA, "antes": ([1, 2, 3], 3)}, "Modo gaming 4K",
                               despues=([3], 3))
        self.assertEqual((datos["rutina"], datos["activos"], datos["principal"], datos["despues"]),
                         ("Modo gaming 4K", [1, 2, 3], 3, [[3], 3]))

    def test_chained_routine_keeps_the_original_base(self) -> None:
        guardado = {}
        self._recordar(guardado, {"previa": PREVIA, "antes": ([1, 2, 3], 3)}, "Modo gaming 4K", despues=([3], 3))
        otra = d.ConfiguracionGuardada(b"\x09", b"", 1, 0)
        # «2K» parte de como lo dejó «4K»: se conserva la de antes de «4K».
        datos = self._recordar(guardado, {"previa": otra, "antes": ([3], 3)}, "Modo gaming 2K", despues=([1], 1))
        self.assertEqual(datos["activos"], [1, 2, 3])
        self.assertEqual(d.base_desde_texto(json.dumps(datos))["configuracion"], PREVIA)
        self.assertEqual(datos["despues"], [[1], 1])

    def test_unrelated_state_starts_a_new_base(self) -> None:
        guardado = {}
        self._recordar(guardado, {"previa": PREVIA, "antes": ([1, 2, 3], 3)}, "Modo gaming 4K", despues=([3], 3))
        otra = d.ConfiguracionGuardada(b"\x09", b"", 1, 0)
        datos = self._recordar(guardado, {"previa": otra, "antes": ([1, 2], 1)}, "Modo trabajo", despues=([1], 1))
        self.assertEqual((datos["rutina"], datos["activos"]), ("Modo trabajo", [1, 2]))


class UndoRoutineTests(unittest.TestCase):
    def _buscar(self, texto, rutina, base="{}"):
        host = Mock()
        with patch.object(r, "buscar_rutina_por_frase", side_effect=lambda t: rutina if t == "modo gaming 2k" else None), \
                patch("jarvis_app.core.obtener_preferencia", return_value=base), \
                patch.object(d, "base_desde_texto", return_value={"rutina": "x"} if base else None):
            return JarvisApp._find_undo_routine(host, texto)

    def test_builds_a_direct_restore(self) -> None:
        rutina = r.Routine(7, "Modo gaming 2K", "", (), (r.RoutineStep("configure_display", {"action": "disable", "monitor": 2}),),
                           r.DISPARADOR_FRASE)
        deshacer = self._buscar("Desactivar modo gaming 2K.", rutina)
        self.assertEqual(deshacer.name, "Desactivar Modo gaming 2K")
        self.assertEqual(deshacer.steps, (r.RoutineStep("restore_display_config", {"routine": "Modo gaming 2K"}),))

    def test_only_for_routines_that_changed_monitors_and_with_a_saved_base(self) -> None:
        sin_monitores = r.Routine(8, "Modo noche", "", (), (r.RoutineStep("set_theme", {"theme": "mark"}),), r.DISPARADOR_FRASE)
        self.assertIsNone(self._buscar("desactiva modo gaming 2k", sin_monitores))
        con_monitores = r.Routine(7, "Modo gaming 2K", "", (), (r.RoutineStep("configure_display", {}),), r.DISPARADOR_FRASE)
        self.assertIsNone(self._buscar("desactiva modo gaming 2k", con_monitores, base=""))
        self.assertIsNone(self._buscar("modo gaming 2k", con_monitores))


class PlanCardTextTests(unittest.TestCase):
    def test_plain_language(self) -> None:
        self.assertEqual(c.describir_paso("configure_display", {"action": "disable", "monitor": 2}),
                         "Desactivar el monitor 2")
        self.assertEqual(c.describir_paso("configure_display", {"action": "duplicate", "monitor": 1, "second_monitor": 3}),
                         "Duplicar el monitor 1 en el monitor 3")
        self.assertEqual(c.describir_paso("restore_display_config", {"routine": "Modo gaming 2K"}),
                         "Volver los monitores a como estaban antes de «Modo gaming 2K»")
        self.assertEqual(c.describir_paso("mute_browser_tab", {"mute": True, "monitor": 3}),
                         "Silenciar la pestaña en el monitor 3")
        self.assertEqual(c.describir_paso("media_control", {"action": "play", "monitor": 1}),
                         "Reanudar el vídeo o la música del monitor 1")
        self.assertEqual(c.describir_paso("fix_display_scaling", {"monitor": 3}),
                         "Reajustar el escalado del monitor 3 (arregla la barra de tareas mal ubicada)")
        self.assertEqual(c.describir_paso("video_theater", {"enabled": False}), "Quitar el modo teatro del vídeo")
        self.assertEqual(c.describir_paso("clean_disk", {"categories": ["temp"]}), "Liberar espacio en el disco (temp)")
        self.assertEqual(c.describir_paso("set_reminder", {"text": "x"}), "Texto: x")
        self.assertEqual(c.describir_paso("get_current_datetime", {}), "")
        self.assertNotIn("{", c.describir_paso("media_control", {"action": "play", "monitor": 1}))

    def test_plain_notice(self) -> None:
        self.assertIn("vuelve solo a como estaba", c.aviso("configure_display", "high"))
        self.assertIn("revisa", c.aviso("algo_nuevo", "high"))
        self.assertIn("integración externa", c.aviso("mcp_borrar", "high"))

    def test_risk_labels(self) -> None:
        self.assertEqual(c.riesgo("high"), ("RIESGO ALTO", "ERROR"))
        self.assertEqual(c.riesgo("low"), ("RIESGO BAJO", "MUTED"))


if __name__ == "__main__":
    unittest.main()
