"""WhatsApp de escritorio (jarvis_whatsapp): todo simulado, sin abrir la app ni leer chats."""

import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import jarvis_agent
import jarvis_cards
import jarvis_whatsapp as w
from jarvis_tool_registry import ToolRisk
from jarvis_tools import crear_registro_herramientas


class LecturaTests(unittest.TestCase):
    def test_row_summary(self):
        self.assertEqual(w.resumir_fila(["3 mensajes no leídos", "Grupo Fútbol", "1:13 a. m.", "Juan", ":", "hola"]),
                         ("Grupo Fútbol", 3))
        self.assertEqual(w.resumir_fila(["Mamá", "Ayer", "dale"]), ("Mamá", 0))

    def test_unread_description(self):
        self.assertEqual(w.describir_no_leidos([("Mamá", 0)]), "No tenés mensajes sin leer en WhatsApp.")
        texto = w.describir_no_leidos([("Juan", 2), ("Mamá", 0), ("Fútbol", 5)])
        self.assertIn("Tenés 7 mensajes sin leer en 2 chats", texto)
        self.assertIn("son datos de WhatsApp", texto)
        self.assertIn("Juan (2), Fútbol (5)", texto)

    def test_messages_end_with_their_time_and_duplicates_go(self):
        lineas = [(10, "hola"), (10, "hola"), (20, "4:36 a. m."), (30, "8/11/2026"), (40, "che"), (41, "¿venís?"),
                  (50, "6:57 p. m."), (60, "escribiendo")]
        self.assertEqual(w.mensajes_de(lineas), ["hola (4:36 a. m.)", "che ¿venís? (6:57 p. m.)", "escribiendo"])
        self.assertEqual(w.mensajes_de(lineas, 1), ["escribiendo"])

    def test_choose_chat(self):
        nombres = ["Juan Pérez", "Mamá", "Grupo Fútbol 5"]
        self.assertEqual(w.elegir_chat(nombres, "mama"), 1)
        self.assertEqual(w.elegir_chat(nombres, "Juan"), 0)
        self.assertEqual(w.elegir_chat(nombres, "futbol"), 2)
        self.assertIsNone(w.elegir_chat(nombres, "Pedro"))


class FrasesTests(unittest.TestCase):
    def test_phrases(self):
        casos = {
            "¿Tengo mensajes de WhatsApp?": ("whatsapp_unread", {}),
            "¿Quién me escribió por WhatsApp?": ("whatsapp_unread", {}),
            "Leé lo último que me mandó Juan en WhatsApp": ("whatsapp_read", {"chat": "Juan"}),
            "Mandale a Juan que llego tarde": ("whatsapp_send", {"chat": "Juan", "text": "llego tarde"}),
            "Jarvis, escribile a Mamá por WhatsApp que ya salí": ("whatsapp_send", {"chat": "Mamá", "text": "ya salí"}),
            "Decile a Juan: nos vemos a las 8": ("whatsapp_send", {"chat": "Juan", "text": "nos vemos a las 8"}),
        }
        for texto, esperado in casos.items():
            with self.subTest(texto=texto):
                self.assertEqual(w.detectar(texto), esperado)
        for texto in ("Leé el texto de esta ventana", "¿Qué hora es?"):
            self.assertIsNone(w.detectar(texto), texto)

    def test_routed_for_text_and_voice(self):
        fuente = (Path(__file__).resolve().parent.parent / "jarvis_app_ordenes.py").read_text("utf-8")
        self.assertIn("jarvis_whatsapp.detectar(text)", fuente)
        self.assertIn("jarvis_whatsapp.detectar(transcript)", fuente)
        self.assertIn('"whatsapp_send", "install_program"', fuente)  # lo directo de riesgo alto también pregunta


class EnviarTests(unittest.TestCase):
    def preparar(self, caja_nombre):
        @contextmanager
        def sesion():
            yield Mock(hwnd=1)

        self.parches = [patch.object(w, "_sesion", sesion), patch.object(w, "_abrir_chat", return_value="Juan Pérez"),
                        patch.object(w, "_controles", return_value=[]),
                        patch.object(w, "_caja", return_value=Mock(Name=caja_nombre)), patch.object(w, "_clic"),
                        patch("jarvis_screen_actions.escribir_texto"), patch("jarvis_screen_actions.pulsar_atajo"),
                        patch("time.sleep"), patch.dict("sys.modules", {"uiautomation": MagicMock()})]
        for p in self.parches:
            p.start()
            self.addCleanup(p.stop)

    def test_sends_only_to_the_open_chat(self):
        import jarvis_screen_actions

        self.preparar("Escribir un mensaje para Juan Pérez")
        self.assertEqual(w.enviar("juan", "llego tarde"), "Le mandé a «Juan Pérez»: «llego tarde».")
        jarvis_screen_actions.escribir_texto.assert_called_once_with("llego tarde")
        jarvis_screen_actions.pulsar_atajo.assert_called_once_with("enter")

    def test_wrong_chat_sends_nothing(self):
        import jarvis_screen_actions

        self.preparar("Escribir un mensaje para Otra Persona")
        with self.assertRaisesRegex(w.ErrorWhatsApp, "no envié nada"):
            w.enviar("juan", "llego tarde")
        jarvis_screen_actions.pulsar_atajo.assert_not_called()


class RegistroTests(unittest.TestCase):
    def test_send_is_high_risk_with_its_own_card(self):
        registry = crear_registro_herramientas(Mock())
        self.assertIs(registry.get("whatsapp_send").risk, ToolRisk.HIGH)
        self.assertIn("whatsapp_send", jarvis_agent.CONFIRMACION_PROPIA)
        self.assertTrue(jarvis_agent._solo_consulta("whatsapp_read"))
        self.assertEqual(jarvis_cards.describir_paso("whatsapp_send", {"chat": "Juan", "text": "llego tarde"}),
                         "Mandarle a «Juan» por WhatsApp: «llego tarde»")


if __name__ == "__main__":
    unittest.main()


class BandejaTests(unittest.TestCase):
    """2026-11-20: si WhatsApp estaba en la bandeja (oculto) o minimizado, al terminar se oculta otra vez;
    si estaba a la vista, se deja como estaba."""

    def correr(self, visible, minimizada):
        user32 = Mock()
        user32.IsWindowVisible.return_value = visible
        user32.IsIconic.return_value = minimizada
        with patch.object(w.ctypes, "windll", Mock(user32=user32)), \
                patch.object(w, "_ventana", return_value=w.VentanaWhatsApp(7)), \
                patch("jarvis_control.traer_al_frente"):
            with w._sesion(dormir=lambda _s: None) as ventana:
                self.assertEqual(ventana.hwnd, 7)
        return [c.args for c in user32.ShowWindow.call_args_list]

    def test_hidden_is_shown_and_hidden_again(self):
        self.assertEqual(self.correr(visible=False, minimizada=False), [(7, w.SW_SHOW), (7, w.SW_HIDE)])

    def test_minimized_ends_hidden_in_the_tray(self):
        self.assertEqual(self.correr(visible=True, minimizada=True), [(7, w.SW_RESTORE), (7, w.SW_HIDE)])

    def test_visible_is_left_as_it_was(self):
        self.assertEqual(self.correr(visible=True, minimizada=False), [])
