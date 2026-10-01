"""A clean clone loads safe defaults without local keys or sibling checkouts."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ConfigurationTests(unittest.TestCase):
    def test_clean_checkout_and_external_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            project = base / "toolkit"
            (project / "src").mkdir(parents=True)
            shutil.copy2(ROOT / "src/config.py", project / "src/config.py")
            shutil.copy2(ROOT / "config.example.ini", project / "config.example.ini")
            environment = {k: v for k, v in os.environ.items() if not k.startswith("IDOLY_")}
            environment["PYTHONPATH"] = str(project)
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            script = ('import json; from src.config import FILE_KEY, FILE_IV, ASSET_PATH, MAX_RETRIES; '
                      'print(json.dumps([len(FILE_KEY), len(FILE_IV), ASSET_PATH, MAX_RETRIES]))')
            def inspect():
                result = subprocess.run([sys.executable, "-c", script], cwd=base,
                                        env=environment, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                return json.loads(result.stdout)
            self.assertEqual(inspect(), [0, 0, str(project / "cache/asset"), 5])
            config = base / "private.ini"
            config.write_text("[Download settings]\nMAX_RETRIES = 2\n", encoding="utf-8")
            environment.update(IDOLY_TOOLKIT_CONFIG=str(config), IDOLY_FILE_KEY="00" * 16,
                               IDOLY_FILE_IV="00" * 16, IDOLY_ASSET_PATH=str(base / "downloads"))
            self.assertEqual(inspect(), [16, 16, str(base / "downloads"), 2])


if __name__ == "__main__":
    unittest.main()
