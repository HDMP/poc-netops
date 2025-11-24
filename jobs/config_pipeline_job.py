# config_pipeline_job.py

from nautobot.apps.jobs import Job, ObjectVar, register_jobs
from nautobot.dcim.models import Device

# WICHTIG: Paket-relative Importe
from .backup_config_job import BackupDeviceConfig
from .intended_config_job import BuildIntendedConfig
from .push_config_job import PushConfigToDevice

name = "00_Vlan-Change-Jobs"


class ConfigPipeline(Job):
    """
    Main POC pipeline job:
      1) Backup
      2) Intended config
      3) Push
    """

    class Meta:
        name = "00_Config pipeline (POC)"
        description = "Runs backup, intended, and push jobs in sequence for one device."
        commit_default = False

    device = ObjectVar(
        model=Device,
        required=True,
        description="Device that should go through the pipeline.",
    )

    def run(self, device, interface=None, vlan=None, **kwargs):
        self.logger.info(
            f"[ConfigPipeline] Starting pipeline for device {device} (pk={device.pk})."
        )
        self.logger.info(
            f"[ConfigPipeline] Context received: interface={interface}, "
            f"vlan={getattr(vlan, 'id', vlan)}"
        )

        self.logger.info("[ConfigPipeline] Step 1: calling BackupDeviceConfig.")
        backup_job = BackupDeviceConfig()
        backup_job.logger = self.logger
        backup_job.run(device=device, interface=interface, vlan=vlan)

        self.logger.info("[ConfigPipeline] Step 2: calling BuildIntendedConfig.")
        intended_job = BuildIntendedConfig()
        intended_job.logger = self.logger
        intended_job.run(device=device, interface=interface, vlan=vlan)

        self.logger.info("[ConfigPipeline] Step 3: calling PushConfigToDevice.")
        push_job = PushConfigToDevice()
        push_job.logger = self.logger
        push_job.run(device=device, interface=interface, vlan=vlan)

        self.logger.info(
            f"[ConfigPipeline] Pipeline finished for device {device} (pk={device.pk})."
        )


register_jobs(ConfigPipeline)
