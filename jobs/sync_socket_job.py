# sync_socket_job.py
# 
# This job hook automatically keeps the VLAN configuration synchronized between
# a "Socket" interface (customer-facing port) and its connected "Switch" interface.
# 
# How it works:
# - Triggers whenever an Interface object is updated in Nautobot
# - Checks if the interface is part of a Socket<->Switch connection
# - Syncs the untagged VLAN in both directions (bidirectional sync)
# - Only triggers the config pipeline if something actually changed
# 
# Why we need this:
# When a customer changes their socket VLAN, we want the switch port to match automatically.
# This ensures the source of truth (Nautobot) stays consistent before we push config.

from nautobot.apps.jobs import JobHookReceiver, register_jobs
from nautobot.dcim.models import Interface

from .config_pipeline_job import ConfigPipeline

# This groups all related jobs together in the Nautobot UI
name = "00_Vlan-Change-Jobs"


class SyncSocketVlanToSwitch(JobHookReceiver):
    """
    JobHook receiver that synchronizes untagged VLANs between Socket and Switch interfaces.
    
    This job is triggered automatically whenever an Interface is updated in Nautobot.
    It ensures that when someone changes the VLAN on a Socket, the connected Switch port
    gets updated too (and vice versa), then triggers the config pipeline to push changes.
    """

    class Meta:
        # Name shown in the Nautobot job list
        name = "99_Sync Socket VLAN to Switch"
        
        # Description helps other users understand what this does
        description = "Synchronize untagged VLAN between Socket and Switch and trigger config pipeline."
        
        # This job modifies the database (saves interfaces), so we commit by default
        commit_default = True

    def receive_job_hook(self, change, action, changed_object):
        """
        Main entry point - called automatically when any model object changes.
        
        Args:
            change: Information about what changed (fields that were modified)
            action: The type of change ('create', 'update', 'delete')
            changed_object: The actual object that was modified
        """
        
        # We only care about updates, not creates or deletes
        # Creates don't have a previous state to compare, deletes are being removed anyway
        if action != "update":
            self.logger.debug(
                f"[SyncSocketVlanToSwitch] Ignoring action '{action}' - we only handle 'update' actions."
            )
            return

        # Make sure we're actually dealing with an Interface object
        # JobHooks can be triggered by any model, so we need to filter
        if not isinstance(changed_object, Interface):
            self.logger.debug(
                f"[SyncSocketVlanToSwitch] Changed object is not an Interface "
                f"(got {type(changed_object).__name__}), skipping this hook."
            )
            return

        # Now we know it's an Interface update, let's work with it
        iface: Interface = changed_object

        # Get the device that owns this interface
        # We need this to check the device role (Socket vs Switch vs something else)
        device = getattr(iface, "device", None)
        role = getattr(device, "role", None)
        role_name = getattr(role, "name", None)

        # Find out what this interface is connected to
        # In Nautobot, interfaces can be cabled to other interfaces, patch panels, etc.
        peer = getattr(iface, "connected_endpoint", None)
        
        if peer is None:
            # No cable connection = nothing to sync
            self.logger.info(
                f"[SyncSocketVlanToSwitch] Interface {iface} has no cable connection, "
                f"nothing to synchronize."
            )
            return

        # Handle patch panel scenarios (front port / rear port)
        # Sometimes a Socket connects to a patch panel, which connects to a Switch
        # In that case, peer is a FrontPort/RearPort, not an Interface
        # So we need to "hop" one more time to find the actual Switch interface
        if not isinstance(peer, Interface):
            self.logger.debug(
                f"[SyncSocketVlanToSwitch] First peer of {iface} is a {type(peer).__name__}, "
                f"not an Interface. Checking if there's a second hop..."
            )
            
            # Try to get the second-hop connection
            peer2 = getattr(peer, "connected_endpoint", None)
            
            if isinstance(peer2, Interface):
                # Found an Interface on the other side of the patch panel
                self.logger.debug(
                    f"[SyncSocketVlanToSwitch] Second-hop peer is {peer2} (an Interface), using that."
                )
                peer = peer2
            else:
                # Even the second hop isn't an Interface, can't sync
                self.logger.info(
                    f"[SyncSocketVlanToSwitch] Second-hop peer is also not an Interface, "
                    f"cannot sync (got {type(peer2).__name__ if peer2 else 'None'})."
                )
                return

        # Now we have two interfaces - let's get info about the peer's device
        peer_device = getattr(peer, "device", None)
        peer_role = getattr(peer_device, "role", None)
        peer_role_name = getattr(peer_role, "name", None)

        # Determine which interface is the Socket and which is the Switch
        # We need to know this because we're syncing Socket <-> Switch pairs specifically
        # This logic handles sync in both directions:
        # - User updates Socket -> we sync to Switch
        # - User updates Switch -> we sync to Socket
        if role_name == "Socket" and peer_role_name != "Socket":
            # The interface that changed is a Socket, connected to something that's not a Socket
            socket_iface = iface
            switch_iface = peer
            direction = "Socket -> Switch"
        elif role_name != "Socket" and peer_role_name == "Socket":
            # The interface that changed is not a Socket, but it's connected to a Socket
            socket_iface = peer
            switch_iface = iface
            direction = "Switch -> Socket"
        else:
            # Neither is a Socket, or both are Sockets - not a valid Socket<->Switch pair
            self.logger.info(
                f"[SyncSocketVlanToSwitch] Interface {iface} (role={role_name}) and "
                f"peer {peer} (role={peer_role_name}) do not form a Socket<->Switch pair. "
                f"Skipping sync."
            )
            return

        # Now we know which is which, let's get the VLAN we need to sync
        # We take the VLAN from whichever interface was just updated (the source)
        source = iface
        new_vlan = getattr(source, "untagged_vlan", None)
        
        if new_vlan is None:
            # No VLAN set on the source interface, nothing to sync
            self.logger.info(
                f"[SyncSocketVlanToSwitch] Source interface {source} (in direction {direction}) "
                f"has no untagged VLAN assigned, nothing to synchronize."
            )
            return

        # Track if we actually made any changes
        # We only want to trigger the pipeline if something changed
        changes_made = False

        # Sync to the Socket side
        # Check if the Socket already has the correct VLAN
        if getattr(socket_iface, "untagged_vlan_id", None) != new_vlan.id:
            # VLANs don't match, update the Socket
            self.logger.info(
                f"[SyncSocketVlanToSwitch] {direction}: Setting untagged VLAN on Socket "
                f"{socket_iface} to VLAN {new_vlan} (synced from {source})."
            )
            socket_iface.untagged_vlan = new_vlan
            socket_iface.save()
            changes_made = True
        else:
            # Socket already has the correct VLAN
            self.logger.info(
                f"[SyncSocketVlanToSwitch] Socket {socket_iface} already has VLAN {new_vlan}, "
                f"no update needed on Socket side."
            )

        # Sync to the Switch side
        # Check if the Switch already has the correct VLAN
        if getattr(switch_iface, "untagged_vlan_id", None) != new_vlan.id:
            # VLANs don't match, update the Switch
            self.logger.info(
                f"[SyncSocketVlanToSwitch] {direction}: Setting untagged VLAN on Switch "
                f"{switch_iface} to VLAN {new_vlan} (synced from {source})."
            )
            switch_iface.untagged_vlan = new_vlan
            switch_iface.save()
            changes_made = True
        else:
            # Switch already has the correct VLAN
            self.logger.info(
                f"[SyncSocketVlanToSwitch] Switch {switch_iface} already has VLAN {new_vlan}, "
                f"no update needed on Switch side."
            )

        # Check if we actually changed anything
        if not changes_made:
            # Both interfaces already had the correct VLAN, nothing to do
            self.logger.info(
                f"[SyncSocketVlanToSwitch] Both Socket {socket_iface} and Switch {switch_iface} "
                f"already had the correct VLAN. Source of truth is in sync, not triggering pipeline."
            )
            return

        # We made changes to the source of truth (Nautobot)
        # Now trigger the pipeline to backup, render intended config, and push to the device
        self.logger.info(
            f"[SyncSocketVlanToSwitch] Successfully synced VLAN {new_vlan} between "
            f"Socket {socket_iface} and Switch {switch_iface}. Triggering ConfigPipeline "
            f"to push changes to the physical device."
        )

        # Create an instance of the pipeline job
        pipeline_job = ConfigPipeline()
        
        # Share our logger so all pipeline logs appear in the same place
        pipeline_job.logger = self.logger
        
        # Run the pipeline with the switch device/interface and VLAN info
        # We pass the switch interface because that's what needs to be configured on the device
        pipeline_job.run(
            device=switch_iface.device,
            interface=switch_iface,
            vlan=new_vlan,
        )


# Register this job so Nautobot knows about it
register_jobs(SyncSocketVlanToSwitch)
