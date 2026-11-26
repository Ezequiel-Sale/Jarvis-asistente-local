"""«Crea una rutina llamada X. Todos los miércoles y los viernes a las 17:00 …» (2026-11-25: los días
quedaban dentro del nombre y la rutina salía «todos los días»)."""

import json
import unittest

import jarvis_routines as r


class DiasEnNombreTests(unittest.TestCase):
    def test_days_after_the_name(self) -> None:
        q = r.parsear_solicitud_creacion(
            "Crea una rutina llamada Publicación LinkedIn. Todos los miércoles y los viernes a las 17:00 "
            "abrir una nueva pestaña y navegar a linkedin.com")
        self.assertEqual((q.name, q.time, q.days), ("Publicación LinkedIn", "17:00", "3,5"))
        self.assertEqual([p["tool"] for p in json.loads(q.steps_json)], ["open_new_browser_tab", "navigate_browser"])
        q = r.parsear_solicitud_creacion("Crea una rutina llamada Mañana los lunes a las 8 pausa la música")
        self.assertEqual((q.name, q.days), ("Mañana", "1"))

    def test_day_word_at_the_start_stays_in_the_name(self) -> None:
        q = r.parsear_solicitud_creacion("Crea una rutina llamada Lunes feliz a las 8 pausa la música")
        self.assertEqual((q.name, q.days), ("Lunes feliz", "1,2,3,4,5,6,7"))

    def test_days_after_the_hour_still_work(self) -> None:
        q = r.parsear_solicitud_creacion("Programa una rutina llamada Música a las 8 de los lunes y jueves "
                                         "pausa la música")
        self.assertEqual((q.name, q.days), ("Música", "1,4"))


if __name__ == "__main__":
    unittest.main()
