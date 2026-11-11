"""Offline tests for load_spool (loading.py), the slot helpers (trays.py) and tag routing (tags.py).

Run: python3 tools/bambu/test_spulen_laden.py
No Home Assistant install needed: the few homeassistant modules these files
import are stubbed with fakes (registries, state machine, service registry).

Checks:
- load_spool: print lock, release of the old binding, tray command "own",
  tray command "service" with status 200 / 409 / 404 / no answer / 500.
- unload_slot: print lock, unassign.
- trays: slot dicts from registry entries, labels, binding lookup, recent-load tracker.
- tags: tag id <-> spool id, tag_scanned -> notify service of exactly the
  scanning phone (mobile_app util path and device-name fallback), phone filter.
"""
import asyncio
import importlib.util
import pathlib
import sys
import types

ROOT = pathlib.Path(__file__).resolve().parents[2]
PKG = ROOT / "custom_components" / "spoolbook"


# ----------------------------------------------------------------- fakes
class HomeAssistantError(Exception):
    pass


class State:
    def __init__(self, state, attributes=None):
        self.state = state
        self.attributes = attributes or {}


class Entry:
    def __init__(self, entity_id, unique_id, device_id, platform="bambu_lab", disabled=False):
        self.entity_id = entity_id
        self.unique_id = unique_id
        self.device_id = device_id
        self.platform = platform
        self.domain = entity_id.split(".")[0]
        self.disabled = disabled


class Device:
    def __init__(self, did, name, model=None, via=None, name_by_user=None, identifiers=()):
        self.id = did
        self.name = name
        self.model = model
        self.via_device_id = via
        self.name_by_user = name_by_user
        self.identifiers = set(identifiers)


class EntityRegistry:
    def __init__(self, entries):
        self.entities = {e.entity_id: e for e in entries}

    def async_get(self, entity_id):
        return self.entities.get(entity_id)

    def async_get_entity_id(self, domain, platform, unique_id):
        for e in self.entities.values():
            if e.domain == domain and e.platform == platform and e.unique_id == unique_id:
                return e.entity_id
        return None


class DeviceRegistry:
    def __init__(self, devices):
        self.devices = {d.id: d for d in devices}

    def async_get(self, did):
        return self.devices.get(did)


class Services:
    def __init__(self, responses=None, known=()):
        self.calls = []
        self.responses = responses or {}
        self.known = set(known)

    def supports_response(self, domain, service):
        return types.SimpleNamespace(value="optional")

    def has_service(self, domain, service):
        return f"{domain}.{service}" in self.known

    async def async_call(self, domain, service, data, blocking=False, return_response=False):
        self.calls.append((f"{domain}.{service}", data))
        resp = self.responses.get(f"{domain}.{service}")
        if isinstance(resp, Exception):
            raise resp
        return resp if return_response else None


class States:
    def __init__(self, states):
        self.s = states

    def get(self, entity_id):
        return self.s.get(entity_id)


class Hass:
    def __init__(self, states=None, services=None):
        self.states = States(states or {})
        self.services = services or Services()
        self.data = {}


REG = {}


def _stub_ha():
    mods = {}

    def mod(name, **attrs):
        m = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(m, k, v)
        mods[name] = m
        return m

    mod("homeassistant")
    mod("homeassistant.core", HomeAssistant=object, callback=lambda f: f)
    mod("homeassistant.const", EVENT_STATE_CHANGED="state_changed")
    mod("homeassistant.exceptions", HomeAssistantError=HomeAssistantError)
    mod("homeassistant.helpers")
    mod("homeassistant.helpers.entity_registry", async_get=lambda hass: REG["er"],
        EVENT_ENTITY_REGISTRY_UPDATED="entity_registry_updated")
    mod("homeassistant.helpers.device_registry", async_get=lambda hass: REG["dr"])
    mod("homeassistant.util", slugify=lambda t: "".join(c if c.isalnum() else "_" for c in t.lower()).strip("_"))
    mod("homeassistant.components")
    sys.modules.update(mods)
    pkg = types.ModuleType("bf")
    pkg.__path__ = [str(PKG)]
    sys.modules["bf"] = pkg
    loaded = {}
    for name in ("const", "trays", "loading", "tags"):
        spec = importlib.util.spec_from_file_location(f"bf.{name}", PKG / f"{name}.py")
        m = importlib.util.module_from_spec(spec)
        sys.modules[f"bf.{name}"] = m
        spec.loader.exec_module(m)
        loaded[name] = m
    return loaded


