# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Spoolbook is a Home Assistant custom integration (HACS) for Bambu Lab filament: the Bambu
cloud filament library as HA entities, a local spool book, AMS slot loading, live usage
while printing, and a bundled Lovelace card + sidebar page. The display name is
"Spoolbook" and the domain is `spoolbook` (it began as a fork of
Timmes123/ha-bambu-filaments). Never rename the domain, service names, the card element
name `spoolbook-card`, the panel URL `/spoolbook` or storage keys: they are a public API.
Legacy `bambu_spule_<id>` tags (printed labels) stay accepted next to `spoolbook_<id>`
(`LEGACY_TAG_PREFIX`). `migrate.py` moves an old `bambu_filaments` install (config entry,
storage, entities, devices) to `spoolbook` once. It runs from `async_setup`, which HA only calls
when the domain has an entry or a YAML line, so the move needs `spoolbook:` in
`configuration.yaml` for one start (documented in docs/REFERENCE.md).

There is no build step. Python runs inside Home Assistant; the card is plain ES module JS
served by the integration itself.

## Commands

Offline tests (no HA install needed; HA imports are stubbed with fakes):

```bash
python3 tests/offline/test_spool_ledger.py   # spool book: tare, weighing, history, low-stock rule
python3 tests/offline/test_spulen_laden.py   # load/unload services, slot discovery, active slot, tags, watcher
python3 tests/offline/test_print_live.py     # live usage per slot during a print
```

Each file is a plain script with asserts and prints "all passed" style output; run one
directly to run a single test file. Also check syntax after edits:

```bash
node --check custom_components/spoolbook/www/spoolbook.js
python3 -m py_compile custom_components/spoolbook/*.py
```

Real verification needs a running HA with ha-bambulab and a Bambu account. Changed Python
needs an HA restart; the card URL carries a digest computed at startup, so a changed card
also only reaches browsers after a restart.

## Architecture

**Two sources of truth, never mixed up:**
- The **Bambu cloud filament library** (`api.py`, unofficial API via `curl_cffi`, blocking,
  always called through an executor) holds spools and their grams. `coordinator.py` polls
  it and is the only writer of weights.
- **ha-bambulab** (domain `bambu_lab`) is the only source for printers and AMS slots.
  `trays.py` finds slots, the printer's print-status / active-tray sensors and the AMS
  names purely through HA's registries and state machine, by unique_id patterns
  (`{serial}_active_tray`, …), never by fixed entity ids. Nothing here talks to a printer
  directly except through ha-bambulab actions or the configured slot action.

**Local state** lives in HA storage per config entry: `spool_ledger.py` (empty-spool tare
weights, history, archive, own colour names, low-stock rule), `live_monitor.py` (running
print state, survives a restart).

**Write paths:**
- `services.py`: cloud CRUD actions (create/edit/delete spool, weigh).
- `loading.py`: `load_spool` / `unload_slot`. Order: printer must be idle → free the spool
  from any other slot → set the printer slot → bind in the cloud. Setting the printer slot
  goes through option `tray_command`: either ha-bambulab's `set_filament`, or a
  user-configured action that receives `befehl: ams/set_tray` (200 = set, 409 = RFID slot,
  bind only, 404/no answer = fall back to own command). Bambu firmware blocks third-party
  control in cloud mode, which is why that hook exists.
- `usage.py`: books a finished print's grams onto manual (non-RFID) spools; cancelled prints
  are booked proportionally to the last progress `live_monitor.py` persisted (unknown → a
  notification asking to weigh, no guess). This is the only consumption booking.
- `ams_register.py`: registers RFID spools sitting in the AMS but missing from the library.

**Display-only logic:** `print_live.py` (pure, no HA imports, unit-tested) +
`live_monitor.py` (HA wiring) estimate grams used per slot while printing (job grams ×
progress on the active slot). They never write to the cloud; after `usage.py` books, the
estimate drops so nothing is counted twice.

**Housekeeping:** `tray_sync.py` notifies when a slot is filled without a bound spool for
15 minutes, and re-sends slot settings 60 s after a printer comes online and 20 s after a
spool is loaded (never while it prints).

`docs/REFERENCE.md` (Options, Entities, Events, Actions) is the user-facing API
reference: update it whenever an option, attribute, event or service changes. `README.md` is the
showcase: short, visual, no tables or YAML. The card has a visual editor (`getConfigForm`);
add every new card option there too.

**Entities:** one hub device ("Spoolbook") with aggregate sensors and one
sensor per AMS slot (`TraySensor`: remaining grams minus live usage, spool details,
`active`, `used_in_print`, `missing_spool`); one device per spool (`entity.py` owns
creating/removing them as the library changes).

**Frontend:** `frontend.py` serves `www/` and registers the card resource and the sidebar
panel. `websocket.py` is the card's API (`spoolbook/trays`, tag helpers, …).
`www/spoolbook.js` is one custom element with modes `ams`, `lager` (storage), `fach`
(slot window), `wiegen` (weigh), `neu` (new spool); all dialogs live inside the card
(no browser_mod). UI strings are in its `I18N = {de, en}` table and go through `T()`;
backend messages that reach the card in German are mapped there too. Add every new
string in both languages. `qrcodegen.js` (Project Nayuki, MIT) renders labels offline.
`tags.py` makes each spool an HA tag so a QR/NFC scan opens that spool on the scanning
phone.

Translations for the config/options flow: `strings.json` + `translations/{de,en}.json`
(keep all three in sync).

## Conventions

- Identifier names in the card and ledger are German (`lager`, `fach`, `wiegen`, `spule`);
  they are part of the config API, keep them.
- No fixed entity ids anywhere: everything is discovered through registries.
- Diagnostics (`diagnostics.py`) redact tokens, RFIDs and device ids; keep it that way for
  anything new.
- `docs/screenshots/*.png` are shown in the README; regenerate them when the card's look
  changes.
