"""Control multimedia. No toca la reproducción ni el volumen reales."""

import unittest
from unittest.mock import Mock, patch

import jarvis_media as media
from jarvis_commands import detectar_comando_multimedia, detectar_comando_volumen
from jarvis_verifiers import verificar_reproduccion, verificar_volumen_app


class PhraseTests(unittest.TestCase):
    def test_playback(self) -> None:
        casos = {
            "Pausa la música": "pause",
            "pausa": "pause",
            "para la canción": "pause",
            "reanuda": "play",
            "continúa la música": "play",
            "quita la pausa": "play",
            "siguiente canción": "next",
            "pasa": "next",
            "otra canción": "next",
            "la anterior": "previous",
            "canción anterior": "previous",
        }
        for texto, accion in casos.items():
            with self.subTest(texto=texto):
                self.assertEqual(
                    detectar_comando_multimedia(texto), ("media_control", {"action": accion})
                )

    def test_now_playing(self) -> None:
        for texto in ("¿Qué canción es esta?", "qué está sonando", "¿cómo se llama esta canción?"):
            with self.subTest(texto=texto):
                self.assertEqual(detectar_comando_multimedia(texto), ("get_now_playing", {}))

    def test_app_volume(self) -> None:
        self.assertEqual(
            detectar_comando_multimedia("pon el volumen de Spotify al 30"),
            ("set_app_volume", {"app": "spotify", "percentage": 30}),
        )
        self.assertEqual(
            detectar_comando_multimedia("baja el volumen del navegador a 20 por ciento"),
            ("set_app_volume", {"app": "navegador", "percentage": 20}),
        )
        self.assertEqual(
            detectar_comando_multimedia("silencia Discord"),
            ("set_app_volume", {"app": "discord", "mute": True}),
        )
        self.assertEqual(
            detectar_comando_multimedia("quita el silencio de discord"),
            ("set_app_volume", {"app": "discord", "mute": False}),
        )

    def test_not_media(self) -> None:
        for texto in (
            "reproduce despacito",  # búsqueda en YouTube
            "silencia tu voz",  # audio de Jarvis
            "pon el volumen al 30",  # volumen del sistema
            "pon el volumen del sistema al 30",
            "pon el volumen de Spotify al 300",
            "silencia el equipo",
        ):
            with self.subTest(texto=texto):
                self.assertIsNone(detectar_comando_multimedia(texto))
        self.assertEqual(detectar_comando_volumen("pon el volumen al 30%"), 30)


class AppNameTests(unittest.TestCase):
    def test_names(self) -> None:
        self.assertEqual(media.nombre_app("Spotify.exe"), "Spotify")
        self.assertEqual(media.nombre_app("brave.exe"), "Brave")
        self.assertEqual(media.nombre_app("steam.exe"), "Steam")
        self.assertEqual(media.resolver_app("YouTube"), "brave")
        self.assertEqual(media.resolver_app("el navegador"), "brave")


def _sesion(nombre: str, volumen: float = 1.0) -> Mock:
    sesion = Mock()
    sesion.Process.name.return_value = nombre
    sesion.SimpleAudioVolume.GetMasterVolume.return_value = volumen
    return sesion


class AppVolumeTests(unittest.TestCase):
    def test_sets_every_session_of_the_app(self) -> None:
        a, b, otra = _sesion("brave.exe"), _sesion("brave.exe"), _sesion("Spotify.exe")
        texto = media.volumen_aplicacion("youtube", 25, sesiones=lambda: [a, b, otra])
        self.assertEqual(texto, "Volumen de Brave al 25 %.")
        a.SimpleAudioVolume.SetMasterVolume.assert_called_once_with(0.25, None)
        b.SimpleAudioVolume.SetMasterVolume.assert_called_once_with(0.25, None)
        otra.SimpleAudioVolume.SetMasterVolume.assert_not_called()

    def test_mute_and_unmute(self) -> None:
        discord = _sesion("Discord.exe")
        self.assertEqual(media.volumen_aplicacion("discord", mute=True, sesiones=lambda: [discord]), "Silencié Discord.")
        discord.SimpleAudioVolume.SetMute.assert_called_with(1, None)
        self.assertIn("Quité el silencio", media.volumen_aplicacion("discord", mute=False, sesiones=lambda: [discord]))

    def test_unknown_app_lists_available(self) -> None:
        texto = media.volumen_aplicacion("spotify", 30, sesiones=lambda: [_sesion("steam.exe")])
        self.assertIn("No encuentro audio de «spotify»", texto)
        self.assertIn("Steam", texto)

    def test_validation(self) -> None:
        with self.assertRaises(ValueError):
            media.volumen_aplicacion("spotify", sesiones=lambda: [])
        with self.assertRaises(ValueError):
            media.volumen_aplicacion("spotify", 150, sesiones=lambda: [])


class PlaybackFallbackTests(unittest.TestCase):
    def test_without_session_pause_does_nothing_and_next_uses_key(self) -> None:
        tecla = Mock()
        with patch.object(media, "_ejecutar", return_value=None):
            self.assertIn("No hay nada", media.controlar_reproduccion("pause", tecla=tecla))
            tecla.assert_not_called()
            self.assertIn("siguiente pista", media.controlar_reproduccion("next", tecla=tecla))
            tecla.assert_called_once_with(0xB0)

    def test_with_session(self) -> None:
        with patch.object(media, "_ejecutar", return_value=("Spotify", True)):
            self.assertEqual(media.controlar_reproduccion("pause"), "Pausé Spotify.")
        with patch.object(media, "_ejecutar", return_value=("Spotify", False)):
            with self.assertRaises(OSError):
                media.controlar_reproduccion("next")

    def test_unknown_action(self) -> None:
        with self.assertRaises(ValueError):
            media.controlar_reproduccion("rewind")


class VerifierTests(unittest.TestCase):
    def test_playback_state(self) -> None:
        pausado = media.Sesion("Spotify", "pausado")
        ok = verificar_reproduccion({"action": "pause"}, None, leer=lambda: pausado, espera=0)
        self.assertTrue(ok.ok)
        mal = verificar_reproduccion({"action": "play"}, None, leer=lambda: pausado, espera=0)
        self.assertFalse(mal.ok)
        self.assertIsNone(verificar_reproduccion({"action": "next"}, None, leer=lambda: pausado).ok)

    def test_app_volume(self) -> None:
        args = {"app": "spotify", "percentage": 30}
        self.assertTrue(verificar_volumen_app(args, "ok", leer=lambda _a: 30).ok)
        self.assertFalse(verificar_volumen_app(args, "ok", leer=lambda _a: 80).ok)
        self.assertIsNone(verificar_volumen_app(args, "No encuentro audio", leer=lambda _a: 0).ok)


if __name__ == "__main__":
    unittest.main()
