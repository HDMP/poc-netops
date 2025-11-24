# backup_config_job.py

from nautobot.apps.jobs import Job, ObjectVar, register_jobs
from nautobot.dcim.models import Device

name = "00_Vlan-Change-Jobs"


class BackupDeviceConfig(Job):
    """
    Step 1: Dummy backup job – currently only logs.
    """

    class Meta:
        name = "01_Backup device config (POC)"
        description = "Dummy job: would backup config for a single device."
        commit_default = False  # no DB changes

    device = ObjectVar(
        model=Device,
        required=True,
        description="Device to backup.",
    )

    def run(self, device, interface=None, vlan=None, **kwargs):
        self.logger.info(
            f"[BackupDeviceConfig] Would backup config for device {device} (pk={device.pk})."
        )
        self.logger.info(
            f"[BackupDeviceConfig] Context: interface={interface}, vlan={getattr(vlan, 'id', vlan)}"
        )


register_jobs(BackupDeviceConfig)
