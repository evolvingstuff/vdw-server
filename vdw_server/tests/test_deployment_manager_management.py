import importlib.util
from pathlib import Path
from types import ModuleType
from unittest import TestCase
from unittest.mock import MagicMock


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
            "management_alert_email": "tom.lahore@gmail.com",
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
        self.sns.subscribe.assert_called_once_with(
            TopicArn=topic_arn,
            Protocol="email",
            Endpoint="tom.lahore@gmail.com",
            ReturnSubscriptionArn=True,
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
                }
            ]
        }

        self.deployer.ensure_management_health_alarms("i-0123456789abcdef0")

        self.sns.subscribe.assert_not_called()

    def test_management_alert_email_is_required(self):
        self.deployer.provisioning = {"aws_region": "us-west-2"}

        with self.assertRaisesRegex(ValueError, "management_alert_email"):
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
