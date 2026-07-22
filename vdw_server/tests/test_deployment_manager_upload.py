import importlib.util
from pathlib import Path
from types import ModuleType
from unittest import TestCase


def load_deployment_manager() -> ModuleType:
    module_path = Path(__file__).resolve().parents[2] / "deployment-manager.py"
    spec = importlib.util.spec_from_file_location("deployment_manager_upload", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DEPLOYMENT_MANAGER = load_deployment_manager()
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class RootCodeUploadPatternTests(TestCase):
    def test_patterns_select_every_required_root_deployment_file(self):
        selected_files = {
            file_path.name
            for pattern in DEPLOYMENT_MANAGER.ROOT_CODE_UPLOAD_PATTERNS
            for file_path in PROJECT_ROOT.glob(pattern)
            if file_path.name not in {".env", "db.sqlite3"}
        }
        required_files = {
            ".dockerignore",
            "Dockerfile",
            "docker-compose.yml",
            "docker-entrypoint.sh",
            "requirements.txt",
        }

        self.assertEqual(set(), required_files - selected_files)
