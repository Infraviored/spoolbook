"""Local spool bookkeeping next to the cloud library (HA store of the entry).

The Bambu cloud only knows grams. Everything else the household needs for
its spools lives here, per config entry:

- empty spools ("Leerspulen"): name + tare weight, used to turn a scale
  reading of a full spool into filament grams;
- per filament spool: its remembered empty spool, archive flag, own colour
  name and a booking history (prints, weighing, create, archive);
- per brand: what was used last time (material, name, profile, empty spool),
  so creating the next spool of a known brand needs few taps;
- low-stock bookkeeping: which "sorts" (material + colour) are down to their
  last non-empty spool and were already reported.

Archive instead of delete: an archived spool stays in the cloud library and
in this store with its history, it only disappears from selection lists.
"""

from __future__ import annotations

from collections.abc import Iterable
import logging
import re
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
STORAGE_MINOR_VERSION = 1
SAVE_DELAY_S = 2
HISTORY_MAX = 100
HISTORY_IN_ATTRS = 3

# Start list. Bambu and Sunlu empty spools both weigh 225 g (weighed);
# placeholder=True would make the UI show "~" for an unweighed value.
DEFAULT_EMPTY_SPOOLS: list[dict[str, Any]] = [
    {"id": "sunlu", "name": "Sunlu", "weight_g": 225, "vendor": "SUNLU"},
    {"id": "bambu", "name": "Bambu", "weight_g": 225, "vendor": "Bambu Lab"},
]

# Material base types the cloud uses as filamentType; a label like
# "PLA Matte" becomes type PLA + name "PLA Matte".
BASE_MATERIALS = ["PLA", "PETG", "TPU", "ABS", "ASA", "PA", "PC", "PET", "PVA", "HIPS"]

_LEERSPULE_RE = re.compile(r"leerspule\s*(\d+)", re.IGNORECASE)


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def _hex6(color: Any) -> str:
    return str(color or "").lstrip("#").upper()[:6]


