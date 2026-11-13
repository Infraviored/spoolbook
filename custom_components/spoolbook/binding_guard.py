"""Restore slot bindings the Bambu cloud drops on its own.

Observed 2026-11-12 and -13: the cloud library cleared the binding of two
manual (non-RFID) spools while the printer kept reporting both slots
unchanged - same filament profile, same colour - and nothing on the Home
Assistant side had unassigned them. Studio/Handy syncing the AMS state is the
suspected trigger.

The guard remembers, per slot, the last bound spool together with the
filament profile and colour the printer reported for that slot. When a later
sync shows the slot still filled with that same filament but no spool bound,
the binding is restored (cloud only, the printer is not touched).

Brakes against fighting the cloud or the user:
- the slot must be filled and report the remembered profile and colour
  (a physically swapped spool is never re-bound);
- the spool must still exist, not be archived and not be bound elsewhere;
- at most one restore per slot per RESTORE_COOLDOWN_S; if the cloud drops the
  same binding again within that time the slot is given up and the user
  notified instead;
- unassigning a slot through Spoolbook forgets it (``forget``).

Pure logic: no Home Assistant imports, so it is unit-tested offline.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, MutableMapping
from dataclasses import dataclass, field
from typing import Any

RESTORE_COOLDOWN_S = 3600.0

NO_INFO = ("unknown", "unavailable")  # printer offline/restarting: keep memory
EMPTY = ("", "none", "empty")


def _hex6(value: Any) -> str:
    text = str(value or "").strip().lstrip("#").upper()
    return text[:6]


def _bound(spools: Iterable[Mapping[str, Any]], tray: Mapping[str, Any]) -> Mapping[str, Any] | None:
    for spool in spools:
        if not spool.get("inPrinter"):
            continue
        dev = spool.get("devId")
        if dev and dev != tray["dev_id"]:
            continue
        if str(spool.get("amsId")) == str(tray["ams_id"]) and str(spool.get("slotId")) == str(
            tray["slot_id"]
        ):
            return spool
    return None


@dataclass
class GuardResult:
    restore: list[tuple[dict[str, Any], Mapping[str, Any]]] = field(default_factory=list)
    give_up: list[tuple[dict[str, Any], Mapping[str, Any]]] = field(default_factory=list)
    changed: bool = False


def plan(
    memory: MutableMapping[str, dict[str, Any]],
    trays: Iterable[dict[str, Any]],
    spools: Mapping[int, Mapping[str, Any]],
    tray_attrs: Callable[[str], tuple[str | None, Mapping[str, Any]]],
    now: float,
) -> GuardResult:
    """Update ``memory`` (slot entity id -> remembered binding) and say what to do.

    ``tray_attrs(entity_id)`` returns (state, attributes) of the printer's tray sensor.
    """
    res = GuardResult()
    library = list(spools.values())
    for tray in trays:
        key = tray["entity_id"]
        state, attrs = tray_attrs(key)
        if state is None or str(state).lower() in NO_INFO:
            continue
        filled = str(state).lower() not in EMPTY
        profile = str(attrs.get("filament_id") or "")
        colour = _hex6(attrs.get("color"))
        mem = memory.get(key)
        if not filled:
            if mem is not None:
                del memory[key]
                res.changed = True
            continue
        bound = _bound(library, tray)
        if bound is not None:
            new = {
                "spool_id": int(bound["id"]),
                "filament_id": profile,
                "color": colour,
                "restored_at": (mem or {}).get("restored_at")
                if mem and mem.get("spool_id") == int(bound["id"])
                else None,
            }
            if new != mem:
                memory[key] = new
                res.changed = True
            continue
        if mem is None:
            continue
        spool = spools.get(int(mem["spool_id"]))
        same_filament = mem.get("filament_id") == profile and mem.get("color") == colour
        usable = (
            spool is not None
            and not spool.get("archived")
            and not spool.get("inPrinter")
        )
        if not (same_filament and usable):
            del memory[key]
            res.changed = True
            continue
        last = mem.get("restored_at")
        if last is not None and now - float(last) < RESTORE_COOLDOWN_S:
            res.give_up.append((tray, spool))
            del memory[key]
            res.changed = True
            continue
        mem["restored_at"] = now
        res.restore.append((tray, spool))
        res.changed = True
    return res
