# push_config_job.py

import os
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from nautobot.apps.jobs import Job, ObjectVar, register_jobs
from nautobot.dcim.models import Device, Interface

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
        commit_default = False  # set True später wenn du willst

    device = ObjectVar(
        model=Device,
        required=True,
        description="Device to push config to.",
    )

    REPO_ENV_VAR = "POC_NETOPS_REPO"
    DEFAULT_REPO_PATH = "/opt/nautobot/git/poc_netops"
    TEMPLATE_REL_PATH = "templates/juniper_junos.j2"

    def run(self, device, interface=None, vlan=None, **kwargs):
        # DEBUG marker, damit du siehst, dass diese Version wirklich läuft
        self.logger.info("[PushConfigToDevice] DEBUG: entering NEW run() implementation")

        # Lazy import Netmiko so the module can load even if netmiko is missing.
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

        # Basic checks
        if interface is None:
            self.logger.warning(
                "[PushConfigToDevice] No interface passed, nothing to push. "
                "This job is meant to be called from the pipeline with an interface."
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

        # VLAN: prefer the one passed in, fall back to interface.untagged_vlan
        if vlan is None:
            vlan = getattr(interface, "untagged_vlan", None)

        if vlan is None:
            self.logger.info(
                f"[PushConfigToDevice] No VLAN found for interface {interface}, "
                f"nothing to push."
            )
            return

        # Repo / template path
        repo_root = os.environ.get(self.REPO_ENV_VAR, self.DEFAULT_REPO_PATH)
        repo_root = Path(repo_root)
        self.logger.info(f"[PushConfigToDevice] Using repo root: {repo_root}")

        if not repo_root.exists():
            self.logger.error(
                f"[PushConfigToDevice] Repo root {repo_root} does not exist. "
                f"Check {self.REPO_ENV_VAR} or DEFAULT_REPO_PATH."
            )
            return

        template_path = repo_root / self.TEMPLATE_REL_PATH
        if not template_path.exists():
            self.logger.error(
                f"[PushConfigToDevice] Template file {template_path} does not exist."
            )
            return

        # Render template only for this one interface
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

        # Collect "set ..." lines only (no delete)
        config_lines = []
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

        # Hier explizit loggen, welche Commands wir senden
        self.logger.info(
            f"[PushConfigToDevice] About to send the following commands to "
            f"{device.name} / {interface.name}: {config_lines}"
        )

        # Device connection details (für POC via env vars)
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

        # Prefer platform napalm_* (usually backed by Secrets), fallback to env vars
        platform = getattr(device, "platform", None)
        napalm_username = getattr(platform, "napalm_username", None) if platform else None
        napalm_password = getattr(platform, "napalm_password", None) if platform else None

        username = napalm_username or os.environ.get("NETMIKO_USERNAME")
        password = napalm_password or os.environ.get("NETMIKO_PASSWORD")

        self.logger.info(
            f"[PushConfigToDevice] Using credential source: "
            f"{'platform.napalm_*' if napalm_username and napalm_password else 'ENV NETMIKO_*'}"
        )

        if not username or not password:
            self.logger.error(
                "[PushConfigToDevice] No credentials found on platform.napalm_* "
                "and NETMIKO_* env vars are also not set. Cannot push configuration."
            )
            return


        device_params = {
            "device_type": driver,  # "juniper_junos"
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
                # Log nochmal kurz vor dem Senden
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
