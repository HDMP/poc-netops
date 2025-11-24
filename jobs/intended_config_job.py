# intended_config_job.py

import os
import subprocess
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from nautobot.apps.jobs import Job, ObjectVar, register_jobs
from nautobot.dcim.models import Device

name = "00_Vlan-Change-Jobs"


class BuildIntendedConfig(Job):
    """
    Step 2: Render intended config for a device using the Junos Jinja template
    and write it into the Git repo as intended/<device>.conf.
    """

    class Meta:
        name = "02_Build intended config (POC)"
        description = "Render Jinja intended config for a device and store it in the Git repo."
        commit_default = False  # filesystem + git only

    device = ObjectVar(
        model=Device,
        required=True,
        description="Device to build intended config for.",
    )

    REPO_ENV_VAR = "POC_NETOPS_REPO"
    DEFAULT_REPO_PATH = "/opt/nautobot/git/poc_netops"
    TEMPLATE_REL_PATH = "templates/juniper_junos.j2"
    INTENDED_DIR_NAME = "intended"

    def run(self, device, interface=None, vlan=None, **kwargs):
        self.logger.info(
            f"[BuildIntendedConfig] Building intended config for device {device} (pk={device.pk})."
        )
        self.logger.info(
            f"[BuildIntendedConfig] Context: interface={interface}, "
            f"vlan={getattr(vlan, 'id', vlan)}"
        )

        # 1) Repo root
        repo_root = os.environ.get(self.REPO_ENV_VAR, self.DEFAULT_REPO_PATH)
        repo_root = Path(repo_root)
        self.logger.info(f"[BuildIntendedConfig] Using repo root: {repo_root}")

        if not repo_root.exists():
            self.logger.error(
                f"[BuildIntendedConfig] Repo root {repo_root} does not exist. "
                f"Check {self.REPO_ENV_VAR} or DEFAULT_REPO_PATH."
            )
            return

        # 2) Template
        template_path = repo_root / self.TEMPLATE_REL_PATH
        if not template_path.exists():
            self.logger.error(
                f"[BuildIntendedConfig] Template file {template_path} does not exist."
            )
            return

        # 3) Interfaces from device (complete device intended)
        interfaces = list(device.interfaces.all())
        self.logger.info(
            f"[BuildIntendedConfig] Rendering intended config for {len(interfaces)} interfaces "
            f"on device {device.name}."
        )

        # 4) Render Jinja
        try:
            env = Environment(
                loader=FileSystemLoader(str(template_path.parent)),
                autoescape=False,
            )
            template = env.get_template(template_path.name)
            rendered = template.render(interfaces=interfaces)
        except Exception as e:
            self.logger.error(
                f"[BuildIntendedConfig] Error while rendering template {template_path}: {e}"
            )
            return

        # 5) Write intended file
        intended_dir = repo_root / self.INTENDED_DIR_NAME
        intended_dir.mkdir(parents=True, exist_ok=True)

        intended_file = intended_dir / f"{device.name}.conf"
        try:
            intended_file.write_text(rendered + "\n", encoding="utf-8")
            self.logger.info(
                f"[BuildIntendedConfig] Wrote intended config to {intended_file}."
            )
        except Exception as e:
            self.logger.error(
                f"[BuildIntendedConfig] Failed to write intended config file {intended_file}: {e}"
            )
            return

        # 6) Git add + commit, mit Output im Log
        git_dir = repo_root / ".git"
        if not git_dir.exists():
            self.logger.warning(
                f"[BuildIntendedConfig] {repo_root} is not a Git repository "
                f"(no .git directory), skipping git add/commit."
            )
            return

        rel_intended_path = intended_file.relative_to(repo_root)

        try:
            self.logger.info(
                f"[BuildIntendedConfig] Running 'git add {rel_intended_path}'."
            )
            add_proc = subprocess.run(
                ["git", "-C", str(repo_root), "add", str(rel_intended_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.logger.info(
                f"[BuildIntendedConfig] git add rc={add_proc.returncode}, "
                f"stdout='{add_proc.stdout.strip()}', stderr='{add_proc.stderr.strip()}'"
            )

            commit_msg = f"Update intended config for device {device.name}"
            self.logger.info(
                f"[BuildIntendedConfig] Running 'git commit -m \"{commit_msg}\"'."
            )
            commit_proc = subprocess.run(
                ["git", "-C", str(repo_root), "commit", "-m", commit_msg],
                capture_output=True,
                text=True,
                check=False,
            )
            self.logger.info(
                f"[BuildIntendedConfig] git commit rc={commit_proc.returncode}, "
                f"stdout='{commit_proc.stdout.strip()}', stderr='{commit_proc.stderr.strip()}'"
            )

        except Exception as e:
            self.logger.error(
                f"[BuildIntendedConfig] Error during git add/commit in {repo_root}: {e}"
            )

        self.logger.info(
            f"[BuildIntendedConfig] Finished building intended config for device {device.name}."
        )


register_jobs(BuildIntendedConfig)
