"""Spool labels as Home Assistant tags: QR/NFC scan opens the spool page on that phone.

Each spool gets the tag ``spoolbook_<spool_id>`` (labels printed before the rename
carry ``bambu_spule_<id>`` and are still accepted). Its QR code holds
``https://www.home-assistant.io/tag/spoolbook_<id>``: the Companion app
handles that link, and HA fires ``tag_scanned`` with the scanning phone's
device registry id. We answer exactly that phone with the Companion command
``command_webview`` (navigate the app to ``/spoolbook?spule=<id>``).

The notify action of a phone is looked up the way mobile_app's own device
action does it (device id -> webhook id -> notify service); a fallback uses
the device name (``notify.mobile_app_<slug>``).
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.util import slugify

from .const import LEGACY_TAG_PREFIX, PANEL_URL_PATH, TAG_PREFIX

_LOGGER = logging.getLogger(__name__)

MOBILE_APP = "mobile_app"
TAG_DOMAIN = "tag"


def tag_id_for(spool_id: int | str) -> str:
    return f"{TAG_PREFIX}{int(spool_id)}"


def spool_id_from_tag(tag_id: Any) -> int | None:
    text = str(tag_id or "")
    prefix = next((p for p in (TAG_PREFIX, LEGACY_TAG_PREFIX) if text.startswith(p)), None)
    if prefix is None:
        return None
    try:
        value = int(text[len(prefix):])
    except ValueError:
        return None
    return value if value > 0 else None


def page_path(spool_id: int) -> str:
    return f"/{PANEL_URL_PATH}?spule={int(spool_id)}"


def resolve_notify(hass: HomeAssistant, device_id: str) -> tuple[str, dict[str, Any]] | None:
    """(notify service name, extra service data) for a mobile_app device, or None."""
    try:
        from homeassistant.components.mobile_app.util import (  # noqa: PLC0415
            get_notify_service,
            webhook_id_from_device_id,
        )

        webhook_id = webhook_id_from_device_id(hass, device_id)
        if webhook_id and (service := get_notify_service(hass, webhook_id)):
            return service, {"target": webhook_id}
    except Exception:  # noqa: BLE001 - mobile_app missing/changed: use the fallback
        _LOGGER.debug("mobile_app lookup failed for %s", device_id, exc_info=True)
    device = dr.async_get(hass).async_get(device_id)
    if device is None or not any(i[0] == MOBILE_APP for i in device.identifiers):
        return None
    service = f"{MOBILE_APP}_{slugify(device.name or '')}"
    if hass.services.has_service("notify", service):
        return service, {}
    return None


async def async_handle_tag_scanned(
    hass: HomeAssistant, data: Mapping[str, Any], allowed_devices: Collection[str] = ()
) -> str | None:
    """Open the spool page on the phone that scanned; returns the notify service used."""
    spool_id = spool_id_from_tag(data.get("tag_id"))
    if spool_id is None:
        return None
    device_id = data.get("device_id")
    if not device_id:
        _LOGGER.debug("Spool tag %s scanned without a device", data.get("tag_id"))
        return None
    if allowed_devices and device_id not in allowed_devices:
        _LOGGER.debug("Spool tag scanned by %s, not in the configured phones", device_id)
        return None
    found = resolve_notify(hass, device_id)
    if found is None:
        _LOGGER.info("Spool tag scanned by %s, but no mobile_app notify service found", device_id)
        return None
    service, extra = found
    await hass.services.async_call(
        "notify",
        service,
        {
            "message": "command_webview",
            "data": {"command": page_path(spool_id)},
            **extra,
        },
        blocking=False,
    )
    return service


def tag_name(spool: Mapping[str, Any]) -> str:
    parts = [spool.get("filamentVendor"), spool.get("filamentType"), spool.get("filamentName")]
    seen: list[str] = []
    for part in parts:
        if part and str(part) not in seen:
            seen.append(str(part))
    return ("Spule " + " ".join(seen)).strip()[:60] + f" #{spool.get('id')}"


async def async_ensure_tags(
    hass: HomeAssistant, spools: Iterable[Mapping[str, Any]]
) -> dict[int, str]:
    """Create the HA tag of every given spool that has none yet; {spool_id: tag_id}."""
    out: dict[int, str] = {}
    collection = hass.data.get(TAG_DOMAIN)
    items = getattr(collection, "data", None)
    for spool in spools:
        sid = int(spool["id"])
        tid = tag_id_for(sid)
        out[sid] = tid
        if collection is None or items is None or tid in items:
            continue
        try:
            await collection.async_create_item({"tag_id": tid, "name": tag_name(spool)})
        except Exception as err:  # noqa: BLE001 - a scan creates it anyway
            _LOGGER.warning("Could not create tag %s: %s", tid, err)
    if collection is None:
        _LOGGER.info("tag integration not loaded; tags are created on first scan")
    return out
