from datetime import datetime, timedelta, timezone

from terminalweave.core.errors import CapacityError, ConflictError, ValidationError
from terminalweave.equipment.availability import EquipmentAvailability
from terminalweave.equipment.dispatch import (
    DispatchJob,
    DispatchUnit,
    EquipmentDispatcher,
    Position,
)
from terminalweave.equipment.incidents import EquipmentIncident, IncidentBook
from terminalweave.equipment.maintenance import (
    EquipmentMeter,
    MaintenanceManager,
    MaintenancePlan,
)
import pytest

UTC = timezone.utc
D = datetime(2026, 1, 1, tzinfo=UTC)


def test_overdue_equipment_is_not_dispatched_to_new_work():
    dispatcher = EquipmentDispatcher(
        [DispatchUnit("E1", "tractor", 40.0, Position(0.0, 0.0), maintenance_due=True)]
    )
    job = DispatchJob("J1", "tractor", 20.0, Position(1.0, 0.0), Position(2.0, 0.0))
    with pytest.raises(CapacityError):
        dispatcher.dispatch(job)
    assert dispatcher.busy == set()


def test_late_and_duplicate_readings_do_not_roll_back_state():
    meter = EquipmentMeter("E", 100.0, D)
    assert meter.update(120.0, D + timedelta(hours=1)) == 20.0
    assert meter.update(120.0, D + timedelta(hours=1)) == 0.0
    assert meter.update(90.0, D + timedelta(minutes=30)) == 0.0
    assert meter.hours == 120.0
    assert meter.last_updated == D + timedelta(hours=1)
    with pytest.raises(ValidationError):
        meter.update(80.0, D + timedelta(hours=2))


def test_backfilled_meter_readings_do_not_duplicate_work_orders():
    mgr = MaintenanceManager()
    mgr.register_plan(MaintenancePlan("P", 100))
    meter = EquipmentMeter("E", 105.0, D)
    mgr.register_meter(meter)
    first = mgr.open_due_orders(D)
    assert len(first) == 1 and first[0].due_at_meter == 100
    mgr.start(first[0].order_id, D)
    mgr.complete(first[0].order_id, D + timedelta(hours=1))
    meter.update(210.0, D + timedelta(hours=2))
    second = mgr.open_due_orders(D + timedelta(hours=2))
    assert len(second) == 1 and second[0].due_at_meter == 205
    mgr.start(second[0].order_id, D + timedelta(hours=2))
    # A backfilled registration must not roll the canonical meter back.
    mgr.register_meter(EquipmentMeter("E", 40.0, D))
    assert meter.hours == 210.0
    mgr.complete(second[0].order_id, D + timedelta(hours=3))
    # Replayed readings must not move the meter either.
    assert meter.update(150.0, D + timedelta(minutes=30)) == 0.0
    assert meter.update(210.0, D + timedelta(hours=2)) == 0.0
    assert meter.hours == 210.0
    # Serviced thresholds never generate another order.
    assert mgr.open_due_orders(D + timedelta(hours=4)) == []
    meter.update(260.0, D + timedelta(hours=5))
    assert mgr.open_due_orders(D + timedelta(hours=5)) == []
    meter.update(315.0, D + timedelta(hours=6))
    third = mgr.open_due_orders(D + timedelta(hours=6))
    assert len(third) == 1 and third[0].due_at_meter == 310


def test_started_job_is_not_silently_reassigned():
    dispatcher = EquipmentDispatcher(
        [
            DispatchUnit("E1", "tractor", 40.0, Position(0.0, 0.0)),
            DispatchUnit("E2", "tractor", 40.0, Position(5.0, 0.0)),
        ]
    )
    job = DispatchJob("J1", "tractor", 20.0, Position(1.0, 0.0), Position(2.0, 0.0))
    assert dispatcher.dispatch(job).equipment_id == "E1"
    with pytest.raises(ConflictError):
        dispatcher.dispatch(job)
    assert dispatcher.assignment("J1") == "E1"
    # Availability sync must not disturb the in-flight assignment.
    dispatcher.sync_unit("E1", maintenance_due=True)
    assert dispatcher.assignment("J1") == "E1"
    assert dispatcher.busy == {"E1"}
    dispatcher.release("E1", Position(2.0, 0.0))
    assert dispatcher.assignment("J1") is None
    # After release the maintenance flag still gates new work for E1.
    other = DispatchJob("J2", "tractor", 20.0, Position(2.0, 0.0), Position(3.0, 0.0))
    assert dispatcher.dispatch(other).equipment_id == "E2"


def test_availability_sync_uses_one_version_for_meters_failures_and_maintenance():
    mgr = MaintenanceManager()
    mgr.register_plan(MaintenancePlan("P", 100))
    mgr.register_meter(EquipmentMeter("E1", 150.0, D))
    mgr.register_meter(EquipmentMeter("E2", 10.0, D))
    incidents = IncidentBook()
    incidents.add(EquipmentIncident("I1", "E3", D, 5, "collision"))
    dispatcher = EquipmentDispatcher(
        [
            DispatchUnit("E1", "tractor", 40.0, Position(0.0, 0.0)),
            DispatchUnit("E2", "tractor", 40.0, Position(0.0, 0.0)),
            DispatchUnit("E3", "tractor", 40.0, Position(0.0, 0.0)),
        ]
    )
    availability = EquipmentAvailability(mgr, incidents)
    statuses = availability.sync_dispatcher(dispatcher)
    assert not statuses["E1"].dispatchable
    assert statuses["E2"].dispatchable
    assert not statuses["E3"].dispatchable
    job = DispatchJob("J1", "tractor", 20.0, Position(1.0, 0.0), Position(2.0, 0.0))
    assert dispatcher.dispatch(job).equipment_id == "E2"
    # Completing the service and resolving the failure restores availability.
    order = mgr.open_due_orders(D)[0]
    mgr.start(order.order_id, D)
    mgr.complete(order.order_id, D + timedelta(hours=1))
    incidents.resolve("I1")
    statuses = availability.sync_dispatcher(dispatcher)
    assert statuses["E1"].dispatchable
    assert statuses["E3"].dispatchable
    # The in-flight task on E2 was never reassigned by the syncs.
    assert dispatcher.assignment("J1") == "E2"