# --------------------------------------------------------------- fixtures
SERIAL = "P1SERIAL"


def setup_registry():
    printer = Device("dev-printer", "H2X_P1SERIAL", model="H2X")
    ams_a = Device("dev-ams1", f"H2X_{SERIAL}_AMS_1", model="AMS 2 Pro", via="dev-printer")
    ams_b = Device("dev-ams2", f"H2X_{SERIAL}_AMS_2", model="AMS", via="dev-printer", name_by_user="AMS Links")
    phone_a = Device("phone-a", "Pixel 9", identifiers={("mobile_app", "x1")})
    phone_b = Device("phone-b", "Galaxy Tab", identifiers={("mobile_app", "x2")})
    lamp = Device("lamp", "Lampe", identifiers={("hue", "1")})
    entries = [Entry("sensor.drucker_status", f"{SERIAL}_print_status", "dev-printer")]
    for n in range(1, 5):
        entries.append(Entry(f"sensor.ams_a_{n}", f"H2X_{SERIAL}_AMS_AAA_tray_{n}", "dev-ams1"))
        entries.append(Entry(f"sensor.ams_b_{n}", f"H2X_{SERIAL}_AMS_BBB_tray_{n}", "dev-ams2"))
    entries.append(Entry("sensor.other", "x_y", "dev-ams1", platform="other"))
    REG["er"] = EntityRegistry(entries)
    REG["dr"] = DeviceRegistry([printer, ams_a, ams_b, phone_a, phone_b, lamp])


def spool(sid, in_printer=False, ams=None, slot=None, fid="GFL99", mat="PLA"):
    return {"id": sid, "filamentId": fid, "filamentType": mat, "color": "#FF6A13FF",
            "inPrinter": in_printer, "devId": SERIAL if in_printer else "", "amsId": ams, "slotId": slot}


def run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------------ tests
def test_trays(m):
    trays = m["trays"]
    hass = Hass()
    t = trays.resolve_tray(hass, "sensor.ams_a_1")
    assert t["label"] == "AMS 2 Pro · Fach 1", t
    assert (t["ams_id"], t["slot_id"], t["ams_type"], t["dev_id"]) == (0, "0", 3, SERIAL)
    assert t["status_entity"] == "sensor.drucker_status"
    assert trays.resolve_tray(hass, "sensor.ams_b_4")["label"] == "AMS Links · Fach 4"
    for bad in ("sensor.other", "sensor.nope", "sensor.drucker_status"):
        try:
            trays.resolve_tray(hass, bad)
        except HomeAssistantError as err:
            assert "kein AMS-Fach" in str(err)
        else:
            raise AssertionError(bad)
    all_trays = trays.list_trays(hass)
    assert [x["entity_id"] for x in all_trays][:5] == [
        "sensor.ams_a_1", "sensor.ams_a_2", "sensor.ams_a_3", "sensor.ams_a_4", "sensor.ams_b_1"]
    spools = [spool(1), spool(2, True, 1, "2"), spool(3, True, 0, "0")]
    assert trays.bound_spool(spools, t)["id"] == 3
    assert trays.spool_tray(spools[1], all_trays)["entity_id"] == "sensor.ams_b_3"
    assert trays.spool_tray(spools[0], all_trays) is None
    # other printer serial -> not the same slot
    other = dict(spools[2], devId="OTHER")
    assert not trays.same_slot(other, t)

    tracker = trays.TrayTracker(hass)
    empty, full = State("Empty", {"empty": True}), State("PLA", {"empty": False})
    tracker.note("sensor.ams_a_2", empty, full, now=1000)
    tracker.note("sensor.ams_a_3", State("unavailable"), full, now=1000)  # startup: no hint
    tracker.note("sensor.ams_a_4", full, full, now=1000)
    assert tracker.recent(now=1100) == {"sensor.ams_a_2": 1000}
    assert tracker.recent(now=1000 + 601) == {}
    tracker.note("sensor.ams_a_2", empty, full, now=2000)
    tracker.note("sensor.ams_a_2", full, empty, now=2001)  # taken out again
    assert tracker.recent(now=2002) == {}
    print("trays: ok")


