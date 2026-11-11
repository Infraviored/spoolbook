"""Which AMS slots a print uses and how much they used so far (pure logic, no HA imports).

Display only: nothing here is written to the cloud. The booking after the
print (usage.py) stays the single source of truth; the numbers here make the
tiles/sensors sink while the print runs.

Per printer serial the tracker knows

    running   a print is in progress (status running / pause)
    active    (ams_id, slot_id) the printer reports as active right now
    used      slots that were active at some point of this print (ordered)
    progress  last progress (percent) already attributed
    last_seen (percent, epoch seconds) of the last progress reading; kept after the
              print ended (until the next begin/forget) so a cancelled job can be
              booked proportionally, also across a restart

and per slot ``(serial, ams_id, slot_id)`` a record

    g         grams used so far (job grams x progress, attributed to the slot
              that was active while the progress advanced)
    base      remaining grams of the bound spool when the slot was first used
    net_end   remaining grams when the print ended (None while printing)
    ended     epoch seconds of the print end (None while printing)

The cloud library may lower a spool's weight by itself during the print (AMS
percent sync of RFID spools) and the booking lowers it after the print. Both
are subtracted again from ``g`` (see ``live_g``), so nothing is counted twice.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import re
import time
from typing import Any

Slot = tuple[int, int]  # (ams_id, slot_id), 0-based like the cloud
Key = tuple[str, int, int]  # (printer serial, ams_id, slot_id)

# Seconds a finished print's numbers stay while the booking is still to come.
SETTLE_S = 900
NO_TRAY = 254  # printer reports "no tray active"
# Slack (seconds) between the cloud's task times and our own clock when deciding
# whether a progress reading belongs to a task.
TASK_SLACK_S = 900

_PLAN_AMS_RE = re.compile(r"^AMS (\d+) Tray (\d+)$")
_PLAN_HT_RE = re.compile(r"^AMS HT (\d+)$")


def active_slot(attrs: Mapping[str, Any] | None) -> Slot | None:
    """(ams_id, slot_id) from the printer's active-tray sensor attributes."""
    if not attrs or "ams_index" not in attrs or "tray_index" not in attrs:
        return None
    try:
        ams, tray = int(attrs["ams_index"]), int(attrs["tray_index"])
    except (TypeError, ValueError):
        return None
    if ams == NO_TRAY or ams < 0 or tray < 0:
        return None
    return ams, tray


def parse_plan(attrs: Mapping[str, Any] | None) -> dict[Slot, float]:
    """Planned grams per slot from the print weight sensor's attributes.

    ha-bambulab names them "AMS 2 Tray 3" / "AMS HT 1" (1-based).
    """
    plan: dict[Slot, float] = {}
    for name, value in (attrs or {}).items():
        try:
            grams = float(value)
        except (TypeError, ValueError):
            continue
        if grams <= 0:
            continue
        if m := _PLAN_AMS_RE.match(str(name)):
            plan[(int(m.group(1)) - 1, int(m.group(2)) - 1)] = grams
        elif m := _PLAN_HT_RE.match(str(name)):
            plan[(127 + int(m.group(1)), 0)] = grams
    return plan


def pick_progress(
    last: tuple[float, float] | None,
    task_start: float | None,
    task_end: float | None,
    slack: float = TASK_SLACK_S,
) -> float | None:
    """Printed share (0..1) of a cancelled job from the last progress reading, or None.

    ``last`` = (percent, epoch seen). The reading belongs to the task when it was
    taken no earlier than the task's start and no later than its end (both with
    ``slack``). It may be long before the end: if the print ended while Home
    Assistant was down, the last reading before the outage is the best value
    there is, so there is deliberately no "must be close to the end" rule.
    """
    if last is None:
        return None
    percent, when = last
    if task_end is not None and when > task_end + slack:
        return None
    if task_start is not None and when < task_start - slack:
        return None
    return max(0.0, min(1.0, float(percent) / 100))


