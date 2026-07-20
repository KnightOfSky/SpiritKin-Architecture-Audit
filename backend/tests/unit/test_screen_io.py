import importlib
import sys
import unittest
from unittest import mock


class ScreenIOTests(unittest.TestCase):
    def test_screen_io_import_does_not_require_pytesseract(self):
        module_name = "backend.perception.screen_io"
        original_module = sys.modules.pop(module_name, None)
        try:
            with mock.patch.dict(sys.modules, {"pytesseract": None}):
                module = importlib.import_module(module_name)
                self.assertTrue(callable(module.take_screenshot))
        finally:
            sys.modules.pop(module_name, None)
            if original_module is not None:
                sys.modules[module_name] = original_module


if __name__ == "__main__":
    unittest.main()

