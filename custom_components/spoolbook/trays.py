"""AMS slots of the Bambu Lab printer integration, as this integration sees them.

Everything here reads Home Assistant's registries and state machine only
(entities of github.com/greghesp/ha-bambulab, domain ``bambu_lab``); nothing
talks to a printer. Used by the load/unload services, the websocket API of
the card/sidebar page and the "recently loaded slot" hint.

A slot ("tray") is described by a plain dict:

    entity_id     sensor of the slot (ha-bambulab AMS tray sensor)
    dev_id        printer serial (cloud devId)
    ams_sn        AMS serial
    ams_id        cloud amsId (AMS_1 -> 0, AMS HT keeps 128+)
    ams_type      cloud amsType (const.AMS_TYPE_BY_MODEL)
    slot_id       cloud slotId, "0".."3"
    slot          1..4
    ams_name      AMS device name as the user named it (else its model)
    printer_name  printer device name
    label         "AMS 2 Pro · Fach 1" (printer name in front with >1 printer)
    status_entity print status sensor of the printer (or None)
    active_entity active-tray sensor of the printer (or None)
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import logging
import re
import time
from typing import Any

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import AMS_TYPE_BY_MODEL, BAMBULAB_DOMAIN, RECENT_LOAD_S

_LOGGER = logging.getLogger(__name__)

# unique_id shape of ha-bambulab's AMS tray sensors (sensor.py):
#   {device_type}_{printer_serial}_AMS_{ams_serial}_tray_{1..4}
AMS_TRAY_RE = re.compile(r"^(?P<dev>[^_]+)_(?P<serial>[^_]+)_AMS_(?P<ams>[^_]+)_tray_(?P<n>\d+)$")
AMS_DEVICE_INDEX_RE = re.compile(r"_AMS_(?P<n>\d+)$")
BUSY_STATES = {"running", "pause", "prepare", "slicing", "init"}


def _status_entity(hass: HomeAssistant, serial: str) -> str | None:
    return er.async_get(hass).async_get_entity_id(
        "sensor", BAMBULAB_DOMAIN, f"{serial}_print_status"
    )


def _active_entity(hass: HomeAssistant, serial: str) -> str | None:
    return er.async_get(hass).async_get_entity_id(
        "sensor", BAMBULAB_DOMAIN, f"{serial}_active_tray"
    )


def active_by_sensor(state: Any, tray: Mapping[str, Any]) -> bool | None:
    """Is ``tray`` the one the printer reports as active?

    The printer's active-tray sensor carries ``ams_index`` (0-based, same as the
    cloud amsId; 255 = external spool, 254 = none) and ``tray_index`` (0-based,
    = slotId). Returns None while the sensor is unknown or has no attributes
    (caller falls back to the tray's own ``active`` attribute).
    """
    if state is None or state.state in ("unavailable", "unknown"):
        return None
    attrs = state.attributes
    if "ams_index" not in attrs or "tray_index" not in attrs:
        return None
    try:
        return int(attrs["ams_index"]) == int(tray["ams_id"]) and int(
            attrs["tray_index"]
        ) == int(tray["slot_id"])
    except (TypeError, ValueError):
        return None


def tray_active(hass: HomeAssistant, tray: Mapping[str, Any], tray_state: Any = None) -> bool:
    """Active flag: printer's active-tray sensor first, tray attribute as fallback."""
    entity_id = tray.get("active_entity")
    derived = active_by_sensor(hass.states.get(entity_id) if entity_id else None, tray)
    if derived is not None:
        return derived
    return bool(tray_state.attributes.get("active")) if tray_state is not None else False


def _tray_from_entry(hass: HomeAssistant, entry: Any) -> dict[str, Any] | None:
    if entry is None or entry.platform != BAMBULAB_DOMAIN:
        return None
    match = AMS_TRAY_RE.match(entry.unique_id or "")
    if match is None:
        return None
    tray_no = int(match.group("n"))
    if not 1 <= tray_no <= 4:
        return None
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get(entry.device_id) if entry.device_id else None
    index_match = AMS_DEVICE_INDEX_RE.search(device.name or "") if device else None
    if index_match is None:
        return None
    n = int(index_match.group("n"))
    printer = dev_reg.async_get(device.via_device_id) if device.via_device_id else None
    ams_name = device.name_by_user or device.model or device.name or "AMS"
    serial = match.group("serial")
    return {
        "entity_id": entry.entity_id,
        "dev_id": serial,
        "ams_sn": match.group("ams"),
        "ams_id": n - 1 if n < 128 else n,
        "ams_type": AMS_TYPE_BY_MODEL.get(device.model or "", 0),
        "slot_id": str(tray_no - 1),
        "slot": tray_no,
        "ams_name": ams_name,
        "printer_name": (printer.name_by_user or printer.name) if printer else match.group("dev"),
        "label": f"{ams_name} · Fach {tray_no}",
        "status_entity": _status_entity(hass, serial),
        "active_entity": _active_entity(hass, serial),
    }


def resolve_tray(hass: HomeAssistant, entity_id: str) -> dict[str, Any]:
    """Slot dict for one ha-bambulab AMS tray sensor (German error otherwise)."""
    tray = _tray_from_entry(hass, er.async_get(hass).async_get(entity_id))
    if tray is None:
        raise HomeAssistantError(
            f"{entity_id} ist kein AMS-Fach der Bambu-Lab-Integration (Fach 1 bis 4)."
        )
    return tray


def list_trays(hass: HomeAssistant) -> list[dict[str, Any]]:
    """All enabled AMS slots, sorted by printer, AMS and slot."""
    trays = [
        tray
        for entry in er.async_get(hass).entities.values()
        if entry.domain == "sensor" and not entry.disabled
        and (tray := _tray_from_entry(hass, entry)) is not None
    ]
    if len({t["dev_id"] for t in trays}) > 1:
        for tray in trays:
            tray["label"] = f"{tray['printer_name']} {tray['label']}"
    trays.sort(key=lambda t: (t["printer_name"], t["dev_id"], t["ams_id"], t["slot"]))
    return trays


def print_state(hass: HomeAssistant, tray: Mapping[str, Any]) -> str | None:
    entity_id = tray.get("status_entity")
    state = hass.states.get(entity_id) if entity_id else None
    return state.state if state is not None else None


def ensure_idle(hass: HomeAssistant, tray: Mapping[str, Any]) -> None:
    """Slots are only changed while the printer is idle."""
    if print_state(hass, tray) in BUSY_STATES:
        raise HomeAssistantError("Der Drucker arbeitet gerade, Fächer erst danach tauschen.")


def same_slot(spool: Mapping[str, Any], tray: Mapping[str, Any]) -> bool:
    """True when the cloud spool (raw library data) is bound to this slot."""
    if not spool.get("inPrinter"):
        return False
    dev = spool.get("devId")
    if dev and dev != tray["dev_id"]:
        return False
    return str(spool.get("amsId")) == str(tray["ams_id"]) and str(spool.get("slotId")) == str(
        tray["slot_id"]
    )


def bound_spool(
    spools: Iterable[Mapping[str, Any]], tray: Mapping[str, Any]
) -> Mapping[str, Any] | None:
    return next((s for s in spools if same_slot(s, tray)), None)


def spool_tray(
    spool: Mapping[str, Any], trays: Iterable[Mapping[str, Any]]
) -> Mapping[str, Any] | None:
    return next((t for t in trays if same_slot(spool, t)), None)


def tray_filled(state: Any) -> bool:
    """A slot that holds filament (unknown / unavailable / empty count as not filled)."""
    return (
        state is not None
        and str(state.state).lower() not in ("", "unknown", "unavailable", "none", "empty")
        and not state.attributes.get("empty")
    )


class MissingWatch:
    """Slot filled but without a bound spool for ``delay`` seconds -> one message per occurrence.

    Pure bookkeeping (no timers): ``observe`` tells whether a timer must be armed,
    ``due`` is asked when it fires.
    """

    def __init__(self, delay: float = 900.0) -> None:
        self.delay = delay
        self._since: dict[str, float] = {}
        self._notified: set[str] = set()

    def observe(self, key: str, missing: bool, now: float) -> bool:
        """True when ``key`` just became missing (arm the timer); a fix resets it."""
        if not missing:
            self._since.pop(key, None)
            self._notified.discard(key)
            return False
        if key in self._since:
            return False
        self._since[key] = now
        return True

    def due(self, key: str, now: float) -> bool:
        """True once per occurrence, when it has been missing for the full delay."""
        since = self._since.get(key)
        if since is None or key in self._notified or now - since < self.delay:
            return False
        self._notified.add(key)
        return True


def repush_targets(
    trays: Iterable[Mapping[str, Any]],
    spools: Iterable[Mapping[str, Any]],
    state_of: Any,
    busy: Any,
    only: str | None = None,
) -> list[tuple[Mapping[str, Any], Mapping[str, Any]]]:
    """(tray, spool) pairs whose printer slot is to be re-sent to the printer.

    Filled slots with a bound spool that has a printer profile; printers that
    are busy are skipped; ``only`` limits it to one slot entity.
    """
    spools = list(spools)
    out = []
    for tray in trays:
        if only is not None and tray["entity_id"] != only:
            continue
        if not tray_filled(state_of(tray["entity_id"])) or busy(tray):
            continue
        spool = bound_spool(spools, tray)
        if spool is None or not spool.get("filamentId") or not spool.get("filamentType"):
            continue
        out.append((tray, spool))
    return out


def _is_empty(state: Any) -> bool | None:
    """True/False for a known slot, None while unknown (startup, unavailable)."""
    if state is None or state.state in ("unavailable", "unknown"):
        return None
    return bool(state.attributes.get("empty"))


class TrayTracker:
    """Remembers when a slot went from empty to filled (for the "Kommt in …?" hint)."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._ids: set[str] = set()
        self._loaded: dict[str, float] = {}
        self._unsubs: list[Any] = []

    @callback
    def async_start(self) -> None:
        self.refresh_ids()
        self._unsubs.append(
            self.hass.bus.async_listen(
                EVENT_STATE_CHANGED, self._handle, event_filter=self._filter
            )
        )
        self._unsubs.append(
            self.hass.bus.async_listen(
                er.EVENT_ENTITY_REGISTRY_UPDATED, self._registry_updated
            )
        )

    @callback
    def _registry_updated(self, _event: Any) -> None:
        self.refresh_ids()

    @callback
    def async_stop(self) -> None:
        while self._unsubs:
            self._unsubs.pop()()

    @callback
    def refresh_ids(self) -> None:
        self._ids = {
            entry.entity_id
            for entry in er.async_get(self.hass).entities.values()
            if entry.platform == BAMBULAB_DOMAIN
            and entry.domain == "sensor"
            and AMS_TRAY_RE.match(entry.unique_id or "")
        }

    @callback
    def _filter(self, event_data: Mapping[str, Any]) -> bool:
        return event_data.get("entity_id") in self._ids

    @callback
    def _handle(self, event: Any) -> None:
        self.note(event.data["entity_id"], event.data.get("old_state"), event.data.get("new_state"))

    def note(self, entity_id: str, old: Any, new: Any, now: float | None = None) -> None:
        before, after = _is_empty(old), _is_empty(new)
        if before is True and after is False:
            self._loaded[entity_id] = time.time() if now is None else now
        elif after is True:
            self._loaded.pop(entity_id, None)

    def clear(self, entity_id: str) -> None:
        self._loaded.pop(entity_id, None)

    def recent(self, now: float | None = None) -> dict[str, float]:
        """{entity_id: loaded_at (epoch s)} for slots filled in the last 10 minutes."""
        now = time.time() if now is None else now
        for entity_id, at in list(self._loaded.items()):
            if now - at > RECENT_LOAD_S:
                del self._loaded[entity_id]
        return dict(self._loaded)
