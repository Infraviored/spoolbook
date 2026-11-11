"""Write actions for Bambu Filaments (verified against the cloud API)."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Iterable
from typing import Any

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.service import async_register_admin_service

from .api import AuthExpired, BambuCloudError
from .colors import normalize_hex
from .const import (
    BAMBULAB_DOMAIN,
    DEFAULT_TRAY_COMMAND,
    DEFAULT_TRAY_SERVICE,
    DOMAIN,
    OPT_TRAY_COMMAND,
    OPT_TRAY_SERVICE,
    SERVICE_LOAD_SPOOL,
    SERVICE_REFRESH,
    SERVICE_UNLOAD_SLOT,
)
from .coordinator import BambuFilamentsCoordinator
from .loading import async_load_spool, async_unload_slot
from .trays import ensure_idle, list_trays, resolve_tray

SERVICE_SET_REMAINING = "set_remaining"
SERVICE_SET_NOTE = "set_note"
SERVICE_SET_FILAMENT_ID = "set_filament_id"
SERVICE_UPDATE_SPOOL = "update_spool"
SERVICE_CREATE_SPOOL = "create_spool"
SERVICE_DELETE_SPOOL = "delete_spool"
SERVICE_GET_CATALOG = "get_catalog"
SERVICE_ASSIGN_SPOOL = "assign_spool"
SERVICE_UNASSIGN_SLOT = "unassign_slot"
SERVICE_WEIGH_SPOOL = "weigh_spool"
SERVICE_SET_EMPTY_SPOOL = "set_empty_spool"
SERVICE_DELETE_EMPTY_SPOOL = "delete_empty_spool"
SERVICE_ARCHIVE_SPOOL = "archive_spool"
SERVICE_GET_HISTORY = "get_history"
SERVICE_SET_COLOR_NAME = "set_color_name"

# Wait for the printer tray to report the new filament after set_filament.
TRAY_WAIT_S = 20
# Fallback nozzle range (min, max) by material when the tray has no known
# range for this filament; the printer takes the real range from its profile.
DEFAULT_NOZZLE_TEMPS = {
    "PLA": (190, 240), "PETG": (220, 260), "ABS": (240, 280), "ASA": (240, 280),
    "TPU": (200, 240), "PC": (250, 290), "PA": (260, 300), "PET": (260, 300),
    "PVA": (190, 230), "HIPS": (220, 260),
}

_LOGGER = logging.getLogger(__name__)

# 6-digit hex, optionally with alpha and/or leading '#'.
COLOR_SCHEMA = vol.All(
    cv.string, vol.Match(r"^#?[0-9A-Fa-f]{6}(?:[0-9A-Fa-f]{2})?$")
)

SET_REMAINING_SCHEMA = vol.Schema(
    {
        vol.Required("spool_id"): cv.positive_int,
        vol.Required("remaining_g"): vol.All(vol.Coerce(int), vol.Range(min=0, max=20000)),
    }
)
SET_NOTE_SCHEMA = vol.Schema(
    {
        vol.Required("spool_id"): cv.positive_int,
        vol.Required("note"): cv.string,
    }
)
SET_FILAMENT_ID_SCHEMA = vol.Schema(
    {
        vol.Required("spool_id"): cv.positive_int,
        # Empty string is meaningful: it clears the profile (Studio's
        # "non-official spool" state), so no vol.Length guard here.
        vol.Required("filament_id"): cv.string,
    }
)
CREATE_SPOOL_SCHEMA = vol.Schema(
    {
        vol.Optional("vendor", default="Bambu Lab"): cv.string,
        vol.Required("material"): cv.string,
        vol.Required("name"): cv.string,
        vol.Required("color"): COLOR_SCHEMA,
        vol.Optional("total_g", default=1000): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=20000)
        ),
        vol.Optional("remaining_g"): vol.All(vol.Coerce(int), vol.Range(min=0, max=20000)),
        vol.Optional("filament_id"): cv.string,
        vol.Optional("display_name"): cv.string,
        vol.Optional("note"): cv.string,
        # Studio/Handy let you add N spools of one type in a single step
        # ("stock"); the cloud has no batch create, so this loops the POST.
        vol.Optional("count", default=1): vol.All(vol.Coerce(int), vol.Range(min=1, max=50)),
        # Local ledger: empty spool (tare) to remember; with gross_g the
        # remaining weight is gross_g minus that tare (weighed on creation).
        vol.Optional("empty_spool"): cv.string,
        vol.Optional("gross_g"): vol.All(vol.Coerce(int), vol.Range(min=0, max=25000)),
        vol.Optional("color_name"): cv.string,
    }
)
DELETE_SPOOL_SCHEMA = vol.Schema({vol.Required("spool_id"): cv.positive_int})
# Every field is optional (empty strings clear display_name/note/filament_id);
# the handler requires at least one field besides spool_id.
UPDATE_SPOOL_SCHEMA = vol.Schema(
    {
        vol.Required("spool_id"): cv.positive_int,
        vol.Optional("vendor"): vol.All(cv.string, vol.Length(min=1)),
        vol.Optional("material"): vol.All(cv.string, vol.Length(min=1)),
        vol.Optional("name"): vol.All(cv.string, vol.Length(min=1)),
        vol.Optional("color"): COLOR_SCHEMA,
        vol.Optional("total_g"): vol.All(vol.Coerce(int), vol.Range(min=1, max=20000)),
        vol.Optional("remaining_g"): vol.All(vol.Coerce(int), vol.Range(min=0, max=20000)),
        vol.Optional("note"): cv.string,
        vol.Optional("filament_id"): cv.string,
        vol.Optional("display_name"): cv.string,
    }
)
# service field -> cloud PUT field (verified live: all editable via minimal PUT)
UPDATE_FIELD_MAP = {
    "vendor": "filamentVendor",
    "material": "filamentType",
    "name": "filamentName",
    "total_g": "totalNetWeight",
    "remaining_g": "netWeight",
    "note": "note",
    "filament_id": "filamentId",
    "display_name": "displayName",
}

ASSIGN_SPOOL_SCHEMA = vol.Schema(
    {
        vol.Required("spool_id"): cv.positive_int,
        vol.Required("tray"): cv.entity_id,
        vol.Optional("set_printer", default=True): cv.boolean,
    }
)
UNASSIGN_SLOT_SCHEMA = vol.Schema({vol.Required("tray"): cv.entity_id})
LOAD_SPOOL_SCHEMA = vol.Schema(
    {
        vol.Required("spool_id"): cv.positive_int,
        vol.Required("tray"): cv.entity_id,
        # Optional scale reading right after loading (spool + filament, minus
        # the spool's remembered empty spool).
        vol.Optional("weight_g"): vol.All(vol.Coerce(int), vol.Range(min=0, max=25000)),
        vol.Optional("with_spool", default=True): cv.boolean,
        vol.Optional("empty_spool"): cv.string,
    }
)
UNLOAD_SLOT_SCHEMA = vol.Schema({vol.Required("tray"): cv.entity_id})

WEIGH_SPOOL_SCHEMA = vol.Schema(
    {
        vol.Required("spool_id"): cv.positive_int,
        # What the scale shows: spool + filament (with_spool) or filament only.
        vol.Required("weight_g"): vol.All(vol.Coerce(int), vol.Range(min=0, max=25000)),
        vol.Optional("with_spool", default=True): cv.boolean,
        # Empty spool id; default: the one this spool remembered / its brand's.
        vol.Optional("empty_spool"): cv.string,
    }
)
SET_EMPTY_SPOOL_SCHEMA = vol.Schema(
    {
        vol.Optional("empty_spool"): cv.string,
        vol.Optional("name"): vol.All(cv.string, vol.Length(min=1, max=40)),
        vol.Optional("weight_g"): vol.All(vol.Coerce(int), vol.Range(min=1, max=2000)),
        vol.Optional("vendor"): cv.string,
    }
)
DELETE_EMPTY_SPOOL_SCHEMA = vol.Schema({vol.Required("empty_spool"): cv.string})
ARCHIVE_SPOOL_SCHEMA = vol.Schema(
    {
        vol.Required("spool_id"): cv.positive_int,
        vol.Optional("archived", default=True): cv.boolean,
    }
)
GET_HISTORY_SCHEMA = vol.Schema(
    {
        vol.Required("spool_id"): cv.positive_int,
        vol.Optional("limit", default=20): vol.All(vol.Coerce(int), vol.Range(min=1, max=100)),
    }
)
SET_COLOR_NAME_SCHEMA = vol.Schema(
    {
        vol.Required("spool_id"): cv.positive_int,
        vol.Required("color_name"): vol.All(cv.string, vol.Length(min=1, max=40)),
    }
)


def _coordinators(hass: HomeAssistant) -> Iterable[BambuFilamentsCoordinator]:
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        coordinator = getattr(entry, "runtime_data", None)
        if isinstance(coordinator, BambuFilamentsCoordinator):
            yield coordinator


def _first_coordinator(hass: HomeAssistant) -> BambuFilamentsCoordinator:
    for coordinator in _coordinators(hass):
        return coordinator
    raise HomeAssistantError("No Bambu Filaments account is set up")


def _find_spool(
    hass: HomeAssistant, spool_id: int
) -> tuple[BambuFilamentsCoordinator, dict[str, Any]]:
    for coordinator in _coordinators(hass):
        if spool := (coordinator.data or {}).get(spool_id):
            return coordinator, spool
    raise HomeAssistantError(f"Spool {spool_id} not found in any filament library")


def _resolve_tray(hass: HomeAssistant, entity_id: str) -> dict[str, Any]:
    """ha-bambulab AMS tray sensor -> slot dict (see trays.py)."""
    return resolve_tray(hass, entity_id)


def _mapping(tray: dict[str, Any], spool_id: int, rfid: str) -> dict[str, Any]:
    return {
        "amsSn": tray["ams_sn"],
        "slotId": tray["slot_id"],
        "spoolId": spool_id,
        "rfid": rfid,
        "amsId": tray["ams_id"],
        "amsType": tray["ams_type"],
    }


async def _cloud_write(
    hass: HomeAssistant,
    coordinator: BambuFilamentsCoordinator,
    func,
    *args,
    action: str,
) -> Any:
    """Run a blocking cloud write; token expiry starts the reauth flow."""
    try:
        return await hass.async_add_executor_job(func, *args)
    except AuthExpired as err:
        coordinator.config_entry.async_start_reauth(hass)
        raise HomeAssistantError(
            "Bambu cloud token expired - re-authentication started"
        ) from err
    except BambuCloudError as err:
        raise HomeAssistantError(f"{action}: {err}") from err


async def _update_fields(hass: HomeAssistant, spool_id: int, fields: dict[str, Any]) -> None:
    """PUT only the changed fields (plus the mandatory id + filamentName).

    A minimal body matches what Bambu Studio sends and avoids reverting
    concurrent cloud-side changes (e.g. AMS weight updates between polls)
    that a full read-modify-write of the cached spool would write back.
    """
    coordinator, spool = _find_spool(hass, spool_id)
    payload = {
        "id": spool["id"],
        "filamentName": spool.get("filamentName") or "",
        **fields,
    }
    await _cloud_write(
        hass,
        coordinator,
        coordinator.client.update_spool,
        payload,
        action="Bambu cloud rejected the update",
    )
    await coordinator.async_request_refresh()


def _book_weight_change(
    coordinator: BambuFilamentsCoordinator,
    spool_id: int,
    before: float,
    after: float,
    kind: str,
    text: str | None = None,
) -> None:
    """History entry for a manual weight change; refreshes the entities."""
    if not coordinator.ledger.loaded:
        return
    coordinator.ledger.add_history(
        spool_id, kind, grams=float(after) - before, rest=after, text=text
    )
    coordinator.ledger_changed()


async def async_set_printer_tray(
    hass: HomeAssistant, spool: dict[str, Any], tray: dict[str, Any]
) -> None:
    """Set the printer tray (filament profile + colour) with the printer integration."""
    # Printer first: Studio drops a slot binding whose tray does not
    # match the spool, so the tray must already show this filament.
    filament_id = str(spool.get("filamentId") or "")
    material = str(spool.get("filamentType") or "")
    if not filament_id or not material:
        raise HomeAssistantError(
            f"Spool {spool['id']} has no filament id/material; cannot set the printer tray"
        )
    colors = spool.get("colors")
    color = normalize_hex(
        (colors[0] if isinstance(colors, list) and colors else None)
        or spool.get("color")
    )
    if not color:
        raise HomeAssistantError(f"Spool {spool['id']} has no valid colour")
    tray_state = hass.states.get(tray["entity_id"])
    attrs = tray_state.attributes if tray_state else {}
    tmin = tmax = None
    if str(attrs.get("filament_id") or "") == filament_id:
        tmin, tmax = attrs.get("nozzle_temp_min"), attrs.get("nozzle_temp_max")
    if not (tmin and tmax):
        tmin, tmax = DEFAULT_NOZZLE_TEMPS.get(material.upper(), (190, 240))
    data = {
        "entity_id": tray["entity_id"],
        "tray_info_idx": filament_id,
        "tray_type": material,
        # RRGGBBAA, no '#': the printer integration publishes it as-is.
        "tray_color": color.lstrip("#"),
        "nozzle_temp_min": int(tmin),
        "nozzle_temp_max": int(tmax),
    }
    await hass.services.async_call(BAMBULAB_DOMAIN, "set_filament", data, blocking=True)
    deadline = time.monotonic() + TRAY_WAIT_S
    while True:
        st = hass.states.get(tray["entity_id"])
        a = st.attributes if st else {}
        if (
            str(a.get("filament_id") or "") == filament_id
            and normalize_hex(str(a.get("color") or "")) == color
        ):
            break
        if time.monotonic() >= deadline:
            _LOGGER.warning(
                "Tray %s did not report filament %s within %s s; syncing the slot anyway",
                tray["entity_id"], filament_id, TRAY_WAIT_S,
            )
            break
        await asyncio.sleep(0.5)


async def async_assign(
    hass: HomeAssistant,
    coordinator: BambuFilamentsCoordinator,
    spool: dict[str, Any],
    tray: dict[str, Any],
    set_printer: bool,
    refresh: bool = True,
) -> dict[str, Any]:
    """Bind a spool to a slot in the cloud; with set_printer set the printer tray first."""
    rfid = str(spool.get("RFID") or "")
    if set_printer:
        await async_set_printer_tray(hass, spool, tray)
    cloud = await _cloud_write(
        hass,
        coordinator,
        coordinator.client.sync_slot_mappings,
        tray["dev_id"],
        [_mapping(tray, int(spool["id"]), rfid)],
        action="Assigning the spool failed",
    )
    if refresh:
        await coordinator.async_request_refresh()
    return {
        "cloud": cloud,
        "dev_id": tray["dev_id"],
        "ams_id": tray["ams_id"],
        "slot_id": tray["slot_id"],
    }


async def async_unassign(
    hass: HomeAssistant,
    coordinator: BambuFilamentsCoordinator,
    tray: dict[str, Any],
    refresh: bool = True,
) -> dict[str, Any]:
    """Clear the cloud binding of a slot."""
    cloud = await _cloud_write(
        hass,
        coordinator,
        coordinator.client.sync_slot_mappings,
        tray["dev_id"],
        [_mapping(tray, 0, "")],
        action="Unassigning the slot failed",
    )
    if refresh:
        await coordinator.async_request_refresh()
    return {
        "cloud": cloud,
        "dev_id": tray["dev_id"],
        "ams_id": tray["ams_id"],
        "slot_id": tray["slot_id"],
    }


async def async_weigh(
    hass: HomeAssistant,
    spool_id: int,
    weight: int,
    with_spool: bool = True,
    empty_spool: str | None = None,
) -> dict[str, Any]:
    """Book a scale reading (with or without the empty spool) as remaining filament."""
    coordinator, spool = _find_spool(hass, spool_id)
    ledger = coordinator.ledger
    await ledger.async_load()
    esid = None
    tare: dict[str, Any] | None = None
    if with_spool:
        esid = empty_spool or ledger.suggest_empty_spool(spool)
        tare = ledger.empty_spool(esid) if esid else None
        if tare is None:
            raise HomeAssistantError(
                "Leerspule unbekannt - bitte eine Leerspule wählen oder 'Nur Filament' wiegen"
            )
        net = max(0, weight - int(tare["weight_g"]))
    else:
        net = weight
    before = float(spool.get("netWeight") or 0)
    total = float(spool.get("totalNetWeight") or 0)
    await _update_fields(hass, spool["id"], {"netWeight": net})
    if esid:
        ledger.remember_empty_spool(spool["id"], esid)
    _book_weight_change(
        coordinator,
        spool["id"],
        before,
        net,
        "wiegen",
        text=f"{weight} g mit {tare['name']}" if tare else f"{weight} g ohne Spule",
    )
    return {
        "spool_id": spool["id"],
        "remaining_g": net,
        "previous_g": before,
        "remaining_pct": max(0, min(100, round(net / total * 100))) if total > 0 else 0,
        "empty": net <= 0,
        "empty_spool": esid,
    }


def async_register_services(hass: HomeAssistant) -> None:
    """Register domain-level services (idempotent)."""

    async def handle_refresh(call: ServiceCall) -> None:
        for coordinator in _coordinators(hass):
            await coordinator.async_request_refresh()

    async def handle_set_remaining(call: ServiceCall) -> None:
        coordinator, spool = _find_spool(hass, call.data["spool_id"])
        before = float(spool.get("netWeight") or 0)
        await _update_fields(
            hass, call.data["spool_id"], {"netWeight": call.data["remaining_g"]}
        )
        _book_weight_change(
            coordinator, call.data["spool_id"], before, call.data["remaining_g"], "korrektur"
        )

    async def handle_set_note(call: ServiceCall) -> None:
        await _update_fields(hass, call.data["spool_id"], {"note": call.data["note"]})

    async def handle_set_filament_id(call: ServiceCall) -> None:
        # Verified live: a minimal PUT changes filamentId in place and leaves
        # vendor/material/displayName/color untouched, so an existing custom
        # spool can be linked to a Studio slicer profile retroactively.
        await _update_fields(
            hass,
            call.data["spool_id"],
            {"filamentId": call.data["filament_id"].strip()},
        )

    async def handle_create_spool(call: ServiceCall) -> dict[str, Any]:
        coordinator = _first_coordinator(hass)
        await coordinator.ledger.async_load()
        total = call.data["total_g"]
        esid = call.data.get("empty_spool")
        if esid and coordinator.ledger.empty_spool(esid) is None:
            raise HomeAssistantError(f"Unknown empty spool '{esid}'")
        remaining = call.data.get("remaining_g", total)
        if "gross_g" in call.data:
            tare = coordinator.ledger.empty_spool(esid) if esid else None
            if tare is None:
                raise HomeAssistantError("gross_g needs an empty_spool (its tare weight)")
            remaining = max(0, call.data["gross_g"] - int(tare["weight_g"]))
        # The create endpoint expects #RRGGBB without alpha (Studio MITM shape);
        # manual entries omit RFID/trayIdName/rolls, and there is no `colors`,
        # `note` or `status` field in the create body.
        payload: dict[str, Any] = {
            "createType": "manual",
            "filamentVendor": call.data["vendor"],
            "filamentType": call.data["material"],
            "filamentName": call.data["name"],
            "color": normalize_hex(call.data["color"])[:7],
            "colorType": 2,
            "isSupport": False,
            "netWeight": remaining,
            "totalNetWeight": total,
        }
        filament_id = call.data.get("filament_id")
        if filament_id is None:
            # Best effort for official filaments: resolve the canonical id from
            # the catalog - exact name match first, then vendor + material.
            # An EXPLICIT empty string means "custom/non-official spool" and
            # deliberately skips this lookup.
            try:
                catalog = await coordinator.async_get_catalog()
            except BambuCloudError:
                catalog = []
            for item in catalog:
                if (
                    item["vendor"] == payload["filamentVendor"]
                    and item["name"] == payload["filamentName"]
                ):
                    filament_id = item["filament_id"]
                    break
            else:
                for item in catalog:
                    if (
                        item["vendor"] == payload["filamentVendor"]
                        and item["material"] == payload["filamentType"]
                    ):
                        filament_id = item["filament_id"]
                        break
        # The cloud requires the filamentId FIELD to be present (missing field
        # -> HTTP 400) but accepts an empty string for custom/third-party
        # brands - that is how Studio models "non-official" spools.
        payload["filamentId"] = filament_id or ""
        if display_name := call.data.get("display_name"):
            payload["displayName"] = display_name
        known_ids = set((coordinator.data or {}).keys())
        count = call.data["count"]
        created_n = 0
        try:
            for _ in range(count):
                await _cloud_write(
                    hass,
                    coordinator,
                    coordinator.client.create_spool,
                    payload,
                    action="Bambu cloud rejected the new spool",
                )
                created_n += 1
        except HomeAssistantError as err:
            if created_n == 0:
                raise
            # Partial batch: keep what was created, tell the caller how far
            # it got (the refresh below shows the spools that made it).
            await coordinator.async_request_refresh()
            raise HomeAssistantError(
                f"{err} (created {created_n} of {count} spools before the error)"
            ) from err
        # Identify the just-created spool(s): re-fetch and take the newest ids
        # we did not know before that match vendor + name. Needed for the
        # note (the create endpoint 400s on a note field, so it is written
        # with a follow-up PUT), the ledger and the caller (insert into tray).
        created: list[dict[str, Any]] = []
        try:
            spools = await hass.async_add_executor_job(coordinator.client.get_spools)
            created = sorted(
                (
                    s
                    for s in spools
                    if isinstance(s.get("id"), int)
                    and s["id"] not in known_ids
                    and s.get("filamentVendor") == payload["filamentVendor"]
                    and s.get("filamentName") == payload["filamentName"]
                ),
                key=lambda s: s["id"],
                reverse=True,
            )[:created_n]
        except BambuCloudError as err:
            _LOGGER.warning("Spool was created but the re-fetch failed: %s", err)
        if not created:
            _LOGGER.warning("Spool was created but not found on re-fetch; note/ledger not set")
        note = call.data.get("note")
        ledger = coordinator.ledger
        for target in created:
            if note:
                try:
                    await hass.async_add_executor_job(
                        coordinator.client.update_spool,
                        {
                            "id": target["id"],
                            "filamentName": target.get("filamentName") or "",
                            "note": note,
                        },
                    )
                except BambuCloudError as err:
                    _LOGGER.warning("Spool was created but setting its note failed: %s", err)
            ledger.remember_empty_spool(target["id"], esid)
            ledger.set_color_name(target["id"], call.data.get("color_name"))
            ledger.add_history(
                target["id"],
                "angelegt",
                rest=remaining,
                text="gewogen" if "gross_g" in call.data else None,
            )
        ledger.remember_brand(
            payload["filamentVendor"],
            material=payload["filamentType"],
            name=payload["filamentName"],
            filament_id=payload["filamentId"],
            empty_spool=esid,
            total_g=total,
        )
        await coordinator.async_request_refresh()
        coordinator.ledger_changed()
        return {"spool_ids": [t["id"] for t in created], "remaining_g": remaining}

    async def handle_update_spool(call: ServiceCall) -> None:
        fields: dict[str, Any] = {
            cloud_key: call.data[key]
            for key, cloud_key in UPDATE_FIELD_MAP.items()
            if key in call.data
        }
        if "color" in call.data:
            color = normalize_hex(call.data["color"])[:7]
            # Also rewrite the colors array: the apps (and the card) display
            # `colors` over `color` when present, so updating only `color`
            # would leave the visible swatch unchanged. Verified: PUT accepts
            # `colors` (unlike create, which 400s on it).
            fields["color"] = color
            fields["colors"] = [color]
        if not fields:
            raise HomeAssistantError("update_spool needs at least one field to change")
        coordinator, spool = _find_spool(hass, call.data["spool_id"])
        before = float(spool.get("netWeight") or 0)
        await _update_fields(hass, call.data["spool_id"], fields)
        if "remaining_g" in call.data:
            _book_weight_change(
                coordinator, call.data["spool_id"], before, call.data["remaining_g"], "korrektur"
            )

    async def handle_delete_spool(call: ServiceCall) -> None:
        coordinator, _spool = _find_spool(hass, call.data["spool_id"])
        await _cloud_write(
            hass,
            coordinator,
            coordinator.client.delete_spools,
            [call.data["spool_id"]],
            action="Deleting the spool failed",
        )
        await coordinator.async_request_refresh()

    async def handle_get_catalog(call: ServiceCall) -> dict[str, Any]:
        """Canonical vendor/product combos the cloud accepts (cached 1 h).

        `presets` lists the account's own slicer presets (filament_id only,
        e.g. "P1a2b3c4"): valid as a spool's slicer profile, but not as an
        official vendor/product combination.
        """
        coordinator = _first_coordinator(hass)
        try:
            filaments = await coordinator.async_get_catalog()
        except BambuCloudError as err:
            raise HomeAssistantError(f"Fetching the filament catalog failed: {err}") from err
        try:
            presets = await coordinator.async_get_user_presets()
        except BambuCloudError:
            presets = []
        return {"filaments": filaments, "presets": presets}

    async def handle_assign_spool(call: ServiceCall) -> dict[str, Any]:
        coordinator, spool = _find_spool(hass, call.data["spool_id"])
        tray = _resolve_tray(hass, call.data["tray"])
        ensure_idle(hass, tray)
        return await async_assign(hass, coordinator, spool, tray, call.data["set_printer"])

    async def handle_unassign_slot(call: ServiceCall) -> dict[str, Any]:
        coordinator = _first_coordinator(hass)
        tray = _resolve_tray(hass, call.data["tray"])
        return await async_unassign(hass, coordinator, tray)

    async def handle_load_spool(call: ServiceCall) -> dict[str, Any]:
        try:
            coordinator, spool = _find_spool(hass, call.data["spool_id"])
        except HomeAssistantError as err:
            raise HomeAssistantError(
                f"Spule {call.data['spool_id']} ist in keiner Filament-Bibliothek."
            ) from err
        tray = _resolve_tray(hass, call.data["tray"])
        options = coordinator.config_entry.options
        result = await async_load_spool(
            hass,
            spool=spool,
            tray=tray,
            trays=list_trays(hass),
            assign=lambda sp, tr, set_printer: async_assign(
                hass, coordinator, sp, tr, set_printer, refresh=False
            ),
            unassign=lambda tr: async_unassign(hass, coordinator, tr, refresh=False),
            tray_command=options.get(OPT_TRAY_COMMAND, DEFAULT_TRAY_COMMAND),
            tray_service=str(options.get(OPT_TRAY_SERVICE, DEFAULT_TRAY_SERVICE) or "").strip(),
        )
        if tracker := hass.data.get(DOMAIN, {}).get("tray_tracker"):
            tracker.clear(tray["entity_id"])
        await coordinator.async_request_refresh()
        if "weight_g" in call.data:
            result["weighed"] = await async_weigh(
                hass,
                spool["id"],
                call.data["weight_g"],
                call.data["with_spool"],
                call.data.get("empty_spool"),
            )
        return result

    async def handle_unload_slot(call: ServiceCall) -> dict[str, Any]:
        coordinator = _first_coordinator(hass)
        tray = _resolve_tray(hass, call.data["tray"])
        if tracker := hass.data.get(DOMAIN, {}).get("tray_tracker"):
            tracker.clear(tray["entity_id"])
        return await async_unload_slot(
            hass, tray=tray, unassign=lambda tr: async_unassign(hass, coordinator, tr)
        )

    async def handle_weigh_spool(call: ServiceCall) -> dict[str, Any]:
        return await async_weigh(
            hass,
            call.data["spool_id"],
            call.data["weight_g"],
            call.data["with_spool"],
            call.data.get("empty_spool"),
        )

    async def handle_set_empty_spool(call: ServiceCall) -> dict[str, Any]:
        coordinator = _first_coordinator(hass)
        await coordinator.ledger.async_load()
        try:
            item = coordinator.ledger.set_empty_spool(
                call.data.get("name"),
                call.data.get("weight_g"),
                call.data.get("vendor"),
                call.data.get("empty_spool"),
            )
        except KeyError as err:
            raise HomeAssistantError(f"Unknown empty spool {err}") from err
        except ValueError as err:
            raise HomeAssistantError(str(err)) from err
        coordinator.ledger_changed()
        return dict(item)

    async def handle_delete_empty_spool(call: ServiceCall) -> None:
        coordinator = _first_coordinator(hass)
        await coordinator.ledger.async_load()
        try:
            coordinator.ledger.delete_empty_spool(call.data["empty_spool"])
        except KeyError as err:
            raise HomeAssistantError(f"Unknown empty spool {err}") from err
        coordinator.ledger_changed()

    async def handle_archive_spool(call: ServiceCall) -> None:
        coordinator, spool = _find_spool(hass, call.data["spool_id"])
        await coordinator.ledger.async_load()
        archived = call.data["archived"]
        if coordinator.ledger.set_archived(spool["id"], archived):
            coordinator.ledger.add_history(
                spool["id"],
                "archiv" if archived else "zurueck",
                rest=spool.get("netWeight"),
            )
        coordinator.ledger_changed()

    async def handle_get_history(call: ServiceCall) -> dict[str, Any]:
        coordinator, spool = _find_spool(hass, call.data["spool_id"])
        await coordinator.ledger.async_load()
        return {
            "spool_id": spool["id"],
            "archived": coordinator.ledger.is_archived(spool["id"]),
            "history": coordinator.ledger.history(spool["id"], call.data["limit"]),
        }

    async def handle_set_color_name(call: ServiceCall) -> None:
        coordinator, spool = _find_spool(hass, call.data["spool_id"])
        await coordinator.ledger.async_load()
        coordinator.ledger.set_color_name(spool["id"], call.data["color_name"])
        coordinator.ledger_changed()

    hass.services.async_register(DOMAIN, SERVICE_REFRESH, handle_refresh)
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_HISTORY,
        handle_get_history,
        GET_HISTORY_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_CATALOG,
        handle_get_catalog,
        supports_response=SupportsResponse.ONLY,
    )
    # Cloud-write services are admin-only: they irreversibly modify the user's
    # Bambu account (automations without a user context are still allowed).
    async_register_admin_service(
        hass, DOMAIN, SERVICE_SET_REMAINING, handle_set_remaining, SET_REMAINING_SCHEMA
    )
    async_register_admin_service(
        hass, DOMAIN, SERVICE_SET_NOTE, handle_set_note, SET_NOTE_SCHEMA
    )
    async_register_admin_service(
        hass, DOMAIN, SERVICE_SET_FILAMENT_ID, handle_set_filament_id, SET_FILAMENT_ID_SCHEMA
    )
    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_CREATE_SPOOL,
        handle_create_spool,
        CREATE_SPOOL_SCHEMA,
        SupportsResponse.OPTIONAL,
    )
    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_WEIGH_SPOOL,
        handle_weigh_spool,
        WEIGH_SPOOL_SCHEMA,
        SupportsResponse.OPTIONAL,
    )
    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_SET_EMPTY_SPOOL,
        handle_set_empty_spool,
        SET_EMPTY_SPOOL_SCHEMA,
        SupportsResponse.OPTIONAL,
    )
    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_DELETE_EMPTY_SPOOL,
        handle_delete_empty_spool,
        DELETE_EMPTY_SPOOL_SCHEMA,
    )
    async_register_admin_service(
        hass, DOMAIN, SERVICE_ARCHIVE_SPOOL, handle_archive_spool, ARCHIVE_SPOOL_SCHEMA
    )
    async_register_admin_service(
        hass, DOMAIN, SERVICE_SET_COLOR_NAME, handle_set_color_name, SET_COLOR_NAME_SCHEMA
    )
    async_register_admin_service(
        hass, DOMAIN, SERVICE_DELETE_SPOOL, handle_delete_spool, DELETE_SPOOL_SCHEMA
    )
    async_register_admin_service(
        hass, DOMAIN, SERVICE_UPDATE_SPOOL, handle_update_spool, UPDATE_SPOOL_SCHEMA
    )
    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_ASSIGN_SPOOL,
        handle_assign_spool,
        ASSIGN_SPOOL_SCHEMA,
        SupportsResponse.OPTIONAL,
    )
    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_LOAD_SPOOL,
        handle_load_spool,
        LOAD_SPOOL_SCHEMA,
        SupportsResponse.OPTIONAL,
    )
    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_UNLOAD_SLOT,
        handle_unload_slot,
        UNLOAD_SLOT_SCHEMA,
        SupportsResponse.OPTIONAL,
    )
    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_UNASSIGN_SLOT,
        handle_unassign_slot,
        UNASSIGN_SLOT_SCHEMA,
        SupportsResponse.OPTIONAL,
    )
