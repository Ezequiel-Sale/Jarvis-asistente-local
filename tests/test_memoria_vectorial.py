"""Memoria por significado y aprendizaje automático. BD temporal y embeddings simulados."""

import sqlite3
import tempfile
import unittest
import zlib
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

import jarvis_aprendizaje as aprendizaje
import jarvis_core as core
import jarvis_embeddings
import jarvis_memory as memoria

TEMAS = ["cena", "brave", "boca", "perro", "monitor", "hija"]
SINONIMOS = {
    "club": "boca", "hincha": "boca", "mascota": "perro", "navegador": "brave", "ceno": "cena",
    "prefiero": "prefer", "prefiere": "prefer",
}
CUBETAS = 64


def vectorizar_falso(textos, *, consulta=False):
    """Un eje fuerte por tema (los sinónimos comparten eje, como en un embedding
    real) y ejes débiles por el resto de palabras, para distinguir variantes."""
    del consulta
    filas = []
    for texto in textos:
        palabras = texto.lower().replace("¿", " ").replace("?", " ").replace(".", " ").split()
        fila = [0.0] * (len(TEMAS) + CUBETAS)
        for palabra in {SINONIMOS.get(p, p) for p in palabras}:
            if palabra in TEMAS:
                fila[TEMAS.index(palabra)] = 1.0
            else:
                fila[len(TEMAS) + zlib.crc32(palabra.encode()) % CUBETAS] = 0.6
        filas.append(fila)
    matriz = np.asarray(filas, dtype=np.float32)
    return matriz / np.linalg.norm(matriz, axis=1, keepdims=True)


