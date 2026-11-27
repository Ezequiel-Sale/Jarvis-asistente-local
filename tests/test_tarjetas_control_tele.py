"""Los botones del control de la tele no llenan el chat de tarjetas (2026-11-26); los errores sí se ven."""

import unittest

import jarvis_app


class OrigenesSinTarjetaTests(unittest.TestCase):
    def test_tv_remote_successes_have_no_card(self) -> None:
        self.assertIn("control", jarvis_app.ORIGENES_SIN_TARJETA)
        # Lo pedido por voz, texto o la IA sí deja tarjeta.
        for origen in ("directo", "comando", "agente", "rutina"):
            self.assertNotIn(origen, jarvis_app.ORIGENES_SIN_TARJETA)


if __name__ == "__main__":
    unittest.main()
