# push_config_job.py
#
# This job pushes configuration changes to a network device.
#
# What it does:
# 1. Takes a device and specific interface from the pipeline
# 2. Renders Jinja2 template with JUST that interface to get the config commands
# 3. Connects to the device via SSH (using Netmiko)
# 4. First deletes any existing VLAN configuration on the interface
# 5. Then pushes the new "set" commands from the template
#
# Why we need this:
# After updating Nautobot (source of truth) and building the intended config,
# we need to actually apply those changes to the physical/virtual device.
# This job makes that happen by sending the config commands via SSH.

import os
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from nautobot.apps.jobs import Job, ObjectVar, register_jobs
from nautobot.dcim.models import Device, Interface

from nautobot.extras.choices import SecretsGroupAccessTypeChoices, SecretsGroupSecretTypeChoices
from nautobot.extras.secrets.exceptions import SecretError

# Groups all related jobs together in the Nautobot UI
name = "00_Vlan-Change-Jobs"


class PushConfigToDevice(Job):
    """
    Step 3 of the pipeline: Push configuration to the physical device.
    
    This job connects to a network device via SSH and sends Junos "set" commands
    to configure a specific interface with the VLAN from Nautobot.
    """

    class Meta:
        # Job name as shown in Nautobot
        name = "03_Push config to device (POC)"
        
        # Help text for users
        description = "Render and push Junos 'set' commands for a single interface to the device."
        
        # We're not changing Nautobot database, only device config
        commit_default = False

    # Define input parameter - which device to push config to
    device = ObjectVar(
        model=Device,
        required=True,
        description="Device to push configuration to.",
    )

    # Configuration constants
    REPO_ENV_VAR = "POC_NETOPS_REPO"  # Where to find the Git repo
    DEFAULT_REPO_PATH = "/opt/nautobot/git/poc_netops"
    TEMPLATE_REL_PATH = "templates/juniper_junos.j2"  # Jinja template for generating config

    def run(self, device, interface=None, vlan=None, **kwargs):
        """
        Main execution method for pushing config to device.
        
        Args:
            device: The Device object to push config to (required)
            interface: The specific Interface to configure (passed from pipeline)
            vlan: The VLAN to configure (optional, we'll get it from interface if not provided)
            **kwargs: Additional arguments (ignored)
        """
        
        self.logger.info(
            "[PushConfigToDevice] Starting config push process for device "
            f"{device.name} (database ID: {device.pk})."
        )

        # Import Netmiko lazily (only when we need it)
        # This prevents import errors if netmiko isn't installed
        try:
            from netmiko import ConnectHandler
        except ModuleNotFoundError:
            self.logger.error(
                "[PushConfigToDevice] The 'netmiko' library is not installed. "
                "Cannot push configuration to device. Please install it: pip install netmiko"
            )
            return
        except Exception as e:
            self.logger.error(
                f"[PushConfigToDevice] Unexpected error importing netmiko: {e}"
            )
            return

        self.logger.info(
            f"[PushConfigToDevice] Pipeline context: interface={interface}, "
            f"vlan={getattr(vlan, 'id', vlan) if vlan else 'None'}"
        )

        # --- Validation: Make sure we have an interface to configure ---
        if interface is None:
            self.logger.warning(
                "[PushConfigToDevice] No interface specified in pipeline context. "
                "Cannot push config without knowing which interface to configure. "
                "This job should be called from the pipeline with interface parameter."
            )
            return

        # Make sure the interface parameter is actually an Interface object
        if not isinstance(interface, Interface):
            self.logger.error(
                f"[PushConfigToDevice] The 'interface' parameter is not an Interface object "
                f"(got {type(interface).__name__}). Cannot proceed. "
                f"This indicates a bug in the calling code."
            )
            return

        # Verify that this interface actually belongs to the device we're configuring
        # This prevents accidentally configuring the wrong device
        if interface.device != device:
            self.logger.error(
                f"[PushConfigToDevice] Interface {interface.name} does not belong to "
                f"device {device.name} (it belongs to {interface.device.name}). "
                f"Cannot push config. This indicates a logic error in the pipeline."
            )
            return

        # Get the VLAN we're supposed to configure
        # If not passed explicitly, get it from the interface
        if vlan is None:
            vlan = getattr(interface, "untagged_vlan", None)

        if vlan is None:
            # No VLAN to configure - nothing to do
            self.logger.info(
                f"[PushConfigToDevice] No VLAN configured on interface {interface.name}. "
                f"Nothing to push to device."
            )
            return

        # --- Render the configuration using Jinja2 template ---
        # Locate the Git repository
        repo_root = os.environ.get(self.REPO_ENV_VAR, self.DEFAULT_REPO_PATH)
        repo_root = Path(repo_root)
        self.logger.info(f"[PushConfigToDevice] Using Git repository at: {repo_root}")

        # Find the template file
        template_path = repo_root / self.TEMPLATE_REL_PATH
        if not template_path.exists():
            self.logger.error(
                f"[PushConfigToDevice] Template file not found at {template_path}. "
                f"Cannot generate configuration commands. Please ensure template exists."
            )
            return

        try:
            # Set up Jinja2 to render the template
            env = Environment(
                loader=FileSystemLoader(str(template_path.parent)),
                autoescape=False,  # No HTML escaping needed for network configs
            )
            template = env.get_template(template_path.name)
            
            # Render template with ONLY this specific interface
            # This generates the configuration commands for just this one interface
            rendered = template.render(interfaces=[interface])
            
            self.logger.info(
                f"[PushConfigToDevice] Rendered template for interface {interface.name}. "
                f"Generated {len(rendered)} characters of configuration."
            )
            
        except Exception as e:
            self.logger.error(
                f"[PushConfigToDevice] Failed to render template {template_path}: {e}"
            )
            return

        # --- Build the list of commands to send ---
        # Start with a delete command to remove any existing VLAN config
        # This ensures a clean slate before applying new config
        config_lines = [
            f"delete interfaces {interface.name} unit 0 family ethernet-switching vlan members"
        ]

        # Add all the "set" commands from the rendered template
        # Each line becomes a separate command
        for line in rendered.splitlines():
            line = line.strip()  # Remove leading/trailing whitespace
            if not line:  # Skip empty lines
                continue
            config_lines.append(line)

        # Sanity check - make sure we actually have commands to send
        if not config_lines:
            self.logger.warning(
                f"[PushConfigToDevice] No configuration commands generated for interface "
                f"{interface.name}. Template might be empty or misconfigured. Nothing to push."
            )
            return

        self.logger.info(
            f"[PushConfigToDevice] Prepared {len(config_lines)} commands to send to "
            f"device {device.name} for interface {interface.name}:"
        )
        # Log each command so we can see exactly what will be sent
        for i, cmd in enumerate(config_lines, 1):
            self.logger.info(f"  Command {i}: {cmd}")

        # --- Validate device platform ---
        # This PoC only supports Juniper JunOS devices
        platform = getattr(device, "platform", None)
        driver = getattr(platform, "network_driver", None)

        if driver != "juniper_junos":
            self.logger.info(
                f"[PushConfigToDevice] Device {device.name} has network driver '{driver}', "
                f"not 'juniper_junos'. This PoC only supports Juniper devices. Skipping push."
            )
            return

        # --- Get device IP address ---
        primary_ip = getattr(device, "primary_ip4", None)
        if primary_ip is None:
            self.logger.warning(
                f"[PushConfigToDevice] Device {device.name} has no primary IPv4 address. "
                f"Cannot establish SSH connection. Please configure a primary IP in Nautobot."
            )
            return

        host = str(primary_ip.address.ip)  # Extract just the IP, not the subnet

        # --- Retrieve credentials ---
        # Same logic as backup job - try Secrets Group first, then environment variables
        username = None
        password = None

        # Try device's assigned Secrets Group (recommended approach)
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
                    f"[PushConfigToDevice] Retrieved credentials from SecretsGroup "
                    f"'{secrets_group.name}' for device {device.name}."
                )
            except SecretError as e:
                self.logger.error(
                    f"[PushConfigToDevice] Failed to get credentials from SecretsGroup: {e}"
                )

        # Fallback to environment variables
        if not username or not password:
            env_user = os.environ.get("NETMIKO_USERNAME")
            env_pass = os.environ.get("NETMIKO_PASSWORD")
            
            if env_user and env_pass:
                username = env_user
                password = env_pass
                self.logger.info(
                    "[PushConfigToDevice] Using fallback credentials from environment "
                    "variables (NETMIKO_USERNAME and NETMIKO_PASSWORD)."
                )

        # Final check - do we have credentials?
        if not username or not password:
            self.logger.error(
                "[PushConfigToDevice] No credentials available. Tried: "
                "1) Device SecretsGroup, 2) Environment variables. "
                "Cannot push configuration without credentials."
            )
            return

        # Build connection parameters for Netmiko
        device_params = {
            "device_type": driver,  # juniper_junos
            "host": host,  # Device IP
            "username": username,
            "password": password,
            "timeout": 30,  # Connection timeout
            "banner_timeout": 15,  # Banner timeout
        }

        # --- Connect and push configuration ---
        self.logger.info(
            f"[PushConfigToDevice] Connecting to device {device.name} at {host} via SSH "
            f"to push configuration for interface {interface.name}..."
        )

        try:
            # Use context manager to ensure connection cleanup
            with ConnectHandler(**device_params) as conn:
                self.logger.info(
                    f"[PushConfigToDevice] Successfully connected. Sending {len(config_lines)} "
                    f"configuration commands..."
                )
                
                # Send all commands to the device
                # send_config_set enters configuration mode, sends commands, and exits
                output = conn.send_config_set(config_lines)
                
                # Log the device's response
                self.logger.info(
                    f"[PushConfigToDevice] Device response:\n{output}"
                )
                
                # Check if there were any errors in the output
                # Junos typically includes "error" or "invalid" in error messages
                if "error" in output.lower() or "invalid" in output.lower():
                    self.logger.warning(
                        "[PushConfigToDevice] Device output contains 'error' or 'invalid'. "
                        "Configuration might not have been applied successfully. "
                        "Please review the output above."
                    )
                
        except Exception as e:
            # Connection or command execution failed
            self.logger.error(
                f"[PushConfigToDevice] Failed to push configuration to device "
                f"{device.name} ({host}). Error: {e}"
            )
            return

        self.logger.info(
            f"[PushConfigToDevice] Successfully completed config push for device "
            f"{device.name}, interface {interface.name}."
        )


# Register this job so Nautobot can discover and run it
register_jobs(PushConfigToDevice)
