# config_pipeline_job.py
#
# This is the main orchestration job that runs the entire configuration pipeline.
#
# What it does:
# 1. Calls the Backup job to save current device config
# 2. Calls the Intended Config job to render what config should look like
# 3. Calls the Push job to send config changes to the device
# 4. Optionally runs "git push" at the end to sync to remote repository
#
# Why we need this:
# This ties everything together into one automated workflow. When a VLAN changes
# in Nautobot, this pipeline ensures we:
# - Have a backup before making changes (safety)
# - Generate the correct intended config (consistency)
# - Push changes to the device (automation)
# - Track everything in Git (audit trail)
#
# The pipeline can be triggered manually or automatically by the Socket sync job hook.

import os
import subprocess
from pathlib import Path

from nautobot.apps.jobs import Job, ObjectVar, register_jobs
from nautobot.dcim.models import Device

# Import the individual jobs that make up our pipeline
# These are relative imports from the same package
from .backup_config_job import BackupDeviceConfig
from .intended_config_job import BuildIntendedConfig
from .push_config_job import PushConfigToDevice

# Groups all related jobs together in the Nautobot UI
name = "00_Vlan-Change-Jobs"


class ConfigPipeline(Job):
    """
    Main pipeline orchestrator job - runs backup, intended, and push in sequence.
    
    This job coordinates the entire configuration management workflow:
    1. Backup current device state (safety net)
    2. Build intended config from Nautobot data (source of truth)
    3. Push changes to device (automation)
    4. Sync to Git remote (version control)
    """

    class Meta:
        # Name as shown in Nautobot - using "00_" prefix to sort it to the top
        name = "00_Config pipeline (POC)"
        
        # Help text for users
        description = "Runs backup, intended config build, and config push in sequence for a device."
        
        # We don't modify Nautobot database directly (sub-jobs might, but we don't)
        commit_default = False

    # Define input parameter - which device to run pipeline for
    device = ObjectVar(
        model=Device,
        required=True,
        description="Device to run the complete configuration pipeline for.",
    )

    def run(self, device, interface=None, vlan=None, **kwargs):
        """
        Main execution method that orchestrates the entire pipeline.
        
        Args:
            device: The Device object to process (required)
            interface: The specific Interface that triggered this (optional, for context)
            vlan: The VLAN being configured (optional, for context)
            **kwargs: Additional arguments (ignored)
            
        The interface and vlan parameters are passed through to each sub-job for
        context and logging purposes. They help us understand what triggered the
        pipeline and what changes we're making.
        """
        
        self.logger.info(
            "[ConfigPipeline] =========================================="
        )
        self.logger.info(
            f"[ConfigPipeline] Starting configuration pipeline for device {device.name} "
            f"(database ID: {device.pk})"
        )
        self.logger.info(
            "[ConfigPipeline] =========================================="
        )
        
        # Log the context that triggered this pipeline
        # This helps with debugging and understanding the audit trail
        self.logger.info(
            f"[ConfigPipeline] Pipeline context:"
        )
        self.logger.info(
            f"  - Target device: {device.name}"
        )
        self.logger.info(
            f"  - Interface: {interface.name if interface else 'N/A'}"
        )
        self.logger.info(
            f"  - VLAN: {getattr(vlan, 'id', vlan) if vlan else 'N/A'}"
        )

        # --- STEP 1: BACKUP ---
        # Before making any changes, save the current device configuration
        # This gives us a rollback point if something goes wrong
        self.logger.info(
            "[ConfigPipeline] =========================================="
        )
        self.logger.info(
            "[ConfigPipeline] STEP 1 of 3: Running device configuration backup"
        )
        self.logger.info(
            "[ConfigPipeline] =========================================="
        )
        
        # Create an instance of the backup job
        backup_job = BackupDeviceConfig()
        
        # Share our logger so all output appears in the same log stream
        # This makes it easier to follow the entire pipeline in one place
        backup_job.logger = self.logger
        
        # Run the backup job with our device and context
        backup_job.run(device=device, interface=interface, vlan=vlan)
        
        self.logger.info(
            "[ConfigPipeline] Step 1 completed: Backup finished"
        )

        # --- STEP 2: BUILD INTENDED CONFIG ---
        # Generate what the configuration SHOULD look like based on Nautobot data
        # This is our "desired state" derived from the source of truth
        self.logger.info(
            "[ConfigPipeline] =========================================="
        )
        self.logger.info(
            "[ConfigPipeline] STEP 2 of 3: Building intended configuration"
        )
        self.logger.info(
            "[ConfigPipeline] =========================================="
        )
        
        # Create an instance of the intended config job
        intended_job = BuildIntendedConfig()
        
        # Share our logger
        intended_job.logger = self.logger
        
        # Run the intended config job
        intended_job.run(device=device, interface=interface, vlan=vlan)
        
        self.logger.info(
            "[ConfigPipeline] Step 2 completed: Intended config built"
        )

        # --- STEP 3: PUSH CONFIG TO DEVICE ---
        # Send the configuration commands to the actual device
        # This makes the real-world device match our source of truth
        self.logger.info(
            "[ConfigPipeline] =========================================="
        )
        self.logger.info(
            "[ConfigPipeline] STEP 3 of 3: Pushing configuration to device"
        )
        self.logger.info(
            "[ConfigPipeline] =========================================="
        )
        
        # Create an instance of the push job
        push_job = PushConfigToDevice()
        
        # Share our logger
        push_job.logger = self.logger
        
        # Run the push job
        # This is where the actual device configuration changes happen
        push_job.run(device=device, interface=interface, vlan=vlan)
        
        self.logger.info(
            "[ConfigPipeline] Step 3 completed: Configuration pushed to device"
        )

        # --- PIPELINE COMPLETION ---
        self.logger.info(
            "[ConfigPipeline] =========================================="
        )
        self.logger.info(
            f"[ConfigPipeline] Pipeline completed successfully for device {device.name}"
        )
        self.logger.info(
            "[ConfigPipeline] =========================================="
        )

        # --- OPTIONAL: GIT PUSH ---
        # At this point, we have:
        # - Backed up the config (committed to Git)
        # - Built intended config (committed to Git)
        # - Pushed changes to device
        # 
        # Now we can optionally push to a remote Git repository
        # This syncs our local commits to a central server for team collaboration
        
        # Locate the Git repository
        repo_root = Path(
            os.environ.get("POC_NETOPS_REPO", "/opt/nautobot/git/poc_netops")
        )
        git_dir = repo_root / ".git"

        # Check if this is actually a Git repository
        if not git_dir.exists():
            self.logger.warning(
                f"[ConfigPipeline] Directory {repo_root} is not a Git repository "
                f"(no .git directory found). Skipping git push. "
                f"To initialize: cd {repo_root} && git init"
            )
            return

        # Try to push to remote
        self.logger.info(
            "[ConfigPipeline] =========================================="
        )
        self.logger.info(
            f"[ConfigPipeline] Running 'git push' to sync commits to remote repository"
        )
        self.logger.info(
            f"[ConfigPipeline] Repository: {repo_root}"
        )
        self.logger.info(
            "[ConfigPipeline] =========================================="
        )
        
        try:
            # Run git push command
            push_proc = subprocess.run(
                ["git", "-C", str(repo_root), "push"],
                capture_output=True,  # Capture output for logging
                text=True,  # Return strings instead of bytes
                check=False,  # Don't raise exception on failure
            )
            
            # Log the results
            self.logger.info(
                f"[ConfigPipeline] git push exit code: {push_proc.returncode}"
            )
            
            if push_proc.returncode == 0:
                # Success
                self.logger.info(
                    f"[ConfigPipeline] git push succeeded. Output: '{push_proc.stdout.strip()}'"
                )
            else:
                # Failed - might be no remote configured, authentication issue, etc.
                self.logger.warning(
                    f"[ConfigPipeline] git push failed. This is not critical - commits are "
                    f"still saved locally. Error: '{push_proc.stderr.strip()}'"
                )
                
        except Exception as e:
            # Command execution failed
            self.logger.error(
                f"[ConfigPipeline] Exception while running git push: {e}. "
                f"Commits are still saved locally in {repo_root}."
            )

        self.logger.info(
            "[ConfigPipeline] =========================================="
        )
        self.logger.info(
            "[ConfigPipeline] All pipeline operations completed"
        )
        self.logger.info(
            "[ConfigPipeline] =========================================="
        )


# Register this job so Nautobot can discover and run it
register_jobs(ConfigPipeline)
