from datetime import datetime, timedelta, timezone

from terminalweave.core.errors import CapacityError, StateTransitionError, ValidationError
from terminalweave.equipment.availability import AvailabilitySync
from terminalweave.equipment.dispatch import (
    DispatchJob,
    DispatchUnit,
    EquipmentDispatcher,
    Position,
)
from terminalweave.equipment.fleet import Equipment, EquipmentState, FleetRegistry
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


def test_late_and_duplicate_readings_do_not_regress_meter_or_respawn_orders():
    mgr = MaintenanceManager()
    mgr.register_plan(MaintenancePlan("P", 100))
    meter = EquipmentMeter("E", 0.0, D)
    mgr.register_meter(meter)
    meter.update(105.0, D + timedelta(hours=2))
    (order,) = mgr.open_due_orders(D + timedelta(hours=2))
    mgr.start(order.order_id, D + timedelta(hours=3))
    # a backfilled reading arrives while the work order is in progress
    mgr.register_meter(EquipmentMeter("E", 40.0, D + timedelta(hours=1)))
    mgr.complete(order.order_id, D + timedelta(hours=4))
    assert meter.hours == 105.0
    assert mgr.due_hours("E", "P") == 205.0
    # late and duplicate readings are absorbed without moving the meter
    assert meter.update(150.0, D + timedelta(hours=1)) == 0.0
    assert meter.update(105.0, D + timedelta(hours=2)) == 0.0
    assert meter.hours == 105.0
    assert meter.last_updated == D + timedelta(hours=2)
    # the serviced interval must not generate another work order
    assert mgr.open_due_orders(D + timedelta(hours=5)) == []
    # the next interval produces exactly one order, never a duplicate
    meter.update(206.0, D + timedelta(hours=6))
    (next_order,) = mgr.open_due_orders(D + timedelta(hours=6))
    assert next_order.due_at_meter == 205.0
    assert mgr.open_due_orders(D + timedelta(hours=7)) == []


def test_conflicting_or_backwards_readings_are_rejected():
    meter = EquipmentMeter("E", 100.0, D)
    with pytest.raises(ValidationError):
        meter.update(120.0, D)
    with pytest.raises(ValidationError):
        meter.update(90.0, D + timedelta(hours=1))
    assert meter.hours == 100.0
    assert meter.last_updated == D


def test_sync_preserves_started_jobs_and_blocks_new_dispatch():
    dispatcher = EquipmentDispatcher(
        [DispatchUnit("E1", "tractor", 40.0, Position(0.0, 0.0))]
    )
    job = DispatchJob("J1", "tractor", 20.0, Position(1.0, 0.0), Position(2.0, 0.0))
    assert dispatcher.dispatch(job).equipment_id == "E1"
    # availability sync flags the unit for maintenance while it is working
    dispatcher.sync_unit(
        DispatchUnit("E1", "tractor", 40.0, Position(0.0, 0.0), available=False, maintenance_due=True)
    )
    # the started job is not silently reassigned: the unit stays busy
    assert dispatcher.busy == {"E1"}
    with pytest.raises(CapacityError):
        dispatcher.dispatch(DispatchJob("J2", "tractor", 20.0, Position(1.0, 0.0), Position(2.0, 0.0)))
    # releasing a unit with no active job is an explicit error
    with pytest.raises(StateTransitionError):
        dispatcher.release("E9", Position(0.0, 0.0))
    dispatcher.release("E1", Position(5.0, 5.0))
    assert dispatcher.busy == set()
    # the synced maintenance flag still keeps it out of the pool
    with pytest.raises(CapacityError):
        dispatcher.dispatch(DispatchJob("J3", "tractor", 20.0, Position(1.0, 0.0), Position(2.0, 0.0)))
    # once maintenance clears, a fresh sync makes it dispatchable again
    dispatcher.sync_unit(DispatchUnit("E1", "tractor", 40.0, Position(5.0, 5.0)))
    assert dispatcher.dispatch(DispatchJob("J4", "tractor", 20.0, Position(6.0, 5.0), Position(7.0, 5.0))).equipment_id == "E1"


def test_availability_sync_projects_fleet_and_maintenance_state():
    fleet = FleetRegistry()
    fleet.add(Equipment("E1", "tractor", 40.0))
    fleet.add(Equipment("E2", "tractor", 40.0))
    mgr = MaintenanceManager()
    mgr.register_plan(MaintenancePlan("P", 100))
    mgr.register_meter(EquipmentMeter("E1", 50.0, D))
    mgr.register_meter(EquipmentMeter("E2", 150.0, D))
    dispatcher = EquipmentDispatcher(
        [
            DispatchUnit("E1", "tractor", 40.0, Position(2.0, 0.0)),
            DispatchUnit("E2", "tractor", 40.0, Position(0.5, 0.0)),
            DispatchUnit("E9", "tractor", 40.0, Position(0.2, 0.0)),
        ]
    )
    sync = AvailabilitySync(fleet, mgr)
    sync.refresh(dispatcher)
    # E2 is meter-due and E9 is unknown to the fleet: both fail closed
    assert dispatcher.units["E2"].maintenance_due
    assert not dispatcher.units["E9"].available
    job = DispatchJob("J1", "tractor", 20.0, Position(1.0, 0.0), Position(2.0, 0.0))
    assert dispatcher.dispatch(job).equipment_id == "E1"
    # a fault takes E1 out of service; the next refresh reflects it
    fleet.transition("E1", EquipmentState.OUT_OF_SERVICE)
    sync.refresh(dispatcher)
    assert not dispatcher.units["E1"].available
    assert dispatcher.busy == {"E1"}