def _loader(m, states=None, responses=None):
    hass = Hass(states or {}, Services(responses))
    calls = []

    async def assign(sp, tr, set_printer):
        calls.append(("assign", sp["id"], tr["entity_id"], set_printer))

    async def unassign(tr):
        calls.append(("unassign", tr["entity_id"]))

    return hass, calls, assign, unassign


def test_load(m):
    trays, loading = m["trays"], m["loading"]
    idle = {"sensor.drucker_status": State("idle")}
    hass, calls, assign, unassign = _loader(m, idle)
    all_trays = trays.list_trays(hass)
    target = trays.resolve_tray(hass, "sensor.ams_a_1")

    # print lock (every busy state)
    for busy in ("running", "pause", "prepare", "slicing", "init"):
        h, c, a, u = _loader(m, {"sensor.drucker_status": State(busy)})
        try:
            run(loading.async_load_spool(h, spool=spool(5), tray=target, trays=all_trays,
                                         assign=a, unassign=u, tray_command="own"))
        except HomeAssistantError as err:
            assert "arbeitet gerade" in str(err)
        else:
            raise AssertionError(busy)
        assert c == [] and h.services.calls == []
        try:
            run(loading.async_unload_slot(h, tray=target, unassign=u))
        except HomeAssistantError:
            pass
        else:
            raise AssertionError("unload while busy")

    # own command: release the old slot first, then assign with set_printer
    r = run(loading.async_load_spool(hass, spool=spool(5, True, 1, "2"), tray=target, trays=all_trays,
                                     assign=assign, unassign=unassign, tray_command="own"))
    assert calls == [("unassign", "sensor.ams_b_3"), ("assign", 5, "sensor.ams_a_1", True)], calls
    assert r["released"] == "sensor.ams_b_3" and r["tray_label"] == "AMS 2 Pro · Fach 1"
    assert hass.services.calls == []

    # same slot again: nothing released
    hass, calls, assign, unassign = _loader(m, idle)
    run(loading.async_load_spool(hass, spool=spool(5, True, 0, "0"), tray=target, trays=all_trays,
                                 assign=assign, unassign=unassign, tray_command="own"))
    assert calls == [("assign", 5, "sensor.ams_a_1", True)], calls

    # service: status codes
    cases = {
        200: (False, "gesetzt"),
        409: (False, "RFID-Fach"),
        404: (True, "eigener Befehl (Dienst nicht erreichbar)"),
    }
    for status, (set_printer, printer) in cases.items():
        hass, calls, assign, unassign = _loader(m, idle, {"rest_command.agent": {"status": status, "content": ""}})
        r = run(loading.async_load_spool(hass, spool=spool(7), tray=target, trays=all_trays, assign=assign,
                                         unassign=unassign, tray_command="service", tray_service="rest_command.agent"))
        assert calls == [("assign", 7, "sensor.ams_a_1", set_printer)], (status, calls)
        assert r["printer"] == printer, (status, r)
        name, data = hass.services.calls[0]
        assert name == "rest_command.agent"
        assert data == {"befehl": "ams/set_tray", "daten": {"ams_id": 0, "slot": 0, "filament_id": "GFL99",
                                                           "color": "#FF6A13", "type": "PLA"}}, data
    # no answer (call raises) -> own command
    hass, calls, assign, unassign = _loader(m, idle, {"rest_command.agent": RuntimeError("timeout")})
    r = run(loading.async_load_spool(hass, spool=spool(7), tray=target, trays=all_trays, assign=assign,
                                     unassign=unassign, tray_command="service", tray_service="rest_command.agent"))
    assert calls == [("assign", 7, "sensor.ams_a_1", True)]
    # refused -> error, nothing bound
    hass, calls, assign, unassign = _loader(m, idle, {"rest_command.agent": {"status": 500, "content": "nope"}})
    try:
        run(loading.async_load_spool(hass, spool=spool(7), tray=target, trays=all_trays, assign=assign,
                                     unassign=unassign, tray_command="service", tray_service="rest_command.agent"))
    except HomeAssistantError as err:
        assert "Status 500" in str(err)
    else:
        raise AssertionError("500 accepted")
    assert calls == []
    # own command needs profile + material
    hass, calls, assign, unassign = _loader(m, idle)
    try:
        run(loading.async_load_spool(hass, spool=spool(8, fid=""), tray=target, trays=all_trays,
                                     assign=assign, unassign=unassign, tray_command="own"))
    except HomeAssistantError as err:
        assert "Druckerprofil" in str(err)
    else:
        raise AssertionError("no profile accepted")
    # ... but an RFID slot (409) only binds, so no profile needed
    hass, calls, assign, unassign = _loader(m, idle, {"rest_command.agent": {"status": 409}})
    run(loading.async_load_spool(hass, spool=spool(8, fid=""), tray=target, trays=all_trays, assign=assign,
                                 unassign=unassign, tray_command="service", tray_service="rest_command.agent"))
    assert calls == [("assign", 8, "sensor.ams_a_1", False)]
    # unload
    hass, calls, assign, unassign = _loader(m, idle)
    run(loading.async_unload_slot(hass, tray=target, unassign=unassign))
    assert calls == [("unassign", "sensor.ams_a_1")]
    print("load_spool: ok")


