from nautobot.apps.jobs import Job, ObjectVar, register_jobs
from nautobot.dcim.models import Device


class BackupDeviceConfig(Job):
    """
    Step 1: Dummy backup job.
    """

    class Meta:
        name = "Backup device config (POC)"
        description = "Dummy job: would backup config for a single device."
        commit_default = False  # safe: no real changes

    device = ObjectVar(
        model=Device,
        required=True,
        description="Device to backup.",
    )

    def run(self, device, interface=None, vlan=None, **kwargs):
        # Only log for now, but also show interface / vlan if passed.
        self.logger.info(
            f"[BackupDeviceConfig] Would backup config for device {device} (pk={device.pk})."
        )
        self.logger.info(
            f"[BackupDeviceConfig] Context: interface={interface}, vlan={getattr(vlan, 'id', vlan)}"
        )


register_jobs(BackupDeviceConfig)
