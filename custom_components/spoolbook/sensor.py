"""Sensors for the Bambu Filaments integration.

Model: one hub device per account ("Spoolbook") carrying the
aggregate sensors, plus one device per spool (via_device_id -> hub) carrying a
remaining-% and a remaining-weight sensor.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfMass
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import BambuFilamentsConfigEntry
from .colors import normalize_hex
from .const import DOMAIN
from .coordinator import BambuFilamentsCoordinator, spool_remaining_pct
from .trays import bound_spool, list_trays, resolve_tray, tray_active, tray_filled
from .spool_ledger import BASE_MATERIALS, HISTORY_IN_ATTRS
from .entity import (
    async_setup_spool_platform,
    clean_display_name,
    spool_device_info,
)


def _spool_colors(spool: dict[str, Any]) -> list[str]:
    """Validated, normalized hex colors of a spool (may be empty)."""
    raw = spool.get("colors") or [spool.get("color")]
    return [h for h in (normalize_hex(c) for c in raw if c) if h]


def _swatch_picture(colors: list[str]) -> str | None:
    """Small SVG color swatch as data URI, used as the entity picture."""
    if not colors:
        return None
    if len(colors) == 1:
        body = f"<circle cx='16' cy='16' r='14' fill='{colors[0]}'/>"
    else:
        body = (
            f"<path d='M16 2 A14 14 0 0 0 16 30 Z' fill='{colors[0]}'/>"
            f"<path d='M16 2 A14 14 0 0 1 16 30 Z' fill='{colors[1]}'/>"
        )
    svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'>"
        f"{body}"
        "<circle cx='16' cy='16' r='14' fill='none' stroke='#7f8c8d' stroke-width='1.5'/>"
        "</svg>"
    )
    return f"data:image/svg+xml,{quote(svg)}"


def spool_attributes(
    spool: dict[str, Any], coordinator: BambuFilamentsCoordinator
) -> dict[str, Any]:
    color_name, color_code = coordinator.color_lookup(spool)
    colors = _spool_colors(spool)
    ledger = coordinator.ledger
    sid = spool.get("id")
    own_color_name = ledger.color_name(spool, color_name)
    return {
        "spool_id": spool.get("id"),
        "vendor": spool.get("filamentVendor"),
        "material": spool.get("filamentType"),
        "name": spool.get("filamentName"),
        "display_name": clean_display_name(spool),
        "filament_id": spool.get("filamentId"),
        # Only validated hex values are exposed - raw cloud color strings end
        # up in CSS in the card, so this doubles as the injection guard.
        "color": colors[0] if colors else None,
        "colors": colors or None,
        "color_name": own_color_name,
        "bambu_color_code": color_code,
        "remaining_g": spool.get("netWeight"),
        "total_g": spool.get("totalNetWeight"),
        "remaining_pct": spool_remaining_pct(spool),
        "status": spool.get("status"),
        "note": spool.get("note"),
        "tray_id_name": spool.get("trayIdName"),
        "create_type": spool.get("createType"),
        "in_printer": spool.get("inPrinter"),
        "device_name": spool.get("deviceName"),
        "dev_id": spool.get("devId"),
        "ams_id": spool.get("amsId"),
        "ams_type": spool.get("amsType"),
        "slot_id": spool.get("slotId"),
        # Local ledger (spool_ledger.py): archive flag, empty spool, history.
        "archived": ledger.is_archived(sid),
        "empty_spool": ledger.meta(sid).get("empty_spool"),
        "empty_spool_suggested": ledger.suggest_empty_spool(spool),
        "own_color_name": ledger.meta(sid).get("color_name"),
        "sort_key": ledger.sort_key(spool, own_color_name),
        "low_stock": any(i.get("spool_id") == sid for i in coordinator.low_stock),
        "history": ledger.history(sid, HISTORY_IN_ATTRS),
    }


def _visible(spool: dict[str, Any], coordinator: BambuFilamentsCoordinator) -> bool:
    """Archived spools leave lists and totals - unless still bound to a tray."""
    return not coordinator.ledger.is_archived(spool.get("id")) or bool(spool.get("inPrinter"))


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BambuFilamentsConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up aggregate sensors and dynamically managed per-spool devices."""
    coordinator = entry.runtime_data
    async_add_entities(
        [SpoolCountSensor(coordinator, entry), TotalRemainingSensor(coordinator, entry)]
    )
    _async_setup_tray_sensors(hass, entry, async_add_entities)
    async_setup_spool_platform(
        hass,
        entry,
        async_add_entities,
        lambda coord, ent, sid: [
            SpoolRemainingSensor(coord, ent, sid),
            SpoolWeightSensor(coord, ent, sid),
        ],
    )