class _DbTestCase(unittest.TestCase):
    def setUp(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.db = Path(temp_dir.name) / "jarvis_memory.db"
        for patcher in (
            patch.object(core, "DB_PATH", self.db),
            patch.object(core, "_database_initialized", False),
            patch.object(jarvis_embeddings, "vectorizar", side_effect=vectorizar_falso),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        core.inicializar_base_datos()


class SemanticSearchTests(_DbTestCase):
    def test_finds_by_meaning_not_only_words(self) -> None:
        boca, _ = memoria.guardar_recuerdo("Mi equipo favorito es Boca")
        memoria.guardar_recuerdo("Mi perro se llama Rocco")
        self.assertEqual(memoria.buscar_recuerdos("¿de qué club soy hincha?"), [(boca, "Mi equipo favorito es Boca")])

    def test_unrelated_query_returns_nothing(self) -> None:
        memoria.guardar_recuerdo("Mi perro se llama Rocco")
        self.assertEqual(memoria.buscar_recuerdos("abre una pestaña nueva"), [])

    def test_falls_back_to_words_without_model(self) -> None:
        identificador, _ = memoria.guardar_recuerdo("Prefiero Brave")
        with patch.object(jarvis_embeddings, "vectorizar", return_value=None):
            self.assertEqual(memoria.buscar_recuerdos("uso brave"), [(identificador, "Prefiero Brave")])
            self.assertEqual(memoria.buscar_recuerdos("¿qué navegador uso?"), [])

    def test_vectors_follow_updates_and_deletes(self) -> None:
        identificador, _ = memoria.guardar_recuerdo("Mi perro se llama Rocco")
        memoria.actualizar_recuerdo(identificador, "Cena a las 21")
        self.assertEqual(memoria.buscar_recuerdos("¿a qué hora ceno?")[0][0], identificador)
        memoria.eliminar_recuerdo(identificador)
        with closing(sqlite3.connect(self.db)) as conexion:
            self.assertEqual(conexion.execute("SELECT COUNT(*) FROM memorias_vectores").fetchone()[0], 0)

    def test_missing_vectors_are_synced_later(self) -> None:
        with patch.object(jarvis_embeddings, "vectorizar", return_value=None):
            memoria.guardar_recuerdo("Mi equipo favorito es Boca")
        self.assertEqual(memoria.sincronizar_vectores(), 1)
        self.assertEqual(memoria.sincronizar_vectores(), 0)

    def test_similar_memories(self) -> None:
        memoria.guardar_recuerdo("Cena a las 21")
        memoria.guardar_recuerdo("Mi perro se llama Rocco")
        parecidos = memoria.buscar_parecidos("Cena a las 22", minimo=0.5)
        self.assertEqual([c for _, c, _ in parecidos], ["Cena a las 21"])


class MigrationTests(unittest.TestCase):
    def test_old_memories_table_gets_origin_column(self) -> None:
        with tempfile.TemporaryDirectory() as carpeta:
            db = Path(carpeta) / "jarvis_memory.db"
            with sqlite3.connect(db) as conexion:
                conexion.execute(
                    "CREATE TABLE memorias (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "contenido TEXT NOT NULL COLLATE NOCASE UNIQUE, creado TEXT)"
                )
                conexion.execute("INSERT INTO memorias (contenido) VALUES ('viejo')")
            conexion.close()
            with patch.object(core, "DB_PATH", db), patch.object(core, "_database_initialized", False):
                core.inicializar_base_datos()
                self.assertEqual(memoria.listar_recuerdos(origen="usuario"), [(1, "viejo")])


def _conversacion(*turnos: tuple[str, str]) -> str:
    conversacion = core.crear_conversacion()
    for usuario, asistente in turnos:
        core.guardar_turno(conversacion, usuario, asistente)
    return conversacion


class LearningTests(_DbTestCase):
    def test_learns_updates_and_skips_duplicates_and_secrets(self) -> None:
        cena, _ = memoria.guardar_recuerdo("Cena a las 21")
        memoria.guardar_recuerdo("Prefiero Brave")
        conversacion = _conversacion(("Ahora ceno a las 22:30 y sigo con Brave", "Ok"))
        hechos = ["Cena a las 22:30", "Prefiere Brave", "Su contraseña del wifi es gato", "Tiene un monitor LG"]
        decidir = Mock(return_value=cena)
        avisos = aprendizaje.aprender_de(conversacion, extraer=lambda _c: hechos, decidir=decidir)
        self.assertEqual(
            avisos,
            [f"Actualicé el recuerdo {cena}: Cena a las 22:30", "Aprendí (3): Tiene un monitor LG"],
        )
        self.assertEqual(memoria.listar_recuerdos(origen="automatica"), [(3, "Tiene un monitor LG")])
        # La segunda vez no hay mensajes nuevos que revisar.
        self.assertEqual(aprendizaje.aprender_de(conversacion, extraer=lambda _c: hechos, decidir=decidir), [])

    def test_only_updates_memories_it_was_shown(self) -> None:
        memoria.guardar_recuerdo("Cena a las 21")
        conversacion = _conversacion(("Ahora ceno a las 22:30 por el trabajo", "Ok"))
        avisos = aprendizaje.aprender_de(
            conversacion, extraer=lambda _c: ["Cena a las 22:30"], decidir=lambda *_: 999
        )
        self.assertEqual(avisos, ["Aprendí (2): Cena a las 22:30"])

    def test_short_conversations_are_skipped_but_marked(self) -> None:
        conversacion = _conversacion(("pausa", "Pausé Spotify."))
        extraer = Mock(return_value=["algo"])
        self.assertEqual(aprendizaje.aprender_de(conversacion, extraer=extraer), [])
        extraer.assert_not_called()
        self.assertEqual(aprendizaje.mensajes_nuevos(conversacion), [])

    def test_pending_only_after_inactivity(self) -> None:
        conversacion = _conversacion(("Me gusta el rock nacional para trabajar", "Anotado"))
        ahora = datetime.now(timezone.utc).replace(tzinfo=None)
        self.assertNotIn(conversacion, aprendizaje.conversaciones_pendientes(ahora))
        self.assertIn(conversacion, aprendizaje.conversaciones_pendientes(ahora + timedelta(minutes=11)))

    def test_disabled_does_nothing(self) -> None:
        aprendizaje.procesar_comando("desactiva el aprendizaje automático")
        self.assertFalse(aprendizaje.activo())
        with patch.object(aprendizaje, "conversaciones_pendientes") as pendientes:
            self.assertEqual(aprendizaje.procesar_pendientes(), [])
            pendientes.assert_not_called()
        aprendizaje.procesar_comando("activa el aprendizaje automático")
        self.assertTrue(aprendizaje.activo())

    def test_waits_after_ollama_failure(self) -> None:
        import requests

        _conversacion(("Me gusta el rock nacional para trabajar", "Anotado"))
        with patch.object(aprendizaje, "conversaciones_pendientes", return_value=["c"]), patch.object(
            aprendizaje, "aprender_de", side_effect=requests.HTTPError("500")
        ) as aprender, patch.object(aprendizaje, "_reintentar_desde", 0.0):
            self.assertEqual(aprendizaje.procesar_pendientes(), [])
            self.assertEqual(aprendizaje.procesar_pendientes(), [])
            aprender.assert_called_once()

    def test_list_command(self) -> None:
        self.assertIn("Todavía no aprendí", aprendizaje.procesar_comando("¿Qué aprendiste de mí?"))
        memoria.guardar_recuerdo("Tiene un perro", origen="automatica")
        memoria.guardar_recuerdo("Prefiero Brave")
        respuesta = aprendizaje.procesar_comando("qué aprendiste sobre mí")
        self.assertIn("Tiene un perro", respuesta)
        self.assertNotIn("Brave", respuesta)
        self.assertIsNone(aprendizaje.procesar_comando("¿qué recuerdas de mí?"))

    def test_sensitive_filter(self) -> None:
        for texto in ("Su PIN es 1234", "Toma medicación para la presión", "Su DNI es 30111222"):
            with self.subTest(texto=texto):
                self.assertIsNone(aprendizaje._limpio(texto))
        self.assertEqual(aprendizaje._limpio("  cena a las 21. "), "Cena a las 21")


if __name__ == "__main__":
    unittest.main()