def test_tags(m):
    tags = m["tags"]
    assert tags.tag_id_for(1397604) == "spoolbook_1397604"
    assert tags.spool_id_from_tag("spoolbook_1397604") == 1397604
    assert tags.spool_id_from_tag("bambu_spule_1397604") == 1397604  # printed legacy label
    assert tags.spool_id_from_tag("bambu_spule_x") is None
    for bad in ("spoolbook_x", "other_1", None, "spoolbook_0"):
        assert tags.spool_id_from_tag(bad) is None

    # fallback path (no mobile_app module): device name -> notify.mobile_app_<slug>
    hass = Hass(services=Services(known={"notify.mobile_app_pixel_9", "notify.mobile_app_galaxy_tab"}))
    svc = run(tags.async_handle_tag_scanned(hass, {"tag_id": "spoolbook_42", "device_id": "phone-b"}))
    assert svc == "mobile_app_galaxy_tab", svc
    assert hass.services.calls == [("notify.mobile_app_galaxy_tab", {
        "message": "command_webview", "data": {"command": "/spoolbook?spule=42"}})], hass.services.calls
    # not our tag / no device / not a phone / phone filtered out
    hass.services.calls.clear()
    assert run(tags.async_handle_tag_scanned(hass, {"tag_id": "garage", "device_id": "phone-a"})) is None
    assert run(tags.async_handle_tag_scanned(hass, {"tag_id": "spoolbook_42"})) is None
    assert run(tags.async_handle_tag_scanned(hass, {"tag_id": "spoolbook_42", "device_id": "lamp"})) is None
    assert run(tags.async_handle_tag_scanned(
        hass, {"tag_id": "spoolbook_42", "device_id": "phone-b"}, ["phone-a"])) is None
    assert hass.services.calls == []
    assert run(tags.async_handle_tag_scanned(
        hass, {"tag_id": "spoolbook_42", "device_id": "phone-a"}, ["phone-a"])) == "mobile_app_pixel_9"

    # mobile_app util path (as mobile_app's own device action): webhook id as target
    util = types.ModuleType("homeassistant.components.mobile_app.util")
    util.webhook_id_from_device_id = lambda h, did: {"phone-a": "wh-a", "phone-b": "wh-b"}.get(did)
    util.get_notify_service = lambda h, wh: {"wh-a": "mobile_app_pixel_9_pro", "wh-b": "mobile_app_tab"}[wh]
    sys.modules["homeassistant.components.mobile_app"] = types.ModuleType("homeassistant.components.mobile_app")
    sys.modules["homeassistant.components.mobile_app.util"] = util
    hass = Hass()
    svc = run(tags.async_handle_tag_scanned(hass, {"tag_id": "spoolbook_7", "device_id": "phone-a"}))
    assert svc == "mobile_app_pixel_9_pro"
    assert hass.services.calls == [("notify.mobile_app_pixel_9_pro", {
        "message": "command_webview", "data": {"command": "/spoolbook?spule=7"}, "target": "wh-a"})]

    # tag creation through the tag integration's storage collection
    class Collection:
        def __init__(self):
            self.data = {"spoolbook_1": {}}
            self.created = []

        async def async_create_item(self, item):
            self.created.append(item)
            self.data[item["tag_id"]] = item

    hass = Hass()
    hass.data["tag"] = coll = Collection()
    out = run(tags.async_ensure_tags(hass, [
        {"id": 1, "filamentVendor": "Bambu Lab", "filamentType": "PLA", "filamentName": "PLA Basic"},
        {"id": 2, "filamentVendor": "SUNLU", "filamentType": "PETG", "filamentName": "PETG"},
    ]))
    assert out == {1: "spoolbook_1", 2: "spoolbook_2"}
    assert coll.created == [{"tag_id": "spoolbook_2", "name": "Spule SUNLU PETG #2"}], coll.created
    print("tags: ok")


