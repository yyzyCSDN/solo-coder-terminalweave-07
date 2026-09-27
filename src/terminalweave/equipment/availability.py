from __future__ import annotations
from dataclasses import replace
from terminalweave.equipment.dispatch import DispatchUnit, EquipmentDispatcher
from terminalweave.equipment.fleet import EquipmentState, FleetRegistry
from terminalweave.equipment.maintenance import MaintenanceManager

class AvailabilitySync:
    """Projects fleet state and maintenance status onto dispatch snapshots.

    Fleet state (working, charging, maintenance, out of service after a
    fault) and meter-driven maintenance status change independently of the
    dispatcher, whose units are frozen snapshots. Refreshing through this
    projection keeps dispatch decisions on one current version of equipment
    availability instead of a stale mix of the two. Units with a job in
    progress stay busy, so started work is never silently reassigned.
    """

    def __init__(self, fleet: FleetRegistry, maintenance: MaintenanceManager):
        self._fleet = fleet
        self._maintenance = maintenance

    def project(self, unit: DispatchUnit) -> DispatchUnit:
        item = self._fleet.items.get(unit.equipment_id)
        available = item is not None and item.state == EquipmentState.AVAILABLE
        return replace(unit, available=available, maintenance_due=self._maintenance.is_due_any(unit.equipment_id))

    def refresh(self, dispatcher: EquipmentDispatcher) -> None:
        for equipment_id in tuple(dispatcher.units):
            dispatcher.sync_unit(self.project(dispatcher.units[equipment_id]))