class BambuFilamentsEntity(CoordinatorEntity[BambuFilamentsCoordinator]):
    """Base entity bound to the account-level Filament Library hub device."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: BambuFilamentsCoordinator, entry: BambuFilamentsConfigEntry
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Spoolbook",
            manufacturer="Bambu Lab",
            model="Filament Manager",
            configuration_url="https://bambulab.com",
        )

    def _spools(self) -> list[dict[str, Any]]:
        """Spools that count: not archived (archived ones still in a tray stay)."""
        return [
            s for s in (self.coordinator.data or {}).values() if _visible(s, self.coordinator)
        ]


class SpoolCountSensor(BambuFilamentsEntity, SensorEntity):
    """Number of active spools; full inventory in the attributes."""

    _attr_translation_key = "spool_count"
    _attr_icon = "mdi:counter"
    # The spool list is live data for the card, not history - keeping it out
    # of the recorder avoids writing the full inventory to the DB every poll.
    _unrecorded_attributes = frozenset(
        {
            "spools",
            "remaining_g_by_material",
            "version",
            "empty_spools",
            "archived_spools",
            "low_stock",
            "brands",
            "materials",
            "trays_used",
            "trays_live_g",
        }
    )

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}-spool-count"

    @property
    def native_value(self) -> int:
        return len(self._spools())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        spools = self._spools()
        ledger = self.coordinator.ledger
        by_material: dict[str, int] = {}
        for spool in spools:
            material = spool.get("filamentType") or "unknown"
            by_material[material] = by_material.get(material, 0) + (
                spool.get("netWeight") or 0
            )
        return {
            "total_spools": len(spools),
            "remaining_g_by_material": by_material,
            "spools": [spool_attributes(s, self.coordinator) for s in spools],
            # Local ledger: empty spools (tare list), sorts down to their last
            # spool, brand memory for the create dialog, the archive.
            "empty_spools": ledger.empty_spools,
            "low_stock": self.coordinator.low_stock,
            "brands": list(ledger.brands.values()),
            "materials": BASE_MATERIALS,
            "archived_spools": [
                {
                    "spool_id": s.get("id"),
                    "vendor": s.get("filamentVendor"),
                    "material": s.get("filamentType"),
                    "name": s.get("filamentName"),
                    "color": (_spool_colors(s) or [None])[0],
                    "archived_at": ledger.meta(s.get("id")).get("archived_at"),
                }
                for s in (self.coordinator.data or {}).values()
                if ledger.is_archived(s.get("id"))
            ],
            # Integration version - the card compares it with its own
            # CARD_VERSION and offers a page reload when they differ.
            "version": self.hass.data.get(DOMAIN, {}).get("version"),
            # AMS slots the running (or last) print used, from the active-tray sensor.
            "trays_used": [
                t["entity_id"]
                for t in list_trays(self.hass)
                if self.coordinator.live.used_in_print(t)
            ],
            # Grams the running print used so far per slot (also makes the card re-read the slots).
            "trays_live_g": self._trays_live_g(),
        }

    def _trays_live_g(self) -> dict[str, float]:
        spools = (self.coordinator.data or {}).values()
        out: dict[str, float] = {}
        for tray in list_trays(self.hass):
            spool = bound_spool(spools, tray)
            net = float(spool.get("netWeight") or 0) if spool else None
            if live := self.coordinator.live.live_g(tray, net):
                out[tray["entity_id"]] = live
        return out


class TotalRemainingSensor(BambuFilamentsEntity, SensorEntity):
    """Total remaining filament weight across active spools."""

    _attr_translation_key = "total_remaining"
    _attr_icon = "mdi:weight-gram"
    _attr_native_unit_of_measurement = UnitOfMass.GRAMS
    _attr_device_class = SensorDeviceClass.WEIGHT
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}-total-remaining"

    @property
    def native_value(self) -> int:
        return sum(s.get("netWeight") or 0 for s in self._spools())


class SpoolEntityBase(CoordinatorEntity[BambuFilamentsCoordinator], SensorEntity):
    """Base for per-spool sensors; each spool is its own HA device."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: BambuFilamentsCoordinator,
        entry: BambuFilamentsConfigEntry,
        spool_id: int,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._spool_id = spool_id
        self._attr_device_info = spool_device_info(coordinator, entry, spool_id)

    @property
    def _spool(self) -> dict[str, Any] | None:
        return (self.coordinator.data or {}).get(self._spool_id)

    @property
    def available(self) -> bool:
        return super().available and self._spool is not None


