"""Regression tests for tools/filelock.py.

Run: python3 tests/test_filelock.py
"""
import os
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
import filelock  # noqa: E402
import gmail_send  # noqa: E402
import verify_email  # noqa: E402


class NoPlatformImportTests(unittest.TestCase):
    """The failure that was real and silent.

    `gmail_send` imported fcntl at module scope, so on Windows the import
    raised before anything ran and the daily send never started. Nothing was
    logged, because the logging sits behind the import, so the machine looked
    idle rather than broken.

    The check is on the module namespace, which is where a module scope import
    lands. Both backends import their module inside the function they belong
    to, so a platform only ever reaches for the one it has. The platform is
    deliberately not faked here: faking os.name to nt breaks the standard
    library, which consults it at call time, and a test that has to disable
    pathlib to run is testing the fake rather than the code.
    """

    def test_no_module_holds_fcntl_in_its_namespace(self):
        for module in (filelock, gmail_send, verify_email):
            self.assertFalse(hasattr(module, "fcntl"), f"{module.__name__} imports fcntl at module scope")

    def test_a_backend_is_chosen_for_the_platform(self):
        expected = filelock._windows if os.name == "nt" else filelock._posix
        self.assertIs(filelock._acquire, expected)
        self.assertEqual(filelock.WAITS_FOREVER, os.name != "nt")


class WindowsBackendTests(unittest.TestCase):
    """The Windows branch, run here against a stand in for msvcrt.

    The stand in keys its lock on the file, the way a byte range lock does, so
    a second handle on the same file is refused exactly as a second sender
    would be. It records the mode it was asked for, which is what shows the
    blocking and non blocking calls are not the same call.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "q.lock")
        self.asked = []
        fake = types.ModuleType("msvcrt")
        fake.LK_LOCK, fake.LK_NBLCK = 1, 2
        held = set()

        def locking(fd, mode, nbytes):
            self.asked.append(mode)
            key = (os.fstat(fd).st_ino, nbytes)
            if mode == fake.LK_NBLCK and key in held:
                raise OSError("locked")
            held.add(key)

        fake.locking = locking
        self.saved = sys.modules.get("msvcrt")
        sys.modules["msvcrt"] = fake

    def tearDown(self):
        if self.saved is None:
            sys.modules.pop("msvcrt", None)
        else:
            sys.modules["msvcrt"] = self.saved
        self.tmp.cleanup()

    def test_a_second_holder_is_refused(self):
        first = open(self.path, "a+")
        second = open(self.path, "a+")
        try:
            self.assertTrue(filelock._windows(first, False))
            self.assertFalse(filelock._windows(second, False))
        finally:
            first.close()
            second.close()

    def test_a_blocking_call_asks_for_the_blocking_mode(self):
        fh = open(self.path, "a+")
        try:
            self.assertTrue(filelock._windows(fh, True))
        finally:
            fh.close()
        self.assertEqual(self.asked, [1])


class LockTests(unittest.TestCase):
    """An exclusive lock is exclusive, whoever asks for it.

    Two handles in one process are enough to test this: both flock and
    msvcrt.locking key on the open file, not on the process, so a second
    handle is in the same position a second run of the sender would be in.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "q.lock")

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_second_holder_is_refused(self):
        first = open(self.path, "a+")
        second = open(self.path, "a+")
        try:
            self.assertTrue(filelock.try_lock(first))
            self.assertFalse(filelock.try_lock(second))
        finally:
            first.close()
            second.close()

    def test_the_lock_is_given_back_when_the_holder_closes(self):
        first = open(self.path, "a+")
        self.assertTrue(filelock.try_lock(first))
        first.close()
        second = open(self.path, "a+")
        try:
            self.assertTrue(filelock.try_lock(second))
        finally:
            second.close()

    def test_a_blocking_lock_waits_for_the_holder(self):
        order = []
        first = open(self.path, "a+")
        filelock.lock(first)

        def holder():
            time.sleep(0.3)
            order.append("released")
            first.close()

        t = threading.Thread(target=holder)
        t.start()
        second = open(self.path, "a+")
        try:
            filelock.lock(second)
            order.append("acquired")
        finally:
            second.close()
        t.join()
        self.assertEqual(order, ["released", "acquired"])


if __name__ == "__main__":
    unittest.main()
