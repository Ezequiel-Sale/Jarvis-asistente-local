"""Archivos y portapapeles con el celular: rutas seguras, subida, listado, descarga y compartir.

Sin tocar las carpetas reales del usuario ni su portapapeles: todo en carpetas temporales
y con funciones falsas.
"""

import io
import json
import queue
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import jarvis_archivos as a
import jarvis_mobile as m

TS = "100.120.145.38"


class FolderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp())
        (self.base / "Fotos").mkdir()
        (self.base / "Fotos" / "playa.jpg").write_bytes(b"x" * 10)
        (self.base / "notas.txt").write_text("hola", encoding="utf-8")
        (self.base / "desktop.ini").write_text("", encoding="utf-8")

    def test_resolve_stays_inside(self) -> None:
        self.assertEqual(a.resolver(self.base, "Fotos/playa.jpg"), (self.base / "Fotos" / "playa.jpg").resolve())
        self.assertEqual(a.resolver(self.base, ""), self.base.resolve())
        for mala in ("../secreto.txt", "Fotos/../../x", "C:/Windows/win.ini", "Fotos/C:x"):
            with self.subTest(ruta=mala), self.assertRaises(a.ErrorArchivo):
                a.resolver(self.base, mala)
        # Las barras del principio se quitan: quedan dentro de la carpeta, no en la raíz del disco.
        for ruta in ("/etc/passwd", "\\\\servidor\\x"):
            self.assertIn(self.base.resolve(), a.resolver(self.base, ruta).parents)

    def test_listing_folders_first_without_system_files(self) -> None:
        elementos = a.listar(self.base)
        self.assertEqual([(e["name"], e["dir"]) for e in elementos], [("Fotos", True), ("notas.txt", False)])
        self.assertEqual(a.listar(self.base, "Fotos")[0]["size"], 10)
        with self.assertRaises(a.ErrorArchivo):
            a.listar(self.base, "no_existe")

    def test_safe_and_unique_names(self) -> None:
        self.assertEqual(a.nombre_seguro("../../malo:de?verdad.txt"), "malo_de_verdad.txt")
        self.assertEqual(a.nombre_seguro("CON"), "archivo_del_celular_CON")
        self.assertEqual(a.nombre_seguro(""), "archivo_del_celular")
        self.assertEqual(a.destino_libre(self.base, "notas.txt").name, "notas (1).txt")
        self.assertEqual(a.destino_libre(self.base, "nuevo.txt").name, "nuevo.txt")

    def test_upload_is_written_in_parts_and_cleaned_on_error(self) -> None:
        ruta = a.guardar_subida(io.BytesIO(b"datos del celular"), 17, "foto.jpg", self.base)
        self.assertEqual(ruta.read_bytes(), b"datos del celular")
        with self.assertRaisesRegex(a.ErrorArchivo, "se cortó"):
            a.guardar_subida(io.BytesIO(b"corto"), 100, "roto.bin", self.base)
        self.assertFalse(any(p.name.endswith(".jarvis-parcial") for p in self.base.iterdir()))
        self.assertFalse((self.base / "roto.bin").exists())
        with self.assertRaises(a.ErrorArchivo):
            a.guardar_subida(io.BytesIO(b""), 0, "vacio.txt", self.base)

    def test_other_disks_but_not_the_system_one(self) -> None:
        from jarvis_system_info import Disco

        discos = [Disco("C:", "", 1, 2), Disco("D:", "Juegos", 1, 2), Disco("E:", "", 1, 2)]
        with patch("jarvis_system_info.listar_discos", return_value=discos),                 patch.dict("os.environ", {"SystemDrive": "C:"}):
            self.assertEqual([r["key"] for r in a.raices()], ["descargas", "documentos", "disco:D", "disco:E"])
            self.assertEqual(a.raices()[2]["name"], "Disco D: (Juegos)")
            self.assertEqual(a.carpeta("disco:D"), Path("D:\\"))
            for mala in ("disco:C", "disco:Z", "escritorio"):
                with self.subTest(raiz=mala), self.assertRaises(a.ErrorArchivo):
                    a.carpeta(mala)

    def test_hidden_system_entries(self) -> None:
        (self.base / "$RECYCLE.BIN").mkdir()
        (self.base / "System Volume Information").mkdir()
        self.assertNotIn("$RECYCLE.BIN", [e["name"] for e in a.listar(self.base)])
        self.assertNotIn("System Volume Information", [e["name"] for e in a.listar(self.base)])

    def test_readable_size(self) -> None:
        self.assertEqual(a.tamano_legible(500), "500 B")
        self.assertEqual(a.tamano_legible(3 * 1024 * 1024 + 400_000), "3,4 MB")


class ServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp())
        (self.base / "informe.pdf").write_bytes(b"%PDF" * 100)
        self.portapapeles = {"texto": "copiado en la PC"}
        self.links: list[str] = []
        self.eventos: queue.Queue = queue.Queue()
        guardado: dict[str, str] = {}
        self.servidor = m.ServidorMovil(
            self.eventos, m.Vinculacion(guardado.get, guardado.__setitem__),
            carpetas=lambda raiz: self.base,
            raices=lambda: [{"key": "descargas", "name": "Descargas"}, {"key": "documentos", "name": "Documentos"},
                            {"key": "disco:D", "name": "Disco D:"}],
            abrir_link=lambda url: self.links.append(url) or "Abrí el link.",
            portapapeles=(lambda: self.portapapeles["texto"],
                          lambda texto: self.portapapeles.__setitem__("texto", texto)),
        )
        codigo = self.servidor.vinculacion.nuevo_codigo()
        respuesta = self.servidor.manejar("POST", "/api/pair", {}, json.dumps({"code": codigo, "device": "x"}).encode(), TS)
        self.token = json.loads(respuesta[2])["token"]
        self.eventos.get_nowait()
        self.cabeceras = {"Authorization": f"Bearer {self.token}"}

    def _pedir(self, metodo, ruta, cuerpo=None):
        respuesta = self.servidor.manejar(metodo, ruta, self.cabeceras, json.dumps(cuerpo or {}).encode(), TS)
        return respuesta[0], (json.loads(respuesta[2]) if respuesta[1] == "application/json" else respuesta)

    def test_share_link_opens_and_text_goes_to_clipboard(self) -> None:
        estado, datos = self._pedir("POST", "/api/share", {"text": "Mira esto: https://youtu.be/abc123."})
        self.assertEqual((estado, datos["text"]), (200, "Abrí el link."))
        self.assertEqual(self.links, ["https://youtu.be/abc123"])
        self.assertEqual(self.eventos.get_nowait(), ("mobile_shared", "link", "https://youtu.be/abc123."))
        self._pedir("POST", "/api/share", {"text": "una dirección: Calle 1"})
        self.assertEqual(self.portapapeles["texto"], "una dirección: Calle 1")
        self.assertEqual(self._pedir("POST", "/api/share", {"text": " "})[0], 400)

    def test_clipboard_both_ways(self) -> None:
        self.assertEqual(self._pedir("GET", "/api/clipboard")[1], {"text": "copiado en la PC"})
        self._pedir("POST", "/api/clipboard", {"text": "desde el celular"})
        self.assertEqual(self.portapapeles["texto"], "desde el celular")
        self.assertEqual(self._pedir("POST", "/api/clipboard", {"text": ""})[0], 400)

    def test_upload_needs_token_and_saves_to_downloads(self) -> None:
        estado, _tipo, _cuerpo = self.servidor.subir("/api/upload?name=foto.jpg", {}, TS, io.BytesIO(b"abc"), 3)
        self.assertEqual(estado, 401)
        estado, _tipo, cuerpo = self.servidor.subir("/api/upload?name=..%2Ffoto.jpg", self.cabeceras, TS,
                                                     io.BytesIO(b"abc"), 3)
        self.assertEqual((estado, json.loads(cuerpo)["name"]), (200, "foto.jpg"))
        self.assertEqual((self.base / "foto.jpg").read_bytes(), b"abc")
        self.assertEqual(self.eventos.get_nowait(), ("mobile_file_received", "foto.jpg", 3))
        self.assertEqual(self.servidor.subir("/api/upload?name=x", self.cabeceras, "192.168.1.5",
                                             io.BytesIO(b"abc"), 3)[0], 403)

    def test_list_and_download(self) -> None:
        estado, datos = self._pedir("GET", "/api/files?root=documentos")
        self.assertEqual((estado, [i["name"] for i in datos["items"]]), (200, ["informe.pdf"]))
        self.assertEqual(self._pedir("GET", "/api/files?root=escritorio")[0], 400)
        self.assertEqual(self._pedir("GET", "/api/files?root=disco:D")[0], 200)
        self.assertEqual(self._pedir("GET", "/api/files?root=disco:C")[0], 400)
        self.assertEqual([r["key"] for r in self._pedir("GET", "/api/files/roots")[1]["roots"]],
                         ["descargas", "documentos", "disco:D"])
        self.assertEqual(self._pedir("GET", "/api/files?root=documentos&path=..")[0], 404)
        estado, tipo, enviar, cabeceras = self.servidor.manejar(
            "GET", "/api/files/download?root=documentos&path=informe.pdf", self.cabeceras, b"", TS)
        salida = io.BytesIO()
        enviar(salida)
        self.assertEqual((estado, cabeceras["Content-Length"]), (200, "400"))
        self.assertEqual(salida.getvalue(), b"%PDF" * 100)
        self.assertIn("informe.pdf", cabeceras["Content-Disposition"])
        self.assertEqual(self._pedir("GET", "/api/files/download?root=documentos&path=no.pdf")[0], 404)


