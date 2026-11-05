import os
import subprocess
import sys
import threading
import unittest
import uuid
from pathlib import Path

from jarvis_single_instance import SingleInstance

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == "nt", "El mutex con nombre solo existe en Windows.")
class SingleInstanceTests(unittest.TestCase):
    def setUp(self) -> None:
        suffix = uuid.uuid4().hex
        # Nombres únicos para no chocar con un Jarvis real abierto.
        self.names = (f"Local\\JarvisTest_{suffix}_m", f"Local\\JarvisTest_{suffix}_e")
        self.first = SingleInstance(*self.names)
        self.addCleanup(self.first.release)

    def test_second_instance_is_rejected_and_first_is_notified(self) -> None:
        self.assertTrue(self.first.acquire())
        shown = threading.Event()
        self.first.start_listener(shown.set)

        second = SingleInstance(*self.names)
        self.assertFalse(second.acquire())
        self.assertTrue(second.notify_existing())
        self.assertTrue(shown.wait(2))

    def test_another_process_is_rejected(self) -> None:
        self.assertTrue(self.first.acquire())
        shown = threading.Event()
        self.first.start_listener(shown.set)
        code = (
            "import sys; sys.path.insert(0, sys.argv[1]);"
            "from jarvis_single_instance import SingleInstance;"
            "i = SingleInstance(sys.argv[2], sys.argv[3]);"
            "sys.exit(0 if (not i.acquire() and i.notify_existing()) else 1)"
        )
        result = subprocess.run(
            [sys.executable, "-c", code, str(ROOT), *self.names], timeout=20, check=False
        )
        self.assertEqual(result.returncode, 0)
        self.assertTrue(shown.wait(2))

    def test_released_instance_can_be_acquired_again(self) -> None:
        self.assertTrue(self.first.acquire())
        self.first.release()
        again = SingleInstance(*self.names)
        self.addCleanup(again.release)
        self.assertTrue(again.acquire())

    def test_notify_without_running_instance_returns_false(self) -> None:
        self.assertFalse(SingleInstance(*self.names).notify_existing())


if __name__ == "__main__":
    unittest.main()