def slugify(name: str, taken: Iterable[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", _norm(name)).strip("_") or "spule"
    taken = set(taken)
    slug, n = base, 2
    while slug in taken:
        slug, n = f"{base}_{n}", n + 1
    return slug


def _migrate(old_major: int, old_minor: int, data: dict[str, Any]) -> dict[str, Any]:
    """Forward-compatible migration hook (only fills missing keys so far)."""
    return _with_defaults(data)


def _with_defaults(data: dict[str, Any] | None) -> dict[str, Any]:
    data = dict(data or {})
    data.setdefault("empty_spools", [dict(e) for e in DEFAULT_EMPTY_SPOOLS])
    data.setdefault("spools", {})
    data.setdefault("brands", {})
    data.setdefault("low_notified", [])
    data.setdefault("known_sorts", [])
    data.setdefault("notes_migrated", False)
    return data


class _LedgerStore(Store[dict[str, Any]]):
    async def _async_migrate_func(self, old_major_version, old_minor_version, old_data):
        return _migrate(old_major_version, old_minor_version, old_data)


class SpoolLedger:
    """Local per-entry spool data; all mutators schedule a delayed save."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store = _LedgerStore(
            hass,
            STORAGE_VERSION,
            f"{DOMAIN}.spulen_{entry_id}",
            minor_version=STORAGE_MINOR_VERSION,
        )
        self._data: dict[str, Any] = _with_defaults(None)
        self.loaded = False

    async def async_load(self) -> None:
        if self.loaded:
            return
        self._data = _with_defaults(await self._store.async_load())
        self.loaded = True

    def _save(self) -> None:
        self._store.async_delay_save(lambda: self._data, SAVE_DELAY_S)

    # ------------------------------------------------------------ empty spools

    @property
    def empty_spools(self) -> list[dict[str, Any]]:
        return self._data["empty_spools"]

    def empty_spool(self, esid: str | None) -> dict[str, Any] | None:
        for item in self.empty_spools:
            if item["id"] == esid:
                return item
        return None

    def set_empty_spool(
        self,
        name: str | None,
        weight_g: int | None,
        vendor: str | None = None,
        esid: str | None = None,
    ) -> dict[str, Any]:
        """Create (no esid) or update an empty spool; returns the entry."""
        if esid:
            item = self.empty_spool(esid)
            if item is None:
                raise KeyError(esid)
            if name:
                item["name"] = name.strip()
            if weight_g is not None:
                item["weight_g"] = int(weight_g)
                item.pop("placeholder", None)
            if vendor is not None:
                item["vendor"] = vendor.strip()
        else:
            if not name or weight_g is None:
                raise ValueError("name and weight_g are required for a new empty spool")
            item = {
                "id": slugify(name, (e["id"] for e in self.empty_spools)),
                "name": name.strip(),
                "weight_g": int(weight_g),
            }
            if vendor:
                item["vendor"] = vendor.strip()
            self.empty_spools.append(item)
        self._save()
        return item

    def delete_empty_spool(self, esid: str) -> None:
        before = len(self.empty_spools)
        self._data["empty_spools"] = [e for e in self.empty_spools if e["id"] != esid]
        if len(self.empty_spools) == before:
            raise KeyError(esid)
        for meta in self._data["spools"].values():
            if meta.get("empty_spool") == esid:
                meta.pop("empty_spool", None)
        for brand in self._data["brands"].values():
            if brand.get("empty_spool") == esid:
                brand.pop("empty_spool", None)
        self._save()

    def empty_spool_for_vendor(self, vendor: Any) -> dict[str, Any] | None:
        v = _norm(vendor)
        if not v:
            return None
        for item in self.empty_spools:
            ev = _norm(item.get("vendor") or item.get("name"))
            if ev and (ev == v or ev in v or v in ev):
                return item
        return None

    # ----------------------------------------------------------- spool meta

    def meta(self, spool_id: int) -> dict[str, Any]:
        return self._data["spools"].get(str(spool_id), {})

    def _meta_rw(self, spool_id: int) -> dict[str, Any]:
        return self._data["spools"].setdefault(str(spool_id), {})

    def remember_empty_spool(self, spool_id: int, esid: str | None) -> None:
        meta = self._meta_rw(spool_id)
        if esid and meta.get("empty_spool") != esid:
            meta["empty_spool"] = esid
            self._save()

    def set_color_name(self, spool_id: int, name: str | None) -> None:
        if name:
            self._meta_rw(spool_id)["color_name"] = name.strip()
            self._save()

    def is_archived(self, spool_id: int) -> bool:
        return bool(self.meta(spool_id).get("archived"))

    def set_archived(self, spool_id: int, archived: bool) -> bool:
        """Returns True when the flag changed."""
        meta = self._meta_rw(spool_id)
        if bool(meta.get("archived")) == archived:
            return False
        if archived:
            meta["archived"] = True
            meta["archived_at"] = dt_util.utcnow().isoformat()
        else:
            meta.pop("archived", None)
            meta.pop("archived_at", None)
        self._save()
        return True

    def suggest_empty_spool(self, spool: dict[str, Any]) -> str | None:
        """Remembered empty spool, else the brand's last one, else a vendor match."""
        meta = self.meta(spool.get("id"))
        if (esid := meta.get("empty_spool")) and self.empty_spool(esid):
            return esid
        brand = self._data["brands"].get(_norm(spool.get("filamentVendor")))
        if brand and (esid := brand.get("empty_spool")) and self.empty_spool(esid):
            return esid
        if item := self.empty_spool_for_vendor(spool.get("filamentVendor")):
            return item["id"]
        return None

    # -------------------------------------------------------------- history

    def add_history(
        self,
        spool_id: int,
        kind: str,
        *,
        grams: float | None = None,
        rest: float | None = None,
        text: str | None = None,
        when: str | None = None,
    ) -> None:
        entry: dict[str, Any] = {"t": when or dt_util.utcnow().isoformat(), "typ": kind}
        if grams is not None:
            entry["g"] = round(float(grams), 1)
        if rest is not None:
            entry["rest"] = int(round(float(rest)))
        if text:
            entry["text"] = str(text)[:80]
        hist = self._meta_rw(spool_id).setdefault("history", [])
        hist.append(entry)
        del hist[:-HISTORY_MAX]
        self._save()

    def history(self, spool_id: int, limit: int | None = None) -> list[dict[str, Any]]:
        """Newest first."""
        hist = list(reversed(self.meta(spool_id).get("history") or []))
        return hist[:limit] if limit else hist

    # --------------------------------------------------------------- brands

    @property
    def brands(self) -> dict[str, dict[str, Any]]:
        return self._data["brands"]

    def remember_brand(
        self,
        vendor: str,
        *,
        material: str | None,
        name: str | None,
        filament_id: str | None,
        empty_spool: str | None,
        total_g: int | None,
    ) -> None:
        key = _norm(vendor)
        if not key:
            return
        self.brands[key] = {
            "vendor": vendor.strip(),
            "material": material,
            "name": name,
            "filament_id": filament_id,
            "empty_spool": empty_spool or (self.brands.get(key) or {}).get("empty_spool"),
            "total_g": total_g,
        }
        self._save()

    # --------------------------------------------------------- sorts / stock

    def color_name(self, spool: dict[str, Any], official: str | None) -> str | None:
        return self.meta(spool.get("id")).get("color_name") or official

    @staticmethod
    def sort_key(spool: dict[str, Any], color_name: str | None) -> str:
        """Material + colour; brand does not matter (spec: 'Marke egal')."""
        material = _norm(spool.get("filamentType")) or "?"
        colors = spool.get("colors")
        color = (colors[0] if isinstance(colors, list) and colors else None) or spool.get("color")
        return f"{material}|{_norm(color_name) or _hex6(color)}"

    @classmethod
    def stock_key(cls, spool: dict[str, Any], color_name: str | None, level: str = "material") -> str:
        """What counts as one filament for the low-stock warning.

        material: any spool of the same material (PETG left from brand A -> no warning
        when brand B runs out); color: material + colour; vendor: material + colour + brand.
        """
        material = _norm(spool.get("filamentType")) or "?"
        if level == "material":
            return material
        key = cls.sort_key(spool, color_name)
        if level == "vendor":
            key += "|" + (_norm(spool.get("filamentVendor")) or "?")
        return key

    def active(self, spool: dict[str, Any]) -> bool:
        """Counts as stock: not archived and filament left."""
        return not self.is_archived(spool.get("id")) and float(spool.get("netWeight") or 0) > 0

    def stock_check(
        self,
        spools: dict[int, dict[str, Any]],
        color_names: dict[int, str | None],
        level: str = "material",
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """(all sorts down to one spool, the ones to report now).

        Report rule: once per sort until stock is back to >= 2 spools. Sorts
        seen for the first time (first run, or a brand-new sort with a single
        spool just created) are marked as reported silently - no push flood.
        """
        groups: dict[str, list[dict[str, Any]]] = {}
        for spool in spools.values():
            key = self.stock_key(spool, color_names.get(spool.get("id")), level)
            groups.setdefault(key, [])
            if self.active(spool):
                groups[key].append(spool)
        known = set(self._data["known_sorts"])
        notified = set(self._data["low_notified"])
        low: list[dict[str, Any]] = []
        new: list[dict[str, Any]] = []
        for key, members in groups.items():
            if len(members) >= 2:
                notified.discard(key)
            elif len(members) == 1:
                spool = members[0]
                item = {
                    "key": key,
                    "material": spool.get("filamentType"),
                    "color": spool.get("color"),
                    "color_name": color_names.get(spool.get("id")),
                    "spool_id": spool.get("id"),
                    "vendor": spool.get("filamentVendor"),
                    "name": spool.get("filamentName"),
                    "remaining_g": spool.get("netWeight"),
                }
                low.append(item)
                if key not in notified:
                    notified.add(key)
                    if key in known:
                        new.append(item)
        changed = (
            sorted(notified) != sorted(self._data["low_notified"])
            or not set(groups) <= known
        )
        if changed:
            self._data["low_notified"] = sorted(notified)
            self._data["known_sorts"] = sorted(known | set(groups))
            self._save()
        return low, new

    # ------------------------------------------------------------ migration

    def migrate_notes(self, spools: dict[int, dict[str, Any]]) -> None:
        """One-off: the old weigh popup kept the tare as 'Leerspule N g' in the note."""
        if self._data["notes_migrated"]:
            return
        for spool in spools.values():
            m = _LEERSPULE_RE.search(str(spool.get("note") or ""))
            if not m or self.meta(spool["id"]).get("empty_spool"):
                continue
            grams = int(m.group(1))
            item = next((e for e in self.empty_spools if int(e["weight_g"]) == grams), None)
            if item is None:
                item = self.set_empty_spool(f"Eigene {grams} g", grams)
            self.remember_empty_spool(spool["id"], item["id"])
        self._data["notes_migrated"] = True
        self._save()
