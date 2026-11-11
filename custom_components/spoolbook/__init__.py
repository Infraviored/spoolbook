"""The Bambu Filaments integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import Event, HomeAssistant, callback
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.loader import async_get_integration

from .api import BambuCloudClient
from .colors import BambuColorDB
from .const import (
    CONF_REGION,
    CONF_TOKEN,
    DOMAIN,
    LEGACY_TAG_PREFIX,
    OPT_TAG_DEVICES,
    TAG_PREFIX,
)
from .coordinator import BambuFilamentsCoordinator
from .frontend import async_setup_frontend
from .migrate import async_start_import_flows, async_migrate_legacy_entry
from .services import async_register_services
from .tags import async_handle_tag_scanned
from .tray_sync import TraySync
from .trays import TrayTracker
from .websocket import async_register_websocket

# tag integration's event (homeassistant.components.tag.const.EVENT_TAG_SCANNED);
# literal so a missing tag integration does not break the import.
EVENT_TAG_SCANNED = "tag_scanned"

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS = [Platform.BUTTON, Platform.SENSOR]

type BambuFilamentsConfigEntry = ConfigEntry[BambuFilamentsCoordinator]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register domain-level services, websocket API, slot tracker and tag listener."""
    async_register_services(hass)
    async_register_websocket(hass)
    domain_data = hass.data.setdefault(DOMAIN, {})
    tracker = TrayTracker(hass)
    tracker.async_start()
    domain_data["tray_tracker"] = tracker

    async def _tag_scanned(event: Event) -> None:
        allowed: list[str] = []
        entries = hass.config_entries.async_loaded_entries(DOMAIN)
        if not entries:
            return
        for entry in entries:
            allowed.extend(entry.options.get(OPT_TAG_DEVICES) or [])
        await async_handle_tag_scanned(hass, event.data, allowed)

    @callback
    def _is_spool_tag(data) -> bool:
        return str(data.get("tag_id") or "").startswith((TAG_PREFIX, LEGACY_TAG_PREFIX))

    hass.bus.async_listen(EVENT_TAG_SCANNED, _tag_scanned, event_filter=_is_spool_tag)
    # One-time move of an old "bambu_filaments" install (see migrate.py).
    async_start_import_flows(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: BambuFilamentsConfigEntry) -> bool:
    """Set up a Bambu account from a config entry."""
    # Register the card once per HA run - re-registering the static path on
    # every entry reload would accumulate duplicate routes.
    domain_data = hass.data.setdefault(DOMAIN, {})
    integration = await async_get_integration(hass, DOMAIN)
    # Exposed as a sensor attribute so the card can detect a stale, cached
    # copy of itself after an update (browser tabs survive a HA restart).
    domain_data["version"] = str(integration.version)
    if not domain_data.get("frontend_registered"):
        await async_setup_frontend(hass, str(integration.version))
        domain_data["frontend_registered"] = True

    # Move the old install's storage/entities/devices over before any platform
    # or tracker creates fresh ones. Never fatal.
    await async_migrate_legacy_entry(hass, entry)

    client = BambuCloudClient(entry.data[CONF_REGION], entry.data[CONF_TOKEN])
    colordb = BambuColorDB(hass)
    await colordb.async_load()
    coordinator = BambuFilamentsCoordinator(hass, entry, client, colordb)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator

    # Create the hub device before the platforms run; per-spool devices link
    # to it by registry id (via_device_id - the identifier-based via_device is
    # deprecated since HA 2026.8 and removed in 2027.8).
    hub = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name="Spoolbook",
        manufacturer="Bambu Lab",
        model="Filament Manager",
    )
    coordinator.hub_device_id = hub.id

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await coordinator.live.async_start()
    tray_sync = TraySync(hass, coordinator)
    await tray_sync.async_start()
    entry.async_on_unload(tray_sync.async_stop)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: BambuFilamentsConfigEntry) -> None:
    """Reload the entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: BambuFilamentsConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
