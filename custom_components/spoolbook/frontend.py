"""Serve the Bambu Filaments card and register it as a Lovelace resource."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

from .const import (
    CARD_FILENAME,
    CARD_URL_BASE,
    PANEL_COMPONENT,
    PANEL_ICON,
    PANEL_TITLE,
    PANEL_URL_PATH,
    QR_JS_FILENAME,
    SPULEN_JS_FILENAME,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_frontend(hass: HomeAssistant, version: str) -> None:
    """Register the static path for the card and add the Lovelace resource."""
    www_path = Path(__file__).parent / "www"
    try:
        await hass.http.async_register_static_paths(
            [StaticPathConfig(CARD_URL_BASE, str(www_path), cache_headers=True)]
        )
    except RuntimeError:
        # Path already registered (e.g. after a config entry reload).
        pass
    await _ensure_resource(hass, f"{CARD_URL_BASE}/{CARD_FILENAME}?v={version}")
    url = await _add_spulen_js(hass, www_path)
    if url:
        await _add_panel(hass, url)


async def _add_spulen_js(hass: HomeAssistant, www_path: Path) -> str | None:
    """Load the Spulen card (weigh/create/stock/slot) on every frontend page.

    add_extra_js_url instead of a Lovelace resource: no write to the
    dashboard resources, and the card is also there inside popups of other
    cards. The content hash busts the browser cache.
    """
    try:
        from homeassistant.components.frontend import add_extra_js_url

        data = await hass.async_add_executor_job((www_path / SPULEN_JS_FILENAME).read_bytes)
        digest = hashlib.sha1(data).hexdigest()[:10]
        url = f"{CARD_URL_BASE}/{SPULEN_JS_FILENAME}?v={digest}"
        add_extra_js_url(hass, url)
        return url
    except Exception:  # noqa: BLE001 - the card is optional, setup must go on
        _LOGGER.exception("Could not register the Spoolbook card")
        return None


async def _add_panel(hass: HomeAssistant, module_url: str) -> None:
    """Sidebar page "Spulen" (/spoolbook, ?spule=<id> opens a spool)."""
    try:
        from homeassistant.components import panel_custom

        await panel_custom.async_register_panel(
            hass,
            frontend_url_path=PANEL_URL_PATH,
            webcomponent_name=PANEL_COMPONENT,
            sidebar_title=PANEL_TITLE,
            sidebar_icon=PANEL_ICON,
            module_url=module_url,
            require_admin=False,
            config={"qr_url": f"{CARD_URL_BASE}/{QR_JS_FILENAME}"},
        )
    except ValueError:
        # Already registered (entry reload in the same HA run).
        pass
    except Exception:  # noqa: BLE001 - the page is optional, setup must go on
        _LOGGER.exception("Could not register the Spulen sidebar page")


async def _ensure_resource(hass: HomeAssistant, url: str) -> None:
    lovelace = hass.data.get("lovelace")
    resources = getattr(lovelace, "resources", None)
    if resources is None or not hasattr(resources, "async_create_item"):
        _LOGGER.info(
            "Lovelace resources not editable (YAML mode?). Add the card resource "
            "manually: %s (type: module)",
            url,
        )
        return
    try:
        if not resources.loaded:
            await resources.async_load()
            resources.loaded = True
        base = url.split("?")[0]
        # Drop the stale card of the pre-rename integration.
        for item in list(resources.async_items()):
            if "/bambu_filaments_files/" in str(item.get("url", "")):
                await resources.async_delete_item(item["id"])
                _LOGGER.info("Removed stale Lovelace resource %s", item["url"])
        for item in list(resources.async_items()):
            if str(item.get("url", "")).split("?")[0] == base:
                if item["url"] != url:
                    await resources.async_update_item(
                        item["id"], {"res_type": "module", "url": url}
                    )
                    _LOGGER.info("Updated Bambu Filaments card resource to %s", url)
                return
        await resources.async_create_item({"res_type": "module", "url": url})
        _LOGGER.info("Registered Bambu Filaments card resource %s", url)
    except Exception:  # noqa: BLE001 - resource registration must never break setup
        _LOGGER.exception("Could not register the Bambu Filaments card resource")
