# backup_config_job.py
#
# This job backs up the current running configuration from a network device.
# 
# What it does:
# 1. Connects to a Juniper device via SSH (using Netmiko)
# 2. Runs "show configuration | display set" to get the config in set format
# 3. Saves the output to a file in the Git repo (backups/<device_name>.set)
# 4. Commits the backup file to Git so we have version history
#
# Why we need this:
# Before making any changes, we want a snapshot of the current config.
# If something goes wrong, we can compare or rollback using these backups.

import os
import subprocess
from pathlib import Path

from nautobot.apps.jobs import Job, ObjectVar, register_jobs
from nautobot.dcim.models import Device

from nautobot.extras.choices import SecretsGroupAccessTypeChoices, SecretsGroupSecretTypeChoices
from nautobot.extras.secrets.exceptions import SecretError

# Groups all related jobs together in the Nautobot UI
name = "00_Vlan-Change-Jobs"


class BackupDeviceConfig(Job):
    """
    Step 1 of the pipeline: Backup current device configuration.
    
    This job connects to a Juniper device and retrieves its running configuration
    in "set" format (display set), then saves it to the Git repository for version control.
    """

    class Meta:
        # Job name as it appears in Nautobot
        name = "01_Backup device config (POC)"
        
        # Help text for users
        description = "Backup Junos configuration (display set format) to the Git repository."
        
        # We're only writing files and committing to git, not changing Nautobot database
        commit_default = False

    # Define the input parameter - which device to backup
    device = ObjectVar(
        model=Device,
        required=True,
        description="Device to backup configuration from.",
    )

    # Configuration constants
    # Where to find the Git repository on the filesystem
    REPO_ENV_VAR = "POC_NETOPS_REPO"  # Environment variable name
    DEFAULT_REPO_PATH = "/opt/nautobot/git/poc_netops"  # Fallback path if env var not set
    BACKUP_DIR_NAME = "backups"  # Subdirectory inside repo for backups

    def run(self, device, interface=None, vlan=None, **kwargs):
        """
        Main execution method for the backup job.
        
        Args:
            device: The Device object to backup (required)
            interface: Not used in backup, but passed through from pipeline
            vlan: Not used in backup, but passed through from pipeline
            **kwargs: Additional arguments (ignored)
        """
        
        self.logger.info(
            f"[BackupDeviceConfig] Starting configuration backup for device {device.name} "
            f"(database ID: {device.pk})."
        )

        # Import Netmiko lazily (only when we actually need it)
        # This prevents import errors if netmiko isn't installed, and keeps startup faster
        try:
            from netmiko import ConnectHandler
        except ModuleNotFoundError:
            self.logger.error(
                "[BackupDeviceConfig] The 'netmiko' library is not installed in the "
                "Nautobot environment. Cannot backup device configuration. "
                "Please install netmiko: pip install netmiko"
            )
            return
        except Exception as e:
            self.logger.error(
                f"[BackupDeviceConfig] Unexpected error while importing netmiko: {e}"
            )
            return

        # Determine where our Git repository is located
        # First check environment variable, then fall back to default
        repo_root = os.environ.get(self.REPO_ENV_VAR, self.DEFAULT_REPO_PATH)
        repo_root = Path(repo_root)  # Convert to Path object for easier manipulation
        self.logger.info(f"[BackupDeviceConfig] Using Git repository at: {repo_root}")

        # Validate that the repo actually exists
        if not repo_root.exists():
            self.logger.error(
                f"[BackupDeviceConfig] Git repository path {repo_root} does not exist. "
                f"Please create it or set the {self.REPO_ENV_VAR} environment variable correctly."
            )
            return

        # Check device platform to ensure it's a Juniper device
        # This PoC only supports Juniper JunOS devices
        platform = getattr(device, "platform", None)
        driver = getattr(platform, "network_driver", None)

        if driver != "juniper_junos":
            # Not a Juniper device, skip backup for this PoC
            self.logger.info(
                f"[BackupDeviceConfig] Device {device.name} has network driver '{driver}', "
                f"not 'juniper_junos'. Skipping backup (this PoC only supports Juniper devices)."
            )
            return

        # Get the device's primary IP address
        # We need this to know where to connect via SSH
        primary_ip = getattr(device, "primary_ip4", None)
        
        if primary_ip is None:
            # No IP address configured, can't connect
            self.logger.warning(
                f"[BackupDeviceConfig] Device {device.name} has no primary IPv4 address configured. "
                f"Cannot establish SSH connection. Please assign a primary IP in Nautobot."
            )
            return

        # Extract just the IP address (without the subnet mask)
        host = str(primary_ip.address.ip)

        # --- Credential retrieval ---
        # We need username and password to connect to the device
        # We'll try multiple sources in order of preference:
        # 1. Device's assigned Secrets Group (most secure, recommended)
        # 2. Environment variables (fallback for testing/development)
        
        username = None
        password = None

        # Try to get credentials from the device's Secrets Group
        # This is the recommended way in production - credentials stored securely in Nautobot
        secrets_group = getattr(device, "secrets_group", None)
        
        if secrets_group:
            try:
                # Retrieve username from the secrets group
                username = secrets_group.get_secret_value(
                    secret_type=SecretsGroupSecretTypeChoices.TYPE_USERNAME,
                    access_type=SecretsGroupAccessTypeChoices.TYPE_GENERIC,
                    obj=device,
                )
                # Retrieve password from the secrets group
                password = secrets_group.get_secret_value(
                    secret_type=SecretsGroupSecretTypeChoices.TYPE_PASSWORD,
                    access_type=SecretsGroupAccessTypeChoices.TYPE_GENERIC,
                    obj=device,
                )
                self.logger.info(
                    f"[BackupDeviceConfig] Successfully retrieved credentials from "
                    f"SecretsGroup '{secrets_group.name}' for device {device.name}."
                )
            except SecretError as e:
                # Secrets Group exists but we couldn't get the credentials
                self.logger.error(
                    f"[BackupDeviceConfig] Failed to retrieve credentials from SecretsGroup "
                    f"'{secrets_group.name}' for device {device.name}: {e}"
                )

        # Fallback to environment variables if Secrets Group didn't work
        # This is useful for development/testing but not recommended for production
        if not username or not password:
            env_user = os.environ.get("NETMIKO_USERNAME")
            env_pass = os.environ.get("NETMIKO_PASSWORD")
            
            if env_user and env_pass:
                username = env_user
                password = env_pass
                self.logger.info(
                    "[BackupDeviceConfig] Using fallback credentials from environment variables "
                    "(NETMIKO_USERNAME and NETMIKO_PASSWORD)."
                )

        # Final check - do we have credentials from anywhere?
        if not username or not password:
            self.logger.error(
                "[BackupDeviceConfig] No credentials found. Tried: "
                "1) Device's SecretsGroup, 2) Environment variables (NETMIKO_USERNAME/PASSWORD). "
                "Cannot backup configuration without credentials."
            )
            return

        # Build the connection parameters for Netmiko
        device_params = {
            "device_type": driver,  # juniper_junos
            "host": host,  # Device IP address
            "username": username,
            "password": password,
            "timeout": 30,  # Connection timeout in seconds
            "banner_timeout": 15,  # Time to wait for login banner
        }

        # --- Connect to device and get configuration ---
        self.logger.info(
            f"[BackupDeviceConfig] Connecting to device {device.name} at {host} via SSH "
            f"to retrieve current configuration..."
        )
        
        try:
            # Use context manager to ensure connection is properly closed
            with ConnectHandler(**device_params) as conn:
                # Run the backup command
                # "show configuration | display set" gives us the config in set format
                # This format is easier to diff and track in version control
                cmd = "show configuration | display set"
                self.logger.info(
                    f"[BackupDeviceConfig] Running command on device: '{cmd}'"
                )
                output = conn.send_command(cmd)
                
        except Exception as e:
            # Connection or command execution failed
            self.logger.error(
                f"[BackupDeviceConfig] Failed to retrieve configuration from device "
                f"{device.name} ({host}). Error: {e}"
            )
            return

        # Validate that we actually got some configuration data
        # Empty or very short output usually means something went wrong
        if not output or len(output) < 50:
            self.logger.warning(
                f"[BackupDeviceConfig] Retrieved configuration seems suspiciously short "
                f"({len(output)} characters). This might indicate a connection problem or "
                f"that the device returned an error."
            )
            # Continue anyway - maybe it's a very minimal config

        # --- Save configuration to file ---
        # Create the backups directory if it doesn't exist
        backup_dir = repo_root / self.BACKUP_DIR_NAME
        backup_dir.mkdir(parents=True, exist_ok=True)

        # Create filename based on device name
        # Format: <device_name>.set
        backup_file = backup_dir / f"{device.name}.set"
        
        try:
            # Write the configuration to the file
            # We add a newline at the end for consistent formatting
            backup_file.write_text(output + "\n", encoding="utf-8")
            self.logger.info(
                f"[BackupDeviceConfig] Successfully wrote backup configuration to {backup_file} "
                f"({len(output)} characters)."
            )
        except Exception as e:
            self.logger.error(
                f"[BackupDeviceConfig] Failed to write backup file {backup_file}: {e}"
            )
            return

        # --- Commit to Git ---
        # Check if this directory is actually a Git repository
        git_dir = repo_root / ".git"
        if not git_dir.exists():
            self.logger.warning(
                f"[BackupDeviceConfig] Directory {repo_root} is not a Git repository "
                f"(no .git directory found). Skipping git commit. "
                f"Initialize git: cd {repo_root} && git init"
            )
            return

        # Get the relative path for git commands
        # Git works better with relative paths from the repo root
        rel_backup_path = backup_file.relative_to(repo_root)

        try:
            # Stage the backup file for commit
            self.logger.info(
                f"[BackupDeviceConfig] Running 'git add {rel_backup_path}' to stage backup file."
            )
            add_proc = subprocess.run(
                ["git", "-C", str(repo_root), "add", str(rel_backup_path)],
                capture_output=True,  # Capture stdout and stderr
                text=True,  # Return strings instead of bytes
                check=False,  # Don't raise exception on non-zero exit
            )
            # Log the result of git add
            self.logger.info(
                f"[BackupDeviceConfig] git add completed with exit code {add_proc.returncode}. "
                f"Output: '{add_proc.stdout.strip()}' | Errors: '{add_proc.stderr.strip()}'"
            )

            # Commit the staged changes
            # We create a descriptive commit message that includes the device name
            commit_msg = f"Backup config for device {device.name}"
            self.logger.info(
                f"[BackupDeviceConfig] Running 'git commit -m \"{commit_msg}\"' to commit backup."
            )
            commit_proc = subprocess.run(
                ["git", "-C", str(repo_root), "commit", "--allow-empty", "-m", commit_msg],
                capture_output=True,
                text=True,
                check=False,
            )
            # Log the result of git commit
            self.logger.info(
                f"[BackupDeviceConfig] git commit completed with exit code {commit_proc.returncode}. "
                f"Output: '{commit_proc.stdout.strip()}' | Errors: '{commit_proc.stderr.strip()}'"
            )
            
            # Note: We use --allow-empty so we get a commit even if the config didn't change
            # This gives us an audit trail showing when backups were taken, even if nothing changed
            
        except Exception as e:
            self.logger.error(
                f"[BackupDeviceConfig] Error during git operations in {repo_root}: {e}"
            )

        self.logger.info(
            f"[BackupDeviceConfig] Backup process completed successfully for device {device.name}."
        )


# Register this job so Nautobot can discover and run it
register_jobs(BackupDeviceConfig)
