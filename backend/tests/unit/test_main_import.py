import importlib
import unittest


class MainImportTests(unittest.TestCase):
    def test_backend_main_import_is_thin(self):
        module = importlib.import_module("backend.main")

        self.assertTrue(callable(module.main))


if __name__ == "__main__":
    unittest.main()