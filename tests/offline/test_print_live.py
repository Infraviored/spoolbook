"""Offline tests of custom_components/spoolbook/print_live.py (pure logic, no HA needed).

Run: python3 tools/bambu/test_print_live.py
Checks: slots used per print, live grams attributed to the active slot, per-slot plan cap,
no double counting against a weight that dropped by itself (AMS sync) or by the booking,
settle after the print, progress reset, persistence round trip.
"""
import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "print_live", ROOT / "custom_components" / "spoolbook" / "print_live.py"
)
pl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pl)

S = "SER"
A, B = (1, 1), (0, 3)  # AMS 2 slot 2, AMS 1 slot 4


def key(slot):
    return (S, *slot)


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


def make(nets=None):
    nets = nets if nets is not None else {key(A): 899.0, key(B): 500.0}
    clock = Clock()
    return pl.LivePrint(net_of=lambda k: nets.get(k), clock=clock), nets, clock


def close(a, b, tol=0.05):
    return abs(a - b) <= tol


def test_helpers():
    assert pl.active_slot({"ams_index": 1, "tray_index": 1}) == (1, 1)
    assert pl.active_slot({"ams_index": "0", "tray_index": "3"}) == (0, 3)
    assert pl.active_slot({"ams_index": 254, "tray_index": 0}) is None
    assert pl.active_slot({"ams_index": 255, "tray_index": 3}) == (255, 3)  # external, no tray sensor
    assert pl.active_slot({}) is None and pl.active_slot(None) is None
    plan = pl.parse_plan({"AMS 2 Tray 2": 40.5, "AMS HT 1": "10", "friendly_name": "x", "AMS 1 Tray 1": 0})
    assert plan == {(1, 1): 40.5, (128, 0): 10.0}, plan


def test_used_trays_and_live_grams():
    lp, nets, _ = make()
    lp.begin(S, A, 0)
    assert lp.used_slots(S) == [A] and lp.used_in_print(key(A)) and not lp.used_in_print(key(B))
    lp.progress(S, 25, 80.0)  # 20 g on A
    assert close(lp.live_g(key(A), 899), 20.0)
    lp.set_active(S, B)  # switch (progress was fed before, as live_monitor does)
    lp.progress(S, 50, 80.0)  # 20 g on B
    lp.progress(S, 100, 80.0)  # 40 g more on B
    assert lp.used_slots(S) == [A, B]
    assert close(lp.live_g(key(A), 899), 20.0) and close(lp.live_g(key(B), 500), 60.0)
    lp.set_active(S, A)  # back to A: no duplicate in the used list
    assert lp.used_slots(S) == [A, B]
    # grams never go negative / exceed what the weight shows
    assert lp.live_g(key(B), 500) == 60.0 and lp.live_g(("x", 9, 9)) == 0.0


def test_no_attribution_without_active_or_weight():
    lp, _, _ = make()
    lp.begin(S, None, 0)
    assert not lp.progress(S, 10, 50.0)  # no active slot yet: nothing attributed, `last` kept
    lp.set_active(S, A)
    assert lp.progress(S, 10, 50.0) and close(lp.live_g(key(A)), 5.0)  # catches up
    assert not lp.progress(S, 20, None) and close(lp.live_g(key(A)), 5.0)
    assert not lp.progress("other", 20, 50.0)  # not running


def test_plan_caps_per_slot():
    lp, _, _ = make()
    lp.begin(S, A, 0)
    lp.set_plan(S, {A: 15.0})
    lp.progress(S, 50, 80.0)  # 40 g but the slicer planned 15 g for this slot
    assert close(lp.live_g(key(A)), 15.0)


def test_weight_dropped_by_itself_is_not_counted_twice():
    lp, nets, _ = make()
    lp.begin(S, A, 0)  # base = 899
    lp.progress(S, 50, 100.0)  # 50 g live
    assert close(lp.live_g(key(A), 899), 50.0)
    nets[key(A)] = 874.0  # AMS sync already lowered the cloud weight by 25 g
    assert close(lp.live_g(key(A), 874), 25.0)
    assert close(lp.live_g(key(A), 840), 0.0)  # lower than the estimate: never negative


