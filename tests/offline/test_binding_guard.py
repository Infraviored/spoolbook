"""Offline tests of custom_components/spoolbook/binding_guard.py (pure logic, no HA needed).

Run: python3 tests/offline/test_binding_guard.py
Checks: remember a binding, restore after the cloud dropped it, brake (second drop within
the cooldown -> give up), swapped filament / emptied slot forget, printer offline keeps memory,
archived or elsewhere-bound spool is not restored.
"""
import importlib.util
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "binding_guard", ROOT / "custom_components" / "spoolbook" / "binding_guard.py"
)
bg = importlib.util.module_from_spec(spec)
import sys
sys.modules["binding_guard"] = bg
spec.loader.exec_module(bg)

T = {"entity_id": "sensor.ams_1_tray_1", "dev_id": "SER", "ams_id": 0, "slot_id": "0"}
PETG = ("PETG", {"filament_id": "P2f", "color": "#131313FF"})


def spool(bound=True, **kw):
    s = {"id": 7, "inPrinter": bound, "devId": "SER" if bound else "", "amsId": 0 if bound else None,
         "slotId": "0" if bound else ""}
    s.update(kw)
    return {7: s}


def run(mem, spools, attrs=PETG, now=0.0):
    return bg.plan(mem, [T], spools, lambda _e: attrs, now)


mem = {}
r = run(mem, spool())
assert mem[T["entity_id"]]["spool_id"] == 7 and r.changed and not r.restore

# cloud drops it -> restore
r = run(mem, spool(bound=False), now=100)
assert [s["id"] for _, s in r.restore] == [7] and not r.give_up
# next sync shows it bound again: memory keeps the restore time
r = run(mem, spool(), now=160)
assert mem[T["entity_id"]]["restored_at"] == 100
# dropped again within the hour -> give up, notify, forget
r = run(mem, spool(bound=False), now=1000)
assert not r.restore and [s["id"] for _, s in r.give_up] == [7] and T["entity_id"] not in mem

# after the cooldown a drop is restored again
mem = {}
run(mem, spool())
run(mem, spool(bound=False), now=0)
run(mem, spool(), now=10)
r = run(mem, spool(bound=False), now=10 + bg.RESTORE_COOLDOWN_S)
assert len(r.restore) == 1

# swapped filament (other colour) -> forget, no restore
mem = {}
run(mem, spool())
r = run(mem, spool(bound=False), attrs=("PETG", {"filament_id": "P2f", "color": "#FFFFFFFF"}))
assert not r.restore and T["entity_id"] not in mem

# emptied slot -> forget
mem = {}
run(mem, spool())
run(mem, spool(bound=False), attrs=("Empty", {}))
assert T["entity_id"] not in mem

# printer offline -> memory kept, nothing done
mem = {}
run(mem, spool())
r = run(mem, spool(bound=False), attrs=("unavailable", {}))
assert T["entity_id"] in mem and not r.restore

# archived spool or bound elsewhere -> no restore
for extra in ({"archived": True},):
    mem = {}
    run(mem, spool())
    r = run(mem, spool(bound=False, **extra))
    assert not r.restore
mem = {}
run(mem, spool())
other = spool(bound=False)
other[7].update(inPrinter=True, devId="SER", amsId=1, slotId="2")
r = run(mem, other)
assert not r.restore and T["entity_id"] not in mem

# spool deleted -> forget
mem = {}
run(mem, spool())
r = run(mem, {})
assert not r.restore and T["entity_id"] not in mem

print("binding_guard: all checks passed")
