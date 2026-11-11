"""Home Assistant wiring of print_live.LivePrint (display only, no cloud writes).

Listens to the Bambu Lab printer sensors (print status, progress, print weight,
active tray) and feeds the tracker. Job grams come from the printer integration's
"Print weight" sensor (total grams of the loaded job; its attributes carry the
planned grams per AMS slot), progress from "Print progress". The state is
persisted (HA storage) so a restart in the middle of a print keeps it.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store

from .const import BAMBULAB_DOMAIN, DOMAIN
from .print_live import Key, LivePrint, active_slot, parse_plan
from .trays import bound_spool, list_trays

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
SAVE_DELAY_S = 10
RUNNING_STATES = ("running", "pause")
_UID_RE = re.compile(r"^(?P<serial>[^_]+)_(?P<kind>print_status|print_progress|print_weight|active_tray)$")
_KINDS = {
    "print_status": "status",
    "print_progress": "progress",
    "print_weight": "weight",
    "active_tray": "active",
}


def tray_key(tray: Mapping[str, Any]) -> Key:
    return tray["dev_id"], int(tray["ams_id"]), int(tray["slot_id"])


def _float(state: Any) -> float | None:
    try:
        return float(state.state) if state is not None else None
    except (TypeError, ValueError):
        return None


class LiveMonitor:
    """Tracks used slots and live consumption for one config entry."""

    def __init__(self, hass: HomeAssistant, coordinator: Any, entry_id: str) -> None:
        self.hass = hass
        self.coordinator = coordinator
        self.state = LivePrint(net_of=self._net_of)
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.live_{entry_id}")
        self._ids: dict[str, tuple[str, str]] = {}
        self._unsubs: list[Any] = []

    # ---------------------------------------------------------------- reading

    def _net_of(self, key: Key, data: Mapping[int, Any] | None = None) -> float | None:
        spools = (self.coordinator.data if data is None else data or {}).values()
        for tray in list_trays(self.hass):
            if tray_key(tray) == key:
                spool = bound_spool(spools, tray)
                if spool is not None:
                    try:
                        return float(spool.get("netWeight") or 0)
                    except (TypeError, ValueError):
                        return None
                return None
        return None

    def nets(self, data: Mapping[int, Any] | None = None) -> dict[Key, float | None]:
        return {key: self._net_of(key, data) for key in list(self.state.rec)}

    def last_progress(self, serial: str) -> tuple[float, float] | None:
        """(percent, epoch seconds) of the last progress reading of the printer's latest print."""
        return self.state.last_progress(serial)

    def live_g(self, tray: Mapping[str, Any], net: float | None) -> float:
        return self.state.live_g(tray_key(tray), net)

    def used_in_print(self, tray: Mapping[str, Any]) -> bool:
        return self.state.used_in_print(tray_key(tray))

    # --------------------------------------------------------------- lifecycle

    async def async_start(self) -> None:
        self.state.load(await self._store.async_load())
        self._refresh_ids()
        self._unsubs.append(
            self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._handle, event_filter=self._filter)
        )
        self._unsubs.append(
            self.hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, self._registry_updated)
        )
        self.reconcile()

    @callback
    def async_stop(self) -> None:
        while self._unsubs:
            self._unsubs.pop()()
        self._store.async_delay_save(self.state.dump, 0)

    @callback
    def _refresh_ids(self) -> None:
        ids: dict[str, tuple[str, str]] = {}
        for entry in er.async_get(self.hass).entities.values():
            if entry.platform != BAMBULAB_DOMAIN or entry.domain != "sensor" or entry.disabled:
                continue
            if m := _UID_RE.match(entry.unique_id or ""):
                ids[entry.entity_id] = (m.group("serial"), _KINDS[m.group("kind")])
        self._ids = ids

    @callback
    def _registry_updated(self, _event: Any) -> None:
        self._refresh_ids()
        self.reconcile()

    @callback
    def _filter(self, event_data: Mapping[str, Any]) -> bool:
        return event_data.get("entity_id") in self._ids

    def _entity(self, serial: str, kind: str) -> str | None:
        return next((e for e, (s, k) in self._ids.items() if s == serial and k == kind), None)

    def _current(self, serial: str) -> tuple[float | None, float | None]:
        """(progress %, job grams) as the printer reports them now."""
        progress = _float(self.hass.states.get(self._entity(serial, "progress") or ""))
        weight = _float(self.hass.states.get(self._entity(serial, "weight") or ""))
        return progress, weight

    def _active(self, serial: str):
        state = self.hass.states.get(self._entity(serial, "active") or "")
        return active_slot(state.attributes) if state is not None else None

    def _plan(self, serial: str) -> None:
        state = self.hass.states.get(self._entity(serial, "weight") or "")
        if state is not None:
            self.state.set_plan(serial, parse_plan(state.attributes))

    @callback
    def reconcile(self) -> None:
        """Bring the tracker in line with the printers (start-up, entities appearing late)."""
        for serial in {s for s, _ in self._ids.values()}:
            status = self.hass.states.get(self._entity(serial, "status") or "")
            if status is None or status.state in ("unavailable", "unknown"):
                continue
            running = status.state in RUNNING_STATES
            if running and not self.state.running.get(serial):
                progress, _ = self._current(serial)
                self.state.begin(serial, self._active(serial), progress or 0.0)
                self._plan(serial)
            elif not running and self.state.running.get(serial):
                self.state.end(serial)
            elif running:
                self.state.set_active(serial, self._active(serial))
                self._plan(serial)
        self._changed()

    # ------------------------------------------------------------------ events

    @callback
    def _handle(self, event: Event) -> None:
        serial, kind = self._ids.get(event.data["entity_id"], ("", ""))
        new, old = event.data.get("new_state"), event.data.get("old_state")
        if new is None or not serial:
            return
        st = self.state
        if kind == "status":
            if old is not None and old.state == new.state:
                return
            if new.state in RUNNING_STATES:
                if not st.running.get(serial):
                    progress, _ = self._current(serial)
                    st.begin(serial, self._active(serial), progress or 0.0)
                    self._plan(serial)
            elif st.running.get(serial):
                progress, weight = self._current(serial)
                st.progress(serial, progress, weight)
                st.end(serial)
            elif new.state == "prepare":
                st.forget(serial)  # a new job is being prepared: drop the old one
        elif kind == "active":
            if st.running.get(serial):
                progress, weight = self._current(serial)
                st.progress(serial, progress, weight)  # up to now: still the old slot
                st.set_active(serial, active_slot(new.attributes))
        elif kind == "weight":
            self._plan(serial)
            return
        elif kind == "progress":
            progress, weight = self._current(serial)
            if not st.progress(serial, progress, weight):
                return
        self._changed()

    @callback
    def _changed(self) -> None:
        self._store.async_delay_save(self.state.dump, SAVE_DELAY_S)
        self.coordinator.async_update_listeners()

    @callback
    def settle(self, data: Mapping[int, Any]) -> None:
        """Called after every cloud refresh: release finished prints whose booking arrived."""
        if self.state.settle(self.nets(data)):
            self._store.async_delay_save(self.state.dump, SAVE_DELAY_S)
