# intended_config_job.py
#
# This job builds the "intended" configuration for a device based on Nautobot data.
#
# What it does:
# 1. Queries all interfaces for a device from Nautobot (source of truth)
# 2. Renders a Jinja2 template with that data to generate the intended config
# 3. Saves the rendered config to a file in the Git repo (intended/<device_name>.conf)
# 4. Commits the file to Git so we can track changes over time
#
# Why we need this:
# This is the "desired state" - what the device configuration SHOULD look like
# based on what's in Nautobot. We can later compare this to the actual device config
# to detect drift, or use it to generate commands to push to the device.

import os
import subprocess
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from nautobot.apps.jobs import Job, ObjectVar, register_jobs
from nautobot.dcim.models import Device

# Groups all related jobs together in the Nautobot UI
name = "00_Vlan-Change-Jobs"


class BuildIntendedConfig(Job):
    """
    Step 2 of the pipeline: Build the intended (desired) configuration.
    
    This job takes interface data from Nautobot and renders a Jinja2 template
    to create the configuration file that represents what the device SHOULD look like.
    """

    class Meta:
        # Job name as shown in Nautobot
        name = "02_Build intended config (POC)"
        
        # Help text for users
        description = "Render Jinja2 template to create intended device configuration and store in Git."
        
        # We're only writing files and committing to git, not modifying Nautobot database
        commit_default = False

    # Define input parameter - which device to build config for
    device = ObjectVar(
        model=Device,
        required=True,
        description="Device to build intended configuration for.",
    )

    # Configuration constants
    # Where to find the Git repository and templates
    REPO_ENV_VAR = "POC_NETOPS_REPO"  # Environment variable name
    DEFAULT_REPO_PATH = "/opt/nautobot/git/poc_netops"  # Fallback if env var not set
    TEMPLATE_REL_PATH = "templates/juniper_junos.j2"  # Path to Jinja template in repo
    INTENDED_DIR_NAME = "intended"  # Subdirectory for intended configs

    def run(self, device, interface=None, vlan=None, **kwargs):
        """
        Main execution method for building intended config.
        
        Args:
            device: The Device object to build config for (required)
            interface: Specific interface context (passed through from pipeline, used in logging)
            vlan: Specific VLAN context (passed through from pipeline, used in logging)
            **kwargs: Additional arguments (ignored)
        """
        
        self.logger.info(
            f"[BuildIntendedConfig] Starting intended config build for device {device.name} "
            f"(database ID: {device.pk})."
        )
        self.logger.info(
            f"[BuildIntendedConfig] Pipeline context: interface={interface}, "
            f"vlan={getattr(vlan, 'id', vlan) if vlan else 'None'}"
        )

        # --- Step 1: Locate the Git repository ---
        # First check environment variable, then fall back to default path
        repo_root = os.environ.get(self.REPO_ENV_VAR, self.DEFAULT_REPO_PATH)
        repo_root = Path(repo_root)  # Convert to Path object for easier file operations
        self.logger.info(f"[BuildIntendedConfig] Using Git repository at: {repo_root}")

        # Validate that the repository exists
        if not repo_root.exists():
            self.logger.error(
                f"[BuildIntendedConfig] Git repository path {repo_root} does not exist. "
                f"Please create the directory or set {self.REPO_ENV_VAR} environment variable. "
                f"Example: mkdir -p {repo_root}"
            )
            return

        # --- Step 2: Locate the Jinja2 template ---
        template_path = repo_root / self.TEMPLATE_REL_PATH
        
        if not template_path.exists():
            self.logger.error(
                f"[BuildIntendedConfig] Template file not found at {template_path}. "
                f"Please ensure the Jinja2 template exists at this location. "
                f"Expected path: {self.TEMPLATE_REL_PATH}"
            )
            return

        # --- Step 3: Get interface data from Nautobot ---
        # Query all interfaces for this device
        # This is our source of truth - what Nautobot says the device should have
        interfaces = list(device.interfaces.all())
        self.logger.info(
            f"[BuildIntendedConfig] Retrieved {len(interfaces)} interfaces from Nautobot "
            f"for device {device.name}. These will be used to render the intended config."
        )

        # --- Step 4: Render the Jinja2 template ---
        # The template will generate the configuration based on interface data
        try:
            # Set up Jinja2 environment
            # FileSystemLoader tells Jinja where to find templates
            env = Environment(
                loader=FileSystemLoader(str(template_path.parent)),
                autoescape=False,  # Don't escape characters (we're generating config, not HTML)
            )
            
            # Load the specific template file
            template = env.get_template(template_path.name)
            
            # Render the template with our interface data
            # The template can loop over 'interfaces' and generate config for each one
            rendered = template.render(
                interfaces=interfaces,
                device=device,  # Also pass device in case template needs it
            )
            
            self.logger.info(
                f"[BuildIntendedConfig] Successfully rendered template. "
                f"Generated config is {len(rendered)} characters long."
            )
            
        except Exception as e:
            # Template rendering failed - could be syntax error in template or missing data
            self.logger.error(
                f"[BuildIntendedConfig] Failed to render template {template_path}. "
                f"Error: {e}. Please check the template syntax and ensure all required "
                f"variables are available."
            )
            return

        # Validate that we got some actual config output
        if not rendered or len(rendered) < 10:
            self.logger.warning(
                f"[BuildIntendedConfig] Rendered config seems empty or very short "
                f"({len(rendered)} characters). This might indicate a problem with the template. "
                f"Continuing anyway..."
            )

        # --- Step 5: Write the intended config to a file ---
        # Create the intended directory if it doesn't exist
        intended_dir = repo_root / self.INTENDED_DIR_NAME
        intended_dir.mkdir(parents=True, exist_ok=True)

        # Create filename based on device name
        # Format: <device_name>.conf
        intended_file = intended_dir / f"{device.name}.conf"
        
        try:
            # Write the rendered configuration to the file
            # Add a newline at the end for consistent formatting
            intended_file.write_text(rendered + "\n", encoding="utf-8")
            self.logger.info(
                f"[BuildIntendedConfig] Successfully wrote intended configuration to {intended_file}."
            )
        except Exception as e:
            self.logger.error(
                f"[BuildIntendedConfig] Failed to write intended config file {intended_file}: {e}"
            )
            return

        # --- Step 6: Commit to Git ---
        # Check if this is actually a Git repository
        git_dir = repo_root / ".git"
        if not git_dir.exists():
            self.logger.warning(
                f"[BuildIntendedConfig] Directory {repo_root} is not a Git repository "
                f"(no .git directory found). Skipping git operations. "
                f"To initialize git: cd {repo_root} && git init"
            )
            return

        # Get relative path for git commands
        # Git works better with paths relative to the repo root
        rel_intended_path = intended_file.relative_to(repo_root)

        try:
            # Stage the intended config file
            self.logger.info(
                f"[BuildIntendedConfig] Running 'git add {rel_intended_path}' to stage file."
            )
            add_proc = subprocess.run(
                ["git", "-C", str(repo_root), "add", str(rel_intended_path)],
                capture_output=True,  # Capture output for logging
                text=True,  # Get strings instead of bytes
                check=False,  # Don't raise exception on error
            )
            # Log the result
            self.logger.info(
                f"[BuildIntendedConfig] git add completed with exit code {add_proc.returncode}. "
                f"Output: '{add_proc.stdout.strip()}' | Errors: '{add_proc.stderr.strip()}'"
            )

            # Commit the staged changes
            commit_msg = f"Update intended config for device {device.name}"
            self.logger.info(
                f"[BuildIntendedConfig] Running 'git commit -m \"{commit_msg}\"'."
            )
            commit_proc = subprocess.run(
                ["git", "-C", str(repo_root), "commit", "--allow-empty", "-m", commit_msg],
                capture_output=True,
                text=True,
                check=False,
            )
            # Log the result
            self.logger.info(
                f"[BuildIntendedConfig] git commit completed with exit code {commit_proc.returncode}. "
                f"Output: '{commit_proc.stdout.strip()}' | Errors: '{commit_proc.stderr.strip()}'"
            )
            
            # Note: We use --allow-empty to create commits even if nothing changed
            # This gives us an audit trail of when the job ran, even if config was identical

        except Exception as e:
            self.logger.error(
                f"[BuildIntendedConfig] Error during git operations in {repo_root}: {e}"
            )

        self.logger.info(
            f"[BuildIntendedConfig] Finished building intended config for device {device.name}. "
            f"File saved to {intended_file} and committed to Git."
        )


# Register this job so Nautobot can discover and run it
register_jobs(BuildIntendedConfig)
