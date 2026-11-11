"""Load a library spool into an AMS slot (service load_spool) and empty a slot.

Order, as the household script did it before:

1. Print lock: the printer of the slot must be idle (its ha-bambulab print
   status sensor, found through the slot's device - no fixed entity names).
2. A spool bound to another slot is released there first, so it never sits
   in two slots.
3. The printer slot is set (filament profile + colour). Either
   - "own": the local command of assign_spool (bambu_lab.set_filament), or
   - "service": a configurable HA action (e.g. a rest_command) called with
     {"befehl": "ams/set_tray", "daten": {ams_id, slot, filament_id, color,
     type}}. Its response status decides: 200 = set, 409 = RFID slot (the
     printer reads the spool itself, only bind), 404 / no answer = the route
     is not there, fall back to the own command; anything else is an error.
4. The cloud slot binding (assign_spool).

The cloud/printer calls are injected (assign/unassign), so this module runs
in tests without Home Assistant.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Mapping
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import TRAY_COMMAND_SERVICE
from .trays import ensure_idle, spool_tray

_LOGGER = logging.getLogger(__name__)

TRAY_SERVICE_COMMAND = "ams/set_tray"

Assign = Callable[[Mapping[str, Any], Mapping[str, Any], bool], Awaitable[Any]]
Unassign = Callable[[Mapping[str, Any]], Awaitable[Any]]


def _color7(spool: Mapping[str, Any]) -> str:
    colors = spool.get("colors")
    raw = (colors[0] if isinstance(colors, list) and colors else None) or spool.get("color")
    value = str(raw or "").strip().lstrip("#")[:6].upper()
    if len(value) != 6 or any(c not in "0123456789ABCDEF" for c in value):
        value = "808080"
    return "#" + value


def tray_service_data(spool: Mapping[str, Any], tray: Mapping[str, Any]) -> dict[str, Any]:
    """Data for the "service" tray command (same shape the household script sent)."""
    return {
        "befehl": TRAY_SERVICE_COMMAND,
        "daten": {
            "ams_id": tray["ams_id"],
            "slot": int(tray["slot_id"]),
            "filament_id": str(spool.get("filamentId") or ""),
            "color": _color7(spool),
            "type": str(spool.get("filamentType") or ""),
        },
    }


def _wants_response(hass: HomeAssistant, domain: str, service: str) -> bool:
    try:
        support = hass.services.supports_response(domain, service)
    except Exception:  # noqa: BLE001 - unknown service: the call below says so
        return True
    return str(getattr(support, "value", support)).lower() != "none"


async def async_call_tray_service(
    hass: HomeAssistant, service: str, data: dict[str, Any]
) -> tuple[int, Any]:
    """Call the configured action; (status, content). 0 = no answer / call failed."""
    domain, _, name = service.partition(".")
    if not domain or not name:
        raise HomeAssistantError(
            f"Dienst für das Drucker-Fach ungültig: '{service}' (Form domain.dienst)."
        )
    try:
        if not _wants_response(hass, domain, name):
            await hass.services.async_call(domain, name, data, blocking=True)
            return 200, None
        response = await hass.services.async_call(
            domain, name, data, blocking=True, return_response=True
        )
    except Exception as err:  # noqa: BLE001 - like continue_on_error: fall back
        _LOGGER.warning("Drucker-Fach über %s fehlgeschlagen: %s", service, err)
        return 0, str(err)
    if not isinstance(response, Mapping):
        return 200, response
    try:
        status = int(response.get("status", 200))
    except (TypeError, ValueError):
        status = 0
    return status, response.get("content")


async def async_printer_slot_service(
    hass: HomeAssistant,
    spool: Mapping[str, Any],
    tray: Mapping[str, Any],
    tray_command: str,
    tray_service: str = "",
) -> tuple[str, bool]:
    """Step 3 via the configured action; (what happened, still needs the own command)."""
    printer = "eigener Befehl"
    set_printer = True
    if tray_command == TRAY_COMMAND_SERVICE and tray_service:
        status, content = await async_call_tray_service(
            hass, tray_service, tray_service_data(spool, tray)
        )
        if status == 200:
            printer, set_printer = "gesetzt", False
        elif status == 409:
            printer, set_printer = "RFID-Fach", False
        elif status in (0, 404):
            printer = "eigener Befehl (Dienst nicht erreichbar)"
        else:
            _LOGGER.warning(
                "%s %s Spule %s: %s %s",
                TRAY_SERVICE_COMMAND, tray["entity_id"], spool.get("id"), status, content,
            )
            raise HomeAssistantError(
                f"Drucker-Fach nicht gesetzt, der Dienst {tray_service} hat mit Status "
                f"{status} abgelehnt (Details im Log)."
            )
    return printer, set_printer


async def async_load_spool(
    hass: HomeAssistant,
    *,
    spool: Mapping[str, Any],
    tray: Mapping[str, Any],
    trays: Iterable[Mapping[str, Any]],
    assign: Assign,
    unassign: Unassign,
    tray_command: str,
    tray_service: str = "",
) -> dict[str, Any]:
    """Steps 1-4 above; returns what happened (for the caller / the card)."""
    ensure_idle(hass, tray)
    released = None
    old = spool_tray(spool, trays)
    if old is not None and old["entity_id"] != tray["entity_id"]:
        await unassign(old)
        released = old["entity_id"]
    printer, set_printer = await async_printer_slot_service(
        hass, spool, tray, tray_command, tray_service
    )
    if set_printer and (not spool.get("filamentId") or not spool.get("filamentType")):
        raise HomeAssistantError(
            f"Spule {spool.get('id')} hat kein Druckerprofil oder Material, "
            "das Drucker-Fach kann nicht gesetzt werden."
        )
    await assign(spool, tray, set_printer)
    return {
        "spool_id": spool.get("id"),
        "tray": tray["entity_id"],
        "tray_label": tray.get("label"),
        "printer": printer,
        "released": released,
    }


async def async_unload_slot(
    hass: HomeAssistant, *, tray: Mapping[str, Any], unassign: Unassign
) -> dict[str, Any]:
    ensure_idle(hass, tray)
    await unassign(tray)
    return {"tray": tray["entity_id"], "tray_label": tray.get("label")}
