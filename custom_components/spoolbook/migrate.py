"""One-time move of an old ``bambu_filaments`` installation to ``spoolbook``.

The integration used to live under the domain ``bambu_filaments``. Its config
entries stay in HA's registry after the old code is gone, so:

1. ``async_start_import_flows`` (called from ``async_setup``) starts an import
   flow per old entry; ``config_flow.async_step_import`` creates the new entry
   and remembers ``migrated_from``.
2. ``async_migrate_legacy_entry`` (called from ``async_setup_entry`` before any
   platform is set up) moves storage files, entities and devices to the new
   entry and finally removes the old one.

Every step is logged; a failure is logged and setup carries on. Safe to run
repeatedly: it only acts while the old entry still exists.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.storage import Store

from .colors import STORAGE_VERSION as COLORS_VERSION
from .const import DOMAIN, LEGACY_DOMAIN
from .live_monitor import STORAGE_VERSION as LIVE_VERSION
from .spool_ledger import STORAGE_MINOR_VERSION, STORAGE_VERSION as LEDGER_VERSION, _LedgerStore
from .usage import STORAGE_VERSION as USAGE_VERSION

_LOGGER = logging.getLogger(__name__)

MIGRATED_FROM = "migrated_from"
OLD_HUB_NAME = "Bambu Filament Library"
NEW_HUB_NAME = "Spoolbook"
OLD_ENTITY_PREFIX = "bambu_filament_library_"
NEW_ENTITY_PREFIX = "spoolbook_"


def async_start_import_flows(hass: HomeAssistant) -> None:
    """Start an import flow for each old entry that has no new counterpart yet."""
    old_entries = hass.config_entries.async_entries(LEGACY_DOMAIN)
    if not old_entries:
        return
    new_unique_ids = {e.unique_id for e in hass.config_entries.async_entries(DOMAIN)}
    for old in old_entries:
        if old.unique_id and old.unique_id in new_unique_ids:
            continue
        _LOGGER.info(
            "Importing old %s entry %s (%s) as Spoolbook", LEGACY_DOMAIN, old.entry_id, old.title
        )
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": SOURCE_IMPORT},
                data={
                    "old_entry_id": old.entry_id,
                    "data": dict(old.data),
                    "options": dict(old.options),
                    "title": old.title,
                    "unique_id": old.unique_id,
                },
            ),
            f"spoolbook import of {old.entry_id}",
        )


async def async_migrate_legacy_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Move storage, entities and devices of the old entry to ``entry``; never raises."""
    old_id = entry.data.get(MIGRATED_FROM)
    if not old_id:
        return
    old_entry = hass.config_entries.async_get_entry(old_id)
    if old_entry is None or old_entry.domain != LEGACY_DOMAIN:
        _LOGGER.info("Old entry %s is gone, nothing to migrate", old_id)
        _drop_marker(hass, entry)
        return
    _LOGGER.info(
        "Migrating %s entry %s to Spoolbook entry %s", LEGACY_DOMAIN, old_id, entry.entry_id
    )
    for name, step in (
        ("storage", _migrate_storage),
        ("devices", _migrate_devices),
        ("entities", _migrate_entities),
        ("device cleanup", _migrate_devices_cleanup),
    ):
        try:
            await step(hass, entry, old_id)
        except Exception:  # noqa: BLE001 - migration must never break setup
            _LOGGER.exception("Spoolbook migration step '%s' failed", name)
    _drop_marker(hass, entry)
    hass.async_create_task(hass.config_entries.async_remove(old_id), f"remove old entry {old_id}")
    _LOGGER.info("Spoolbook migration done, removing old entry %s", old_id)


def _drop_marker(hass: HomeAssistant, entry: ConfigEntry) -> None:
    data = {k: v for k, v in entry.data.items() if k != MIGRATED_FROM}
    hass.config_entries.async_update_entry(entry, data=data)


async def _copy_store(old_store: Store, new_store: Store, label: str) -> None:
    if await new_store.async_load() is not None:
        _LOGGER.info("Storage %s: target already has data, skipped", label)
        return
    data = await old_store.async_load()
    if data is None:
        _LOGGER.info("Storage %s: nothing to move", label)
        return
    await new_store.async_save(data)
    await old_store.async_remove()
    _LOGGER.info("Storage %s moved", label)


