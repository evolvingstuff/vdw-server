import importlib.util
from pathlib import Path
from types import ModuleType
from unittest import TestCase


def load_deployment_manager() -> ModuleType:
    module_path = Path(__file__).resolve().parents[2] / "deployment-manager.py"
    spec = importlib.util.spec_from_file_location("deployment_manager", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DEPLOYMENT_MANAGER = load_deployment_manager()


class PreviousBootDiagnosticCommandTests(TestCase):
    def test_compact_diagnostics_include_bounded_previous_boot_kernel_evidence(self):
        commands = DEPLOYMENT_MANAGER.DockerDeployment._previous_boot_diagnostic_commands(
            detailed=False
        )
        command_text = "\n".join(commands)

        self.assertIn("recent boot history", command_text)
        self.assertIn("journalctl -b -1 -k", command_text)
        self.assertIn("out of memory", command_text)
        self.assertIn("tail -n 20", command_text)
        self.assertIn("previous boot connectivity failure indicators", command_text)
        self.assertIn("network is unreachable", command_text)
        self.assertNotIn("previous boot final events", command_text)

    def test_detailed_diagnostics_include_previous_boot_event_tail_newest_first(self):
        commands = DEPLOYMENT_MANAGER.DockerDeployment._previous_boot_diagnostic_commands(
            detailed=True
        )
        command_text = "\n".join(commands)

        self.assertIn("tail -n 50", command_text)
        self.assertIn("tail -n 40", command_text)
        self.assertIn("previous boot final events (newest first)", command_text)
        self.assertIn("journalctl -b -1 --no-pager --reverse", command_text)
        self.assertIn("-n 60", command_text)
        self.assertIn("No previous boot journal is available", command_text)

    def test_failure_pattern_does_not_match_benign_watchdog_status(self):
        pattern = DEPLOYMENT_MANAGER.PREVIOUS_BOOT_KERNEL_FAILURE_PATTERN

        self.assertNotIn("|watchdog|", f"|{pattern}|")
        self.assertIn("watchdog.*lockup", pattern)
        self.assertIn("NETDEV WATCHDOG", pattern)

    def test_connectivity_pattern_includes_ssm_and_route_failures(self):
        pattern = DEPLOYMENT_MANAGER.PREVIOUS_BOOT_CONNECTIVITY_FAILURE_PATTERN

        self.assertIn("network is unreachable", pattern)
        self.assertIn("no route to host", pattern)
        self.assertIn("EC2MetadataError", pattern)
        self.assertIn("Failed to connect to Systems Manager", pattern)
