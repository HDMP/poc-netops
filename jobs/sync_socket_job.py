# sync_socket_job.py

from nautobot.apps.jobs import JobHookReceiver, register_jobs
from nautobot.dcim.models import Interface

from .config_pipeline_job import ConfigPipeline

name = "00_Vlan-Change-Jobs"


class SyncSocketVlanToSwitch(JobHookReceiver):
    """
    JobHook: Keep untagged VLAN in sync between a Socket interface and its connected switch port.
    Works in both directions and triggers the ConfigPipeline when something changed.
    """

    class Meta:
        name = "99_Sync Socket VLAN to Switch"
        description = "Synchronize untagged VLAN between Socket and Switch and trigger config pipeline."
        commit_default = True

    def receive_job_hook(self, change, action, changed_object):
        # Only handle updates
        if action != "update":
            self.logger.debug(
                f"[SyncSocketVlanToSwitch] Ignoring action '{action}' (only 'update' is handled)."
            )
            return

        if not isinstance(changed_object, Interface):
            self.logger.debug(
                f"[SyncSocketVlanToSwitch] Changed object is not an Interface "
                f"(got {type(changed_object)}), skipping."
            )
            return

        iface: Interface = changed_object

        device = getattr(iface, "device", None)
        role = getattr(device, "role", None)
        role_name = getattr(role, "name", None)

        # Get connected endpoint
        peer = getattr(iface, "connected_endpoint", None)
        if peer is None:
            self.logger.info(
                f"[SyncSocketVlanToSwitch] Interface {iface} is not connected to anything, "
                f"nothing to synchronize."
            )
            return

        # If first peer is not an Interface (front/rear port), try second hop
        if not isinstance(peer, Interface):
            peer2 = getattr(peer, "connected_endpoint", None)
            if isinstance(peer2, Interface):
                self.logger.debug(
                    f"[SyncSocketVlanToSwitch] First peer of {iface} is {type(peer)}, "
                    f"second-hop peer {peer2} is an Interface – using that."
                )
                peer = peer2
            else:
                self.logger.info(
                    f"[SyncSocketVlanToSwitch] Peer of {iface} is not an Interface and "
                    f"second-hop peer is also not an Interface, skipping."
                )
                return

        peer_device = getattr(peer, "device", None)
        peer_role = getattr(peer_device, "role", None)
        peer_role_name = getattr(peer_role, "name", None)

        # Decide Socket vs Switch side
        if role_name == "Socket" and peer_role_name != "Socket":
            socket_iface = iface
            switch_iface = peer
            direction = "Socket -> Switch"
        elif role_name != "Socket" and peer_role_name == "Socket":
            socket_iface = peer
            switch_iface = iface
            direction = "Switch -> Socket"
        else:
            self.logger.info(
                f"[SyncSocketVlanToSwitch] Interface {iface} (role={role_name}) and peer {peer} "
                f"(role={peer_role_name}) do not form a Socket<->Switch pair, skipping."
            )
            return

        source = iface
        new_vlan = getattr(source, "untagged_vlan", None)
        if new_vlan is None:
            self.logger.info(
                f"[SyncSocketVlanToSwitch] Source interface {source} in direction {direction} "
                f"has no untagged VLAN, nothing to synchronize."
            )
            return

        changes_made = False

        # Ensure Socket has this VLAN
        if getattr(socket_iface, "untagged_vlan_id", None) != new_vlan.id:
            self.logger.info(
                f"[SyncSocketVlanToSwitch] {direction}: setting untagged VLAN on Socket {socket_iface} "
                f"to {new_vlan} (from {source})."
            )
            socket_iface.untagged_vlan = new_vlan
            socket_iface.save()
            changes_made = True
        else:
            self.logger.info(
                f"[SyncSocketVlanToSwitch] Socket {socket_iface} already has VLAN {new_vlan}, "
                f"no change on Socket side."
            )

        # Ensure Switch has this VLAN
        if getattr(switch_iface, "untagged_vlan_id", None) != new_vlan.id:
            self.logger.info(
                f"[SyncSocketVlanToSwitch] {direction}: setting untagged VLAN on Switch {switch_iface} "
                f"to {new_vlan} (from {source})."
            )
            switch_iface.untagged_vlan = new_vlan
            switch_iface.save()
            changes_made = True
        else:
            self.logger.info(
                f"[SyncSocketVlanToSwitch] Switch {switch_iface} already has VLAN {new_vlan}, "
                f"no change on Switch side."
            )

        if not changes_made:
            self.logger.info(
                f"[SyncSocketVlanToSwitch] SoT already in sync for Socket {socket_iface} "
                f"and Switch {switch_iface}, not triggering pipeline."
            )
            return

        self.logger.info(
            f"[SyncSocketVlanToSwitch] SoT sync complete for Socket {socket_iface} "
            f"and Switch {switch_iface}, triggering ConfigPipeline."
        )

        pipeline_job = ConfigPipeline()
        pipeline_job.logger = self.logger
        pipeline_job.run(
            device=switch_iface.device,
            interface=switch_iface,
            vlan=new_vlan,
        )


register_jobs(SyncSocketVlanToSwitch)