class ToolTests(unittest.TestCase):
    def test_send_file_only_from_allowed_folders(self) -> None:
        from jarvis_app import JarvisApp
        from jarvis_tool_registry import ToolError

        base = Path(tempfile.mkdtemp())
        (base / "Sub").mkdir()
        archivo = base / "Sub" / "foto.png"
        archivo.write_bytes(b"png")
        publicados = []
        host = SimpleNamespace(mobile=SimpleNamespace(
            activo=lambda: True, registro=SimpleNamespace(publicar=lambda tipo, **d: publicados.append((tipo, d)))))
        with patch.object(a, "carpeta", lambda raiz: base if raiz == "documentos" else base / "no_es"):
            texto = JarvisApp._tool_send_file_to_phone(host, str(archivo))
            with self.assertRaisesRegex(ToolError, "Descargas, Documentos o los otros discos"):
                JarvisApp._tool_send_file_to_phone(host, str(Path(tempfile.mkdtemp()) / "x.txt"))
        self.assertIn("foto.png", texto)
        self.assertEqual(publicados, [("file", {"root": "documentos", "path": "Sub/foto.png", "name": "foto.png",
                                                "size": 3})])

    def test_send_clipboard(self) -> None:
        from jarvis_app import JarvisApp

        publicados = []
        host = SimpleNamespace(mobile=SimpleNamespace(
            activo=lambda: True, registro=SimpleNamespace(publicar=lambda tipo, **d: publicados.append((tipo, d)))))
        with patch.object(a, "leer_portapapeles", return_value="clave wifi 123"):
            JarvisApp._tool_send_clipboard_to_phone(host)
        with patch.object(a, "leer_portapapeles", return_value="  "):
            self.assertIn("no tiene texto", JarvisApp._tool_send_clipboard_to_phone(host))
        self.assertEqual(publicados, [("clipboard", {"text": "clave wifi 123"})])


if __name__ == "__main__":
    unittest.main()
