# push_config_job.py

import os
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from nautobot.apps.jobs import Job, ObjectVar, register_jobs
from nautobot.dcim.models import Device, Interface


class PushConfigToDevice(Job):
    """
    Step 3: Push config for a single device/interface using the Junos Jinja template.

    - Expects 'device' from Nautobot (ObjectVar).
    - Optionally receives 'interface' and 'vlan' from the pipeline job.
    - Renders config only for the given interface.

    Netmiko is imported lazily inside run(), so that the job module can be loaded
    even if netmiko is not installed yet.
    """

    class Meta:
        name = "Push config to device (POC)"
        description = "Render and push Junos 'set' config for a single switch interface."
        commit_default = False  # set to True later when you are confident

    device = ObjectVar(
        model=Device,
        required=True,
        description="Device to push config to.",
    )

    REPO_ENV_VAR = "POC_NETOPS_REPO"
    DEFAULT_REPO_PATH = "/opt/nautobot/git/poc_netops"
    TEMPLATE_REL_PATH = "templates/juniper_junos.j2"

    def run(self, device, interface=None, vlan=None, **kwargs):
        # Lazy imports for Netmiko – avoids import errors at job load time.
        try:
            from netmiko import ConnectHandler
            from netmiko.exceptions import NetmikoTimeoutError, NetmikoAuthenticationException
        except ModuleNotFoundError:
            self.logger.error(
                "[PushConfigToDevice] netmiko is not installed in the Nautobot environment, "
                "cannot push configuration."
            )
            return
        except Exception as e:
            self.logger.error(
                f"[PushConfigToDevice] Error importing netmiko: {e}"
            )
            return

        # Log context
        self.logger.info(
            f"[PushConfigToDevice] Starting push job for device {device} (pk={device.pk})."
        )
        self.logger.info(
            f"[PushConfigToDevice] Context received: interface={interface}, "
            f"vlan={getattr(vlan, 'id', vlan)}"
        )

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

        # Determine repo / template path
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

        # Device connection details
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
        username = os.environ.get("NETMIKO_USERNAME")
        password = os.environ.get("NETMIKO_PASSWORD")

        if not username or not password:
            self.logger.error(
                "[PushConfigToDevice] NETMIKO_USERNAME or NETMIKO_PASSWORD not set in environment, "
                "cannot push configuration."
            )
            return

        # Render config for exactly this interface
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

        config_lines = [
            f"delete interfaces {interface.name} unit 0 family ethernet-switching vlan"
        ]
        for line in rendered.splitlines():
            line = line.strip()
            if not line:
                continue
            config_lines.append(line)

        if not config_lines:
            self.logger.warning(
                f"[PushConfigToDevice] No config lines generated for {interface}, "
                f"nothing to push."
            )
            return

        self.logger.info(
            f"[PushConfigToDevice] Generated config lines for {device.name} / {interface.name}: "
            f"{config_lines}"
        )

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
                output = conn.send_config_set(config_lines, exit_config_mode=False)
                self.logger.info(
                    f"[PushConfigToDevice] Config push output for {device.name}:\n{output}"
                )

                # Commit for Junos
                try:
                    commit_out = conn.commit(and_quit=True)
                    self.logger.info(
                        f"[PushConfigToDevice] Commit output for {device.name}:\n{commit_out}"
                    )
                except AttributeError:
                    self.logger.warning(
                        f"[PushConfigToDevice] Connection for {device.name} has no commit(), "
                        f"trying to exit config mode only."
                    )
                    conn.exit_config_mode()

        except NetmikoTimeoutError as e:
            self.logger.error(
                f"[PushConfigToDevice] Timeout connecting to {device.name} ({host}): {e}"
            )
        except NetmikoAuthenticationException as e:
            self.logger.error(
                f"[PushConfigToDevice] Authentication failed for {device.name} ({host}): {e}"
            )
        except Exception as e:
            self.logger.error(
                f"[PushConfigToDevice] Error while pushing config to {device.name} ({host}): {e}"
            )

        self.logger.info(
            f"[PushConfigToDevice] Finished push for {device.name} / {interface.name}."
        )


register_jobs(PushConfigToDevice)