class SpoolRemainingSensor(SpoolEntityBase):
    """Remaining percent of one spool, with the full details as attributes."""

    _attr_translation_key = "spool_remaining_pct"
    _attr_native_unit_of_measurement = PERCENTAGE
    _unrecorded_attributes = frozenset({"history", "empty_spool_suggested", "low_stock", "sort_key"})

    def __init__(self, coordinator, entry, spool_id: int) -> None:
        super().__init__(coordinator, entry, spool_id)
        # Keeps the unique_id of the former single per-spool sensor (< v0.3.0).
        self._attr_unique_id = f"{entry.entry_id}-spool-{spool_id}"

    @property
    def entity_picture(self) -> str | None:
        if spool := self._spool:
            return _swatch_picture(_spool_colors(spool))
        return None

    @property
    def native_value(self) -> int | None:
        if spool := self._spool:
            return spool_remaining_pct(spool)
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if spool := self._spool:
            return spool_attributes(spool, self.coordinator)
        return {}


class SpoolWeightSensor(SpoolEntityBase):
    """Remaining filament weight of one spool in grams."""

    _attr_translation_key = "spool_remaining_g"
    _attr_native_unit_of_measurement = UnitOfMass.GRAMS
    _attr_device_class = SensorDeviceClass.WEIGHT
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator, entry, spool_id: int) -> None:
        super().__init__(coordinator, entry, spool_id)
        self._attr_unique_id = f"{entry.entry_id}-spool-{spool_id}-weight"

    @property
    def native_value(self) -> int | None:
        if spool := self._spool:
            return spool.get("netWeight") or 0
        return None


