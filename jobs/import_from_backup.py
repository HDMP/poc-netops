# /opt/nautobot/jobs/import_from_backup.py
from nautobot.apps import jobs
from nautobot.dcim.models import Device, Interface
from nautobot.ipam.models import VLAN
from nautobot.extras.models import Status
from django.contrib.contenttypes.models import ContentType
from jinja2 import Template
import os, re

name = "Custom Import from Config"  # Gruppierung im UI

class ImportJunosFromBackup(jobs.Job):
    """Junos-Backup einlesen, VLANs erstellen, Access-Ports (untagged VLAN) mappen."""

    device = jobs.ObjectVar(model=Device, description="Target device")
    repo_root = jobs.StringVar(default="/opt/nautobot/git/poc_netops", description="Backup repo root")
    rel_path_tpl = jobs.StringVar(default="backups/{{ device.name }}.cfg", description="Jinja path to backup file")

    class Meta:
        name = "Import Junos (VLANs & Access-Ports) from Backup"
        commit_default = True

    def _get_or_create_active_status(self, model):
        """Get or create Active status for given model."""
        ct = ContentType.objects.get_for_model(model)
        st = Status.objects.filter(content_types=ct, name__iexact="active").first()
        if not st:
            st = Status.objects.create(name="Active", color="green")
            st.content_types.add(ct)
        return st

    def run(self, *, device, repo_root, rel_path_tpl):
        # Datei aufloesen und laden
        path = Template(rel_path_tpl).render(device=device)
        fpath = os.path.join(repo_root, path)
        if not os.path.isfile(fpath):
            self.logger.error(f"Backup file not found: {fpath}")
            return

        with open(fpath, "r", encoding="utf-8", errors="ignore") as fh:
            txt = fh.read()

        dev_loc = getattr(device, "location", None)    # Nautobot 2.x
        vlan_status = self._get_or_create_active_status(VLAN)
        interface_status = self._get_or_create_active_status(Interface)

        # ---------- VLANs: überall Stanzas "NAME { ... vlan-id N; }" finden
        created = updated = 0
        vlan_map = {}  # name -> vid
        for m in re.finditer(
            r"^[ \t]*([A-Za-z0-9\-_]+)[ \t]*\{(?:(?!^\}).)*?vlan-id[ \t]+(\d+);",
            txt, re.M | re.S
        ):
            vname, vid = m.group(1), int(m.group(2))
            vlan_map[vname] = vid
            qs = VLAN.objects.filter(vid=vid)
            if dev_loc:
                qs = qs.filter(location=dev_loc)
            vlan = qs.first()
            if not vlan:
                payload = {"name": vname, "vid": vid, "status": vlan_status}
                if dev_loc:
                    payload["location"] = dev_loc
                VLAN.objects.create(**payload)
                created += 1
            else:
                changed = False
                if vlan.name != vname:
                    vlan.name = vname; changed = True
                if not vlan.status_id:
                    vlan.status = vlan_status; changed = True
                if changed:
                    vlan.save(); updated += 1
        self.logger.info(f"VLANs parsed: {len(vlan_map)}; created: {created}, updated: {updated}")

        # ---------- Interfaces: ge-x/x/x Blocks parsen, Access + untagged VLAN mappen
        port_updates = 0
        for ib in re.finditer(r"\n\s*(ge-\d+/\d+/\d+)\s*{(.*?)}\s*\n", txt, re.S):
            ifname, ibody = ib.group(1), ib.group(2)
            if "interface-mode access" not in ibody:
                continue
            m_vlan = re.search(r"vlan\s*{\s*members\s+([A-Za-z0-9\-_]+);", ibody)
            if not m_vlan:
                continue
            vname = m_vlan.group(1)
            vid = vlan_map.get(vname)
            vlan_obj = None
            if vid:
                qs = VLAN.objects.filter(vid=vid)
                if dev_loc:
                    qs = qs.filter(location=dev_loc)
                vlan_obj = qs.first()

            iface = Interface.objects.filter(device=device, name=ifname).first()
            if not iface:
                iface = Interface.objects.create(
                    device=device, 
                    name=ifname, 
                    type="other", 
                    status=interface_status,  # ADD THIS
                    enabled=True
                )

            changed = False
            if hasattr(iface, "mode") and iface.mode != "access":
                iface.mode = "access"; changed = True
            if vlan_obj and getattr(iface, "untagged_vlan_id", None) != vlan_obj.id:
                iface.untagged_vlan = vlan_obj; changed = True
            if changed:
                iface.save(); port_updates += 1

        self.logger.info(f"Ports updated: {port_updates}")
        self.logger.success("Import fertig.")

jobs.register_jobs(ImportJunosFromBackup)
