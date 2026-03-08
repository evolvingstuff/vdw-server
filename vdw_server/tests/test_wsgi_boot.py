import os
import subprocess
import sys
import textwrap
from pathlib import Path

from django.test import SimpleTestCase


class WsgiBootstrapTests(SimpleTestCase):
    def test_wsgi_bootstraps_when_settings_are_not_preconfigured(self):
        repo_root = Path(__file__).resolve().parents[2]
        env = os.environ.copy()
        env.pop("DJANGO_SETTINGS_MODULE", None)
        env["RUN_MAIN"] = "bootstrap-probe"
        probe = textwrap.dedent(
            """
            import os
            import runpy

            os.environ.pop("DJANGO_SETTINGS_MODULE", None)
            namespace = runpy.run_module("vdw_server.wsgi", run_name="__wsgi_probe__")
            print("application_present=", "application" in namespace)
            print("settings_module=", os.environ.get("DJANGO_SETTINGS_MODULE"))
            """
        )

        result = subprocess.run(
            [sys.executable, "-c", probe],
            cwd=repo_root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, msg=result.stderr)
        self.assertIn("application_present= True", result.stdout)
        self.assertIn("settings_module= vdw_server.settings", result.stdout)