async def _migrate_storage(hass: HomeAssistant, entry: ConfigEntry, old_id: str) -> None:
    new_id = entry.entry_id
    await _copy_store(
        Store(hass, LIVE_VERSION, f"{LEGACY_DOMAIN}.live_{old_id}"),
        Store(hass, LIVE_VERSION, f"{DOMAIN}.live_{new_id}"),
        "live",
    )
    await _copy_store(
        Store(hass, USAGE_VERSION, f"{LEGACY_DOMAIN}.usage_{old_id}"),
        Store(hass, USAGE_VERSION, f"{DOMAIN}.usage_{new_id}"),
        "usage",
    )
    # Ledger: same Store subclass/version so its migration functions apply.
    await _copy_store(
        _LedgerStore(
            hass, LEDGER_VERSION, f"{LEGACY_DOMAIN}.spulen_{old_id}",
            minor_version=STORAGE_MINOR_VERSION,
        ),
        _LedgerStore(
            hass, LEDGER_VERSION, f"{DOMAIN}.spulen_{new_id}",
            minor_version=STORAGE_MINOR_VERSION,
        ),
        "spulen",
    )
    await _copy_store(
        Store(hass, COLORS_VERSION, f"{LEGACY_DOMAIN}.color_codes"),
        Store(hass, COLORS_VERSION, f"{DOMAIN}.color_codes"),
        "color_codes",
    )


def _new_identifiers(
    identifiers: set[tuple[str, str]], old_id: str, new_id: str
) -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for domain, ident in identifiers:
        if domain == LEGACY_DOMAIN:
            out.add((DOMAIN, ident.replace(old_id, new_id)))
        else:
            out.add((domain, ident))
    return out


async def _migrate_devices(hass: HomeAssistant, entry: ConfigEntry, old_id: str) -> None:
    """Link devices to the new entry and swap identifiers (old link stays until entities moved)."""
    dev_reg = dr.async_get(hass)
    for device in list(dr.async_entries_for_config_entry(dev_reg, old_id)):
        kwargs: dict[str, Any] = {
            "add_config_entry_id": entry.entry_id,
            "new_identifiers": _new_identifiers(set(device.identifiers), old_id, entry.entry_id),
        }
        if device.name == OLD_HUB_NAME and not device.name_by_user:
            kwargs["name"] = NEW_HUB_NAME
        dev_reg.async_update_device(device.id, **kwargs)
        _LOGGER.info("Device %s (%s) moved to Spoolbook", device.id, device.name)


async def _migrate_devices_cleanup(hass: HomeAssistant, entry: ConfigEntry, old_id: str) -> None:
    dev_reg = dr.async_get(hass)
    for device in list(dr.async_entries_for_config_entry(dev_reg, old_id)):
        if entry.entry_id in device.config_entries:
            dev_reg.async_update_device(device.id, remove_config_entry_id=old_id)


async def _migrate_entities(hass: HomeAssistant, entry: ConfigEntry, old_id: str) -> None:
    ent_reg = er.async_get(hass)
    new_id = entry.entry_id
    for reg_entry in list(er.async_entries_for_config_entry(ent_reg, old_id)):
        entity_id = reg_entry.entity_id
        uid = reg_entry.unique_id
        kwargs: dict[str, Any] = {"new_config_entry_id": new_id}
        if old_id in uid:
            kwargs["new_unique_id"] = uid.replace(old_id, new_id)
        try:
            updated = ent_reg.async_update_entity_platform(entity_id, DOMAIN, **kwargs)
        except Exception:  # noqa: BLE001 - one bad entity must not stop the rest
            _LOGGER.exception("Could not move entity %s", entity_id)
            continue
        _LOGGER.info("Entity %s moved to Spoolbook", entity_id)
        domain, _, object_id = updated.entity_id.partition(".")
        if object_id.startswith(OLD_ENTITY_PREFIX):
            target = f"{domain}.{NEW_ENTITY_PREFIX}{object_id[len(OLD_ENTITY_PREFIX):]}"
            if ent_reg.async_get(target) is None and hass.states.get(target) is None:
                ent_reg.async_update_entity(updated.entity_id, new_entity_id=target)
                _LOGGER.info("Entity %s renamed to %s", updated.entity_id, target)
            else:
                _LOGGER.info("Entity %s keeps its id (%s is taken)", updated.entity_id, target)