@callback
def _async_setup_tray_sensors(
    hass: HomeAssistant,
    entry: BambuFilamentsConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """One sensor per AMS slot of the ha-bambulab integration, added as they appear."""
    coordinator: BambuFilamentsCoordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _sync(*_: Any) -> None:
        new = [t for t in list_trays(hass) if t["entity_id"] not in known]
        if new:
            known.update(t["entity_id"] for t in new)
            async_add_entities([TraySensor(coordinator, entry, t["entity_id"]) for t in new])

    _sync()
    # Slots show up when ha-bambulab finishes loading (maybe after us) or an AMS is added.
    entry.async_on_unload(coordinator.async_add_listener(_sync))
    entry.async_on_unload(
        hass.bus.async_listen(er.EVENT_ENTITY_REGISTRY_UPDATED, _sync)
    )


class TraySensor(BambuFilamentsEntity, SensorEntity):
    """One AMS slot: remaining grams of the bound spool, details as attributes.

    Replaces template_configs/ams_tray_display.yaml (ams_tray.jinja macro).
    State is unknown while the slot is empty or has no bound spool.
    """

    _attr_icon = "mdi:printer-3d-nozzle"
    _attr_native_unit_of_measurement = UnitOfMass.GRAMS
    _attr_device_class = SensorDeviceClass.WEIGHT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0
    _unrecorded_attributes = frozenset({"history", "display_name"})

    def __init__(self, coordinator, entry, tray_entity: str) -> None:
        super().__init__(coordinator, entry)
        self._tray_entity = tray_entity
        self._attr_unique_id = f"{entry.entry_id}-tray-{tray_entity}"
        try:
            self._attr_name = resolve_tray(coordinator.hass, tray_entity)["label"]
        except Exception:  # noqa: BLE001 - registry entry vanished meanwhile
            self._attr_name = tray_entity

    def _tray(self) -> dict[str, Any] | None:
        try:
            return resolve_tray(self.hass, self._tray_entity)
        except Exception:  # noqa: BLE001
            return None

    def _view(self) -> tuple[dict[str, Any] | None, Any, bool, dict[str, Any] | None]:
        """(tray, tray state, filled, bound spool)."""
        tray = self._tray()
        state = self.hass.states.get(self._tray_entity)
        filled = tray is not None and tray_filled(state)
        spool = bound_spool((self.coordinator.data or {}).values(), tray) if filled else None
        return tray, state, filled, spool

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        ids = [self._tray_entity]
        if (tray := self._tray()) and tray.get("active_entity"):
            ids.append(tray["active_entity"])

        @callback
        def _changed(_event: Any) -> None:
            self.async_write_ha_state()

        self.async_on_remove(async_track_state_change_event(self.hass, ids, _changed))

    @property
    def available(self) -> bool:
        return self.hass.states.get(self._tray_entity) is not None

    @property
    def native_value(self) -> float | None:
        tray, _, _, spool = self._view()
        if spool is None or tray is None:
            return None
        return self._live_net(tray, spool)[0]

    def _live_net(self, tray: dict[str, Any], spool: dict[str, Any]) -> tuple[float, float]:
        """(remaining grams minus what the running print used so far, that live part)."""
        net = float(spool.get("netWeight") or 0)
        live = self.coordinator.live.live_g(tray, net)
        return round(max(0.0, net - live), 1), live

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        tray, state, filled, spool = self._view()
        if tray is None:
            return {}
        t_attrs = state.attributes if state is not None else {}
        attrs: dict[str, Any] = {
            "tray_entity": self._tray_entity,
            "ams_id": tray["ams_id"],
            "slot_id": tray["slot_id"],
            "ams_name": tray["ams_name"],
            "filled": filled,
            "active": tray_active(self.hass, tray, state),
            "used_in_print": self.coordinator.live.used_in_print(tray),
            "live_used_g": 0.0,
            "spool_id": None,
            "missing_spool": bool(filled and spool is None),
        }
        if spool is not None:
            sa = spool_attributes(spool, self.coordinator)
            name = sa["display_name"] or sa["name"] or ""
            material = sa["material"] or ""
            if material and material.lower() not in name.lower():
                name = f"{name} {material}"
            name = name.strip()
            remaining_g, live = self._live_net(tray, spool)
            total = sa["total_g"]
            pct = (
                max(0, min(100, round(remaining_g / total * 100)))
                if isinstance(total, (int, float)) and total > 0
                else sa["remaining_pct"]
            )
            attrs.update(
                {
                    "live_used_g": live,
                    "spool_id": sa["spool_id"],
                    "name": name or None,
                    "color_name": sa["color_name"],
                    "color": sa["color"],
                    "vendor": sa["vendor"],
                    "material": sa["material"],
                    "remaining_pct": pct,
                    "total_g": total,
                }
            )
        else:
            colors = _tray_color(t_attrs.get("color")) if filled else None
            attrs.update(
                {
                    "name": None,
                    "color_name": None,
                    "color": colors,
                    "vendor": None,
                    "material": (t_attrs.get("type") or state.state) if filled else None,
                    "remaining_pct": t_attrs.get("remaining") if filled else None,
                    "total_g": None,
                }
            )
        return attrs


def _tray_color(raw: Any) -> str | None:
    """Tray colors come as #RRGGBBAA; validated like spool colors."""
    return normalize_hex(raw) or None
