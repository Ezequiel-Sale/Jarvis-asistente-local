"""Pestañas de Brave por voz (sin tocar Brave, el teclado ni la accesibilidad reales)."""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import jarvis_pestanas as p
from jarvis_commands import detectar_navegacion


class PhraseTests(unittest.TestCase):
    def test_orders(self) -> None:
        casos = {
            "siguiente pestaña": {"action": "next"},
            "pasá a la siguiente pestaña": {"action": "next"},
            "próxima pestaña en Brave": {"action": "next"},
            "pestaña anterior": {"action": "previous"},
            "volvé a la pestaña anterior": {"action": "previous"},
            "andá a la tercera pestaña": {"action": "number", "number": 3},
            "pestaña 3": {"action": "number", "number": 3},
            "pestaña número dos": {"action": "number", "number": 2},
            "última pestaña": {"action": "last"},
            "cerrá la pestaña": {"action": "close"},
            "cerrá esta pestaña": {"action": "close"},
            "cerrá la pestaña de Gmail": {"action": "close", "title": "gmail"},
            "reabrí la pestaña": {"action": "reopen"},
            "volvé a abrir la pestaña que cerré": {"action": "reopen"},
            "andá a la pestaña de YouTube": {"action": "go", "title": "youtube"},
            "pasá a la pestaña de Mercado Libre": {"action": "go", "title": "mercado libre"},
            "¿Qué pestañas tengo abiertas?": {"action": "list"},
            "decime las pestañas": {"action": "list"},
        }
        for frase, argumentos in casos.items():
            with self.subTest(frase=frase):
                self.assertEqual(p.detectar(frase), ("browser_tab", argumentos))

    def test_other_orders_are_not_taken(self) -> None:
        for frase in ("abrí una pestaña nueva", "silenciá la pestaña", "siguiente canción", "cerrá esto",
                      "pasá este tema"):
            with self.subTest(frase=frase):
                self.assertIsNone(p.detectar(frase))
        # «la pestaña de Ajustes» es la sección de Jarvis: esa detección va antes en las órdenes.
        self.assertEqual(detectar_navegacion("andá a la pestaña de ajustes"), "ajustes")


class TitleTests(unittest.TestCase):
    def test_clean_title_and_pick_by_name(self) -> None:
        self.assertEqual(p.titulo_limpio("Lofi Girl - YouTube: Reproducción de audio: Uso de memoria: 120 MB"),
                         "Lofi Girl - YouTube")
        titulos = ["Recibidos - Gmail", "Lofi Girl - YouTube", "Mercado Libre Argentina", "GitHub"]
        self.assertEqual(p.elegir(titulos, "youtube"), 1)
        self.assertEqual(p.elegir(titulos, "you tube"), 1)  # Whisper lo separa
        self.assertEqual(p.elegir(titulos, "mercado libre"), 2)
        self.assertEqual(p.elegir(titulos, "Gmail"), 0)
        self.assertIsNone(p.elegir(titulos, "netflix"))


class ActionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.enviadas = []
        for parche in (patch.object(p, "_brave_al_frente", return_value=SimpleNamespace(hwnd=7)),
                       patch.object(p, "_enviar", side_effect=self.enviadas.append)):
            parche.start()
            self.addCleanup(parche.stop)

    def test_shortcuts(self) -> None:
        self.assertEqual(p.pestana("next"), "Pasé a la pestaña siguiente.")
        self.assertEqual(p.pestana("number", 3), "Fui a la pestaña 3.")
        p.pestana("reopen")
        teclas = [[vk for _, vk, _, subir in combo if not subir] for combo in self.enviadas]
        self.assertEqual(teclas, [[p.VK_CONTROL, p.VK_TAB], [p.VK_CONTROL, ord("3")],
                                  [p.VK_CONTROL, p.VK_SHIFT, ord("T")]])
        # Cada tecla se suelta, en orden inverso.
        self.assertEqual([vk for _, vk, _, subir in self.enviadas[0] if subir], [p.VK_TAB, p.VK_CONTROL])

    def _con_pestanas(self, nombres):
        pestanas = []
        for i, nombre in enumerate(nombres):
            patron = Mock(IsSelected=(i == 1))
            pestanas.append(Mock(Name=nombre, GetSelectionItemPattern=Mock(return_value=patron)))
        auto = MagicMock()
        return pestanas, patch.dict("sys.modules", {"uiautomation": auto}), \
            patch.object(p, "_pestanas_de", return_value=pestanas)

    def test_go_by_name_selects_without_the_mouse(self) -> None:
        pestanas, modulo, lista = self._con_pestanas(["Recibidos - Gmail", "GitHub", "Lofi Girl - YouTube"])
        with modulo, lista:
            self.assertEqual(p.pestana("go", title="youtube"), "Fui a la pestaña «Lofi Girl - YouTube».")
            pestanas[2].GetSelectionItemPattern().Select.assert_called_once()
            self.assertEqual(self.enviadas, [])  # sin teclas
            # Cerrar una por su nombre: se elige y Ctrl+W.
            self.assertEqual(p.pestana("close", title="gmail"), "Cerré la pestaña «Recibidos - Gmail».")
            self.assertEqual(self.enviadas, [p.ATAJOS["close"]])
            with self.assertRaisesRegex(RuntimeError, "No encontré"):
                p.pestana("go", title="netflix")

    def test_list_and_numbers_beyond_eight(self) -> None:
        nombres = [f"Página {i}" for i in range(1, 11)]
        pestanas, modulo, lista = self._con_pestanas(nombres)
        with modulo, lista:
            texto = p.pestana("list")
            self.assertTrue(texto.startswith("Tenés 10 pestañas en Brave: 1) Página 1; 2) Página 2 (la que estás viendo)"))
            p.pestana("number", 10)  # Ctrl+10 no existe: por accesibilidad
            pestanas[9].GetSelectionItemPattern().Select.assert_called_once()
            with self.assertRaisesRegex(RuntimeError, "10 pestañas"):
                p.pestana("number", 12)


if __name__ == "__main__":
    unittest.main()
