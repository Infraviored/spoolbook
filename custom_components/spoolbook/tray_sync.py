"""Slot housekeeping that used to be household automations.

1. Watcher "slot filled but no spool bound": after 15 continuous minutes one
   message through the configured notify action (or a persistent notification).
   Once per occurrence; resets when the slot gets a spool or becomes empty.
2. Printer slot re-send: when a printer comes online (its ha-bambulab "online"
   binary sensor turns on, 60 s debounce) or a spool is inserted into a slot
   that already has a bound spool (20 s), the printer slot settings of the
   bound spool are sent again - the same command path load_spool uses
   (option tray_command), but no cloud re-binding. Busy printers are skipped.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Mapping
from typing import Any

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_call_later

from .const import (
    BAMBULAB_DOMAIN,
    DEFAULT_TRAY_COMMAND,
    DEFAULT_TRAY_SERVICE,
    DOMAIN,
    OPT_TRAY_COMMAND,
    OPT_TRAY_SERVICE,
)
from .loading import async_printer_slot_service
from .services import async_set_printer_tray
from .trays import (
    AMS_TRAY_RE,
    BUSY_STATES,
    MissingWatch,
    bound_spool,
    list_trays,
    print_state,
    repush_targets,
    tray_filled,
)

_LOGGER = logging.getLogger(__name__)

MISSING_DELAY_S = 15 * 60
ONLINE_DEBOUNCE_S = 60
INSERTED_DEBOUNCE_S = 20
_ONLINE_RE = re.compile(r"^(?P<serial>[^_]+)_online$")
_DOWN = ("unavailable", "unknown", "off")


def missing_message(language: str, label: str, material: str) -> tuple[str, str]:
    """(title, message) in German or English."""
    what = f" ({material})" if material else ""
    if str(language or "").lower().startswith("de"):
        return (
            "AMS-Fach ohne Spule",
            f"{label} ist belegt{what}, aber keiner Spule zugeordnet. "
            "Drucke daraus werden nirgends abgebucht.",
        )
    return (
        "AMS slot without a spool",
        f"{label} holds filament{what}, but no spool is assigned. "
        "Prints from it are not booked anywhere.",
    )


class TraySync:
    """Watcher + re-send for one config entry."""

    def __init__(self, hass: HomeAssistant, coordinator: Any) -> None:
        self.hass = hass
        self.coordinator = coordinator
        self.watch = MissingWatch(MISSING_DELAY_S)
        self._timers: dict[str, Callable[[], None]] = {}
        self._tray_ids: set[str] = set()
        self._online: dict[str, str] = {}  # entity_id -> printer serial
        self._unsubs: list[Callable[[], None]] = []

    # --------------------------------------------------------------- lifecycle

    async def async_start(self) -> None:
        self._refresh_ids()
        self._unsubs.append(
            self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._handle, event_filter=self._filter)
        )
        self._unsubs.append(
            self.hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, self._registry_updated)
        )
        self._unsubs.append(self.coordinator.async_add_listener(self._evaluate))
        self._evaluate()

    @callback
    def async_stop(self) -> None:
        while self._unsubs:
            self._unsubs.pop()()
        for cancel in list(self._timers.values()):
            cancel()
        self._timers.clear()

    @callback
    def _refresh_ids(self) -> None:
        trays, online = set(), {}
        for entry in er.async_get(self.hass).entities.values():
            if entry.platform != BAMBULAB_DOMAIN or entry.disabled:
                continue
            if entry.domain == "sensor" and AMS_TRAY_RE.match(entry.unique_id or ""):
                trays.add(entry.entity_id)
            elif entry.domain == "binary_sensor" and (m := _ONLINE_RE.match(entry.unique_id or "")):
                online[entry.entity_id] = m.group("serial")
        self._tray_ids, self._online = trays, online

    @callback
    def _registry_updated(self, _event: Any) -> None:
        self._refresh_ids()
        self._evaluate()

    @callback
    def _filter(self, event_data: Mapping[str, Any]) -> bool:
        entity_id = event_data.get("entity_id")
        return entity_id in self._tray_ids or entity_id in self._online

    def _arm(self, key: str, delay: float, action: Callable[[], Any]) -> None:
        self._cancel(key)

        async def _fire(_now: Any) -> None:
            self._timers.pop(key, None)
            await action()

        self._timers[key] = async_call_later(self.hass, delay, _fire)

    def _cancel(self, key: str) -> None:
        if cancel := self._timers.pop(key, None):
            cancel()

    # ------------------------------------------------------------------ events

    @callback
    def _handle(self, event: Event) -> None:
        entity_id = event.data["entity_id"]
        old, new = event.data.get("old_state"), event.data.get("new_state")
        if entity_id in self._online:
            key = f"online:{entity_id}"
            if new is not None and new.state == "on":
                if old is None or old.state in _DOWN:
                    serial = self._online[entity_id]
                    self._arm(key, ONLINE_DEBOUNCE_S, lambda: self._push_printer(serial))
            else:
                self._cancel(key)
            return
        self._evaluate()
        if (
            new is not None
            and old is not None
            and old.attributes.get("empty") is True
            and tray_filled(new)
        ):
            self._arm(
                f"insert:{entity_id}",
                INSERTED_DEBOUNCE_S,
                lambda: self._push(only=entity_id),
            )
        elif not tray_filled(new):
            self._cancel(f"insert:{entity_id}")

    # -------------------------------------------------- 1. missing-spool watcher

    @callback
    def _evaluate(self) -> None:
        if self.coordinator.data is None:  # cloud data not there (yet): judge nothing
            return
        spools = self.coordinator.data.values()
        now = time.monotonic()
        for tray in list_trays(self.hass):
            key = tray["entity_id"]
            state = self.hass.states.get(key)
            if state is None or state.state == "unavailable":
                continue
            missing = tray_filled(state) and bound_spool(spools, tray) is None
            if self.watch.observe(key, missing, now):
                self._arm(f"missing:{key}", MISSING_DELAY_S, lambda k=key: self._notify(k))
            elif not missing:
                self._cancel(f"missing:{key}")

    async def _notify(self, entity_id: str) -> None:
        if not self.watch.due(entity_id, time.monotonic()):
            return
        tray = next((t for t in list_trays(self.hass) if t["entity_id"] == entity_id), None)
        if tray is None:
            return
        state = self.hass.states.get(entity_id)
        material = str(state.attributes.get("type") or "") if state is not None else ""
        title, message = missing_message(self.hass.config.language, tray["label"], material)
        await self.coordinator.async_notify(title, message, f"{DOMAIN}_missing_{entity_id}")

    # ------------------------------------------------- 2. printer slot re-send

    async def _push_printer(self, serial: str) -> None:
        await self._push(serial=serial)

    async def _push(self, serial: str | None = None, only: str | None = None) -> None:
        if self.coordinator.data is None:
            return
        trays = [t for t in list_trays(self.hass) if serial is None or t["dev_id"] == serial]
        targets = repush_targets(
            trays,
            self.coordinator.data.values(),
            self.hass.states.get,
            lambda t: print_state(self.hass, t) in BUSY_STATES,
            only=only,
        )
        options = self.coordinator.config_entry.options
        command = options.get(OPT_TRAY_COMMAND, DEFAULT_TRAY_COMMAND)
        service = str(options.get(OPT_TRAY_SERVICE, DEFAULT_TRAY_SERVICE) or "").strip()
        for tray, spool in targets:
            try:
                _, own = await async_printer_slot_service(
                    self.hass, spool, tray, command, service
                )
                if own:
                    await async_set_printer_tray(self.hass, dict(spool), dict(tray))
            except Exception as err:  # noqa: BLE001 - one slot failing must not stop the rest
                _LOGGER.warning("Re-sending slot %s to the printer failed: %s", tray["label"], err)
