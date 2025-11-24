# push_config_job.py

import os
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from nautobot.apps.jobs import Job, ObjectVar, register_jobs
from nautobot.dcim.models import Device, Interface

from nautobot.extras.choices import SecretsGroupAccessTypeChoices, SecretsGroupSecretTypeChoices
from nautobot.extras.secrets.exceptions import SecretError

name = "00_Vlan-Change-Jobs"


class PushConfigToDevice(Job):
    """
    Step 3: Very simple push job.

    - Takes a device (ObjectVar).
    - Expects 'interface' and 'vlan' via kwargs (from the pipeline job).
    - Renders Junos 'set' commands for exactly this interface using the Jinja template.
    - Sends only these 'set' commands via Netmiko (no deletes, no extra logic).
    """

    class Meta:
        name = "03_Push config to device (POC)"
        description = "Render and push simple Junos 'set' commands for a single switch interface."
        commit_default = False

    device = ObjectVar(
        model=Device,
        required=True,
        description="Device to push config to.",
    )

    REPO_ENV_VAR = "POC_NETOPS_REPO"
    DEFAULT_REPO_PATH = "/opt/nautobot/git/poc_netops"
    TEMPLATE_REL_PATH = "templates/juniper_junos.j2"

    def run(self, device, interface=None, vlan=None, **kwargs):
        self.logger.info("[PushConfigToDevice] DEBUG: entering NEW run() implementation")

        # Lazy import Netmiko
        try:
            from netmiko import ConnectHandler
        except ModuleNotFoundError:
            self.logger.error(
                "[PushConfigToDevice] netmiko is not installed in the Nautobot environment, "
                "cannot push configuration."
            )
            return
        except Exception as e:
            self.logger.error(
                f"[PushConfigToDevice] Error importing netmiko (NEW): {e}"
            )
            return

        self.logger.info(
            f"[PushConfigToDevice] Starting push job for device {device} (pk={device.pk})."
        )
        self.logger.info(
            f"[PushConfigToDevice] Context received: interface={interface}, "
            f"vlan={getattr(vlan, 'id', vlan)}"
        )

        if interface is None:
            self.logger.warning(
                "[PushConfigToDevice] No interface passed, nothing to push."
            )
            return

        if not isinstance(interface, Interface):
            self.logger.error(
                f"[PushConfigToDevice] 'interface' is not an Interface instance "
                f"(got {type(interface)}), aborting."
            )
            return

        if interface.device != device:
            self.logger.error(
                f"[PushConfigToDevice] Interface {interface} does not belong to device {device}, "
                f"aborting."
            )
            return

        if vlan is None:
            vlan = getattr(interface, "untagged_vlan", None)

        if vlan is None:
            self.logger.info(
                f"[PushConfigToDevice] No VLAN found for interface {interface}, "
                f"nothing to push."
            )
            return

        # Template / repo
        repo_root = os.environ.get(self.REPO_ENV_VAR, self.DEFAULT_REPO_PATH)
        repo_root = Path(repo_root)
        self.logger.info(f"[PushConfigToDevice] Using repo root: {repo_root}")

        template_path = repo_root / self.TEMPLATE_REL_PATH
        if not template_path.exists():
            self.logger.error(
                f"[PushConfigToDevice] Template file {template_path} does not exist."
            )
            return

        try:
            env = Environment(
                loader=FileSystemLoader(str(template_path.parent)),
                autoescape=False,
            )
            template = env.get_template(template_path.name)
            rendered = template.render(interfaces=[interface])
        except Exception as e:
            self.logger.error(
                f"[PushConfigToDevice] Error while rendering template {template_path}: {e}"
            )
            return

        # Config Lines
        config_lines = [
            f"delete interfaces {interface.name} unit 0 family ethernet-switching vlan members"
        ]

        for line in rendered.splitlines():
            line = line.strip()
            if not line:
                continue
            config_lines.append(line)
            
        if not config_lines:
            self.logger.warning(
                f"[PushConfigToDevice] No 'set' lines generated for {interface}, "
                f"nothing to push."
            )
            return

        self.logger.info(
            f"[PushConfigToDevice] About to send the following commands to "
            f"{device.name} / {interface.name}: {config_lines}"
        )

        # Device / Platform / IP
        platform = getattr(device, "platform", None)
        driver = getattr(platform, "network_driver", None)

        if driver != "juniper_junos":
            self.logger.info(
                f"[PushConfigToDevice] Device {device} has platform driver '{driver}', "
                f"not 'juniper_junos' – skipping push for this PoC."
            )
            return

        primary_ip = getattr(device, "primary_ip4", None)
        if primary_ip is None:
            self.logger.warning(
                f"[PushConfigToDevice] Device {device} has no primary IPv4 address, "
                f"cannot connect for config push."
            )
            return

        host = str(primary_ip.address.ip)

            # --- Credentials via device.secrets_group (zugewiesene Secrets Group) ---
        username = None
        password = None

        # device.secrets_group ist ein RelatedManager, wir nehmen einfach die erste Gruppe
        secrets_group = getattr(device, "secrets_group", None)

        if secrets_group:
            try:
                # access_type lassen wir weg, wir nehmen einfach die USERNAME/PASSWORD Secrets
                username = secrets_group.get_secret_value(
                    secret_type=SecretsGroupSecretTypeChoices.TYPE_USERNAME,
                    access_type=SecretsGroupAccessTypeChoices.TYPE_GENERIC,
                    obj=device,
                )
                password = secrets_group.get_secret_value(
                    secret_type=SecretsGroupSecretTypeChoices.TYPE_PASSWORD,
                    access_type=SecretsGroupAccessTypeChoices.TYPE_GENERIC,
                    obj=device,
                )
                self.logger.info(
                    f"[PushConfigToDevice] Retrieved username/password from SecretsGroup "
                    f"'{secrets_group}' for device {device}."
                )
            except SecretError as e:
                self.logger.error(
                    f"[PushConfigToDevice] Error retrieving credentials from SecretsGroup "
                    f"for device {device}: {e}"
                )

        # Fallback: ENV
        if not username or not password:
            env_user = os.environ.get("NETMIKO_USERNAME")
            env_pass = os.environ.get("NETMIKO_PASSWORD")
            if env_user and env_pass:
                username = env_user
                password = env_pass
                self.logger.info(
                    "[PushConfigToDevice] Using fallback credentials from NETMIKO_* env vars."
                )

        if not username or not password:
            self.logger.error(
                "[PushConfigToDevice] No credentials found in device.secrets_group "
                "and no NETMIKO_* env vars set. Cannot push configuration."
            )
            return

        device_params = {
            "device_type": driver,
            "host": host,
            "username": username,
            "password": password,
        }

        self.logger.info(
            f"[PushConfigToDevice] Connecting to device {device.name} ({host}) "
            f"to push config for interface {interface.name}."
        )

        try:
            with ConnectHandler(**device_params) as conn:
                self.logger.info(
                    f"[PushConfigToDevice] Sending config_set with lines: {config_lines}"
                )
                output = conn.send_config_set(config_lines)
                self.logger.info(
                    f"[PushConfigToDevice] Config push output for {device.name}:\n{output}"
                )
        except Exception as e:
            self.logger.error(
                f"[PushConfigToDevice] Error while pushing config to {device.name} ({host}): {e}"
            )
            return

        self.logger.info(
            f"[PushConfigToDevice] Finished push for {device.name} / {interface.name}."
        )


register_jobs(PushConfigToDevice)
