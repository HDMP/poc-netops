# backup_config_job.py

import os
import subprocess
from pathlib import Path

from nautobot.apps.jobs import Job, ObjectVar, register_jobs
from nautobot.dcim.models import Device

from nautobot.extras.choices import SecretsGroupAccessTypeChoices, SecretsGroupSecretTypeChoices
from nautobot.extras.secrets.exceptions import SecretError

name = "00_Vlan-Change-Jobs"


class BackupDeviceConfig(Job):
    """
    Step 1: Backup job – holt 'show configuration | display set' und schreibt es ins Git-Repo.
    """

    class Meta:
        name = "01_Backup device config (POC)"
        description = "Backup der Junos Config (display set) in das Git-Repo."
        commit_default = False  # nur Files/Git, keine DB-Changes

    device = ObjectVar(
        model=Device,
        required=True,
        description="Device to backup.",
    )

    REPO_ENV_VAR = "POC_NETOPS_REPO"
    DEFAULT_REPO_PATH = "/opt/nautobot/git/poc_netops"
    BACKUP_DIR_NAME = "backups"

    def run(self, device, interface=None, vlan=None, **kwargs):
        self.logger.info(
            f"[BackupDeviceConfig] Starting backup for device {device} (pk={device.pk})."
        )

        # --- Netmiko lazy import ---
        try:
            from netmiko import ConnectHandler
        except ModuleNotFoundError:
            self.logger.error(
                "[BackupDeviceConfig] netmiko is not installed in the Nautobot environment, "
                "cannot backup configuration."
            )
            return
        except Exception as e:
            self.logger.error(
                f"[BackupDeviceConfig] Error importing netmiko: {e}"
            )
            return

        # Repo root
        repo_root = os.environ.get(self.REPO_ENV_VAR, self.DEFAULT_REPO_PATH)
        repo_root = Path(repo_root)
        self.logger.info(f"[BackupDeviceConfig] Using repo root: {repo_root}")

        if not repo_root.exists():
            self.logger.error(
                f"[BackupDeviceConfig] Repo root {repo_root} does not exist."
            )
            return

        # Device / Platform / IP
        platform = getattr(device, "platform", None)
        driver = getattr(platform, "network_driver", None)

        if driver != "juniper_junos":
            self.logger.info(
                f"[BackupDeviceConfig] Device {device} has platform driver '{driver}', "
                f"not 'juniper_junos' – skipping backup for this PoC."
            )
            return

        primary_ip = getattr(device, "primary_ip4", None)
        if primary_ip is None:
            self.logger.warning(
                f"[BackupDeviceConfig] Device {device} has no primary IPv4 address, "
                f"cannot connect for backup."
            )
            return

        host = str(primary_ip.address.ip)

        # --- Credentials via device.secrets_group (wie im Push-Job) ---
        username = None
        password = None

        secrets_group = getattr(device, "secrets_group", None)
        if secrets_group:
            try:
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
                    f"[BackupDeviceConfig] Retrieved username/password from SecretsGroup "
                    f"'{secrets_group}' for device {device}."
                )
            except SecretError as e:
                self.logger.error(
                    f"[BackupDeviceConfig] Error retrieving credentials from SecretsGroup "
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
                    "[BackupDeviceConfig] Using fallback credentials from NETMIKO_* env vars."
                )

        if not username or not password:
            self.logger.error(
                "[BackupDeviceConfig] No credentials found in device.secrets_group "
                "and no NETMIKO_* env vars set. Cannot backup configuration."
            )
            return

        device_params = {
            "device_type": driver,
            "host": host,
            "username": username,
            "password": password,
        }

        # --- Config holen: show configuration | display set ---
        self.logger.info(
            f"[BackupDeviceConfig] Connecting to device {device.name} ({host}) for backup."
        )
        try:
            with ConnectHandler(**device_params) as conn:
                cmd = "show configuration | display set"
                self.logger.info(
                    f"[BackupDeviceConfig] Running backup command: '{cmd}'"
                )
                output = conn.send_command(cmd)
        except Exception as e:
            self.logger.error(
                f"[BackupDeviceConfig] Error while fetching config from {device.name} ({host}): {e}"
            )
            return

        # --- Datei schreiben ---
        backup_dir = repo_root / self.BACKUP_DIR_NAME
        backup_dir.mkdir(parents=True, exist_ok=True)

        backup_file = backup_dir / f"{device.name}.set"
        try:
            backup_file.write_text(output + "\n", encoding="utf-8")
            self.logger.info(
                f"[BackupDeviceConfig] Wrote backup config to {backup_file}."
            )
        except Exception as e:
            self.logger.error(
                f"[BackupDeviceConfig] Failed to write backup file {backup_file}: {e}"
            )
            return

        # --- git add/commit (optional, aber nice) ---
        git_dir = repo_root / ".git"
        if git_dir.exists():
            rel_backup_path = backup_file.relative_to(repo_root)
            try:
                self.logger.info(
                    f"[BackupDeviceConfig] Running 'git add {rel_backup_path}'."
                )
                add_proc = subprocess.run(
                    ["git", "-C", str(repo_root), "add", str(rel_backup_path)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.logger.info(
                    f"[BackupDeviceConfig] git add rc={add_proc.returncode}, "
                    f"stdout='{add_proc.stdout.strip()}', stderr='{add_proc.stderr.strip()}'"
                )

                commit_msg = f"Backup config for device {device.name}"
                self.logger.info(
                    f"[BackupDeviceConfig] Running 'git commit -m \"{commit_msg}\"'."
                )
                commit_proc = subprocess.run(
                    ["git", "-C", str(repo_root), "commit", "-m", commit_msg],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.logger.info(
                    f"[BackupDeviceConfig] git commit rc={commit_proc.returncode}, "
                    f"stdout='{commit_proc.stdout.strip()}', stderr='{commit_proc.stderr.strip()}'"
                )
            except Exception as e:
                self.logger.error(
                    f"[BackupDeviceConfig] Error during git add/commit in {repo_root}: {e}"
                )

        self.logger.info(
            f"[BackupDeviceConfig] Finished backup for device {device.name}."
        )


register_jobs(BackupDeviceConfig)