def test_end_then_booking_releases():
    lp, nets, clock = make()
    lp.begin(S, A, 0)
    lp.progress(S, 100, 66.47)
    lp.end(S)
    assert not lp.running[S]
    assert not lp.progress(S, 100, 66.47)  # no longer running
    assert close(lp.live_g(key(A), 899), 66.5, 0.1) and lp.used_in_print(key(A))  # stays until booked
    assert not lp.settle({key(A): 899.0})  # not booked yet
    nets[key(A)] = 833.0  # booking arrived
    assert close(lp.live_g(key(A), 833), 0.0, 0.6)  # no double deduction (booking rounds to whole grams)
    assert lp.settle({key(A): 833.0})
    assert lp.live_g(key(A), 833) == 0.0
    assert lp.used_in_print(key(A))  # "used in this print" stays until the next print


def test_settle_timeout():
    lp, _, clock = make()
    lp.begin(S, A, 0)
    lp.progress(S, 10, 100.0)
    lp.end(S)
    clock.t += pl.SETTLE_S - 1
    assert not lp.settle({key(A): 899.0})
    clock.t += 2
    assert lp.settle({key(A): 899.0}) and lp.live_g(key(A), 899) == 0.0


def test_new_print_resets():
    lp, _, _ = make()
    lp.begin(S, A, 0)
    lp.progress(S, 60, 100.0)
    lp.end(S)
    lp.begin(S, B, 0)  # next job
    assert lp.used_slots(S) == [B] and lp.live_g(key(A)) == 0.0
    lp.progress(S, 30, 100.0)
    assert not lp.used_in_print(key(A)) and close(lp.live_g(key(B)), 30.0)
    # progress jumps back without a visible status change = new job
    assert lp.progress(S, 5, 100.0)
    assert lp.live_g(key(B)) == 0.0 and lp.used_slots(S) == [B]
    lp.progress(S, 15, 100.0)
    assert close(lp.live_g(key(B)), 10.0)


def test_persistence_round_trip():
    lp, nets, clock = make()
    lp.begin(S, A, 0)
    lp.set_plan(S, {A: 70.0})
    lp.progress(S, 40, 100.0)
    lp.set_active(S, B)
    lp.progress(S, 50, 100.0)
    data = lp.dump()
    import json

    data = json.loads(json.dumps(data))  # store goes through JSON
    lp2 = pl.LivePrint(net_of=lambda k: nets.get(k), clock=clock)
    lp2.load(data)
    assert lp2.running[S] and lp2.used_slots(S) == [A, B] and lp2.active[S] == B
    assert close(lp2.live_g(key(A), 899), 40.0) and close(lp2.live_g(key(B), 500), 10.0)
    lp2.progress(S, 60, 100.0)  # keeps attributing to B from where it stopped
    assert close(lp2.live_g(key(B), 500), 20.0)
    lp3 = pl.LivePrint()
    lp3.load({"rec": {"broken": 1}})  # garbage is ignored
    assert lp3.rec == {} and not lp3.running


def test_last_progress_survives_end_and_roundtrip():
    import json

    lp, nets, clock = make()
    lp.begin(S, A, 0)
    clock.t = 2000.0
    lp.progress(S, 30, 100.0)
    clock.t = 2500.0
    lp.progress(S, 45, 100.0)
    lp.end(S)
    assert lp.last_progress(S) == (45.0, 2500.0)  # kept after the end
    lp2 = pl.LivePrint(clock=clock)
    lp2.load(json.loads(json.dumps(lp.dump())))
    assert lp2.last_progress(S) == (45.0, 2500.0)
    lp2.begin(S, A, 0)  # next print replaces it
    assert lp2.last_progress(S) == (0.0, clock.t)
    lp2.forget(S)
    assert lp2.last_progress(S) is None
    assert lp2.last_progress("OTHER") is None


def test_load_old_format_without_last_seen():
    lp = pl.LivePrint()
    lp.load({"running": {S: True}, "progress": {S: 12.0}})
    assert lp.running[S] and lp.last_progress(S) is None


def test_pick_progress():
    pick = pl.pick_progress
    assert pick(None, 100, 200) is None
    assert pick((40.0, 190.0), 100, 200) == 0.4
    # print ended while HA was down: last reading long before the end is accepted
    assert pick((40.0, 150.0), 100, 20000) == 0.4
    # reading from before this task started (older print) is rejected
    assert pick((40.0, 100.0), 5000, 6000) is None
    # reading well after the end belongs to something else
    assert pick((40.0, 9000.0), 100, 200) is None
    # unknown task times: accept
    assert pick((120.0, 1.0), None, None) == 1.0


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
    print("all passed")