class LivePrint:
    """Per-printer print tracking; the Home Assistant wiring lives in live_monitor.py."""

    def __init__(
        self,
        net_of: Callable[[Key], float | None] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._net_of = net_of or (lambda _key: None)
        self._clock = clock
        self.running: dict[str, bool] = {}
        self.active: dict[str, Slot | None] = {}
        self.used: dict[str, list[Slot]] = {}
        self.progress_at: dict[str, float] = {}
        self.plan: dict[str, dict[Slot, float]] = {}
        self.rec: dict[Key, dict[str, Any]] = {}
        self.last_seen: dict[str, tuple[float, float]] = {}

    # ------------------------------------------------------------ lifecycle

    def begin(self, serial: str, active: Slot | None, progress: float = 0.0) -> None:
        """A print starts: forget the previous job of this printer, seed the active slot."""
        self.forget(serial)
        self.running[serial] = True
        self.progress_at[serial] = max(0.0, float(progress or 0))
        self.active[serial] = None
        self.used[serial] = []
        self.last_seen[serial] = (self.progress_at[serial], self._clock())
        self.set_active(serial, active)

    def forget(self, serial: str) -> None:
        for key in [k for k in self.rec if k[0] == serial]:
            del self.rec[key]
        self.used.pop(serial, None)
        self.plan.pop(serial, None)
        self.running[serial] = False
        self.progress_at.pop(serial, None)
        self.last_seen.pop(serial, None)

    def last_progress(self, serial: str) -> tuple[float, float] | None:
        """(percent, epoch seen) of the last progress reading of this printer's latest print."""
        return self.last_seen.get(serial)

    def end(self, serial: str) -> None:
        """The print is over: numbers stay until the booking happened (``settle``)."""
        if not self.running.get(serial):
            return
        self.running[serial] = False
        now = self._clock()
        for key, rec in self.rec.items():
            if key[0] == serial:
                rec["ended"] = now
                rec["net_end"] = self._net_of(key)

    def set_active(self, serial: str, slot: Slot | None) -> None:
        """The printer switched to ``slot`` (None = none). Recorded while a print runs."""
        self.active[serial] = slot
        if slot is None or not self.running.get(serial):
            return
        used = self.used.setdefault(serial, [])
        if slot not in used:
            used.append(slot)
        self._rec((serial, *slot))

    def _rec(self, key: Key) -> dict[str, Any]:
        rec = self.rec.get(key)
        if rec is None:
            rec = self.rec[key] = {"g": 0.0, "base": self._net_of(key), "net_end": None, "ended": None}
        return rec

    # ----------------------------------------------------------- consumption

    def set_plan(self, serial: str, plan: Mapping[Slot, float] | None) -> None:
        if plan:
            self.plan[serial] = dict(plan)

    def progress(self, serial: str, percent: float | None, job_grams: float | None) -> bool:
        """Progress advanced: attribute the grams since the last call to the active slot.

        Returns True when something changed. Progress going backwards means a
        new job started without a visible status change: the counters restart.
        """
        if not self.running.get(serial) or percent is None:
            return False
        percent = max(0.0, min(100.0, float(percent)))
        last = self.progress_at.get(serial, 0.0)
        if percent < last:
            slot = self.active.get(serial)
            self.begin(serial, slot, percent)
            return True
        self.last_seen[serial] = (percent, self._clock())
        if percent == last:
            return False
        slot = self.active.get(serial)
        if slot is None or not job_grams or job_grams <= 0:
            # Nothing to attribute to (yet): keep `last`, the next call catches up.
            return False
        rec = self._rec((serial, *slot))
        rec["g"] += float(job_grams) * (percent - last) / 100.0
        planned = self.plan.get(serial, {}).get(slot)
        if planned:
            rec["g"] = min(rec["g"], planned)
        self.progress_at[serial] = percent
        return True

    # --------------------------------------------------------------- reading

    def live_g(self, key: Key, net: float | None = None) -> float:
        """Grams used so far that the spool's current weight does not show yet."""
        rec = self.rec.get(key)
        if not rec:
            return 0.0
        grams = float(rec["g"])
        base = rec.get("base")
        if net is not None and base is not None:
            grams -= max(0.0, float(base) - float(net))
        return round(max(0.0, grams), 1)

    def used_in_print(self, key: Key) -> bool:
        return (key[1], key[2]) in self.used.get(key[0], [])

    def used_slots(self, serial: str) -> list[Slot]:
        return list(self.used.get(serial, []))

    def settle(self, nets: Mapping[Key, float | None]) -> bool:
        """Drop the numbers of finished prints once the booking showed up (or too late).

        ``nets`` = current remaining grams per slot. A finished slot is released
        when its spool lost weight after the end, or SETTLE_S passed. The list of
        used slots stays until the next print starts.
        """
        now = self._clock()
        changed = False
        for key, rec in list(self.rec.items()):
            ended = rec.get("ended")
            if ended is None:
                continue
            net, net_end = nets.get(key), rec.get("net_end")
            dropped = net is not None and net_end is not None and net < float(net_end) - 0.5
            if dropped or now - ended > SETTLE_S:
                del self.rec[key]
                changed = True
        return changed

    # ----------------------------------------------------------- persistence

    def dump(self) -> dict[str, Any]:
        return {
            "running": {s: True for s, v in self.running.items() if v},
            "active": {s: list(a) for s, a in self.active.items() if a},
            "used": {s: [list(x) for x in u] for s, u in self.used.items()},
            "progress": dict(self.progress_at),
            "plan": {s: {f"{a}:{b}": g for (a, b), g in p.items()} for s, p in self.plan.items()},
            "last_seen": {s: list(v) for s, v in self.last_seen.items()},
            "rec": {f"{k[0]}|{k[1]}|{k[2]}": v for k, v in self.rec.items()},
        }

    def load(self, data: Mapping[str, Any] | None) -> None:
        if not data:
            return
        try:
            running = {s: True for s in data.get("running", {})}
            active = {s: (int(a[0]), int(a[1])) for s, a in data.get("active", {}).items()}
            used = {s: [(int(x[0]), int(x[1])) for x in u] for s, u in data.get("used", {}).items()}
            progress = {s: float(p) for s, p in data.get("progress", {}).items()}
            plan = {
                s: {(int(k.split(":")[0]), int(k.split(":")[1])): float(g) for k, g in p.items()}
                for s, p in data.get("plan", {}).items()
            }
            last_seen = {
                s: (float(v[0]), float(v[1])) for s, v in (data.get("last_seen") or {}).items()
            }
            rec: dict[Key, dict[str, Any]] = {}
            for k, v in data.get("rec", {}).items():
                serial, ams, slot = k.split("|")
                rec[(serial, int(ams), int(slot))] = {
                    "g": float(v.get("g") or 0),
                    "base": v.get("base"),
                    "net_end": v.get("net_end"),
                    "ended": v.get("ended"),
                }
        except (TypeError, ValueError, IndexError, KeyError, AttributeError):
            return
        self.running, self.active, self.used = running, active, used
        self.progress_at, self.plan, self.rec = progress, plan, rec
        self.last_seen = last_seen
