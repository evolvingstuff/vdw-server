#!/usr/bin/env python3
"""
Docker-based deployment script for VDW Server
Clean, simple deployment without the Bitnami nightmare
"""

import json
import os
import shlex
import socket
import ssl
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
import http.client
from pathlib import Path
from typing import Callable, Dict, List, Optional
import io

import boto3
import paramiko
from botocore.exceptions import ClientError
from dotenv import load_dotenv
from scp import SCPClient

# Load environment variables
load_dotenv()

MANUAL_BACKUP_PREFIX = "db_backups/manual_backups"
ACME_WEBROOT = "/var/www/letsencrypt"
MAINTENANCE_PAGE_REMOTE_DIR = "/var/www/vdw"
MAINTENANCE_PAGE_REMOTE_PATH = f"{MAINTENANCE_PAGE_REMOTE_DIR}/maintenance.html"
CLOUDWATCH_AGENT_CONFIG_REMOTE_PATH = "/opt/aws/amazon-cloudwatch-agent/etc/cloudwatch-agent.json"
DEFAULT_MANAGEMENT_INSTANCE_PROFILE = "vdw-ec2-management"

class DockerDeployment:
    def __init__(self):
        self.config = {
            'host': os.getenv('DEPLOY_HOST'),
            'user': os.getenv('DEPLOY_USER'),
            'port': int(os.getenv('DEPLOY_PORT')),
            'key_file': os.getenv('DEPLOY_KEY_FILE'),
            'app_path': os.getenv('DEPLOY_APP_PATH'),
            'local_db': os.getenv('DEPLOY_LOCAL_DB'),
            'django_port': os.getenv('DJANGO_PORT'),
        }
        
        self.provision_state_path = Path('tmp/provision-state.json')
        self.provision_config_path = Path('config/provisioning.json')
        self._aws_session: Optional[boto3.session.Session] = None
        self.provisioning: Optional[Dict] = self._load_provisioning_config(required=False)
        
        self.latest_state = self._load_provision_state()
        self.production_host = self.config['host'] or ''
        if not self.production_host and self.provisioning:
            self.production_host = (
                self.provisioning.get('elastic_hostname')
                or self.provisioning.get('elastic_ip_address')
                or ''
            )

        self.active_host = None
        self.active_host_label = ''
        latest_ip = (self.latest_state or {}).get('public_ip')
        if latest_ip:
            self.set_active_host(latest_ip, 'test', announce=False)
        elif self.production_host:
            self.set_active_host(self.production_host, 'prod', announce=False)

        # Validate required config for general deploy actions
        required_fields = ['user', 'port', 'key_file', 'app_path', 'local_db', 'django_port']
        for field in required_fields:
            if not self.config[field]:
                print(f"❌ {field.upper().replace('_', '_')} not set in .env file")
                sys.exit(1)
        if not self.active_host:
            print(
                "❌ Could not determine a target host. Set elastic_hostname in config/provisioning.json"
            )
            sys.exit(1)

        self.ssh_client = None

    def _load_provisioning_config(self, required: bool = True) -> Optional[Dict]:
        if not self.provision_config_path.exists():
            if required:
                print("❌ Missing config/provisioning.json. Run option 0 to capture settings.")
                sys.exit(1)
            return None

        try:
            data = json.loads(self.provision_config_path.read_text())
        except json.JSONDecodeError as exc:
            print(f"❌ Failed to parse {self.provision_config_path}: {exc}")
            sys.exit(1)

        # Normalize some fields
        data['extra_ports'] = [int(p) for p in data.get('extra_ports', [])]
        if isinstance(data.get('associate_public_ip'), str):
            data['associate_public_ip'] = data['associate_public_ip'].lower() != 'false'

        return data

    def _write_provisioning_config(self, payload: Dict) -> None:
        self.provision_config_path.parent.mkdir(parents=True, exist_ok=True)
        self.provision_config_path.write_text(json.dumps(payload, indent=2))
        print(f"📝 Saved provisioning config to {self.provision_config_path}")

    def _get_provisioning(self) -> Dict:
        if self.provisioning is None:
            self.provisioning = self._load_provisioning_config(required=True)
        return self.provisioning

    def _domain_config(self) -> Dict:
        config = self._get_provisioning()
        primary = (config.get('primary_domain') or '').strip()
        alt_domains = config.get('alt_domains') or []
        if isinstance(alt_domains, str):
            alt_domains = [d.strip() for d in alt_domains.split(',') if d.strip()]
        email = (config.get('certbot_email') or '').strip()
        return {
            'primary': primary,
            'alts': alt_domains,
            'email': email,
        }

    def _all_domains(self) -> List[str]:
        cfg = self._domain_config()
        domains = []
        if cfg['primary']:
            domains.append(cfg['primary'])
        domains.extend(d for d in cfg['alts'] if d)
        return domains

    def _ssl_paths(self) -> Dict[str, str]:
        config = self._get_provisioning()
        cfg = self._domain_config()
        primary = cfg['primary']
        base_path = f"/etc/letsencrypt/live/{primary}"
        cert_path = config.get('ssl_certificate_path') or f"{base_path}/fullchain.pem"
        key_path = config.get('ssl_certificate_key_path') or f"{base_path}/privkey.pem"
        chain_path = config.get('ssl_trusted_path') or f"{base_path}/chain.pem"
        return {
            'cert': cert_path,
            'key': key_path,
            'chain': chain_path,
        }

    def set_active_host(self, host: str, label: str, announce: bool = True) -> None:
        host = host.strip()
        if not host:
            return
        self.active_host = host
        self.active_host_label = label
        if announce:
            print(f"🎯 Active target set to {host} ({label})")

    def check_git_branch(self):
        """Check current git branch and warn about risky local deploy state."""
        try:
            result = subprocess.run(['git', 'branch', '--show-current'],
                                  capture_output=True, text=True, cwd='.')
            if result.returncode != 0:
                print("⚠️  Could not determine git branch (not in a git repository?)")
                return True  # Continue deployment if git check fails

            current_branch = result.stdout.strip()
            if current_branch != 'main':
                print(f"\n⚠️  WARNING: You are on branch '{current_branch}', not 'main'")
                print("   Deployment will upload your current local code regardless of branch.")
                print("   Consider switching to 'main' or pushing your changes first.")

                response = input(f"\n   Continue deploying from '{current_branch}'? (y/n): ").lower()
                if response != 'y':
                    print("❌ Deployment cancelled")
                    return False

                print(f"   Proceeding with deployment from '{current_branch}'...")

            status_result = subprocess.run(
                ['git', 'status', '--short'],
                capture_output=True,
                text=True,
                cwd='.',
            )
            if status_result.returncode != 0:
                print("⚠️  Could not determine whether the working tree has uncommitted changes")
                return True

            changed_entries = [
                line for line in status_result.stdout.splitlines()
                if line.strip()
            ]
            if changed_entries:
                print("\n⚠️  WARNING: You have uncommitted local changes on this machine.")
                print("   Deployment will upload these uncommitted changes to the server.")
                preview_limit = 10
                for entry in changed_entries[:preview_limit]:
                    print(f"   {entry}")
                remaining_count = len(changed_entries) - preview_limit
                if remaining_count > 0:
                    print(f"   ... plus {remaining_count} more changed files")

                response = input("\n   Continue deploying with uncommitted changes? (y/n): ").lower()
                if response != 'y':
                    print("❌ Deployment cancelled")
                    return False

                print("   Proceeding with uncommitted changes...")

            return True

        except Exception as e:
            print(f"⚠️  Git branch check failed: {e}")
            return True  # Continue deployment if git check fails

    @staticmethod
    def _prompt_with_default(prompt: str, default: str) -> str:
        value = input(f"{prompt} [{default}]: ").strip()
        return value or default

    @staticmethod
    def _prompt_int(prompt: str, default: int) -> int:
        while True:
            value = input(f"{prompt} [{default}]: ").strip()
            if not value:
                return default
            try:
                return int(value)
            except ValueError:
                print("❌ Please enter a whole number.")

    def capture_provisioning_config(self):
        """Populate config/provisioning.json using the current server as a template."""
        print("\n" + "=" * 50)
        print("CAPTURE EXISTING SERVER SETTINGS")
        print("=" * 50)

        existing = self.provisioning or {}
        default_region = existing.get('aws_region') or os.getenv('AWS_REGION') or 'us-east-1'
        region = self._prompt_with_default("AWS region", default_region)
        default_profile = existing.get('aws_profile') or os.getenv('AWS_PROFILE') or ''
        profile_input = input(f"AWS profile (press Enter for default) [{default_profile}]: ").strip()
        profile = profile_input or default_profile or None

        host_default = self.config['host'] or ''
        elastic_ip_input = self._prompt_with_default(
            "Elastic IP or hostname currently serving production",
            host_default or '1.2.3.4'
        )
        try:
            resolved_ip = socket.gethostbyname(elastic_ip_input)
        except socket.gaierror as exc:
            print(f"❌ Could not resolve {elastic_ip_input}: {exc}")
            return False

        session_kwargs = {'region_name': region}
        if profile:
            session_kwargs['profile_name'] = profile
        session = boto3.session.Session(**session_kwargs)
        ec2 = session.client('ec2')

        try:
            addr_resp = ec2.describe_addresses(PublicIps=[resolved_ip])
        except ClientError as exc:
            print(f"❌ describe_addresses failed: {exc}")
            return False

        addresses = addr_resp.get('Addresses', [])
        if not addresses:
            print("❌ No Elastic IP found for that address. Confirm the IP or region.")
            return False

        address = addresses[0]
        allocation_id = address.get('AllocationId')
        if not allocation_id:
            print("❌ Could not determine Elastic IP allocation ID")
            return False
        instance_id = address.get('InstanceId')
        if instance_id:
            print(f"🔍 Elastic IP currently attached to instance {instance_id}")
        else:
            instance_id = input("Elastic IP not attached. Enter instance ID to copy config from: ").strip()
            if not instance_id:
                print("❌ Instance ID is required")
                return False

        try:
            reservations = ec2.describe_instances(InstanceIds=[instance_id])['Reservations']
        except ClientError as exc:
            print(f"❌ describe_instances failed: {exc}")
            return False

        if not reservations or not reservations[0]['Instances']:
            print("❌ Instance not found")
            return False

        instance = reservations[0]['Instances'][0]

        block_mappings = instance.get('BlockDeviceMappings', [])
        volume_ids = [dev['Ebs']['VolumeId'] for dev in block_mappings if dev.get('Ebs')]
        volumes = {}
        if volume_ids:
            vol_resp = ec2.describe_volumes(VolumeIds=volume_ids)
            for vol in vol_resp.get('Volumes', []):
                volumes[vol['VolumeId']] = vol['Size']

        root_device_name = instance.get('RootDeviceName', '/dev/sda1')
        root_mapping = next((dev for dev in block_mappings if dev.get('DeviceName') == root_device_name and dev.get('Ebs')), None)
        root_volume_size = volumes.get(root_mapping['Ebs']['VolumeId']) if root_mapping else 40

        data_mapping = next((dev for dev in block_mappings if dev.get('DeviceName') != root_device_name and dev.get('Ebs')), None)
        data_volume_size = 0
        data_device_name = '/dev/sdf'
        if data_mapping:
            data_device_name = data_mapping['DeviceName']
            data_volume_size = volumes.get(data_mapping['Ebs']['VolumeId'], 0)

        instance_type_default = instance.get('InstanceType', 't3.small')
        instance_type = self._prompt_with_default("Instance type for new servers", instance_type_default)
        root_volume_gb = self._prompt_int("Root volume size (GB)", root_volume_size or 40)
        data_volume_gb = self._prompt_int("Data volume size for /app/data (GB, 0 = skip)", data_volume_size or 0)
        ssh_cidr_default = existing.get('ssh_ingress_cidr', '0.0.0.0/0')
        ssh_cidr = self._prompt_with_default("SSH ingress CIDR", ssh_cidr_default)

        security_groups = instance.get('SecurityGroups', [])
        if not security_groups:
            print("❌ Instance has no security groups attached")
            return False
        if len(security_groups) > 1:
            print("⚠️  Multiple security groups detected; using the first one.")
        sg_id = security_groups[0]['GroupId']
        sg_name = security_groups[0].get('GroupName')

        iam_profile = instance.get('IamInstanceProfile', {}).get('Arn')
        iam_profile_name = iam_profile.split('/')[-1] if iam_profile else None

        tags = instance.get('Tags', [])
        tag_spec = ','.join(f"{t['Key']}={t['Value']}" for t in tags if 'Key' in t and 'Value' in t)

        payload = {
            'aws_region': region,
            'aws_profile': profile or '',
            'instance_type': instance_type,
            'ami_id': instance.get('ImageId'),
            'subnet_id': instance.get('SubnetId'),
            'vpc_id': instance.get('VpcId'),
            'security_group_id': sg_id,
            'security_group_name': sg_name,
            'key_name': instance.get('KeyName'),
            'root_volume_gb': root_volume_gb,
            'data_volume_gb': data_volume_gb,
            'root_device_name': root_device_name,
            'data_device_name': data_device_name,
            'iam_instance_profile': iam_profile_name,
            'tag_specification': tag_spec,
            'associate_public_ip': True,
            'ssh_ingress_cidr': ssh_cidr,
            'extra_ports': [],
            'elastic_ip_allocation_id': allocation_id,
            'elastic_hostname': elastic_ip_input,
            'elastic_ip_address': resolved_ip,
        }

        self._write_provisioning_config(payload)
        self.provisioning = payload
        print("✅ Provisioning config captured. You can now run option 1 to create a new server.")
        return True
    
    def connect(self, host_override: Optional[str] = None):
        """Establish SSH connection"""
        try:
            target_host = host_override or self.active_host
            print(f"🔌 Connecting to {target_host}...")
            self.ssh_client = paramiko.SSHClient()
            self.ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            if self.config['key_file']:
                self.ssh_client.connect(
                    hostname=target_host,
                    username=self.config['user'],
                    port=self.config['port'],
                    key_filename=os.path.expanduser(self.config['key_file'])
                )
            else:
                self.ssh_client.connect(
                    hostname=target_host,
                    username=self.config['user'],
                    port=self.config['port']
                )
            
            print("✅ Connected successfully!")
            return True
            
        except Exception as e:
            print(f"❌ Connection failed: {e}")
            return False
    
    def disconnect(self):
        """Close SSH connection"""
        if self.ssh_client:
            self.ssh_client.close()
            print("🔌 Disconnected from server")

    def _find_instance_by_public_ip(self, host: str) -> Optional[Dict]:
        ec2 = self._aws_client('ec2')
        response = ec2.describe_instances(Filters=[{'Name': 'ip-address', 'Values': [host]}])
        for reservation in response.get('Reservations', []):
            for instance in reservation.get('Instances', []):
                if instance.get('PublicIpAddress') == host:
                    return instance
        return None

    @staticmethod
    def _permission_sources(permission: Dict) -> List[str]:
        sources = [item['CidrIp'] for item in permission.get('IpRanges', []) if item.get('CidrIp')]
        sources.extend(item['CidrIpv6'] for item in permission.get('Ipv6Ranges', []) if item.get('CidrIpv6'))
        sources.extend(
            pair['GroupId']
            for pair in permission.get('UserIdGroupPairs', [])
            if pair.get('GroupId')
        )
        sources.extend(
            prefix['PrefixListId']
            for prefix in permission.get('PrefixListIds', [])
            if prefix.get('PrefixListId')
        )
        return sources

    def _summarize_port_exposure(self, security_group: Dict, port: int) -> str:
        exposures: List[str] = []
        for permission in security_group.get('IpPermissions', []):
            protocol = permission.get('IpProtocol')
            from_port = permission.get('FromPort')
            to_port = permission.get('ToPort')
            if protocol not in ('tcp', '-1'):
                continue
            if protocol != '-1':
                if from_port is None or to_port is None:
                    continue
                if not (from_port <= port <= to_port):
                    continue
            sources = self._permission_sources(permission)
            assert sources, f"Security group rule for port {port} had no sources"
            exposures.extend(sources)
        if not exposures:
            return 'closed'
        return ', '.join(sorted(set(exposures)))

    def _ssh_banner_diagnostic(self, host: str, timeout: int = 5) -> str:
        port = self.config['port']
        try:
            with socket.create_connection((host, port), timeout=timeout) as sock:
                sock.settimeout(timeout)
                try:
                    banner = sock.recv(255)
                except socket.timeout:
                    return f"TCP connect ok, but no SSH banner within {timeout}s"
        except socket.timeout:
            return f"TCP connect timed out after {timeout}s"
        except OSError as exc:
            return f"TCP connect failed: {exc}"

        if not banner:
            return 'TCP connect ok, but the server closed the socket before sending a banner'

        banner_text = banner.decode('utf-8', errors='replace').strip()
        if banner_text.startswith('SSH-'):
            return f"banner ok: {banner_text}"
        return f"connected, but received non-SSH data: {banner_text}"

    @staticmethod
    def _http_probe(host: str, port: int, use_tls: bool, timeout: int = 5) -> str:
        connection = None
        try:
            if use_tls:
                connection = http.client.HTTPSConnection(
                    host,
                    port=port,
                    timeout=timeout,
                    context=ssl._create_unverified_context(),
                )
            else:
                connection = http.client.HTTPConnection(host, port=port, timeout=timeout)
            connection.request('HEAD', '/', headers={'Host': host})
            response = connection.getresponse()
            server_header = response.getheader('Server') or 'unknown server'
            return f"HTTP {response.status} {response.reason} ({server_header})"
        except Exception as exc:
            return f"request failed: {exc.__class__.__name__}: {exc}"
        finally:
            if connection is not None:
                connection.close()

    def _print_public_network_diagnostics(self, host: str) -> None:
        print("\n🌐 Public network diagnostics:")
        print(f"   SSH {self.config['port']}/tcp: {self._ssh_banner_diagnostic(host)}")
        print(f"   HTTP 80/tcp: {self._http_probe(host, 80, use_tls=False)}")
        print(f"   HTTPS 443/tcp: {self._http_probe(host, 443, use_tls=True)}")

    def _print_aws_host_diagnostics(self, host: str) -> None:
        print("\n☁️  AWS host diagnostics:")
        ec2 = self._aws_client('ec2')

        address = None
        try:
            if host == self.production_host:
                allocation_id = self._get_provisioning().get('elastic_ip_allocation_id')
                if allocation_id:
                    addresses = ec2.describe_addresses(AllocationIds=[allocation_id]).get('Addresses', [])
                    if addresses:
                        address = addresses[0]
            if address is None:
                addresses = ec2.describe_addresses(PublicIps=[host]).get('Addresses', [])
                if addresses:
                    address = addresses[0]
        except ClientError as exc:
            print(f"   ⚠️  Failed to inspect Elastic IP metadata: {exc}")

        if address:
            print(f"   Elastic IP allocation: {address.get('AllocationId') or 'unknown'}")
            print(f"   Elastic IP association: {address.get('AssociationId') or 'none'}")
            print(f"   Elastic IP instance: {address.get('InstanceId') or 'none'}")
            print(f"   Elastic IP ENI: {address.get('NetworkInterfaceId') or 'none'}")
        else:
            print(f"   No Elastic IP metadata found for {host}")

        instance = self._find_instance_by_public_ip(host)
        if not instance:
            print(f"   No EC2 instance found with public IP {host}")
            return

        instance_id = instance['InstanceId']
        print(f"   Instance ID: {instance_id}")
        print(f"   Instance state: {instance.get('State', {}).get('Name', 'unknown')}")
        print(f"   Instance type: {instance.get('InstanceType') or 'unknown'}")
        print(f"   Availability zone: {instance.get('Placement', {}).get('AvailabilityZone') or 'unknown'}")
        print(
            "   Public/private IP: "
            f"{instance.get('PublicIpAddress') or 'unknown'} / "
            f"{instance.get('PrivateIpAddress') or 'unknown'}"
        )
        launch_time = instance.get('LaunchTime')
        if launch_time:
            print(f"   Launch time: {launch_time.isoformat()}")
        profile_arn = instance.get('IamInstanceProfile', {}).get('Arn')
        print(f"   IAM instance profile: {profile_arn or 'none'}")

        status_response = ec2.describe_instance_status(
            InstanceIds=[instance_id],
            IncludeAllInstances=True,
        )
        statuses = status_response.get('InstanceStatuses', [])
        if statuses:
            status = statuses[0]
            instance_check = status.get('InstanceStatus', {})
            system_check = status.get('SystemStatus', {})
            print(f"   Instance reachability check: {instance_check.get('Status') or 'unknown'}")
            print(f"   System reachability check: {system_check.get('Status') or 'unknown'}")
            for label, payload in (
                ('instance', instance_check),
                ('system', system_check),
            ):
                for detail in payload.get('Details', []):
                    detail_name = detail.get('Name') or 'unknown'
                    detail_status = detail.get('Status') or 'unknown'
                    print(f"   {label} detail {detail_name}: {detail_status}")
        else:
            print("   Instance status checks: unavailable")

        security_groups = instance.get('SecurityGroups', [])
        if not security_groups:
            print("   Security groups: none")
            return

        group_ids = [group['GroupId'] for group in security_groups if group.get('GroupId')]
        security_group_response = ec2.describe_security_groups(GroupIds=group_ids)
        print("   Security group ingress summary:")
        for security_group in security_group_response.get('SecurityGroups', []):
            group_id = security_group.get('GroupId') or 'unknown'
            group_name = security_group.get('GroupName') or 'unnamed'
            print(f"     {group_id} ({group_name})")
            for port in (22, 80, 443):
                exposure = self._summarize_port_exposure(security_group, port)
                print(f"       {port}/tcp: {exposure}")

    def _ssm_instance_information(self, instance_id: str) -> Optional[Dict]:
        ssm = self._aws_client('ssm')
        response = ssm.describe_instance_information(
            Filters=[{'Key': 'InstanceIds', 'Values': [instance_id]}]
        )
        info_list = response.get('InstanceInformationList', [])
        if not info_list:
            return None
        assert len(info_list) == 1, f"Expected one SSM record for {instance_id}, got {len(info_list)}"
        return info_list[0]

    def _run_ssm_shell_command(
        self,
        instance_id: str,
        commands: List[str],
        comment: str,
        timeout_seconds: int,
    ) -> bool:
        ssm = self._aws_client('ssm')
        response = ssm.send_command(
            InstanceIds=[instance_id],
            DocumentName='AWS-RunShellScript',
            Comment=comment,
            Parameters={'commands': commands},
        )
        command_id = response['Command']['CommandId']
        print(f"   SSM command ID: {command_id}")

        deadline = time.time() + timeout_seconds
        invocation = None
        while time.time() < deadline:
            try:
                invocation = ssm.get_command_invocation(
                    CommandId=command_id,
                    InstanceId=instance_id,
                )
            except ClientError as exc:
                error_code = exc.response.get('Error', {}).get('Code')
                if error_code == 'InvocationDoesNotExist':
                    time.sleep(2)
                    continue
                raise

            status = invocation['Status']
            if status in ('Pending', 'InProgress', 'Delayed'):
                time.sleep(2)
                continue
            break

        if invocation is None:
            print(f"❌ Timed out waiting for SSM command {command_id}")
            return False

        stdout = invocation.get('StandardOutputContent') or ''
        stderr = invocation.get('StandardErrorContent') or ''
        if stdout.strip():
            print(stdout.rstrip())
        if stderr.strip():
            print(stderr.rstrip(), file=sys.stderr)

        status = invocation['Status']
        if status != 'Success':
            print(f"❌ SSM command failed with status {status}")
            return False
        return True

    def run_ssm_diagnostics(self, host: str) -> bool:
        print("\n📡 AWS Systems Manager diagnostics:")
        instance = self._find_instance_by_public_ip(host)
        if not instance:
            print(f"   No EC2 instance found with public IP {host}")
            return False

        instance_id = instance['InstanceId']
        try:
            info = self._ssm_instance_information(instance_id)
        except ClientError as exc:
            print(f"   ⚠️  Failed to inspect SSM state: {exc}")
            return False

        if not info:
            print("   SSM: instance is not registered as a managed node")
            profile_arn = instance.get('IamInstanceProfile', {}).get('Arn')
            if not profile_arn:
                print("   Instance profile: none attached")
            return False

        ping_status = info.get('PingStatus') or 'unknown'
        platform = info.get('PlatformName') or 'unknown'
        agent_version = info.get('AgentVersion') or 'unknown'
        print(f"   Managed node: {instance_id}")
        print(f"   Ping status: {ping_status}")
        print(f"   Platform: {platform}")
        print(f"   Agent version: {agent_version}")
        last_ping = info.get('LastPingDateTime')
        if last_ping:
            print(f"   Last ping: {last_ping.isoformat()}")

        if ping_status != 'Online':
            print("   SSM is not online; cannot run remote disk diagnostics")
            return False

        app_path = shlex.quote(self.config['app_path'])
        commands = [
            'set -u',
            'echo "== date =="',
            'date -Is',
            'echo',
            'echo "== uptime =="',
            'uptime',
            'echo',
            'echo "== filesystem usage =="',
            'df -h',
            'echo',
            'echo "== inode usage =="',
            'df -ih',
            'echo',
            'echo "== memory (MB) =="',
            'free -m',
            'echo',
            f'echo "== {app_path} usage =="',
            f'if [ -d {app_path} ]; then du -sh {app_path}; else echo "{self.config["app_path"]} not found"; fi',
            f'if [ -d {app_path}/data ]; then du -sh {app_path}/data; else echo "{self.config["app_path"]}/data not found"; fi',
            'echo',
            'echo "== journal disk usage =="',
            'if command -v journalctl >/dev/null 2>&1; then journalctl --disk-usage; else echo "journalctl not available"; fi',
            'echo',
            'echo "== docker disk usage =="',
            'if command -v docker >/dev/null 2>&1; then docker system df; else echo "docker not installed"; fi',
            'echo',
            'echo "== service state =="',
            'if command -v systemctl >/dev/null 2>&1; then '
            'if systemctl list-unit-files ssh.service >/dev/null 2>&1; then echo "ssh: $(systemctl is-active ssh)"; '
            'elif systemctl list-unit-files sshd.service >/dev/null 2>&1; then echo "sshd: $(systemctl is-active sshd)"; '
            'else echo "ssh service unit not found"; fi; '
            'if systemctl list-unit-files nginx.service >/dev/null 2>&1; then echo "nginx: $(systemctl is-active nginx)"; '
            'else echo "nginx service unit not found"; fi; '
            'else echo "systemctl not available"; fi',
        ]
        return self._run_ssm_shell_command(
            instance_id=instance_id,
            commands=commands,
            comment='VDW SSM diagnostics',
            timeout_seconds=120,
        )

    @staticmethod
    def _management_policy_arns() -> List[str]:
        return [
            'arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore',
            'arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy',
        ]

    def _management_profile_name(self) -> str:
        configured = self._get_provisioning().get('iam_instance_profile')
        if configured:
            return configured
        return DEFAULT_MANAGEMENT_INSTANCE_PROFILE

    def _persist_management_profile_name(self, profile_name: str) -> None:
        config = self._get_provisioning()
        if config.get('iam_instance_profile') == profile_name:
            return
        updated_config = dict(config)
        updated_config['iam_instance_profile'] = profile_name
        self._write_provisioning_config(updated_config)
        self.provisioning = updated_config

    def ensure_management_instance_profile(self) -> str:
        profile_name = self._management_profile_name()
        iam = self._aws_client('iam')
        role_name = profile_name
        profile = None

        try:
            profile = iam.get_instance_profile(InstanceProfileName=profile_name)['InstanceProfile']
            print(f"🔐 Using existing IAM instance profile {profile_name}")
            roles = profile.get('Roles', [])
            if roles:
                role_name = roles[0]['RoleName']
        except ClientError as exc:
            error_code = exc.response.get('Error', {}).get('Code')
            if error_code != 'NoSuchEntity':
                raise
            print(f"🔐 Creating IAM instance profile {profile_name}")

        assume_role_policy = {
            'Version': '2012-10-17',
            'Statement': [
                {
                    'Effect': 'Allow',
                    'Principal': {'Service': 'ec2.amazonaws.com'},
                    'Action': 'sts:AssumeRole',
                }
            ],
        }

        try:
            iam.get_role(RoleName=role_name)
        except ClientError as exc:
            error_code = exc.response.get('Error', {}).get('Code')
            if error_code != 'NoSuchEntity':
                raise
            print(f"   Creating IAM role {role_name}")
            iam.create_role(
                RoleName=role_name,
                AssumeRolePolicyDocument=json.dumps(assume_role_policy),
                Description='VDW EC2 management role for SSM and CloudWatch',
            )

        attached_policy_arns = {
            policy['PolicyArn']
            for policy in iam.list_attached_role_policies(RoleName=role_name).get('AttachedPolicies', [])
        }
        for policy_arn in self._management_policy_arns():
            if policy_arn in attached_policy_arns:
                continue
            print(f"   Attaching policy {policy_arn}")
            iam.attach_role_policy(RoleName=role_name, PolicyArn=policy_arn)

        if profile is None:
            iam.create_instance_profile(InstanceProfileName=profile_name)
            time.sleep(5)
            profile = iam.get_instance_profile(InstanceProfileName=profile_name)['InstanceProfile']

        profile_roles = profile.get('Roles', [])
        if not profile_roles:
            print(f"   Adding role {role_name} to instance profile {profile_name}")
            iam.add_role_to_instance_profile(
                InstanceProfileName=profile_name,
                RoleName=role_name,
            )
            time.sleep(5)
        else:
            assert len(profile_roles) == 1, f"Expected one role in instance profile {profile_name}"
            current_role_name = profile_roles[0]['RoleName']
            if current_role_name != role_name:
                raise RuntimeError(
                    f"Instance profile {profile_name} already uses role {current_role_name}; update it manually."
                )

        self._persist_management_profile_name(profile_name)
        return profile_name

    @staticmethod
    def _cloudwatch_agent_download_url(architecture: str) -> str:
        if architecture == 'arm64':
            return 'https://amazoncloudwatch-agent.s3.amazonaws.com/ubuntu/arm64/latest/amazon-cloudwatch-agent.deb'
        return 'https://amazoncloudwatch-agent.s3.amazonaws.com/ubuntu/amd64/latest/amazon-cloudwatch-agent.deb'

    def _cloudwatch_agent_config(self) -> str:
        config = {
            'agent': {
                'metrics_collection_interval': 60,
                'run_as_user': 'root',
            },
            'metrics': {
                'append_dimensions': {
                    'InstanceId': '${aws:InstanceId}',
                    'ImageId': '${aws:ImageId}',
                    'InstanceType': '${aws:InstanceType}',
                },
                'metrics_collected': {
                    'disk': {
                        'measurement': ['used_percent', 'inodes_free'],
                        'resources': ['*'],
                        'drop_device': True,
                        'ignore_file_system_types': [
                            'sysfs',
                            'devtmpfs',
                            'tmpfs',
                            'overlay',
                            'squashfs',
                            'nsfs',
                            'proc',
                            'devpts',
                            'aufs',
                        ],
                    },
                    'mem': {
                        'measurement': ['used_percent'],
                    },
                    'swap': {
                        'measurement': ['used_percent'],
                    },
                },
            },
        }
        return json.dumps(config, indent=2)

    def _management_bootstrap_commands(self, architecture: str) -> List[str]:
        cloudwatch_url = self._cloudwatch_agent_download_url(architecture)
        config_json = shlex.quote(self._cloudwatch_agent_config())
        raw_config_dir = str(Path(CLOUDWATCH_AGENT_CONFIG_REMOTE_PATH).parent)
        raw_config_path = CLOUDWATCH_AGENT_CONFIG_REMOTE_PATH
        config_dir = shlex.quote(raw_config_dir)
        config_path = shlex.quote(raw_config_path)
        return [
            'set -euxo pipefail',
            'sudo apt-get update',
            'sudo apt-get install -y curl snapd',
            'sudo systemctl enable --now snapd.service snapd.socket',
            'if ! snap list amazon-ssm-agent >/dev/null 2>&1; then sudo snap install amazon-ssm-agent --classic; fi',
            'sudo snap start amazon-ssm-agent || sudo systemctl start snap.amazon-ssm-agent.amazon-ssm-agent.service',
            f'curl -fsSL -o /tmp/amazon-cloudwatch-agent.deb {shlex.quote(cloudwatch_url)}',
            'sudo dpkg -i -E /tmp/amazon-cloudwatch-agent.deb',
            f'sudo install -d -m 0755 {config_dir}',
            f'printf %s {config_json} | sudo tee {config_path} >/dev/null',
            (
                'sudo /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl '
                f'-a fetch-config -m ec2 -s -c file:{raw_config_path}'
            ),
            'sudo systemctl enable amazon-cloudwatch-agent',
            'sudo systemctl is-active --quiet amazon-cloudwatch-agent',
        ]

    def _wait_for_ssm_online(self, instance_id: str, timeout_seconds: int) -> bool:
        print(f"⏳ Waiting for SSM on {instance_id}...")
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            try:
                info = self._ssm_instance_information(instance_id)
            except ClientError as exc:
                print(f"⚠️  Failed to inspect SSM state: {exc}")
                return False
            if info and info.get('PingStatus') == 'Online':
                print("   SSM is online")
                return True
            time.sleep(5)
        print("   SSM did not come online in time")
        return False

    def _bootstrap_management_via_ssm(self, instance_id: str, architecture: str) -> bool:
        print("🛠️  Installing/configuring CloudWatch agent via SSM...")
        return self._run_ssm_shell_command(
            instance_id=instance_id,
            commands=self._management_bootstrap_commands(architecture),
            comment='VDW management bootstrap',
            timeout_seconds=300,
        )

    def _bootstrap_management_on_connected_host(self, architecture: str) -> bool:
        print("🛠️  Installing/configuring SSM + CloudWatch agents over SSH...")
        script = '\n'.join(self._management_bootstrap_commands(architecture))
        success, _, error = self.execute_command(
            f"bash -lc {shlex.quote(script)}",
            show_output=True,
            stream_output=True,
        )
        if not success:
            print(f"❌ Management bootstrap failed: {error}")
            return False
        return True

    def _bootstrap_management_via_ssh(self, host: str, architecture: str) -> bool:
        if not self.connect(host_override=host):
            return False
        try:
            return self._bootstrap_management_on_connected_host(architecture)
        finally:
            self.disconnect()

    def _attach_instance_profile(self, instance_id: str, profile_name: str) -> bool:
        ec2 = self._aws_client('ec2')
        association_response = ec2.describe_iam_instance_profile_associations(
            Filters=[{'Name': 'instance-id', 'Values': [instance_id]}]
        )
        associations = association_response.get('IamInstanceProfileAssociations', [])
        assert len(associations) <= 1, f"Expected at most one profile association for {instance_id}"

        try:
            if associations:
                association = associations[0]
                current_arn = association.get('IamInstanceProfile', {}).get('Arn') or 'unknown'
                if current_arn.endswith(f'instance-profile/{profile_name}'):
                    print(f"🔗 IAM instance profile {profile_name} already attached")
                    return True
                print(f"🔁 Replacing current instance profile {current_arn}")
                ec2.replace_iam_instance_profile_association(
                    AssociationId=association['AssociationId'],
                    IamInstanceProfile={'Name': profile_name},
                )
            else:
                print(f"🔗 Attaching instance profile {profile_name} to {instance_id}")
                ec2.associate_iam_instance_profile(
                    IamInstanceProfile={'Name': profile_name},
                    InstanceId=instance_id,
                )
        except ClientError as exc:
            print(f"❌ Failed to attach instance profile: {exc}")
            return False
        return True

    def _latest_metric_statistic(
        self,
        namespace: str,
        metric_name: str,
        dimensions: List[Dict[str, str]],
        statistic: str,
    ) -> Optional[Dict]:
        cloudwatch = self._aws_client('cloudwatch')
        end_time = datetime.now(timezone.utc)
        response = cloudwatch.get_metric_statistics(
            Namespace=namespace,
            MetricName=metric_name,
            Dimensions=dimensions,
            StartTime=end_time - timedelta(minutes=30),
            EndTime=end_time,
            Period=300,
            Statistics=[statistic],
        )
        datapoints = response.get('Datapoints', [])
        if not datapoints:
            return None
        datapoints.sort(key=lambda item: item['Timestamp'])
        return datapoints[-1]

    def _print_ec2_cloudwatch_metrics(self, instance_id: str) -> None:
        print("\n📊 EC2 CloudWatch metrics:")
        dimensions = [{'Name': 'InstanceId', 'Value': instance_id}]

        metric_specs = [
            ('CPUUtilization', 'Average', '%'),
            ('CPUCreditBalance', 'Average', ''),
            ('CPUCreditUsage', 'Sum', ''),
            ('NetworkIn', 'Sum', 'bytes'),
            ('NetworkOut', 'Sum', 'bytes'),
        ]

        for metric_name, statistic, unit_suffix in metric_specs:
            datapoint = self._latest_metric_statistic(
                namespace='AWS/EC2',
                metric_name=metric_name,
                dimensions=dimensions,
                statistic=statistic,
            )
            if not datapoint:
                print(f"   {metric_name}: unavailable")
                continue

            value = datapoint[statistic]
            rendered_value = f"{value:.1f}"
            if unit_suffix:
                rendered_value = f"{rendered_value} {unit_suffix}"
            print(
                f"   {metric_name} ({statistic.lower()}): {rendered_value} "
                f"at {datapoint['Timestamp'].isoformat()}"
            )

    def _print_cloudwatch_metrics(self, host: str) -> None:
        instance = self._find_instance_by_public_ip(host)
        if not instance:
            print("\n📈 CloudWatch metrics:")
            print(f"   No EC2 instance found with public IP {host}")
            return

        instance_id = instance['InstanceId']
        self._print_ec2_cloudwatch_metrics(instance_id)

        print("\n📈 CloudWatch agent metrics:")
        cloudwatch = self._aws_client('cloudwatch')

        def list_metrics(metric_name: str) -> List[Dict]:
            paginator = cloudwatch.get_paginator('list_metrics')
            metrics: List[Dict] = []
            for page in paginator.paginate(
                Namespace='CWAgent',
                MetricName=metric_name,
                Dimensions=[{'Name': 'InstanceId', 'Value': instance_id}],
            ):
                metrics.extend(page.get('Metrics', []))
            return metrics

        mem_metrics = list_metrics('mem_used_percent')
        if mem_metrics:
            datapoint = self._latest_metric_statistic(
                namespace='CWAgent',
                metric_name='mem_used_percent',
                dimensions=mem_metrics[0]['Dimensions'],
                statistic='Average',
            )
            if datapoint:
                print(
                    f"   mem_used_percent: {datapoint['Average']:.1f}% "
                    f"at {datapoint['Timestamp'].isoformat()}"
                )
        else:
            print("   mem_used_percent: unavailable")

        swap_metrics = list_metrics('swap_used_percent')
        if swap_metrics:
            datapoint = self._latest_metric_statistic(
                namespace='CWAgent',
                metric_name='swap_used_percent',
                dimensions=swap_metrics[0]['Dimensions'],
                statistic='Average',
            )
            if datapoint:
                print(
                    f"   swap_used_percent: {datapoint['Average']:.1f}% "
                    f"at {datapoint['Timestamp'].isoformat()}"
                )
        else:
            print("   swap_used_percent: unavailable")

        disk_metrics = list_metrics('disk_used_percent')
        if not disk_metrics:
            print("   disk_used_percent: unavailable")
            return

        entries = []
        for metric in disk_metrics:
            dimensions = {dimension['Name']: dimension['Value'] for dimension in metric['Dimensions']}
            path = dimensions.get('path') or dimensions.get('Path') or 'unknown'
            datapoint = self._latest_metric_statistic(
                namespace='CWAgent',
                metric_name='disk_used_percent',
                dimensions=metric['Dimensions'],
                statistic='Maximum',
            )
            if not datapoint:
                continue
            entries.append((path, datapoint))

        if not entries:
            print("   disk_used_percent: no recent datapoints")
            return

        for path, datapoint in sorted(entries, key=lambda item: item[0]):
            print(
                f"   disk_used_percent[{path}]: {datapoint['Maximum']:.1f}% "
                f"at {datapoint['Timestamp'].isoformat()}"
            )

    def attach_instance_profile(self, host: str) -> bool:
        instance = self._find_instance_by_public_ip(host)
        if not instance:
            print(f"❌ No EC2 instance found with public IP {host}")
            return False

        instance_id = instance['InstanceId']
        profile_name = self.ensure_management_instance_profile()
        attached = self._attach_instance_profile(instance_id, profile_name)
        if not attached:
            return False

        print("✅ Instance profile attachment requested.")
        print(f"   Profile: {profile_name}")
        return True

    def enable_aws_management(self, host: str) -> bool:
        instance = self._find_instance_by_public_ip(host)
        if not instance:
            print(f"❌ No EC2 instance found with public IP {host}")
            return False

        instance_id = instance['InstanceId']
        architecture = instance.get('Architecture') or 'x86_64'

        print("\n☁️  Enabling AWS management (IAM + SSM + CloudWatch)...")
        if not self.attach_instance_profile(host):
            return False

        if self._wait_for_ssm_online(instance_id, timeout_seconds=90):
            if not self._bootstrap_management_via_ssm(instance_id, architecture):
                return False
            self._print_cloudwatch_metrics(host)
            return True

        print("⚠️  SSM is not online yet. Trying SSH bootstrap.")
        if not self._bootstrap_management_via_ssh(host, architecture):
            print("❌ Could not bootstrap management agents over SSH.")
            print("   Reboot the instance, then rerun this option once SSH or SSM is reachable.")
            return False

        self._wait_for_ssm_online(instance_id, timeout_seconds=180)
        self._print_cloudwatch_metrics(host)
        return True

    def reboot_instance(self, host: str) -> bool:
        instance = self._find_instance_by_public_ip(host)
        if not instance:
            print(f"❌ No EC2 instance found with public IP {host}")
            return False

        instance_id = instance['InstanceId']
        ec2 = self._aws_client('ec2')
        min_wait_seconds = 90

        print(f"\n🔄 Rebooting EC2 instance {instance_id} for host {host}...")
        try:
            ec2.reboot_instances(InstanceIds=[instance_id])
        except ClientError as exc:
            print(f"❌ Failed to request reboot: {exc}")
            return False

        print(
            "⏳ Waiting for AWS instance and system checks to pass again "
            f"(minimum {min_wait_seconds}s)..."
        )
        deadline = time.time() + 600
        earliest_success = time.time() + min_wait_seconds
        while time.time() < deadline:
            try:
                response = ec2.describe_instance_status(
                    InstanceIds=[instance_id],
                    IncludeAllInstances=True,
                )
            except ClientError as exc:
                print(f"⚠️  Failed to poll instance status: {exc}")
                time.sleep(10)
                continue

            statuses = response.get('InstanceStatuses', [])
            if not statuses:
                time.sleep(10)
                continue

            status = statuses[0]
            state = status.get('InstanceState', {}).get('Name') or 'unknown'
            instance_check = status.get('InstanceStatus', {}).get('Status') or 'unknown'
            system_check = status.get('SystemStatus', {}).get('Status') or 'unknown'
            print(
                f"   state={state} instance_check={instance_check} system_check={system_check}"
            )
            if (
                time.time() >= earliest_success
                and state == 'running'
                and instance_check == 'ok'
                and system_check == 'ok'
            ):
                print("✅ Reboot complete. AWS status checks are back to ok.")
                return True
            time.sleep(10)

        print("⚠️  Timed out waiting for AWS status checks after reboot request.")
        return False
    
    def execute_command(
        self,
        command,
        show_output=True,
        stream_output=False,
        heartbeat_seconds: Optional[int] = None,
        watchdog_seconds: Optional[int] = None,
        watchdog_callback: Optional[Callable[[], None]] = None,
    ):
        """Execute command on remote server"""
        if not self.ssh_client:
            print("❌ Not connected to server")
            return False
        
        try:
            stdin, stdout, stderr = self.ssh_client.exec_command(command)

            if stream_output:
                output_chunks = []
                error_chunks = []
                channel = stdout.channel
                start_time = time.monotonic()
                next_heartbeat = (start_time + heartbeat_seconds) if heartbeat_seconds else None
                watchdog_fired = False
                heartbeat_printed = False
                while not channel.exit_status_ready():
                    if channel.recv_ready():
                        data = channel.recv(4096).decode()
                        if data:
                            output_chunks.append(data)
                            if show_output:
                                print(data, end='')
                    if channel.recv_stderr_ready():
                        data = channel.recv_stderr(4096).decode()
                        if data:
                            error_chunks.append(data)
                            if show_output:
                                print(data, end='', file=sys.stderr)
                    now = time.monotonic()
                    if heartbeat_seconds and next_heartbeat and now >= next_heartbeat:
                        if show_output:
                            print(".", end="", flush=True)
                        heartbeat_printed = True
                        next_heartbeat = now + heartbeat_seconds
                    if watchdog_seconds and not watchdog_fired and (now - start_time) >= watchdog_seconds:
                        watchdog_fired = True
                        if show_output:
                            print("\n\n--- watchdog: command still running, showing diagnostics ---")
                        if watchdog_callback:
                            watchdog_callback()
                        if show_output:
                            print("--- watchdog: diagnostics complete ---\n")
                    time.sleep(0.1)

                while channel.recv_ready():
                    data = channel.recv(4096).decode()
                    if data:
                        output_chunks.append(data)
                        if show_output:
                            print(data, end='')
                while channel.recv_stderr_ready():
                    data = channel.recv_stderr(4096).decode()
                    if data:
                        error_chunks.append(data)
                        if show_output:
                            print(data, end='', file=sys.stderr)

                exit_status = channel.recv_exit_status()
                if show_output and heartbeat_printed:
                    print()
                output = ''.join(output_chunks).strip()
                error = ''.join(error_chunks).strip()
                return exit_status == 0, output, error

            exit_status = stdout.channel.recv_exit_status()
            output = stdout.read().decode().strip()
            error = stderr.read().decode().strip()

            if show_output and output:
                print(output)
            # Only print stderr when the command failed; avoid noisy benign warnings
            if error and exit_status != 0:
                print(f"⚠️  {error}")

            return exit_status == 0, output, error
            
        except Exception as e:
            print(f"❌ Command execution failed: {e}")
            return False, "", str(e)

    @staticmethod
    def _format_bytes(num_bytes):
        """Convert byte counts into a human readable string"""
        units = ['B', 'KB', 'MB', 'GB', 'TB']
        value = float(num_bytes)
        for unit in units:
            if value < 1024.0:
                return f"{value:.1f} {unit}"
            value /= 1024.0
        return f"{value:.1f} PB"

    def get_remote_free_bytes(self, path):
        """Return available bytes for the filesystem containing path"""
        success, output, error = self.execute_command(f"df -B1 {shlex.quote(path)}", show_output=False)
        if not success or not output:
            raise RuntimeError(f"Failed to check disk space: {error or 'no output'}")

        lines = output.splitlines()
        if len(lines) < 2:
            raise RuntimeError(f"Unexpected df output: {output}")

        parts = lines[1].split()
        try:
            return int(parts[3])
        except (IndexError, ValueError) as exc:
            raise RuntimeError(f"Could not parse df output: {output}") from exc

    def get_remote_file_size_bytes(self, path):
        """Return size in bytes for a remote file, 0 if missing"""
        quoted = shlex.quote(path)
        # Use stat if available, fallback to wc -c
        cmd = (
            f"if [ -f {quoted} ]; then (stat -c %s {quoted} 2>/dev/null || wc -c < {quoted}); else echo 0; fi"
        )
        success, output, error = self.execute_command(cmd, show_output=False)
        if not success or not output:
            raise RuntimeError(f"Failed to stat remote file {path}: {error or 'no output'}")
        try:
            return int(output.strip())
        except ValueError as exc:
            raise RuntimeError(f"Unexpected stat output for {path}: {output}") from exc

    def perform_remote_cleanup(self, remote_app_path):
        """Run disk cleanup commands on the remote host"""
        print("🧹 Running remote cleanup commands...")

        # Quick space report before cleanup
        try:
            self._print_remote_space_summary(remote_app_path, label_prefix="Before")
        except Exception as exc:
            print(f"⚠️  Failed to read space summary (before): {exc}")

        app_q = shlex.quote(remote_app_path)
        data_q = shlex.quote(f"{remote_app_path}/data")

        # Core cleanup actions (kept conservative; no service downtime)
        commands = [
            ("Pruning unused Docker artifacts", "sudo docker system prune -f"),
            ("Pruning Docker builder cache", "sudo docker builder prune -af"),
            ("Pruning unused Docker volumes", "sudo docker volume prune -f"),
            (
                "Removing stray SQLite temp files (app root)",
                f"sudo find {app_q} -maxdepth 1 -name 'db.sqlite3*' -type f ! -name 'db.sqlite3' -delete"
            ),
            (
                "Removing stray SQLite temp files (data dir)",
                f"sudo find {data_q} -maxdepth 1 -name 'db.sqlite3*' -type f ! -name 'db.sqlite3' -delete || true"
            ),
            (
                "Vacuuming system journal (cap to 200M)",
                "sudo journalctl --vacuum-size=200M || true"
            ),
            (
                "Cleaning apt caches",
                "sudo apt-get clean && sudo rm -rf /var/lib/apt/lists/* || true"
            ),
        ]

        for description, command in commands:
            print(f"   {description}...")
            success, _, error = self.execute_command(command, show_output=False)
            if not success:
                raise RuntimeError(f"{description} failed: {error}")

        # Best-effort: clear large temp files inside the running Django container
        print("   Clearing large /tmp files inside Django container (best-effort)...")
        container_tmp_cleanup = (
            f"cd {app_q} && "
            "if sudo docker compose ps -q django | grep -q .; then "
            "sudo docker compose exec -T django sh -lc "
            "'find /tmp -maxdepth 1 -type f -name ""*.sqlite3*"" -delete 2>/dev/null || true; "
            " find /tmp -maxdepth 1 -type f -size +10M -delete 2>/dev/null || true'"
            " || true; "
            "else echo 'django container not running; skipping /tmp cleanup'; fi"
        )
        # Do not fail the whole cleanup if this step fails
        _success, _out, _err = self.execute_command(container_tmp_cleanup, show_output=False)

        # Space report after cleanup
        try:
            self._print_remote_space_summary(remote_app_path, label_prefix="After ")
        except Exception as exc:
            print(f"⚠️  Failed to read space summary (after): {exc}")

        print("✅ Remote cleanup completed")

    def _print_remote_space_summary(self, remote_app_path: str, label_prefix: str ="") -> None:
        """Print a brief free-space summary for key mount points on the host.

        Shows free bytes for '/', '/var/lib/docker', app path, and the data dir.
        """
        paths = [
            ("/", "/"),
            ("/var/lib/docker", "/var/lib/docker"),
            ("app", remote_app_path),
            ("data", f"{remote_app_path}/data"),
        ]

        parts = []
        for label, path in paths:
            try:
                free = self.get_remote_free_bytes(path)
                parts.append(f"{label}:{self._format_bytes(free)}")
            except Exception:
                parts.append(f"{label}:n/a")

        print(f"   {label_prefix} free space → " + " | ".join(parts))

    def maybe_cleanup_remote_disk(self, remote_app_path):
        """Interactively offer to clean up remote disk space"""
        response = input(
            "   Attempt cleanup (docker prune + remove SQLite temp files)? (y/n): "
        ).lower()
        if response != 'y':
            return False

        self.perform_remote_cleanup(remote_app_path)
        return True

    def upload_code(self):
        """Upload application code via SCP"""
        print("📤 Uploading application code...")

        app_path = self.config['app_path']
        remote_app_path = shlex.quote(app_path)
        remote_user = shlex.quote(self.config['user'])
        ensure_cmd = (
            f"sudo mkdir -p {remote_app_path} && "
            f"sudo chown {remote_user}:{remote_user} {remote_app_path}"
        )

        try:
            success, output, error = self.execute_command(ensure_cmd)
            if not success:
                print(f"❌ Failed to prepare remote app directory: {error}")
                return False

            with SCPClient(self.ssh_client.get_transport()) as scp:
                # Upload all important files
                for pattern in ['*.py', '*.txt', '*.yml', '*.yaml', 'Dockerfile', '.dockerignore', 'google*.html']:
                    for file_path in Path('.').glob(pattern):
                        if file_path.name not in ['.env', 'db.sqlite3']:
                            print(f"   Uploading {file_path}...")
                            scp.put(str(file_path), f"{app_path}/{file_path.name}")
                
                # Upload directories (pages, templates, static, etc.)
                for dir_path in Path('.').iterdir():
                    if dir_path.is_dir() and dir_path.name not in ['.git', '__pycache__', '.venv', 'venv', '.pytest_cache', '.idea', '.vscode', 'data.ms']:
                        print(f"   Uploading directory {dir_path}...")
                        scp.put(str(dir_path), app_path, recursive=True)
            
            # Set ownership
            success, output, error = self.execute_command(f"sudo chown -R {self.config['user']}:{self.config['user']} {app_path}")
            if not success:
                print(f"❌ Failed to set ownership: {error}")
                return False
            
            print("✅ Code uploaded successfully!")
            return True
            
        except Exception as e:
            print(f"❌ Code upload failed: {e}")
            return False
    
    def deploy_code(self):
        """Deploy code updates via SCP upload + docker rebuild"""
        target_host = self.prompt_host_for_operation('code deploy')
        return self._deploy_code(target_host)

    def _deploy_code(self, target_host: str) -> bool:
        print(f"\n🚀 Starting code deployment on {target_host}...")

        # Check git branch before deploying
        if not self.check_git_branch():
            return False

        if not self.connect(host_override=target_host):
            return False
        
        try:
            # Upload fresh code from local machine
            print("📦 Uploading fresh code from local machine...")
            if not self.upload_code():
                return False

            print("🔧 Uploading environment (.env) configuration...")
            if not self.setup_environment():
                return False

            print("🌐 Refreshing nginx proxy config...")
            if not self._refresh_nginx_proxy_for_deploy():
                return False

            if not self.rebuild_and_restart_stack():
                return False

            print("✅ Code deployment completed successfully!")
            print(f"🌐 Site should be available at: http://{target_host}:{self.config['django_port']}")
            return True
            
        except Exception as e:
            print(f"❌ Deployment failed: {e}")
            return False
        
        finally:
            self.disconnect()

    def _refresh_nginx_proxy_for_deploy(self) -> bool:
        """Apply nginx config while preserving current HTTP/HTTPS mode."""
        cfg = self._domain_config()
        primary_domain = cfg['primary']
        if not primary_domain:
            return self.configure_nginx_proxy()

        cert_path = self._ssl_paths()['cert']
        cert_exists, _, _ = self.execute_command(
            f"sudo test -f {shlex.quote(cert_path)}",
            show_output=False,
        )
        if cert_exists:
            return self.configure_nginx_proxy(content=self._render_https_nginx())
        return self.configure_nginx_proxy()
    
    def deploy_database(self):
        target_host = self.prompt_host_for_operation('database deploy')
        return self._deploy_database(target_host)

    def _deploy_database(self, target_host: str) -> bool:
        print(f"\n🗄️  Starting database deployment on {target_host}...")
        
        # Check local database exists
        local_db = Path(self.config['local_db'])
        if not local_db.exists():
            print(f"❌ Local database not found: {local_db}")
            return False
        
        db_size_mb = local_db.stat().st_size / (1024 * 1024)
        print(f"📊 Local database: {local_db} ({db_size_mb:.1f} MB)")
        
        if input(f"\n⚠️  This will replace the server database! Proceed? (y/n): ").lower() != 'y':
            print("❌ Database deployment cancelled")
            return False
        
        if not self.connect(host_override=target_host):
            return False

        try:
            app_path = self.config['app_path']
            remote_app_path = shlex.quote(app_path)
            # Use /app/data/db.sqlite3 inside the container; mount a directory
            remote_db_dir = f"{app_path}/data"
            remote_db_path = f"{remote_db_dir}/db.sqlite3"
            remote_db_dir_q = shlex.quote(remote_db_dir)
            remote_db_path_q = shlex.quote(remote_db_path)
            # Upload temp file inside the DB directory to avoid cross-filesystem moves
            remote_tmp = f"{remote_db_dir}/db.sqlite3.upload"
            remote_tmp_q = shlex.quote(remote_tmp)

            db_size_bytes = local_db.stat().st_size
            overhead_bytes = 64 * 1024 * 1024  # 64 MiB overhead for metadata/temp
            required_bytes = db_size_bytes + overhead_bytes

            print("📦 Checking remote disk space...")
            free_bytes = self.get_remote_free_bytes(app_path)
            current_remote_db_bytes = self.get_remote_file_size_bytes(remote_db_path)
            effective_free = free_bytes + current_remote_db_bytes
            print(
                f"   Available: {self._format_bytes(free_bytes)} | "
                f"Current DB: {self._format_bytes(current_remote_db_bytes)} | "
                f"Effective free: {self._format_bytes(effective_free)} | "
                f"Required: {self._format_bytes(required_bytes)}"
            )

            if effective_free < required_bytes:
                print("⚠️  Remote disk space is low; attempting cleanup.")
                cleaned = self.maybe_cleanup_remote_disk(app_path)
                if cleaned:
                    free_bytes = self.get_remote_free_bytes(app_path)
                    current_remote_db_bytes = self.get_remote_file_size_bytes(remote_db_path)
                    effective_free = free_bytes + current_remote_db_bytes
                    print(
                        f"   Post-cleanup free: {self._format_bytes(free_bytes)} | "
                        f"Current DB: {self._format_bytes(current_remote_db_bytes)} | "
                        f"Effective: {self._format_bytes(effective_free)}"
                    )

                if effective_free < required_bytes:
                    print(
                        "❌ Not enough remote disk space after cleanup attempts. "
                        "Aborting deployment."
                    )
                    return False

            # Stop Django container to avoid database locks
            print("🛑 Stopping Django container...")
            self.execute_command(f"cd {remote_app_path} && sudo docker compose stop django")

            # Ensure DB directory exists and is writable for upload
            self.execute_command(f"sudo mkdir -p {remote_db_dir_q}")
            # Temporarily grant ownership to upload user so SCP can write the temp file
            self.execute_command(
                f"sudo chown {self.config['user']}:{self.config['user']} {remote_db_dir_q}"
            )

            # Clean up any previous uploads and remove existing mount target
            print("📤 Uploading database...")
            self.execute_command(f"rm -f {remote_tmp_q}")
            # Remove existing DB first to free space before upload
            success, output, error = self.execute_command(f"sudo rm -rf {remote_db_path_q}")
            if not success:
                print(f"❌ Failed to remove existing database: {error}")
                return False

            with SCPClient(self.ssh_client.get_transport()) as scp:
                scp.put(str(local_db), remote_tmp)

            # Move uploaded file into place atomically
            success, output, error = self.execute_command(f"sudo mv {remote_tmp_q} {remote_db_path_q}")
            if not success:
                print(f"❌ Failed to move uploaded database into place: {error}")
                self.execute_command(f"rm -f {remote_tmp_q}")
                return False

            print("✅ Database uploaded successfully!")

            # Fix database permissions for Docker container (root access)
            print("🔧 Setting database permissions...")
            success, output, error = self.execute_command(f"sudo chown root:root {remote_db_path_q}")
            if not success:
                print(f"❌ Failed to set database ownership: {error}")
                return False
            
            success, output, error = self.execute_command(f"sudo chmod 644 {remote_db_path_q}")
            if not success:
                print(f"❌ Failed to set database permissions: {error}")
                return False
            
            # Restore directory ownership to root so SQLite temp files are created with root-managed perms
            success, output, error = self.execute_command(f"sudo chown root:root {remote_db_dir_q}")
            if not success:
                print(f"❌ Failed to set directory ownership: {error}")
                return False
            
            # Start Django container
            print("🚀 Starting Django container...")
            success, output, error = self.execute_command(
                f"cd {app_path} && sudo docker compose start django"
            )
            if not success:
                print(f"❌ Failed to start Django container: {error}")
                return False
            
            # Wait a moment for container to be ready
            print("⏳ Waiting for container to be ready...")
            import time
            time.sleep(3)
            
            # Reindex search
            print("🔍 Rebuilding search index...")
            success, output, error = self.execute_command(
                f"cd {app_path} && sudo docker compose exec -T django python manage.py reindex_search"
            )
            if not success:
                print(f"❌ Search reindexing failed: {error}")
                return False
            print("✅ Search index rebuilt!")
            
            print("✅ Database deployment completed successfully!")
            return True
            
        except Exception as e:
            print(f"❌ Database deployment failed: {e}")
            return False
        
        finally:
            self.disconnect()
    
    def deploy_full(self):
        """Deploy both code and database"""
        print("\n🎯 Starting full deployment...")
        target_host = self.prompt_host_for_operation('full deploy')

        print("Step 1: Deploying code...")
        if not self._deploy_code(target_host):
            print("❌ Code deployment failed, aborting full deployment")
            return False
        
        print("\nStep 2: Deploying database...")
        if not self._deploy_database(target_host):
            print("❌ Database deployment failed")
            return False
        
        print("🎉 Full deployment completed successfully!")
        return True
    
    def reindex_search(self):
        """Reindex search without other changes"""
        host = self.prompt_host_for_operation('reindex search')
        print(f"\n🔍 Reindexing search on {host}...")
        
        if not self.connect(host_override=host):
            return False
        
        try:
            app_path = self.config['app_path']
            success, output, error = self.execute_command(
                f"cd {app_path} && sudo docker compose exec -T django python manage.py reindex_search"
            )
            
            if success:
                print("✅ Search reindexing completed!")
            else:
                print(f"❌ Search reindexing failed: {error}")
            
            return success
            
        except Exception as e:
            print(f"❌ Search reindexing failed: {e}")
            return False
        
        finally:
            self.disconnect()

    def restore_local_db_from_s3(self) -> bool:
        """Download an S3 backup and swap it into the local db.sqlite3 path."""
        print("\n☁️  Restoring local database from S3 backup...")

        bucket = os.getenv('AWS_STORAGE_BUCKET_NAME')
        if not bucket:
            print("❌ AWS_STORAGE_BUCKET_NAME is not set in your environment")
            return False

        local_db = Path(self.config['local_db'])
        prefix = f"{MANUAL_BACKUP_PREFIX.rstrip('/')}/"

        try:
            s3 = boto3.client('s3')
        except Exception as exc:
            print(f"❌ Failed to initialize S3 client: {exc}")
            return False

        backups = []
        continuation = None
        while True:
            request = {'Bucket': bucket, 'Prefix': prefix}
            if continuation:
                request['ContinuationToken'] = continuation
            try:
                response = s3.list_objects_v2(**request)
            except ClientError as exc:
                print(f"❌ Failed to list backups: {exc}")
                return False

            for obj in response.get('Contents', []):
                key = obj.get('Key') or ''
                if not key or key.endswith('/'):
                    continue
                name = key.split('/')[-1]
                backups.append(
                    {
                        'key': key,
                        'name': name,
                        'size': obj.get('Size', 0),
                        'modified': obj.get('LastModified'),
                    }
                )

            if not response.get('IsTruncated'):
                break
            continuation = response.get('NextContinuationToken')

        if not backups:
            print(f"❌ No backups found under s3://{bucket}/{MANUAL_BACKUP_PREFIX}/")
            return False

        backups.sort(key=lambda item: item['name'])

        print("\nAvailable backups:")
        for idx, entry in enumerate(backups, start=1):
            size_mb = entry['size'] / (1024 * 1024) if entry['size'] else 0
            modified = entry['modified']
            if modified is not None:
                if modified.tzinfo:
                    modified_str = modified.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
                else:
                    modified_str = f"{modified.strftime('%Y-%m-%d %H:%M:%S')} (no tz)"
            else:
                modified_str = 'unknown'
            print(f"  [{idx}] {entry['name']} — {size_mb:.1f} MB — {modified_str}")

        selection = None
        while selection is None:
            raw = input(f"Select backup [1-{len(backups)}] or 'q' to cancel: ").strip()
            if raw.lower() == 'q':
                print("❌ Restore cancelled")
                return False
            try:
                choice = int(raw)
            except ValueError:
                print("❌ Please enter a valid number")
                continue
            if choice < 1 or choice > len(backups):
                print("❌ Selection out of range")
                continue
            selection = backups[choice - 1]

        size_mb = selection['size'] / (1024 * 1024) if selection['size'] else 0
        print("\nYou selected:")
        print(f"  Key: {selection['key']}")
        print(f"  Size: {size_mb:.1f} MB")
        confirm = input("Overwrite local db.sqlite3 with this backup? (y/n): ").lower()
        if confirm != 'y':
            print("❌ Restore cancelled")
            return False

        restore_dir = local_db.parent
        try:
            restore_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            print(f"❌ Failed to prepare restore directory {restore_dir}: {exc}")
            return False

        try:
            fd, tmp_name = tempfile.mkstemp(
                dir=str(restore_dir),
                prefix=f".{local_db.stem}_restore_",
                suffix='.sqlite3'
            )
        except OSError as exc:
            print(f"❌ Failed to create temporary file in {restore_dir}: {exc}")
            return False
        os.close(fd)
        tmp_path = Path(tmp_name)
        try:
            with tmp_path.open('wb') as handle:
                s3.download_fileobj(bucket, selection['key'], handle)
        except ClientError as exc:
            print(f"❌ Failed to download backup: {exc}")
            tmp_path.unlink(missing_ok=True)
            return False
        except Exception as exc:
            print(f"❌ Unexpected error while downloading backup: {exc}")
            tmp_path.unlink(missing_ok=True)
            return False

        timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
        local_backup_path = None
        try:
            if local_db.exists():
                local_backup_path = local_db.with_name(
                    f"{local_db.name}.pre_s3_restore_{timestamp}"
                )
                print(f"💾 Saving current local DB to {local_backup_path}")
                local_db.replace(local_backup_path)

            tmp_path.replace(local_db)
        except Exception as exc:
            print(f"❌ Failed to install downloaded backup: {exc}")
            if local_backup_path and local_backup_path.exists() and not local_db.exists():
                local_backup_path.replace(local_db)
            tmp_path.unlink(missing_ok=True)
            return False

        print(f"✅ Local database updated from {selection['name']}")
        if local_backup_path:
            print(f"   Previous copy saved at: {local_backup_path}")
        return True

    def free_disk_on_server(self):
        """Aggressively free disk on the remote host.
        Steps:
          - sudo docker compose down
          - remove SQLite DB at /app/data/db.sqlite3
          - remove MeiliSearch data volume(s)
          - prune docker builder cache + unused images
          - truncate docker json logs, vacuum systemd journal, clean apt caches, purge large /tmp files
        Leaves containers stopped so you can upload a fresh DB next.
        """
        target_host = self.prompt_host_for_operation('free disk cleanup')
        print(f"\n🧹 Starting disk cleanup on {target_host}...")

        if not self.connect(host_override=target_host):
            return False

        try:
            app_path = self.config['app_path']
            remote_db_dir = f"{app_path}/data"
            remote_db_path = f"{remote_db_dir}/db.sqlite3"
            remote_db_dir_q = shlex.quote(remote_db_dir)
            remote_db_path_q = shlex.quote(remote_db_path)

            # Measure free space before (show multiple mounts)
            try:
                self._print_remote_space_summary(app_path, label_prefix="Before")
            except Exception as exc:
                print(f"⚠️  Failed to read space summary (before): {exc}")

            cmds = [
                ("Stopping containers", f"cd {shlex.quote(app_path)} && sudo docker compose down"),
                ("Removing SQLite database", f"sudo rm -f {remote_db_path_q}"),
                (
                    "Removing MeiliSearch volume(s)",
                    "for v in $(sudo docker volume ls -q | grep meilisearch_data || true); do sudo docker volume rm -f $v || true; done"
                ),
                (
                    "Truncating Docker container JSON logs",
                    "sudo find /var/lib/docker/containers -type f -name '*-json.log' -exec truncate -s 0 {} + 2>/dev/null || true"
                ),
                ("Pruning Docker builder cache", "sudo docker builder prune -af"),
                ("Pruning unused Docker images", "sudo docker image prune -af"),
                ("Pruning unused Docker volumes", "sudo docker volume prune -f"),
                ("Vacuuming system journal (7d)", "sudo journalctl --vacuum-time=7d || true"),
                ("Vacuuming system journal (cap 200M)", "sudo journalctl --vacuum-size=200M || true"),
                ("Cleaning apt caches", "sudo apt-get clean && sudo rm -rf /var/lib/apt/lists/* || true"),
                (
                    "Deleting large temp files (/tmp >10M, older than 1d)",
                    "sudo find /tmp -type f -mtime +1 -size +10M -delete 2>/dev/null || true"
                ),
                (
                    "Removing pip caches",
                    "sudo rm -rf /root/.cache/pip /home/*/.cache/pip 2>/dev/null || true"
                ),
            ]

            for desc, cmd in cmds:
                print(f"   {desc}...")
                success, _, error = self.execute_command(cmd, show_output=False)
                if not success:
                    print(f"⚠️  {desc} failed: {error}")

            try:
                self._print_remote_space_summary(app_path, label_prefix="After ")
            except Exception as exc:
                print(f"⚠️  Failed to read space summary (after): {exc}")

            print("\n✅ Disk cleanup completed.")
            print("Next steps:")
            print("  1) From your machine: run option 4 (Deploy Database) to upload a fresh db.sqlite3")
            print("  2) Then run option 6 (Reindex Search) to rebuild MeiliSearch")
            return True

        except Exception as e:
            print(f"❌ Disk cleanup failed: {e}")
            return False
        finally:
            self.disconnect()
    
    def show_status(self):
        """Show server status, Django logs, and restore/maintenance state."""
        target_host = self.prompt_host_for_operation('server status')
        print(f"\n📊 Checking server status on {target_host}...")

        try:
            self._print_aws_host_diagnostics(target_host)
        except Exception as exc:
            print(f"\n⚠️  AWS diagnostics failed: {exc}")

        try:
            self._print_cloudwatch_metrics(target_host)
        except Exception as exc:
            print(f"\n⚠️  CloudWatch metric lookup failed: {exc}")

        try:
            self.run_ssm_diagnostics(target_host)
        except Exception as exc:
            print(f"\n⚠️  SSM diagnostics failed: {exc}")

        self._print_public_network_diagnostics(target_host)

        if not self.connect(host_override=target_host):
            print("\n⚠️  SSH is unavailable; skipping Docker/log inspection.")
            return False

        try:
            app_path = self.config['app_path']
            
            print("🐳 Container status:")
            self.execute_command(f"cd {app_path} && sudo docker compose ps")
            
            print("\n📋 Django logs (last 200 lines):")
            self.execute_command(f"cd {app_path} && sudo docker compose logs django --tail=200")

            print("\n🔒 Maintenance / restore lock state:")
            lock_state_command = (
                f"cd {app_path} && "
                "if sudo docker compose ps --status running -q django | grep -q .; then "
                "sudo docker compose exec -T django sh -lc "
                "'for f in /app/tmp/maintenance.lock /app/tmp/pending_restore_restart.lock; do "
                "if [ -f \"$f\" ]; then echo FILE:$f; cat \"$f\"; "
                "else echo MISSING:$f; fi; "
                "done'; "
                "else echo 'django container is not running; lock files unavailable'; fi"
            )
            self.execute_command(lock_state_command)
            
            return True
            
        except Exception as e:
            print(f"❌ Status check failed: {e}")
            return False
        
        finally:
            self.disconnect()
    
    def _require_provision_settings(self, *keys: str) -> None:
        config = self._get_provisioning()
        missing = [key for key in keys if not config.get(key)]
        if missing:
            raise ValueError(
                "Missing provisioning settings: " + ", ".join(missing)
            )

    def _aws_client(self, service: str):
        config = self._get_provisioning()
        region = config.get('aws_region')
        if not region:
            raise ValueError('aws_region missing in provisioning config')

        if self._aws_session is None:
            session_kwargs = {'region_name': region}
            profile = config.get('aws_profile') or None
            if profile:
                session_kwargs['profile_name'] = profile
            self._aws_session = boto3.session.Session(**session_kwargs)
        return self._aws_session.client(service)

    def _tag_specifications(self) -> List[Dict[str, str]]:
        raw = self._get_provisioning().get('tag_specification') or ''
        tags: List[Dict[str, str]] = []
        if raw:
            for pair in raw.split(','):
                pair = pair.strip()
                if not pair:
                    continue
                if '=' not in pair:
                    raise ValueError(f"Invalid tag spec '{pair}' (expected key=value)")
                key, value = pair.split('=', 1)
                tags.append({'Key': key.strip(), 'Value': value.strip()})

        tags = [tag for tag in tags if tag.get('Key') != 'Name']
        timestamp = datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')
        tags.append({'Key': 'Name', 'Value': f'vdw{timestamp}'})

        return tags

    def _required_ports(self) -> List[int]:
        base_ports = {22, 80, 443, 7700}
        try:
            base_ports.add(int(self.config['django_port']))
        except (TypeError, ValueError):
            base_ports.add(8000)
        base_ports.update(self._get_provisioning().get('extra_ports') or [])
        return sorted(port for port in base_ports if port)

    def ensure_security_group(self) -> str:
        """Create or reuse a security group with required ports."""
        config = self._get_provisioning()
        ec2 = self._aws_client('ec2')
        sg_id = config.get('security_group_id')
        if sg_id:
            print(f"🔒 Using existing security group {sg_id}")
        else:
            self._require_provision_settings('security_group_name', 'vpc_id')
            sg_name = config['security_group_name']
            vpc_id = config['vpc_id']
            print(f"🔒 Ensuring security group '{sg_name}' in VPC {vpc_id} exists...")
            existing = ec2.describe_security_groups(
                Filters=[
                    {'Name': 'group-name', 'Values': [sg_name]},
                    {'Name': 'vpc-id', 'Values': [vpc_id]},
                ]
            )
            if existing['SecurityGroups']:
                sg_id = existing['SecurityGroups'][0]['GroupId']
                print(f"   Found existing group {sg_id}")
            else:
                response = ec2.create_security_group(
                    GroupName=sg_name,
                    Description='VDW Server security group',
                    VpcId=vpc_id,
                )
                sg_id = response['GroupId']
                tags = self._tag_specifications()
                if tags:
                    ec2.create_tags(Resources=[sg_id], Tags=tags)
                print(f"   Created security group {sg_id}")

        ssh_cidr = config.get('ssh_ingress_cidr') or '0.0.0.0/0'
        for port in self._required_ports():
            cidr = ssh_cidr if port == 22 else '0.0.0.0/0'
            permission = {
                'IpProtocol': 'tcp',
                'FromPort': port,
                'ToPort': port,
                'IpRanges': [{'CidrIp': cidr}],
            }
            try:
                ec2.authorize_security_group_ingress(
                    GroupId=sg_id,
                    IpPermissions=[permission],
                )
                print(f"   Opened port {port}/tcp")
            except ClientError as exc:
                if exc.response['Error']['Code'] != 'InvalidPermission.Duplicate':
                    raise
        print("✅ Security group ready!")
        return sg_id

    def _resolve_security_group_for_host(self, host: str) -> Optional[str]:
        ec2 = self._aws_client('ec2')
        try:
            response = ec2.describe_instances(Filters=[{'Name': 'ip-address', 'Values': [host]}])
        except ClientError as exc:
            print(f"⚠️  Failed to inspect instance for host {host}: {exc}")
            response = {'Reservations': []}

        groups = []
        for reservation in response.get('Reservations', []):
            for instance in reservation.get('Instances', []):
                if instance.get('PublicIpAddress') != host:
                    continue
                for sg in instance.get('SecurityGroups', []):
                    groups.append((sg['GroupId'], sg.get('GroupName')))

        if not groups:
            return self._get_provisioning().get('security_group_id')

        if len(groups) == 1:
            return groups[0][0]

        print("\nMultiple security groups found on the selected host:")
        for idx, (group_id, name) in enumerate(groups, start=1):
            label = f"{group_id} ({name})" if name else group_id
            print(f"  [{idx}] {label}")

        while True:
            choice = input(f"Select group to lock [1-{len(groups)}]: ").strip()
            if not choice:
                print("❌ Selection required")
                continue
            try:
                idx = int(choice)
            except ValueError:
                print("❌ Enter a number")
                continue
            if 1 <= idx <= len(groups):
                return groups[idx - 1][0]
            print("❌ Invalid selection")

    def lock_security_group_https_only(self, host: str) -> bool:
        """Restrict ingress rules to SSH and HTTPS only for the selected host."""
        sg_id = self._resolve_security_group_for_host(host)
        if not sg_id:
            print('❌ Could not determine security group for the selected host. Update config/provisioning.json or ensure the instance has a public IP.')
            return False

        print(f"\n🔒 Locking security group {sg_id} (host {host}) to SSH (22) and HTTPS (443) only...")
        ec2 = self._aws_client('ec2')
        try:
            response = ec2.describe_security_groups(GroupIds=[sg_id])
            security_group = response['SecurityGroups'][0]
        except ClientError as exc:
            print(f"❌ Failed to describe security group: {exc}")
            return False

        current_permissions = security_group.get('IpPermissions', [])
        if current_permissions:
            try:
                ec2.revoke_security_group_ingress(GroupId=sg_id, IpPermissions=current_permissions)
                print("   Removed existing ingress rules")
            except ClientError as exc:
                print(f"❌ Failed to remove existing rules: {exc}")
                return False
        else:
            print("   No existing ingress rules to remove")

        ssh_cidr = self._get_provisioning().get('ssh_ingress_cidr') or '0.0.0.0/0'
        allowed_rules = [
            {
                'IpProtocol': 'tcp',
                'FromPort': 22,
                'ToPort': 22,
                'IpRanges': [{'CidrIp': ssh_cidr}],
            },
            {
                'IpProtocol': 'tcp',
                'FromPort': 443,
                'ToPort': 443,
                'IpRanges': [{'CidrIp': '0.0.0.0/0'}],
            },
        ]

        try:
            ec2.authorize_security_group_ingress(GroupId=sg_id, IpPermissions=allowed_rules)
        except ClientError as exc:
            print(f"❌ Failed to add SSH/HTTPS rules: {exc}")
            return False

        print("✅ Security group locked down. Ports 80, 8000, 7700, etc. are now closed.")
        return True

    def launch_instance(self, security_group_id: str) -> str:
        """Launch a fresh EC2 instance with the configured settings."""
        config = self._get_provisioning()
        self._require_provision_settings('ami_id', 'instance_type', 'subnet_id', 'key_name')
        ec2 = self._aws_client('ec2')

        block_devices = [
            {
                'DeviceName': config.get('root_device_name') or '/dev/sda1',
                'Ebs': {
                    'VolumeSize': int(config.get('root_volume_gb') or 40),
                    'VolumeType': 'gp3',
                    'DeleteOnTermination': True,
                },
            }
        ]

        data_volume_gb = int(config.get('data_volume_gb') or 0)
        if data_volume_gb > 0:
            block_devices.append({
                'DeviceName': config.get('data_device_name') or '/dev/sdf',
                'Ebs': {
                    'VolumeSize': data_volume_gb,
                    'VolumeType': 'gp3',
                    'DeleteOnTermination': False,
                },
            })

        associate_public_ip = (
            str(config.get('associate_public_ip') or 'true').lower() != 'false'
        )

        network_interfaces = [{
            'DeviceIndex': 0,
            'SubnetId': config['subnet_id'],
            'AssociatePublicIpAddress': associate_public_ip,
            'Groups': [security_group_id],
            'DeleteOnTermination': True,
        }]

        tag_specifications = []
        tags = self._tag_specifications()
        if tags:
            tag_specifications.append({'ResourceType': 'instance', 'Tags': tags})
            tag_specifications.append({'ResourceType': 'volume', 'Tags': tags})

        params = {
            'ImageId': config['ami_id'],
            'InstanceType': config['instance_type'],
            'KeyName': config['key_name'],
            'BlockDeviceMappings': block_devices,
            'MaxCount': 1,
            'MinCount': 1,
            'NetworkInterfaces': network_interfaces,
        }
        if tag_specifications:
            params['TagSpecifications'] = tag_specifications
        profile = config.get('iam_instance_profile')
        if profile:
            params['IamInstanceProfile'] = {'Name': profile}

        print("🚀 Launching EC2 instance...")
        response = ec2.run_instances(**params)
        instance_id = response['Instances'][0]['InstanceId']
        print(f"   Instance {instance_id} is provisioning")
        return instance_id

    def _wait_for_instance_running(self, instance_id: str) -> None:
        ec2 = self._aws_client('ec2')
        print("⏳ Waiting for instance to enter running state...")
        waiter = ec2.get_waiter('instance_running')
        waiter.wait(InstanceIds=[instance_id])
        print("   Instance is running")

    def _wait_for_instance_status_ok(self, instance_id: str) -> None:
        ec2 = self._aws_client('ec2')
        print("⏳ Waiting for system checks to pass...")
        waiter = ec2.get_waiter('instance_status_ok')
        waiter.wait(InstanceIds=[instance_id])
        print("   Instance checks passed")

    def describe_instance(self, instance_id: str) -> Dict:
        ec2 = self._aws_client('ec2')
        reservations = ec2.describe_instances(InstanceIds=[instance_id])['Reservations']
        if not reservations or not reservations[0]['Instances']:
            raise RuntimeError(f"Instance {instance_id} not found")
        return reservations[0]['Instances'][0]

    def _wait_for_ssh(self, host: str, timeout: int = 600) -> None:
        print(f"⏳ Waiting for SSH on {host}:{self.config['port']}...")
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with socket.create_connection((host, self.config['port']), timeout=10):
                    print("   SSH reachable")
                    return
            except OSError:
                time.sleep(5)
        raise TimeoutError(f"Timed out waiting for SSH on {host}")

    def _write_provision_state(self, state: Dict) -> None:
        self.provision_state_path.parent.mkdir(parents=True, exist_ok=True)
        state['written_at'] = datetime.now(timezone.utc).isoformat()
        self.provision_state_path.write_text(json.dumps(state, indent=2))
        self.latest_state = state
        print(f"📝 Saved provision details to {self.provision_state_path}")

    def _load_provision_state(self) -> Optional[Dict]:
        if not self.provision_state_path.exists():
            return None
        try:
            return json.loads(self.provision_state_path.read_text())
        except json.JSONDecodeError:
            return None

    def _host_options(self, forced_options: Optional[List] = None) -> List:
        options = forced_options[:] if forced_options else []
        if not options:
            if self.production_host:
                options.append(('0', self.production_host, 'prod (Elastic IP)'))
            latest = self._load_provision_state()
            latest_ip = latest.get('public_ip') if latest else None
            if latest_ip:
                ts = latest.get('written_at')
                label = 'test (latest provisioned)'
                if ts:
                    label += f" ({ts})"
                options.append(('1', latest_ip, label))
        return options

    def choose_active_host(self, forced_options: Optional[List] = None) -> None:
        options = self._host_options(forced_options)

        if not options:
            print("❌ No alternate hosts available (capture provisioning config or provision first)")
            return

        print("\nAvailable targets:")
        for key, host, description in options:
            marker = '*' if host == self.active_host else ' '
            print(f"  [{key}] {host} {description} {marker}")

        prompt_keys = '/'.join(key for key, _, _ in options)
        choice = input(f"Select target ({prompt_keys}): ").strip().lower()
        for key, host, description in options:
            if choice == key:
                label = 'prod' if key == '0' else 'test'
                self.set_active_host(host, label)
                return
        print("❌ Invalid target selection")

    def require_active_host(self, prompt_keys: Optional[str] = None) -> str:
        host = self.active_host
        if host:
            return host
        print("❌ No active host selected.")
        self.choose_active_host()
        if not self.active_host:
            raise RuntimeError("No active host selected")
        return self.active_host

    def prompt_host_for_operation(self, operation: str) -> str:
        options = self._host_options()
        if not options:
            raise RuntimeError("No host choices available. Provision a server or configure the Elastic IP host.")

        print(f"\nTarget selection for {operation}:")
        for key, host, description in options:
            marker = ''
            if host == self.active_host:
                marker = ' (current)'
            print(f"  [{key}] {host} {description}{marker}")

        prompt_keys = '/'.join(key for key, _, _ in options)
        while True:
            choice = input(f"Select target ({prompt_keys}) [Enter to cancel]: ").strip().lower()
            if not choice:
                raise RuntimeError("Operation cancelled by user")
            for key, host, description in options:
                if choice == key:
                    label = 'prod' if key == '0' else 'test'
                    self.set_active_host(host, label)
                    return host
            print("❌ Invalid selection; please try again.")

    def _run_certbot_dns01(self, host: str) -> bool:
        domains = self._all_domains()
        cfg = self._domain_config()
        if not domains or not cfg['email']:
            raise ValueError("primary_domain and certbot_email must be set in config/provisioning.json")

        domain_args = ' '.join(f"-d {shlex.quote(domain)}" for domain in domains)
        command = (
            "sudo certbot certonly --manual --preferred-challenges dns "
            "--manual-public-ip-logging-ok --agree-tos --no-eff-email "
            f"-m {shlex.quote(cfg['email'])} {domain_args}"
        )

        print("\n🚧 Running Certbot (manual DNS-01 challenge)...")
        transport = self.ssh_client.get_transport()
        channel = transport.open_session()
        channel.get_pty()
        channel.exec_command(command)
        stdin = channel.makefile('wb')
        stdout = channel.makefile('r')

        try:
            awaiting_value = False
            current_name = ''
            for raw_line in stdout:
                print(raw_line, end='')
                line = raw_line.strip()
                if line.startswith('_acme-challenge'):
                    current_name = line.rstrip('.')
                elif line == 'with the following value:':
                    awaiting_value = True
                    continue
                elif awaiting_value and line:
                    awaiting_value = False
                    challenge_value = line
                    print("\n🚨 ACTION REQUIRED")
                    print(f"Add TXT record for {current_name} with value:\n{challenge_value}")
                    print("Use a DNS checker (e.g., https://www.whatsmydns.net/#TXT/" + current_name + ") to confirm propagation.")
                    input("After the record propagates globally, press Enter here to let Certbot continue...")
                    try:
                        stdin.write('\n')
                        stdin.flush()
                    except OSError as exc:
                        print("⚠️  SSH channel closed while sending input; re-run option 10 if needed.")
                        raise
                elif 'Press Enter to Continue' in line:
                    input("Press Enter to continue...")
                    try:
                        stdin.write('\n')
                        stdin.flush()
                    except OSError as exc:
                        print("⚠️  SSH channel closed while sending input; re-run option 10 if needed.")
                        raise
        finally:
            stdout.close()
            stdin.close()
        exit_status = channel.recv_exit_status()
        if exit_status == 0:
            print("✅ Certbot completed successfully")
            return True
        print("❌ Certbot failed. Check output above for details.")
        return False

    def _ensure_acme_webroot(self, webroot: str) -> bool:
        challenge_path = f"{webroot}/.well-known/acme-challenge"
        mkdir_cmd = f"sudo mkdir -p {shlex.quote(challenge_path)}"
        success, _, error = self.execute_command(mkdir_cmd, show_output=False)
        if not success:
            print(f"❌ Failed to create ACME webroot at {webroot}: {error}")
            return False

        chmod_cmd = f"sudo chmod -R 755 {shlex.quote(webroot)}"
        success, _, error = self.execute_command(chmod_cmd, show_output=False)
        if not success:
            print(f"❌ Failed to set permissions on {webroot}: {error}")
            return False

        return True

    def _enable_certbot_timer(self) -> bool:
        enable_cmd = "sudo systemctl enable --now certbot.timer"
        success, _, error = self.execute_command(enable_cmd, show_output=False)
        if not success:
            print(f"❌ Failed to enable certbot.timer: {error}")
            return False

        active_cmd = "sudo systemctl is-active --quiet certbot.timer"
        success, _, _ = self.execute_command(active_cmd, show_output=False)
        if not success:
            print("❌ certbot.timer is not active. Check systemctl status certbot.timer.")
            return False

        print("✅ certbot.timer is active (auto-renew enabled)")
        return True

    def _ensure_port_80_ingress(self, host: str, interactive: bool = True) -> bool:
        sg_id = self._resolve_security_group_for_host(host)
        if not sg_id:
            print("⚠️  Could not determine security group for this host.")
            if interactive:
                response = input("Continue without opening port 80? (y/n): ").lower()
                if response != 'y':
                    return False
                return True
            print("❌ Port 80 is required for HTTP-01 validation.")
            return False

        if interactive:
            response = input(f"Ensure port 80/tcp is open in {sg_id}? (y/n): ").lower()
            if response != 'y':
                print("❌ Port 80 is required for HTTP-01 validation.")
                return False
        else:
            print(f"🛠️  Ensuring port 80/tcp is open in {sg_id}...")

        ec2 = self._aws_client('ec2')
        permission = {
            'IpProtocol': 'tcp',
            'FromPort': 80,
            'ToPort': 80,
            'IpRanges': [{'CidrIp': '0.0.0.0/0'}],
        }
        try:
            ec2.authorize_security_group_ingress(
                GroupId=sg_id,
                IpPermissions=[permission],
            )
            print("✅ Port 80/tcp opened on the security group")
            return True
        except ClientError as exc:
            code = exc.response['Error']['Code']
            if code == 'InvalidPermission.Duplicate':
                print("✅ Port 80/tcp already open on the security group")
                return True
            print(f"❌ Failed to update security group: {exc}")
            return False

    def _http_fetch_command(self, url: str) -> str:
        curl_check, _, _ = self.execute_command("command -v curl", show_output=False)
        if curl_check:
            return f"curl -fsS --max-time 10 {shlex.quote(url)}"

        wget_check, _, _ = self.execute_command("command -v wget", show_output=False)
        if wget_check:
            return f"wget -qO- {shlex.quote(url)}"

        python_payload = (
            "import sys, urllib.request\n"
            f"url = {url!r}\n"
            "with urllib.request.urlopen(url, timeout=10) as resp:\n"
            "    sys.stdout.write(resp.read().decode())\n"
        )
        return f"python3 -c {shlex.quote(python_payload)}"

    def _http01_preflight(self, primary_domain: str) -> bool:
        token = f"vdw-acme-{int(time.time())}"
        challenge_dir = f"{ACME_WEBROOT}/.well-known/acme-challenge"
        challenge_file = f"{challenge_dir}/{token}"
        create_cmd = (
            f"sudo mkdir -p {shlex.quote(challenge_dir)} && "
            f"echo {shlex.quote(token)} | sudo tee {shlex.quote(challenge_file)} >/dev/null"
        )
        success, _, error = self.execute_command(create_cmd, show_output=False)
        if not success:
            print(f"❌ Failed to create HTTP-01 challenge file: {error}")
            return False

        url = f"http://{primary_domain}/.well-known/acme-challenge/{token}"
        fetch_cmd = self._http_fetch_command(url)
        success, output, error = self.execute_command(fetch_cmd, show_output=False)
        cleanup_cmd = f"sudo rm -f {shlex.quote(challenge_file)}"
        self.execute_command(cleanup_cmd, show_output=False)

        if not success or output.strip() != token:
            print("❌ HTTP-01 preflight failed. The ACME challenge is not reachable over port 80.")
            print(f"   Expected to fetch: {url}")
            if error:
                print(f"   Fetch error: {error}")
            return False

        print("✅ HTTP-01 preflight succeeded (challenge path reachable)")
        return True

    def _read_renewal_authenticator(self, primary_domain: str) -> str:
        conf_path = f"/etc/letsencrypt/renewal/{primary_domain}.conf"
        cmd = (
            "sudo awk -F= '/^authenticator/ "
            "{gsub(/ /, \"\", $2); print $2; exit}' "
            f"{shlex.quote(conf_path)}"
        )
        success, output, _ = self.execute_command(cmd, show_output=False)
        return output.strip() if success else ""

    def _print_certbot_diagnostics(self):
        print("🔎 Diagnostics: nginx config, nginx status, port 80 listener, certbot log tail")
        self.execute_command("sudo nginx -t", show_output=True)
        self.execute_command("sudo systemctl status nginx --no-pager", show_output=True)
        self.execute_command("sudo ss -ltnp | grep ':80'", show_output=True)
        self.execute_command("sudo tail -n 120 /var/log/letsencrypt/letsencrypt.log", show_output=True)

    def issue_https_certificate(self):
        host = self.prompt_host_for_operation('issue HTTPS certificate')
        cfg = self._domain_config()
        if not cfg['primary'] or not cfg['email']:
            print("❌ primary_domain and certbot_email must be set in config/provisioning.json")
            return False

        if not self.connect(host_override=host):
            return False

        try:
            if not self.install_certbot():
                return False
            if self._run_certbot_dns01(host) is False:
                return False
            content = self._render_https_nginx()
            if not self.configure_nginx_proxy(content=content):
                return False
            print("🎉 HTTPS configuration complete. Test https://{} before swapping DNS.".format(cfg['primary']))
            return True
        finally:
            self.disconnect()

    def issue_https_certificate_http01(self):
        host = self.prompt_host_for_operation('issue HTTPS certificate (HTTP-01)')
        cfg = self._domain_config()
        if not cfg['primary'] or not cfg['email']:
            print("❌ primary_domain and certbot_email must be set in config/provisioning.json")
            return False

        if not self.connect(host_override=host):
            return False

        try:
            if not self.install_certbot():
                return False
            if not self._ensure_port_80_ingress(host, interactive=False):
                return False
            if not self._ensure_acme_webroot(ACME_WEBROOT):
                return False

            paths = self._ssl_paths()
            cert_path = paths['cert']
            cert_exists, _, _ = self.execute_command(f"sudo test -f {shlex.quote(cert_path)}", show_output=False)

            if cert_exists:
                if not self.configure_nginx_proxy(content=self._render_https_nginx()):
                    return False
            else:
                if not self.configure_nginx_proxy():
                    return False

            domains = self._all_domains()
            if not domains:
                print("❌ No domains configured. Set primary_domain (and optional alt_domains).")
                return False

            if not self._http01_preflight(cfg['primary']):
                print("❌ HTTP-01 preflight failed. Fix port 80 routing before issuing certificates.")
                self._print_certbot_diagnostics()
                return False

            domain_args = ' '.join(f"-d {shlex.quote(domain)}" for domain in domains)
            command = (
                "sudo certbot certonly --webroot "
                f"-w {shlex.quote(ACME_WEBROOT)} --agree-tos --no-eff-email "
                "--non-interactive --force-renewal "
                f"-m {shlex.quote(cfg['email'])} {domain_args}"
            )
            print("ℹ️  Cert issuance can take 1–5 minutes; it's normal for this step to be quiet.")
            print("ℹ️  This run is non-interactive and forces renewal to switch to HTTP-01.")
            print("\n🚧 Running Certbot (HTTP-01 webroot)...")
            success, _, _ = self.execute_command(
                command,
                show_output=True,
                stream_output=True,
                heartbeat_seconds=120,
                watchdog_seconds=300,
                watchdog_callback=self._print_certbot_diagnostics,
            )
            if not success:
                print("❌ Certbot failed. Review diagnostics below.")
                self._print_certbot_diagnostics()
                return False

            if not self.configure_nginx_proxy(content=self._render_https_nginx()):
                return False
            if not self._enable_certbot_timer():
                return False

            print("🎉 HTTPS configuration complete (HTTP-01). Auto-renew is enabled via certbot.timer.")
            return True
        finally:
            self.disconnect()

    def renew_https_certificate_dry_run(self):
        host = self.prompt_host_for_operation('dry-run HTTPS renewal (certbot renew)')
        cfg = self._domain_config()
        if not cfg['primary']:
            print("❌ primary_domain must be set in config/provisioning.json")
            return False

        if not self.connect(host_override=host):
            return False

        try:
            if not self.install_certbot():
                return False
            if not self._ensure_port_80_ingress(host, interactive=False):
                return False

            authenticator = self._read_renewal_authenticator(cfg['primary'])
            if authenticator == 'manual':
                print("❌ Renewal config is still using manual DNS-01.")
                print("   Run Option 15 (HTTP-01, auto-renew) to switch the renewal method.")
                return False

            if not self._http01_preflight(cfg['primary']):
                print("❌ HTTP-01 preflight failed. Fix port 80 routing before testing renewals.")
                self._print_certbot_diagnostics()
                return False

            print("ℹ️  Dry-run can take 1–5 minutes; it's normal for this step to be quiet.")
            print("\n🚧 Running certbot renew --dry-run...")
            success, _, _ = self.execute_command(
                "sudo certbot renew --dry-run --non-interactive",
                show_output=True,
                stream_output=True,
                heartbeat_seconds=120,
                watchdog_seconds=300,
                watchdog_callback=self._print_certbot_diagnostics,
            )
            if not success:
                print("❌ Dry-run renewal failed. Review diagnostics below.")
                self._print_certbot_diagnostics()
                return False
            print("✅ Dry-run renewal succeeded")
            return True
        finally:
            self.disconnect()

    def reset_https_configuration(self):
        host = self.prompt_host_for_operation('reset HTTPS configuration')
        cfg = self._domain_config()
        if not cfg['primary']:
            print("❌ primary_domain must be set in config/provisioning.json")
            return False

        if not self.connect(host_override=host):
            return False

        try:
            primary = cfg['primary']
            cleanup_commands = [
                f"sudo rm -rf /etc/letsencrypt/live/{shlex.quote(primary)}",
                f"sudo rm -rf /etc/letsencrypt/archive/{shlex.quote(primary)}",
                f"sudo rm -f /etc/letsencrypt/renewal/{shlex.quote(primary)}.conf",
            ]
            for cmd in cleanup_commands:
                self.execute_command(cmd, show_output=False)

            if not self.configure_nginx_proxy():
                return False
            print("✅ HTTPS configuration reset. Run the issue command once you're ready to request new certificates.")
            return True
        finally:
            self.disconnect()

    def update_hosts_file(self):
        host = self.prompt_host_for_operation('update /etc/hosts entry')
        cfg = self._domain_config()
        if not cfg['primary']:
            print("❌ primary_domain must be set in config/provisioning.json")
            return False

        domains = self._all_domains()
        entry = f"{host} {' '.join(domains)}"
        hosts_path = Path('/etc/hosts')
        backup_path = Path('/etc/hosts.vdw-backup')

        if not hosts_path.exists():
            print("❌ Could not find /etc/hosts on this machine.")
            return False

        try:
            if not backup_path.exists():
                backup_path.write_text(hosts_path.read_text())
                print(f"💾 Backup saved to {backup_path}")

            lines = hosts_path.read_text().splitlines()
            filtered = [line for line in lines if 'vitamindwiki.com' not in line]
            filtered.append(entry)
            hosts_path.write_text('\n'.join(filtered) + '\n')
            print(f"✅ /etc/hosts updated with: {entry}")
            print("(Use the backup at /etc/hosts.vdw-backup to restore your original file.)")
            print("🌐 Now visit: https://{} (remember your browser will resolve it to {} until you restore /etc/hosts)".format(cfg['primary'], host))
            return True
        except PermissionError:
            print("❌ Permission denied updating /etc/hosts. Run this script with sudo or update manually.")
            return False
    
    def install_docker(self):
        """Install Docker and Docker Compose on the server"""
        print("🐳 Installing Docker...")
        
        # Check if Docker is already installed
        success, output, error = self.execute_command("docker --version", show_output=False)
        if success:
            print("✅ Docker already installed, skipping installation")
            return True
        
        commands = [
            "sudo apt-get update",
            "sudo apt-get install -y ca-certificates curl gnupg lsb-release",
            "sudo mkdir -p /etc/apt/keyrings",
            "curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor --batch --yes -o /etc/apt/keyrings/docker.gpg",
            'echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null',
            "sudo apt-get update",
            "sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin",
            "sudo usermod -aG docker $USER",
        ]
        
        for cmd in commands:
            print(f"   Running: {cmd}")
            success, output, error = self.execute_command(cmd, show_output=False)
            if not success:
                print(f"❌ Command failed: {error}")
                return False
        
        print("✅ Docker installed successfully!")
        return True

    def install_certbot(self) -> bool:
        """Install Certbot and dependencies"""
        print("🔐 Installing Certbot...")
        success, _, _ = self.execute_command("certbot --version", show_output=False)
        if success:
            print("✅ Certbot already installed")
            return True

        commands = [
            "sudo apt-get update",
            "sudo apt-get install -y certbot python3-certbot-nginx",
        ]

        for cmd in commands:
            success, _, error = self.execute_command(cmd, show_output=False)
            if not success:
                print(f"❌ Failed to run '{cmd}': {error}")
                return False
        print("✅ Certbot installed")
        return True
    
    def install_nginx(self) -> bool:
        """Install nginx if needed"""
        print("🌐 Installing nginx...")
        success, _, _ = self.execute_command("nginx -v", show_output=False)
        if success:
            print("✅ nginx already installed")
            return True

        commands = [
            "sudo apt-get update",
            "sudo apt-get install -y nginx",
            "sudo systemctl enable nginx",
        ]

        for cmd in commands:
            success, _, error = self.execute_command(cmd, show_output=False)
            if not success:
                print(f"❌ Failed to run '{cmd}': {error}")
                return False
        print("✅ nginx installed")
        return True

    def configure_nginx_proxy(self, content: Optional[str] = None) -> bool:
        """Upload nginx reverse proxy config and reload service."""
        print("📝 Configuring nginx reverse proxy...")
        remote_tmp = '/tmp/vdw_nginx.conf'
        remote_maintenance_tmp = '/tmp/vdw_maintenance.html'
        try:
            with SCPClient(self.ssh_client.get_transport()) as scp:
                if content is None:
                    local_conf = Path('nginx_config')
                    if not local_conf.exists():
                        print("❌ nginx_config file is missing in the project root")
                        return False
                    scp.put(str(local_conf), remote_tmp)
                else:
                    scp.putfo(io.BytesIO(content.encode('utf-8')), remote_tmp)
                scp.putfo(
                    io.BytesIO(self._render_maintenance_page().encode('utf-8')),
                    remote_maintenance_tmp,
                )
        except Exception as exc:
            print(f"❌ Failed to upload nginx config: {exc}")
            return False

        commands = [
            f"sudo mkdir -p {MAINTENANCE_PAGE_REMOTE_DIR}",
            f"sudo mv {remote_maintenance_tmp} {MAINTENANCE_PAGE_REMOTE_PATH}",
            f"sudo chmod 644 {MAINTENANCE_PAGE_REMOTE_PATH}",
            f"sudo mv {remote_tmp} /etc/nginx/sites-available/vdw",
            "sudo ln -sf /etc/nginx/sites-available/vdw /etc/nginx/sites-enabled/vdw",
            "sudo rm -f /etc/nginx/sites-enabled/default",
            "sudo nginx -t",
            "sudo systemctl reload nginx",
        ]

        for cmd in commands:
            success, _, error = self.execute_command(cmd, show_output=False)
            if not success:
                print(f"❌ Failed to configure nginx: {error}")
                return False
        print("✅ nginx reverse proxy configured")
        return True

    def _render_https_nginx(self) -> str:
        domains = self._all_domains()
        server_names = ' '.join(domains) if domains else '_'
        paths = self._ssl_paths()
        static_block = """
    location /static/ {
        alias /app/static/;
    }

    location /media/ {
        alias /app/media/;
    }

    location = /maintenance.html {
        root /var/www/vdw;
        internal;
        add_header Cache-Control "no-store, no-cache, must-revalidate, max-age=0" always;
    }

    location = /tiki-comment-list { return 410; }
    location = /tiki-comment-list/ { return 410; }
    location = /tiki-list_file_gallery.php { return 410; }
    location = /tiki-download_wiki_attachment.php { return 410; }
    location = /tiki-share.php { return 410; }
    location = /tiki-print.php { return 410; }
    location = /tiki-editpage.php { return 410; }

    location ~ ^/tiki- {
        limit_req zone=tiki_legacy burst=15 nodelay;
        proxy_intercept_errors on;
        error_page 502 503 504 /maintenance.html;
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location / {
        proxy_intercept_errors on;
        error_page 502 503 504 /maintenance.html;
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
"""
        acme_block = """
    location ^~ /.well-known/acme-challenge/ {{
        root {webroot};
        default_type "text/plain";
        try_files $uri =404;
    }}
""".format(webroot=ACME_WEBROOT)
        ssl_directives = """
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 10m;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers off;
    ssl_ciphers 'ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384';
    add_header Strict-Transport-Security "max-age=31536000" always;
"""

        return f"""limit_req_zone $binary_remote_addr zone=tiki_legacy:10m rate=3r/s;

server {{
    listen 80;
    server_name {server_names};
{acme_block}
    location / {{
        return 301 https://$host$request_uri;
    }}
}}

server {{
    listen 443 ssl http2;
    server_name {server_names};
    client_max_body_size 100M;
    ssl_certificate {paths['cert']};
    ssl_certificate_key {paths['key']};
{ssl_directives}
{static_block}}}
"""

    def _render_maintenance_page(self) -> str:
        return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>VitaminDWiki Update In Progress</title>
  <style>
    :root {
      color-scheme: light;
    }
    html, body {
      height: 100%;
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      color: #223;
      background: linear-gradient(135deg, #f4f7fb 0%, #eaf3ff 100%);
    }
    main {
      min-height: 100%;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 24px;
      box-sizing: border-box;
    }
    .card {
      width: min(640px, 100%);
      background: #fff;
      border: 1px solid #d9e2ef;
      border-radius: 12px;
      box-shadow: 0 10px 30px rgba(15, 35, 80, 0.12);
      padding: 28px;
    }
    h1 {
      margin: 0 0 10px;
      font-size: 1.75rem;
      line-height: 1.2;
      color: #0f2a56;
    }
    p {
      margin: 0 0 10px;
      font-size: 1rem;
      line-height: 1.5;
    }
    .hint {
      color: #4d5d78;
      font-size: 0.95rem;
    }
  </style>
</head>
<body>
  <main>
    <section class="card" role="status" aria-live="polite">
      <h1>VitaminDWiki is updating</h1>
      <p>The server is currently being updated and will be available again in a few minutes.</p>
      <p class="hint">Please refresh this page shortly.</p>
    </section>
  </main>
</body>
</html>
"""

    def setup_environment(self):
        """Set up environment variables"""
        print("🔧 Setting up environment...")
        
        # Check if local .env exists
        local_env = Path('.env')
        if not local_env.exists():
            print("❌ Local .env file not found. Please create one with your configuration.")
            return False
        
        # Upload .env file
        print("   Uploading .env file...")
        try:
            with SCPClient(self.ssh_client.get_transport()) as scp:
                scp.put(str(local_env), f"{self.config['app_path']}/.env")
            print("✅ Environment file uploaded!")
            return True
        except Exception as e:
            print(f"❌ Failed to upload .env: {e}")
            return False
    
    def prepare_data_volume(self) -> bool:
        data_volume_gb = int(self._get_provisioning().get('data_volume_gb') or 0)
        if data_volume_gb <= 0:
            return True

        print("💽 Preparing dedicated data volume mount at /app/data ...")
        remote_user = shlex.quote(self.config['user'])
        script = r"""
set -euo pipefail
ROOT_SRC=$(findmnt -n -o SOURCE /)
ROOT_DISK=$(lsblk -no PKNAME "$ROOT_SRC")
DATA_DEVICE=$(lsblk -ndo NAME,TYPE,MOUNTPOINT | awk -v root="$ROOT_DISK" '$2=="disk" && $3=="" && $1!=root {print "/dev/"$1; exit}')
if [ -z "$DATA_DEVICE" ]; then
  echo "No secondary disk detected" >&2
  exit 1
fi
TARGET=/app/data
if mountpoint -q "$TARGET"; then
  exit 0
fi
if ! sudo blkid "$DATA_DEVICE" >/dev/null 2>&1; then
  sudo mkfs.ext4 -F "$DATA_DEVICE"
fi
UUID=$(sudo blkid -s UUID -o value "$DATA_DEVICE")
sudo mkdir -p "$TARGET"
if ! grep -q "$UUID" /etc/fstab; then
  echo "UUID=$UUID $TARGET ext4 defaults,nofail 0 2" | sudo tee -a /etc/fstab >/dev/null
fi
sudo mount "$TARGET"
sudo chown %s:%s "$TARGET"
""" % (remote_user, remote_user)

        success, _, error = self.execute_command(f"bash -c {shlex.quote(script)}", show_output=False)
        if not success:
            print(f"❌ Failed to prepare data volume: {error}")
            return False
        print("✅ Data volume mounted at /app/data")
        return True

    def rebuild_and_restart_stack(self) -> bool:
        app_path = shlex.quote(self.config['app_path'])
        steps = [
            ("🐳 Rebuilding Docker containers", f"cd {app_path} && sudo docker compose up --build -d"),
            ("🔄 Running database migrations", f"cd {app_path} && sudo docker compose exec -T django python manage.py migrate"),
            ("📦 Collecting static files", f"cd {app_path} && sudo docker compose exec -T django python manage.py collectstatic --noinput"),
            ("🔍 Checking container status", f"cd {app_path} && sudo docker compose ps"),
        ]

        for description, cmd in steps:
            print(f"   {description}...")
            success, _, error = self.execute_command(cmd)
            if not success:
                print(f"❌ Failed during {description}: {error}")
                return False
        return True

    def provision_server(self):
        """Provision a brand-new EC2 instance and bootstrap Docker/nginx."""
        print("\n🚀 Starting new server provisioning...\n")
        try:
            profile_name = self.ensure_management_instance_profile()
            print(f"🔐 Management profile ready: {profile_name}")
            sg_id = self.ensure_security_group()
            instance_id = self.launch_instance(sg_id)
            self.config['instance_id'] = instance_id
            self._wait_for_instance_running(instance_id)
            self._wait_for_instance_status_ok(instance_id)
            instance = self.describe_instance(instance_id)
            public_ip = instance.get('PublicIpAddress')
            private_ip = instance.get('PrivateIpAddress')
            if not public_ip:
                raise RuntimeError("Instance does not have a public IP (check subnet settings)")

            print(f"🌐 Temporary public IP: {public_ip}")
            print(f"🔐 Private IP: {private_ip}")
            self._wait_for_ssh(public_ip)

            # Bootstrap remote host using temporary IP
            if not self.connect(host_override=public_ip):
                return False
            try:
                if not self.install_docker():
                    return False
                if not self.install_nginx():
                    return False
                if not self.prepare_data_volume():
                    return False
                if not self.configure_nginx_proxy():
                    return False
                if not self._bootstrap_management_on_connected_host(instance.get('Architecture') or 'x86_64'):
                    return False
            finally:
                self.disconnect()

            data_volume_id = None
            target_device = self._get_provisioning().get('data_device_name')
            for device in instance.get('BlockDeviceMappings', []):
                if device.get('DeviceName') == target_device:
                    data_volume_id = device.get('Ebs', {}).get('VolumeId')

            self._write_provision_state({
                'instance_id': instance_id,
                'public_ip': public_ip,
                'private_ip': private_ip,
                'security_group_id': sg_id,
                'data_volume_id': data_volume_id,
                'app_path': self.config['app_path'],
            })
            self.set_active_host(public_ip, 'test')

            print("\n🎉 Provisioning complete!")
            print("Next steps:")
            print("  • With the new host selected (see menu banner), run option 5 (Full Deploy) to upload code + DB")
            print("  • Test via http://{} before swapping DNS".format(public_ip))
            print("  • Once satisfied, run menu option 2 (Associate Elastic IP) to swap traffic")
            return True

        except Exception as exc:
            print(f"❌ Provisioning failed: {exc}")
            return False

    def associate_elastic_ip(self):
        """Attach the pre-allocated Elastic IP to the last provisioned instance."""
        allocation_id = self._get_provisioning().get('elastic_ip_allocation_id')
        if not allocation_id:
            print("❌ PROVISION_ELASTIC_IP_ALLOCATION_ID is not set")
            return False

        state = self._load_provision_state() or {}
        latest_instance = state.get('instance_id')
        latest_label = state.get('public_ip')
        current_instance = None

        try:
            ec2 = self._aws_client('ec2')
            address = ec2.describe_addresses(AllocationIds=[allocation_id])['Addresses'][0]
            current_instance = address.get('InstanceId')
            public_ip = address.get('PublicIp')
        except ClientError as exc:
            print(f"❌ Failed to inspect Elastic IP: {exc}")
            return False

        print("\nElastic IP options:")
        options = []
        if current_instance:
            options.append(('0', current_instance, f"prod (currently attached)"))
        if latest_instance and latest_instance != current_instance:
            label = f"test (latest provisioned @ {latest_label})" if latest_label else 'test (latest provisioned)'
            options.append(('1', latest_instance, label))

        if not options:
            print("❌ No instance IDs available to associate. Provision first.")
            return False

        for key, instance_id, description in options:
            print(f"  [{key}] {instance_id} {description}")

        prompt_keys = '/'.join(key for key, _, _ in options)
        choice = input(f"Select target ({prompt_keys}): ").strip()
        target_instance = None
        for key, instance_id, description in options:
            if choice == key:
                target_instance = instance_id
                break

        if not target_instance:
            print("❌ Invalid selection")
            return False

        print(f"Elastic IP {public_ip} currently attached to: {current_instance or 'none'}")
        if input(f"Associate {public_ip} with {target_instance}? (y/n): ").lower() != 'y':
            print("❌ Operation cancelled")
            return False

        ec2.associate_address(
            AllocationId=allocation_id,
            InstanceId=target_instance,
            AllowReassociation=True,
        )
        print(f"✅ Elastic IP {public_ip} now points to {target_instance}")

        if current_instance and current_instance != target_instance:
            if input(f"Terminate previous instance {current_instance}? (y/n): ").lower() == 'y':
                ec2.terminate_instances(InstanceIds=[current_instance])
                print(f"🗑️ Termination requested for {current_instance}")
        return True

def print_header():
    """Print script header"""
    print("\n" + "=" * 50)
    print("    VDW Server Docker Deployment")
    print("=" * 50)

def print_menu(active_host: str, label: str):
    """Print deployment menu"""
    banner = f"{active_host} ({label})" if label else active_host
    print(f"\n🌐 Active Host: {banner}")
    print(f"📁 App path: {os.getenv('DEPLOY_APP_PATH', '/app')}\n")
    
    print("Select deployment option:\n")
    print("0. Capture provisioning config from current server")
    print("1. Provision + Bootstrap new server (Phase 1)")
    print("2. Associate Elastic IP with last provisioned server")
    print("3. Deploy Code from Local (upload code + retain db + run migrations + rebuild containers)")
    print("4. Deploy Database from Local (retain code + upload db + reindex search)")
    print("5. Deploy Code and Database from Local (upload code + upload db + run migrations + reindex search)")
    print("6. Reindex Search")
    print("7. Free Disk (stop containers, delete DB, remove Meili volume, prune caches)")
    print("8. Troubleshoot (AWS/EIP/CloudWatch/SSM + docker ps + django logs + lock state)")
    print("9. Switch active host (production vs latest)")
    print("10. Issue HTTPS certificate (manual DNS-01)")
    print("11. Reset HTTPS configuration")
    print("12. Update /etc/hosts for testing")
    print("13. Restore local database from S3 backup")
    print("14. Lock security group to SSH + HTTPS only")
    print("15. Issue HTTPS certificate (HTTP-01, auto-renew)")
    print("16. HTTPS renew dry-run (certbot renew --dry-run)")
    print("17. Run SSM diagnostics (disk/memory/services)")
    print("18. Enable AWS management (auto-create profile + install SSM/CloudWatch)")
    print("19. Reboot EC2 instance")
    print("20. Exit")
    print()

def main():
    print_header()
    
    deployer = DockerDeployment()
    
    while True:
        print_menu(deployer.active_host, deployer.active_host_label)
        choice = input("Enter choice [0-20]: ").strip()
        
        if choice == '0':
            deployer.capture_provisioning_config()
        elif choice == '1':
            print("\n" + "=" * 50)
            print("SERVER PROVISIONING (NEW INSTANCE)")
            print("=" * 50)
            print("This will:")
            print("  • Create a brand-new EC2 instance with configured sizes")
            print("  • Install Docker, docker compose, nginx, and prepare the /app/data volume")
            print("  • Configure nginx to reverse proxy to the Django container (code deploy happens separately)")
            print("  • Leave the Elastic IP unassigned so you can deploy + test via the temporary IP")
            
            if input("\nProceed? (y/n): ").lower() == 'y':
                deployer.provision_server()
        elif choice == '2':
            print("\n" + "=" * 50)
            print("ASSOCIATE ELASTIC IP")
            print("=" * 50)
            print("This will move the configured Elastic IP to a chosen instance.")
            deployer.associate_elastic_ip()
        elif choice == '3':
            print("\n" + "=" * 50)
            print("CODE DEPLOYMENT")
            print("=" * 50)
            print("This will:")
            print("  • Upload your current local code to the selected server")
            print("  • Rebuild Docker containers")
            print("  • Restart services")
            
            if input("\nProceed? (y/n): ").lower() == 'y':
                deployer.deploy_code()
        elif choice == '4':
            print("\n" + "=" * 50)
            print("DATABASE DEPLOYMENT")
            print("=" * 50)
            deployer.deploy_database()
        elif choice == '5':
            print("\n" + "=" * 50)
            print("FULL DEPLOYMENT")
            print("=" * 50)
            print("This will:")
            print("  • Deploy code (upload local code + rebuild)")
            print("  • Deploy database (upload + reindex)")
            
            if input("\nProceed? (y/n): ").lower() == 'y':
                deployer.deploy_full()
        elif choice == '6':
            deployer.reindex_search()
        elif choice == '7':
            print("\n" + "=" * 50)
            print("FREE DISK CLEANUP (DANGEROUS)")
            print("=" * 50)
            print("This will:\n  • Stop Docker containers\n  • DELETE the remote SQLite database file\n  • Remove the MeiliSearch data volume\n  • Prune Docker builder cache and unused images\n  • Vacuum system logs")
            if input("\nProceed? (y/n): ").lower() == 'y':
                deployer.free_disk_on_server()
        elif choice == '8':
            deployer.show_status()
        elif choice == '9':
            deployer.choose_active_host()
        elif choice == '10':
            deployer.issue_https_certificate()
        elif choice == '11':
            deployer.reset_https_configuration()
        elif choice == '12':
            deployer.update_hosts_file()
        elif choice == '13':
            deployer.restore_local_db_from_s3()
        elif choice == '14':
            try:
                host = deployer.prompt_host_for_operation('lock security group (HTTPS only)')
            except RuntimeError:
                continue
            prompt = (
                "\nThis will REMOVE all existing inbound rules except 22/443 "
                f"on host {host}. Proceed? (y/n): "
            )
            if input(prompt).lower() == 'y':
                deployer.lock_security_group_https_only(host)
        elif choice == '15':
            deployer.issue_https_certificate_http01()
        elif choice == '16':
            deployer.renew_https_certificate_dry_run()
        elif choice == '17':
            try:
                host = deployer.prompt_host_for_operation('SSM diagnostics')
            except RuntimeError:
                continue
            deployer.run_ssm_diagnostics(host)
        elif choice == '18':
            try:
                host = deployer.prompt_host_for_operation('enable AWS management')
            except RuntimeError:
                continue
            deployer.enable_aws_management(host)
        elif choice == '19':
            try:
                host = deployer.prompt_host_for_operation('reboot EC2 instance')
            except RuntimeError:
                continue
            if input(f"\nRequest AWS reboot for the instance behind {host}? (y/n): ").lower() == 'y':
                deployer.reboot_instance(host)
        elif choice == '20':
            print("\n👋 Goodbye!")
            break
        
        else:
            print("❌ Invalid choice. Please try again.")

if __name__ == "__main__":
    main()
