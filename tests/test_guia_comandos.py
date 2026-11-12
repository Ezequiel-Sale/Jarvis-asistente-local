"""Guía «? COMANDOS»: catálogo, frases de las rutinas y buscador. Sin Tk ni base de datos."""

import unittest
from types import SimpleNamespace

import jarvis_commands as jc
import jarvis_comandos as g
import jarvis_reminders
import jarvis_routines as r
import jarvis_screen_actions

# Secciones cuyas frases las entiende Jarvis directamente (sin pasar por la IA).
_DIRECTAS = ("Volumen y sonido", "Vídeos y música", "Monitores", "Programas, juegos y ventanas",
             "Navegador", "Recordatorios y temporizadores", "Energía", "Limpieza", "Apariencia y sistema")


def _detectores():
    funciones = [getattr(jc, n) for n in dir(jc) if n.startswith("detectar")]
    return funciones + [jarvis_reminders.detectar_comando, jarvis_screen_actions.detectar_comando]


class CatalogTests(unittest.TestCase):
    def test_every_section_has_commands_in_plain_spanish(self) -> None:
        self.assertGreaterEqual(len(g.CATALOGO), 10)
        for seccion in g.CATALOGO:
            with self.subTest(seccion=seccion.titulo):
                self.assertTrue(seccion.comandos)
                for comando in seccion.comandos:
                    self.assertTrue(comando.frase.strip())
                    self.assertTrue(comando.para_que.endswith("."), comando.para_que)

    def test_direct_phrases_are_really_understood(self) -> None:
        detectores = _detectores()
        for seccion in g.CATALOGO:
            if seccion.titulo not in _DIRECTAS:
                continue
            for comando in seccion.comandos:
                for frase in comando.frase.split(" / "):
                    with self.subTest(frase=frase):
                        self.assertTrue(any(f(frase) is not None for f in detectores),
                                        f"Ningún comando directo entiende «{frase}»")


class YourCommandsTests(unittest.TestCase):
    def _rutina(self, nombre, frases, pasos, trigger=r.DISPARADOR_FRASE, active=True):
        return SimpleNamespace(name=nombre, phrases=frases, steps=pasos, trigger=trigger, active=active)

    def test_lists_active_phrase_routines_with_their_steps(self) -> None:
        rutinas = [
            self._rutina("Modo gaming 2K", ("modo gaming 2k",),
                         (r.RoutineStep("configure_display", {"action": "disable", "monitor": 2}),)),
            self._rutina("Noche", (), (r.RoutineStep("lock_computer", {}),), trigger=r.DISPARADOR_HORA),
            self._rutina("Apagada", ("modo cine",), (), active=False),
        ]
        seccion = g.tus_comandos(rutinas)
        self.assertEqual(len(seccion.comandos), 1)
        comando = seccion.comandos[0]
        self.assertEqual(comando.frase, "modo gaming 2k")
        self.assertIn("Desactivar el monitor 2", comando.para_que)
        self.assertIn("«Desactiva modo gaming 2k»", comando.para_que)

    def test_no_section_without_phrase_routines(self) -> None:
        self.assertIsNone(g.tus_comandos([]))


class FilterTests(unittest.TestCase):
    def test_search_ignores_accents_and_case(self) -> None:
        resultado = g.filtrar(g.CATALOGO, "VIDEO monitor 2")
        self.assertTrue(resultado)
        for seccion in resultado:
            for comando in seccion.comandos:
                texto = g._normalizar(comando.frase + " " + comando.para_que)
                self.assertIn("video", texto)
                self.assertIn("monitor", texto)

    def test_section_title_shows_the_whole_section(self) -> None:
        energia = next(s for s in g.CATALOGO if s.titulo == "Energía")
        self.assertEqual(g.filtrar(g.CATALOGO, "energia"), [energia])

    def test_empty_search_and_no_match(self) -> None:
        self.assertEqual(g.filtrar(g.CATALOGO, "  "), list(g.CATALOGO))
        self.assertEqual(g.filtrar(g.CATALOGO, "zzzz"), [])


if __name__ == "__main__":
    unittest.main()
