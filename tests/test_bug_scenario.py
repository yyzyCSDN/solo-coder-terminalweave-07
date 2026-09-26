from terminalweave.core.errors import CapacityError
from terminalweave.equipment.dispatch import (
    DispatchJob,
    DispatchUnit,
    EquipmentDispatcher,
    Position,
)
import pytest


def test_overdue_equipment_is_not_dispatched_to_new_work():
    dispatcher = EquipmentDispatcher(
        [DispatchUnit("E1", "tractor", 40.0, Position(0.0, 0.0), maintenance_due=True)]
    )
    job = DispatchJob("J1", "tractor", 20.0, Position(1.0, 0.0), Position(2.0, 0.0))
    with pytest.raises(CapacityError):
        dispatcher.dispatch(job)
    assert dispatcher.busy == set()
