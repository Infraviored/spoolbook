"""Websocket API for the Spulen card and the sidebar page.

spoolbook/trays        AMS slots (from ha-bambulab) with their bound
                             spool and the "recently loaded, unbound" hint,
                             plus the library sensor's entity id.
spoolbook/ensure_tags  Create the HA tag for the given spools (labels).
"""

from __future__ import annotations

import time
from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN, TAG_URL_BASE
from .tags import async_ensure_tags
from .trays import BUSY_STATES, bound_spool, list_trays, print_state, tray_active


def _coordinators(hass: HomeAssistant) -> list[Any]:
    from .coordinator import BambuFilamentsCoordinator  # noqa: PLC0415

    return [
        entry.runtime_data
        for entry in hass.config_entries.async_loaded_entries(DOMAIN)
        if isinstance(getattr(entry, "runtime_data", None), BambuFilamentsCoordinator)
    ]


def _library_entity(hass: HomeAssistant) -> str | None:
    reg = er.async_get(hass)
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        if eid := reg.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}-spool-count"):
            return eid
    return None


def trays_payload(hass: HomeAssistant) -> dict[str, Any]:
    spools: list[dict[str, Any]] = []
    for coordinator in _coordinators(hass):
        spools.extend((coordinator.data or {}).values())
    tracker = hass.data.get(DOMAIN, {}).get("tray_tracker")
    recent = tracker.recent() if tracker else {}
    now = time.time()
    out = []
    for tray in list_trays(hass):
        state = hass.states.get(tray["entity_id"])
        attrs = state.attributes if state else {}
        spool = bound_spool(spools, tray)
        loaded_at = recent.get(tray["entity_id"])
        live = used = None
        for coordinator in _coordinators(hass):
            used = bool(used) or coordinator.live.used_in_print(tray)
            net = float(spool.get("netWeight") or 0) if spool else None
            live = max(live or 0.0, coordinator.live.live_g(tray, net))
        out.append(
            {
                "entity_id": tray["entity_id"],
                "label": tray["label"],
                "ams_name": tray["ams_name"],
                "printer_name": tray["printer_name"],
                "dev_id": tray["dev_id"],
                "ams_id": tray["ams_id"],
                "slot_id": tray["slot_id"],
                "slot": tray["slot"],
                "empty": bool(attrs.get("empty")) if state else None,
                "material": attrs.get("type") or (state.state if state else None),
                "color": attrs.get("color"),
                "active": tray_active(hass, tray, state),
                "busy": print_state(hass, tray) in BUSY_STATES,
                "spool_id": spool.get("id") if spool else None,
                "used_in_print": bool(used),
                "live_used_g": live or 0.0,
                "loaded_ago_s": int(now - loaded_at) if loaded_at else None,
            }
        )
    pending = sorted(
        (t for t in out if t["loaded_ago_s"] is not None and t["spool_id"] is None and not t["empty"]),
        key=lambda t: t["loaded_ago_s"],
    )
    return {
        "trays": out,
        "pending": [t["entity_id"] for t in pending],
        "library": _library_entity(hass),
        "tag_url": TAG_URL_BASE,
    }


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/trays"})
@callback
def ws_trays(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    connection.send_result(msg["id"], trays_payload(hass))


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/ensure_tags",
        vol.Required("spool_ids"): [vol.Coerce(int)],
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_ensure_tags(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    wanted = set(msg["spool_ids"])
    spools = [
        spool
        for coordinator in _coordinators(hass)
        for sid, spool in (coordinator.data or {}).items()
        if sid in wanted
    ]
    tags = await async_ensure_tags(hass, spools)
    connection.send_result(
        msg["id"], {"tags": {str(k): v for k, v in tags.items()}, "tag_url": TAG_URL_BASE}
    )


@callback
def async_register_websocket(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_trays)
    websocket_api.async_register_command(hass, ws_ensure_tags)
