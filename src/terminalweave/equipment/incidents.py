from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from terminalweave.core.clock import ensure_utc
from terminalweave.core.errors import ValidationError

@dataclass(frozen=True)
class EquipmentIncident:
    incident_id: str
    equipment_id: str
    at: datetime
    severity: int
    category: str
    lost_time_minutes: int = 0

    def __post_init__(self):
        object.__setattr__(self, 'at', ensure_utc(self.at))
        if not 1 <= self.severity <= 5 or self.lost_time_minutes < 0:
            raise ValidationError('invalid incident')

class IncidentBook:

    def __init__(self):
        self.items: dict[str, EquipmentIncident] = {}
        self._resolved: set[str] = set()

    def add(self, i: EquipmentIncident) -> None:
        if i.incident_id in self.items:
            raise ValidationError('duplicate incident')
        self.items[i.incident_id] = i

    def resolve(self, incident_id: str) -> EquipmentIncident:
        incident = self.items[incident_id]
        self._resolved.add(incident_id)
        return incident

    def is_resolved(self, incident_id: str) -> bool:
        return incident_id in self._resolved

    def unresolved(self, equipment_id: str | None=None) -> tuple[EquipmentIncident, ...]:
        rows = [i for i in self.items.values() if i.incident_id not in self._resolved and (equipment_id is None or i.equipment_id == equipment_id)]
        return tuple(sorted(rows, key=lambda x: x.at))

    def unresolved_severe(self, equipment_id: str, threshold: int=4) -> bool:
        return any((i.severity >= threshold for i in self.unresolved(equipment_id)))

    def frequency(self, equipment_id: str) -> int:
        return sum((i.equipment_id == equipment_id for i in self.items.values()))

    def lost_time(self, equipment_id: str | None=None) -> int:
        return sum((i.lost_time_minutes for i in self.items.values() if equipment_id is None or i.equipment_id == equipment_id))

    def severe(self, threshold: int=4) -> tuple[EquipmentIncident, ...]:
        return tuple(sorted((i for i in self.items.values() if i.severity >= threshold), key=lambda x: (-x.severity, x.at)))
