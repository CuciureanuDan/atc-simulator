"""Stage 1 acceptance: three departures taxi, queue at the holding point and
take off one at a time."""
import math
from pathlib import Path

import pytest

from backend.aircraft import ON_GROUND, State
from backend.airport import Airport
from backend.simulation import Simulation

AIRPORT = Path(__file__).resolve().parent.parent / "backend" / "data" / "airport.json"
DT = 0.1


@pytest.fixture
def sim() -> Simulation:
    return Simulation(Airport.load(AIRPORT))


def run(sim: Simulation, seconds: float, check=None) -> None:
    for _ in range(int(seconds / DT)):
        sim.step(DT)
        if check:
            check(sim)
        if not sim.aircraft:
            return


def test_three_departures_share_runway_one_at_a_time(sim: Simulation) -> None:
    for _ in range(3):
        sim.spawn()

    max_queue = 0

    def check(s: Simulation) -> None:
        nonlocal max_queue
        on_runway = [a.callsign for a in s.aircraft.values() if a.occupies_runway]
        assert len(on_runway) <= 1, f"t={s.t:.1f}: runway shared by {on_runway}"
        max_queue = max(max_queue, len(s.tower.queue))

        ground = [a for a in s.aircraft.values() if a.state in ON_GROUND and a.state is not State.PARKED]
        for i, a in enumerate(ground):
            for b in ground[i + 1:]:
                d = math.hypot(a.x - b.x, a.y - b.y)
                assert d > 40, f"t={s.t:.1f}: {a.callsign} and {b.callsign} only {d:.0f} m apart"

    run(sim, 900, check)

    assert not sim.aircraft, f"still active: {[a.to_dict() for a in sim.aircraft.values()]}"
    assert max_queue >= 1, "expected at least one aircraft to wait at the holding point"
    takeoffs = [e["callsign"] for e in sim.events if "cleared for takeoff" in e["msg"]]
    assert len(takeoffs) == 3


def test_gate_reuse_and_validation(sim: Simulation) -> None:
    sim.spawn(gate="G1")
    with pytest.raises(ValueError):
        sim.spawn(gate="G1")
    with pytest.raises(ValueError):
        sim.spawn(gate="NOPE")
    sim.spawn(gate="G2")
    sim.spawn(gate="G3")
    with pytest.raises(ValueError):
        sim.spawn()
    run(sim, 60)
    sim.spawn(gate="G1")  # first aircraft has left the gate by now


def test_takeoff_profile(sim: Simulation) -> None:
    ac = sim.spawn(gate="G1")
    states = []

    def check(s: Simulation) -> None:
        if ac.callsign in s.aircraft and (not states or states[-1] != ac.state):
            states.append(ac.state)

    run(sim, 600, check)
    assert states == [State.PARKED, State.TAXIING, State.HOLDING_SHORT, State.LINE_UP,
                      State.TAKEOFF_ROLL, State.AIRBORNE]
