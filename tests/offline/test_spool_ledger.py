"""Offline test of custom_components/spoolbook/spool_ledger.py (no HA install needed).

Run: python3 tools/bambu/test_spool_ledger.py
Stubs the few homeassistant modules the ledger imports, then checks empty
spools, suggestion order, history, archive, note migration and low stock.
"""
import asyncio
import importlib.util
import pathlib
import sys
import types
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[2]
PKG = ROOT / "custom_components" / "spoolbook"


def _stub_ha():
    ha = types.ModuleType("homeassistant")
    core = types.ModuleType("homeassistant.core")
    core.HomeAssistant = object
    helpers = types.ModuleType("homeassistant.helpers")
    storage = types.ModuleType("homeassistant.helpers.storage")

    class Store:
        saved = None

        def __init__(self, hass, version, key, minor_version=1):
            self.key = key

        def __class_getitem__(cls, item):
            return cls

        async def async_load(self):
            return None

        def async_delay_save(self, func, delay):
            Store.saved = func()

    storage.Store = Store
    util = types.ModuleType("homeassistant.util")
    dt = types.ModuleType("homeassistant.util.dt")
    dt.utcnow = lambda: datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
    util.dt = dt
    for name, mod in {
        "homeassistant": ha, "homeassistant.core": core, "homeassistant.helpers": helpers,
        "homeassistant.helpers.storage": storage, "homeassistant.util": util,
        "homeassistant.util.dt": dt,
    }.items():
        sys.modules[name] = mod
    pkg = types.ModuleType("bf")
    pkg.__path__ = [str(PKG)]
    sys.modules["bf"] = pkg
    const = types.ModuleType("bf.const")
    const.DOMAIN = "spoolbook"
    sys.modules["bf.const"] = const
    spec = importlib.util.spec_from_file_location("bf.spool_ledger", PKG / "spool_ledger.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bf.spool_ledger"] = mod
    spec.loader.exec_module(mod)
    return mod


def spool(sid, vendor, material, color, net, note=""):
    return {"id": sid, "filamentVendor": vendor, "filamentType": material, "filamentName": material,
            "color": color, "netWeight": net, "totalNetWeight": 1000, "note": note}


def main():
    sl = _stub_ha()
    led = sl.SpoolLedger(None, "e1")
    asyncio.run(led.async_load())
    assert [e["id"] for e in led.empty_spools] == ["sunlu", "bambu"]
    # Bambu empty spool weighed (225 g like Sunlu), no placeholder any more.
    assert led.empty_spool("bambu")["weight_g"] == 225
    assert "placeholder" not in led.empty_spool("bambu")

    data = {
        1: spool(1, "SUNLU", "PETG", "#EEEEEEFF", 500),
        2: spool(2, "SUNLU", "PETG", "#EEEEEEFF", 300),
        3: spool(3, "JAYO", "PETG", "#131313FF", 900, note="Spoolman #1 · Leerspule 180 g"),
        4: spool(4, "Bambu Lab", "PLA", "#00AE42FF", 0),
        5: spool(5, "Recreus", "TPU", "#FFFFFFFF", 500),
    }
    # note migration creates "Eigene 180 g" and remembers it for spool 3
    led.migrate_notes(data)
    assert led.meta(3)["empty_spool"] == "eigene_180_g", led.meta(3)
    assert led.empty_spool("eigene_180_g")["weight_g"] == 180

    # suggestion: remembered > brand memory > vendor match
    assert led.suggest_empty_spool(data[1]) == "sunlu"
    assert led.suggest_empty_spool(data[4]) == "bambu"
    assert led.suggest_empty_spool(data[3]) == "eigene_180_g"
    assert led.suggest_empty_spool(data[5]) is None
    led.remember_brand("Recreus", material="TPU", name="Filaflex", filament_id="GFU99",
                       empty_spool="sunlu", total_g=500)
    assert led.suggest_empty_spool(data[5]) == "sunlu"

    # updating the placeholder weight clears the placeholder flag
    led.set_empty_spool(None, 238, esid="bambu")
    assert "placeholder" not in led.empty_spool("bambu") and led.empty_spool("bambu")["weight_g"] == 238
    new = led.set_empty_spool("Sunlu", 230)
    assert new["id"] == "sunlu_2"

    # first stock check: everything silent (seeding), JAYO + Recreus low
    names = {k: None for k in data}
    low, fresh = led.stock_check(data, names, "color")
    assert {i["spool_id"] for i in low} == {3, 5} and fresh == [], (low, fresh)
    # SUNLU PETG white drops to one -> exactly one report, only once
    data[2]["netWeight"] = 0
    low, fresh = led.stock_check(data, names, "color")
    assert [i["spool_id"] for i in fresh] == [1], fresh
    low, fresh = led.stock_check(data, names, "color")
    assert fresh == []
    # restock -> reset, next drop reports again
    data[6] = spool(6, "SUNLU", "PETG", "#EEEEEEFF", 1000)
    led.stock_check(data, names, "color")
    data[6]["netWeight"] = 0
    _, fresh = led.stock_check(data, names, "color")
    assert [i["spool_id"] for i in fresh] == [1]
    # a brand-new sort with a single spool is not reported
    data[7] = spool(7, "SUNLU", "ASA", "#FF0000FF", 1000)
    _, fresh = led.stock_check(data, names, "color")
    assert fresh == []
    # archived spools do not count as stock
    data[8] = spool(8, "eSUN", "PLA", "#00AE42FF", 800)
    data[9] = spool(9, "eSUN", "PLA", "#00AE42FF", 800)
    led.stock_check(data, names, "color")
    assert led.set_archived(9, True) and not led.set_archived(9, True)
    _, fresh = led.stock_check(data, names, "color")
    assert [i["spool_id"] for i in fresh] == [8]
    # level "material": PETG from another brand left -> no warning; last PETG of any brand -> one report
    assert sl.SpoolLedger.stock_key(data[1], None, "material") == "petg"
    assert sl.SpoolLedger.stock_key(data[1], "Weiß", "vendor") == "petg|weiß|sunlu"
    low, _ = led.stock_check(data, names, "material")
    assert not any(i["material"] == "PETG" for i in low), low
    data[1]["netWeight"] = 0
    _, fresh = led.stock_check(data, names, "material")
    assert [i["spool_id"] for i in fresh] == [3], fresh
    data[1]["netWeight"] = 500
    # own colour name groups different hex values into one sort
    assert sl.SpoolLedger.sort_key(data[1], "Weiß") == "petg|weiß"

    # history newest first, capped
    for i in range(sl.HISTORY_MAX + 5):
        led.add_history(1, "druck", grams=-1.234, rest=500 - i, text="x" * 200)
    hist = led.history(1)
    assert len(hist) == sl.HISTORY_MAX and hist[0]["rest"] == 500 - (sl.HISTORY_MAX + 4)
    assert hist[0]["g"] == -1.2 and len(hist[0]["text"]) == 80

    led.delete_empty_spool("eigene_180_g")
    assert "empty_spool" not in led.meta(3)
    print("spool_ledger: all checks passed")


if __name__ == "__main__":
    main()
