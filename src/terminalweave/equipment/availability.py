from __future__ import annotations
from dataclasses import dataclass
from terminalweave.core.errors import ValidationError
from terminalweave.equipment.dispatch import EquipmentDispatcher
from terminalweave.equipment.fleet import EquipmentState, FleetRegistry
from terminalweave.equipment.incidents import IncidentBook
from terminalweave.equipment.maintenance import MaintenanceManager

@dataclass(frozen=True)
class AvailabilityStatus:
    equipment_id: str
    dispatchable: bool
    maintenance_due: bool
    out_of_service: bool
    reasons: tuple[str, ...]

class EquipmentAvailability:
    """Projects meters, failures and maintenance state into one availability
    version and synchronises it to the dispatcher.

    The projection is the single source of truth for dispatch gating: a unit
    is dispatchable only when no source reports a reason to hold it. Syncing
    never reassigns started tasks — units already working keep their jobs,
    the refreshed flags only gate future dispatch decisions.
    """

    def __init__(self, maintenance: MaintenanceManager, incidents: IncidentBook | None=None, fleet: FleetRegistry | None=None, severe_threshold: int=4):
        if not 1 <= severe_threshold <= 5:
            raise ValidationError('severe threshold must be 1..5')
        self._maintenance = maintenance
        self._incidents = incidents
        self._fleet = fleet
        self._severe_threshold = severe_threshold

    def status(self, equipment_id: str) -> AvailabilityStatus:
        reasons = []
        maintenance_due = self._maintenance.needs_service(equipment_id)
        if maintenance_due:
            reasons.append('maintenance_due')
        out_of_service = False
        if self._fleet is not None:
            item = self._fleet.items.get(equipment_id)
            if item is not None and item.state in {EquipmentState.MAINTENANCE, EquipmentState.OUT_OF_SERVICE}:
                out_of_service = True
                reasons.append(item.state.value)
        if self._incidents is not None and self._incidents.unresolved_severe(equipment_id, self._severe_threshold):
            out_of_service = True
            reasons.append('severe_incident')
        return AvailabilityStatus(equipment_id, not reasons, maintenance_due, out_of_service, tuple(reasons))

    def sync_dispatcher(self, dispatcher: EquipmentDispatcher) -> dict[str, AvailabilityStatus]:
        statuses = {}
        for equipment_id in sorted(dispatcher.units):
            status = self.status(equipment_id)
            dispatcher.sync_unit(equipment_id, available=status.dispatchable, maintenance_due=status.maintenance_due)
            statuses[equipment_id] = status
        return statuses
