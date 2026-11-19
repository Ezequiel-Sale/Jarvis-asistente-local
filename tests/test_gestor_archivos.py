"""Gestión de archivos, «ordená Descargas» y el portapapeles: en una carpeta temporal, sin la
papelera real ni el portapapeles de Windows."""

import os
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

import jarvis_gestor_archivos as g


class ConCarpeta(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.raiz = Path(self._tmp.name).resolve() / "Descargas"
        self.raiz.mkdir()
        self.deshacer = []
        for objetivo, valor in (("raices_permitidas", lambda: [self.raiz]),
                                ("anotar_deshacer", lambda d, f: self.deshacer.append((d, f))),
                                ("_a_la_papelera", Mock())):
            parche = patch.object(g, objetivo, valor)
            parche.start()
            self.addCleanup(parche.stop)

    def archivo(self, nombre, contenido=b"hola", dias=0):
        ruta = self.raiz / nombre
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_bytes(contenido)
        if dias:
            viejo = time.time() - dias * 86400
            os.utime(ruta, (viejo, viejo))
        return ruta


class AccionesTests(ConCarpeta):
    def test_outside_and_main_folders_are_refused(self):
        with self.assertRaisesRegex(g.ErrorArchivo, "fuera"):
            g.accion("trash", "C:/Windows/notepad.exe")
        with self.assertRaisesRegex(g.ErrorArchivo, "carpetas principales"):
            g.accion("trash", str(self.raiz))
        with self.assertRaisesRegex(g.ErrorArchivo, "fuera"):
            g.accion("move", str(self.raiz / ".." / "otro.txt"))

    def test_rename_keeps_extension_and_undoes(self):
        origen = self.archivo("informe viejo.pdf")
        self.assertIn("«informe final.pdf»", g.accion("rename", str(origen), new_name="informe final"))
        self.assertTrue((self.raiz / "informe final.pdf").exists())
        self.deshacer[-1][1]()
        self.assertTrue(origen.exists())

    def test_move_to_a_folder_without_overwriting_and_undo(self):
        origen = self.archivo("foto.png")
        self.archivo("Fotos/foto.png", b"otra")
        g.accion("move", str(origen), destination=str(self.raiz / "Fotos"))
        self.assertTrue((self.raiz / "Fotos" / "foto (1).png").exists())
        self.assertEqual((self.raiz / "Fotos" / "foto.png").read_bytes(), b"otra")
        self.deshacer[-1][1]()
        self.assertTrue(origen.exists())

    def test_zip_and_unzip(self):
        self.archivo("proyecto/a.txt")
        self.archivo("proyecto/sub/b.txt")
        g.accion("zip", str(self.raiz / "proyecto"))
        with zipfile.ZipFile(self.raiz / "proyecto.zip") as z:
            self.assertEqual(sorted(z.namelist()), ["proyecto/a.txt", "proyecto/sub/b.txt"])
        g.accion("unzip", str(self.raiz / "proyecto.zip"))
        self.assertTrue((self.raiz / "proyecto (1)" / "proyecto" / "sub" / "b.txt").exists())

    def test_zip_slip_is_refused(self):
        malo = self.raiz / "malo.zip"
        with zipfile.ZipFile(malo, "w") as z:
            z.writestr("../fuera.txt", "x")
        with self.assertRaisesRegex(g.ErrorArchivo, "fuera de su carpeta"):
            g.accion("unzip", str(malo))
        self.assertFalse((self.raiz.parent / "fuera.txt").exists())

    def test_trash_goes_to_the_recycle_bin_and_undo_restores(self):
        origen = self.archivo("borrar.txt")
        self.assertIn("papelera", g.accion("trash", str(origen)))
        g._a_la_papelera.assert_called_once_with(origen)
        with patch.object(g, "restaurar_de_papelera") as restaurar:
            self.deshacer[-1][1]()
        restaurar.assert_called_once_with(origen)


class OrdenarTests(ConCarpeta):
    def setUp(self):
        super().setUp()
        parche = patch.object(g.jarvis_archivos, "carpeta", return_value=self.raiz)
        parche.start()
        self.addCleanup(parche.stop)

    def test_plan_counts_and_finds_duplicates_and_old_installers(self):
        self.archivo("a.jpg", b"1")
        self.archivo("b.pdf", b"22")
        self.archivo("b (1).pdf", b"22")
        self.archivo("setup.exe", b"333", dias=60)
        self.archivo("bajando.crdownload", b"4")
        plan = g.planear(self.raiz)
        self.assertEqual(len(plan.movimientos), 4)
        self.assertEqual([(c.name, o.name) for c, o in plan.duplicados], [("b.pdf", "b (1).pdf")]
                         if plan.duplicados[0][0].name == "b.pdf" else [("b (1).pdf", "b.pdf")])
        self.assertEqual([a.name for a in plan.instaladores_viejos], ["setup.exe"])
        texto = g.describir_plan(plan)
        self.assertIn("Movería 4 archivos", texto)
        self.assertIn("1 archivos duplicados", texto)

    def test_apply_moves_and_undo_returns_everything(self):
        self.archivo("a.jpg")
        self.archivo("c.mp3")
        self.assertIn("ordené 2 archivos", g.ordenar("downloads", "apply"))
        self.assertTrue((self.raiz / "Imágenes" / "a.jpg").exists())
        self.assertTrue((self.raiz / "Música" / "c.mp3").exists())
        self.deshacer[-1][1]()
        self.assertTrue((self.raiz / "a.jpg").exists())
        self.assertTrue((self.raiz / "c.mp3").exists())

    def test_yes_order_it_only_after_a_plan(self):
        g._ultimo_plan.clear()
        self.assertIsNone(g.detectar("sí, ordenala"))
        self.assertEqual(g.detectar("Ordená Descargas"), ("organize_folder", {"folder": "downloads", "step": "plan"}))
        g.ordenar("downloads", "plan")
        self.assertEqual(g.detectar("sí, ordenala"), ("organize_folder", {"folder": "downloads", "step": "apply"}))
        self.assertEqual(g.detectar("Mandá los duplicados a la papelera"),
                         ("organize_folder", {"folder": "downloads", "step": "trash_duplicates"}))
        g._ultimo_plan.clear()


class PortapapelesTests(unittest.TestCase):
    def armar(self, protegida=False):
        self.secuencia = 0
        self.texto = ""
        self.reloj = 1000.0
        return g.HistorialPortapapeles(leer=lambda: self.texto, secuencia=lambda: self.secuencia,
                                       ventana_protegida=lambda: protegida, reloj=lambda: self.reloj)

    def copiar(self, historial, texto):
        self.secuencia += 1
        self.texto = texto
        historial.revisar()

    def test_keeps_recent_text_newest_first_without_repeats(self):
        h = self.armar()
        for texto in ("uno", "dos", "uno", "tres"):
            self.copiar(h, texto)
        self.assertEqual([c.texto for c in h.recientes()], ["tres", "uno", "dos"])
        self.reloj += g.VIGENCIA_PORTAPAPELES + 1
        self.assertEqual(h.recientes(), [])

    def test_protected_windows_are_not_recorded(self):
        h = self.armar(protegida=True)
        self.copiar(h, "clave del banco")
        self.assertEqual(h.recientes(), [])

    def test_paste_the_previous_one(self):
        h = self.armar()
        for texto in ("primero", "segundo"):
            self.copiar(h, texto)
        escribir, pegar = Mock(), Mock()
        self.assertIn("«primero»", g.usar_historial(h, "paste", 2, escribir=escribir, pegar=pegar))
        escribir.assert_called_once_with("primero")
        pegar.assert_called_once()
        self.assertTrue(h.ignorar_siguiente)  # lo que puso Jarvis no se vuelve a anotar
        self.assertIn("1. segundo", g.usar_historial(h, "list"))
        with self.assertRaises(ValueError):
            g.usar_historial(h, "paste", 5, escribir=escribir, pegar=pegar)

    def test_phrases(self):
        self.assertEqual(g.detectar("¿Qué copié antes?"), ("clipboard_history", {"action": "list"}))
        self.assertEqual(g.detectar("Pegá lo que copié antes"), ("clipboard_history", {"action": "paste", "number": 2}))
        self.assertEqual(g.detectar("Pegá el tercero del portapapeles"),
                         ("clipboard_history", {"action": "paste", "number": 3}))


class AppTests(unittest.TestCase):
    def test_result_number_becomes_a_path(self):
        from jarvis_app import JarvisApp

        host = Mock(_FILE_RESULTS_TTL=600)
        host._last_file_results = (time.monotonic(), [Mock(ruta="C:/a.txt"), Mock(ruta="C:/b.txt")])
        self.assertEqual(JarvisApp._ruta_de_resultado(host, "2"), "C:/b.txt")
        self.assertEqual(JarvisApp._ruta_de_resultado(host, "C:/x.txt"), "C:/x.txt")
        with self.assertRaises(ValueError):
            JarvisApp._ruta_de_resultado(host, "#5")

    def test_routed_for_text_and_voice(self):
        fuente = (Path(__file__).resolve().parent.parent / "jarvis_app_ordenes.py").read_text("utf-8")
        self.assertIn("jarvis_gestor_archivos.detectar(text)", fuente)
        self.assertIn("jarvis_gestor_archivos.detectar(transcript)", fuente)


if __name__ == "__main__":
    unittest.main()
