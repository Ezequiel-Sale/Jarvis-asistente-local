import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from jarvis_app import JarvisApp


class CloseRequestTests(unittest.TestCase):
    def _host(self) -> Mock:
        host = Mock()
        host._closing = False
        host.SOLICITUD_CIERRE = JarvisApp.SOLICITUD_CIERRE
        return host

    def test_file_closes_jarvis_and_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as carpeta:
            solicitud = Path(carpeta) / JarvisApp.SOLICITUD_CIERRE
            solicitud.write_text("")
            host = self._host()
            with patch("jarvis_core.APP_DIR", Path(carpeta)):
                JarvisApp._check_close_request(host)
            host._close.assert_called_once()
            host.root.after.assert_not_called()
            self.assertFalse(solicitud.exists())

    def test_without_file_keeps_checking(self) -> None:
        with tempfile.TemporaryDirectory() as carpeta:
            host = self._host()
            with patch("jarvis_core.APP_DIR", Path(carpeta)):
                JarvisApp._check_close_request(host)
            host._close.assert_not_called()
            host.root.after.assert_called_once()


if __name__ == "__main__":
    unittest.main()