def test_missing_and_repush(m):
    trays = m["trays"]
    w = trays.MissingWatch(900)
    assert w.observe("a", True, 0) is True          # arm timer
    assert w.observe("a", True, 100) is False       # already running
    assert w.due("a", 899) is False                 # too early
    assert w.due("a", 900) is True                  # fires once
    assert w.due("a", 2000) is False
    assert w.observe("a", True, 2100) is False      # same occurrence, still missing
    assert w.observe("a", False, 2200) is False     # fixed -> reset
    assert w.due("a", 5000) is False
    assert w.observe("a", True, 3000) is True       # new occurrence
    assert w.due("a", 3900) is True
    w.observe("b", True, 0)
    w.observe("b", False, 500)                      # slot emptied before the delay
    assert w.due("b", 2000) is False

    setup_registry()
    hass = Hass()
    all_trays = trays.list_trays(hass)
    spools = [spool(1, True, 0, "0"), spool(2, True, 0, "1", fid=""), spool(3, True, 1, "0")]
    states = {
        "sensor.ams_a_1": State("PLA", {"empty": False}),
        "sensor.ams_a_2": State("PLA", {"empty": False}),   # spool without printer profile
        "sensor.ams_a_3": State("PLA", {"empty": False}),   # no bound spool
        "sensor.ams_b_1": State("PETG", {"empty": True}),   # empty slot
    }
    pairs = trays.repush_targets(all_trays, spools, states.get, lambda t: False)
    assert [(t["entity_id"], sp["id"]) for t, sp in pairs] == [("sensor.ams_a_1", 1)], pairs
    assert trays.repush_targets(all_trays, spools, states.get, lambda t: True) == []
    only = trays.repush_targets(all_trays, spools, states.get, lambda t: False, only="sensor.ams_a_3")
    assert only == []
    states["sensor.ams_b_1"] = State("PETG", {"empty": False})
    pairs = trays.repush_targets(all_trays, spools, states.get, lambda t: False)
    assert [sp["id"] for _, sp in pairs] == [1, 3]
    print("missing watch / repush: ok")


def main():
    m = _stub_ha()
    setup_registry()
    test_trays(m)
    test_load(m)
    test_tags(m)
    test_missing_and_repush(m)
    print("spulen laden/tags: all checks passed")


if __name__ == "__main__":
    main()
