import importlib.util
from pathlib import Path
from types import ModuleType
from unittest import TestCase
from unittest.mock import MagicMock, call, patch


def load_deployment_manager() -> ModuleType:
    module_path = Path(__file__).resolve().parents[2] / "deployment-manager.py"
    spec = importlib.util.spec_from_file_location("deployment_manager_management", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DEPLOYMENT_MANAGER = load_deployment_manager()


class ManagementHealthAlarmTests(TestCase):
    def setUp(self):
        self.deployer = DEPLOYMENT_MANAGER.DockerDeployment.__new__(
            DEPLOYMENT_MANAGER.DockerDeployment
        )
        self.deployer.provisioning = {
            "aws_region": "us-west-2",
            "management_alert_emails": [
                "tom.lahore@gmail.com",
                "hlahore@gmail.com",
            ],
        }
        self.sns = MagicMock()
        self.cloudwatch = MagicMock()
        self.sns.create_topic.return_value = {
            "TopicArn": "arn:aws:sns:us-west-2:123456789012:vdw-ec2-health-alerts"
        }
        self.sns.list_subscriptions_by_topic.return_value = {"Subscriptions": []}

        clients = {"sns": self.sns, "cloudwatch": self.cloudwatch}
        self.deployer._aws_client = lambda service: clients[service]

    def test_creates_email_subscription_and_instance_and_system_alarms(self):
        self.deployer.ensure_management_health_alarms("i-0123456789abcdef0")

        topic_arn = "arn:aws:sns:us-west-2:123456789012:vdw-ec2-health-alerts"
        self.assertEqual(self.sns.subscribe.call_count, 2)
        self.sns.subscribe.assert_has_calls(
            [
                call(
                    TopicArn=topic_arn,
                    Protocol="email",
                    Endpoint="tom.lahore@gmail.com",
                    ReturnSubscriptionArn=True,
                ),
                call(
                    TopicArn=topic_arn,
                    Protocol="email",
                    Endpoint="hlahore@gmail.com",
                    ReturnSubscriptionArn=True,
                ),
            ]
        )
        self.assertEqual(self.cloudwatch.put_metric_alarm.call_count, 2)

        alarms = {
            call.kwargs["MetricName"]: call.kwargs
            for call in self.cloudwatch.put_metric_alarm.call_args_list
        }
        instance_alarm = alarms["StatusCheckFailed_Instance"]
        self.assertEqual(instance_alarm["EvaluationPeriods"], 3)
        self.assertEqual(instance_alarm["DatapointsToAlarm"], 3)
        self.assertEqual(
            instance_alarm["AlarmActions"],
            [topic_arn, "arn:aws:automate:us-west-2:ec2:reboot"],
        )
        self.assertEqual(instance_alarm["OKActions"], [topic_arn])
        self.assertEqual(instance_alarm["TreatMissingData"], "missing")

        system_alarm = alarms["StatusCheckFailed_System"]
        self.assertEqual(system_alarm["EvaluationPeriods"], 2)
        self.assertEqual(system_alarm["DatapointsToAlarm"], 2)
        self.assertEqual(
            system_alarm["AlarmActions"],
            [topic_arn, "arn:aws:automate:us-west-2:ec2:recover"],
        )
        self.assertEqual(system_alarm["OKActions"], [topic_arn])
        self.assertEqual(system_alarm["TreatMissingData"], "missing")

    def test_existing_email_subscription_is_not_duplicated(self):
        self.sns.list_subscriptions_by_topic.return_value = {
            "Subscriptions": [
                {
                    "Protocol": "email",
                    "Endpoint": "tom.lahore@gmail.com",
                    "SubscriptionArn": "PendingConfirmation",
                },
                {
                    "Protocol": "email",
                    "Endpoint": "hlahore@gmail.com",
                    "SubscriptionArn": "confirmed-subscription-arn",
                },
            ]
        }

        self.deployer.ensure_management_health_alarms("i-0123456789abcdef0")

        self.sns.subscribe.assert_not_called()

    def test_management_alert_emails_are_required(self):
        self.deployer.provisioning = {"aws_region": "us-west-2"}

        with self.assertRaisesRegex(ValueError, "management_alert_emails"):
            self.deployer.ensure_management_health_alarms("i-0123456789abcdef0")

    def test_management_alert_emails_must_not_contain_duplicates(self):
        self.deployer.provisioning["management_alert_emails"] = [
            "tom.lahore@gmail.com",
            "tom.lahore@gmail.com",
        ]

        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.deployer.ensure_management_health_alarms("i-0123456789abcdef0")

    def test_enable_management_configures_alarms_before_agent_bootstrap(self):
        instance_id = "i-0123456789abcdef0"
        self.deployer._find_instance_by_public_ip = MagicMock(
            return_value={"InstanceId": instance_id, "Architecture": "x86_64"}
        )
        self.deployer.attach_instance_profile = MagicMock(return_value=True)
        self.deployer.ensure_management_health_alarms = MagicMock(return_value=True)
        self.deployer._wait_for_ssm_online = MagicMock(return_value=True)
        self.deployer._bootstrap_management_via_ssm = MagicMock(return_value=True)
        self.deployer._print_cloudwatch_metrics = MagicMock()

        result = self.deployer.enable_aws_management("44.228.184.247")

        self.assertTrue(result)
        self.deployer.ensure_management_health_alarms.assert_called_once_with(instance_id)
        self.deployer._bootstrap_management_via_ssm.assert_called_once_with(
            instance_id,
            "x86_64",
        )

    def test_management_bootstrap_uses_portable_posix_shell_options(self):
        commands = self.deployer._management_bootstrap_commands("x86_64")

        self.assertEqual(commands[0], "set -eux")
        self.assertNotIn("pipefail", "\n".join(commands))

    def test_management_bootstrap_warns_that_installation_can_take_five_minutes(self):
        self.deployer._run_ssm_shell_command = MagicMock(return_value=True)

        with patch("builtins.print") as print_mock:
            result = self.deployer._bootstrap_management_via_ssm(
                "i-0123456789abcdef0",
                "x86_64",
            )

        self.assertTrue(result)
        print_mock.assert_any_call("   This may take up to 5 minutes...")

    def test_ssm_command_prints_throttled_progress_while_running(self):
        ssm = MagicMock()
        ssm.send_command.return_value = {"Command": {"CommandId": "command-123"}}
        in_progress = {"Status": "InProgress"}
        ssm.get_command_invocation.side_effect = [in_progress] * 9 + [
            {"Status": "Success"}
        ]
        self.deployer._aws_client = MagicMock(return_value=ssm)
        elapsed_seconds = [0]

        def advance_clock(seconds):
            elapsed_seconds[0] += seconds

        with (
            patch.object(
                DEPLOYMENT_MANAGER.time,
                "monotonic",
                side_effect=lambda: elapsed_seconds[0],
            ),
            patch.object(DEPLOYMENT_MANAGER.time, "sleep", side_effect=advance_clock),
            patch("builtins.print") as print_mock,
        ):
            result = self.deployer._run_ssm_shell_command(
                instance_id="i-0123456789abcdef0",
                commands=["true"],
                comment="test command",
                timeout_seconds=300,
            )

        self.assertTrue(result)
        progress_messages = [
            call.args[0]
            for call in print_mock.call_args_list
            if call.args and "Still working" in call.args[0]
        ]
        self.assertEqual(
            progress_messages,
            ["   Still working... SSM status: InProgress (16s elapsed)"],
        )
