# Spoolbook reference

Everything technical in one place. Back to the [overview](../README.md).

## All features

- **Library**: every spool in your Bambu cloud library as a sensor; create, edit, delete.
- **Spool book**: empty spools (tare), weighing with or without the spool, per-spool history,
  archive instead of delete.
- **Low-stock warning**: a message when only one spool of a filament is left. You choose what
  counts as "the same filament": the material (default: no warning while any PETG is left),
  material + colour, or material + colour + brand.
- **Load a slot**: `spoolbook.load_spool` (spool + slot sensor) checks that the printer
  is idle, frees the spool from any other slot, sets the printer slot and binds the spool in
  the cloud. `spoolbook.unload_slot` empties a slot.
- **Sidebar page** `/spoolbook`: storage, slots and spool details.
  `/spoolbook?spule=<id>` opens one spool directly.
- **Card** `custom:spoolbook-card` with five modes: `ams` (AMS overview), `lager` (storage),
  `wiegen` (weigh), `neu` (new spool) and `fach` (slot window). All dialogs live inside the card, no
  browser_mod needed.
- **QR / NFC labels**: each spool gets the Home Assistant tag `spoolbook_<id>`. Its QR code
  holds `https://www.home-assistant.io/tag/spoolbook_<id>`; scanning it with the Companion
  app opens that spool's page on the scanning phone. If a slot was filled in the last
  10 minutes and has no spool yet, the page first asks "Goes into AMS … · slot …?".
  You can write the same tag to an NFC sticker.
- **Slot watcher**: a slot that holds filament for 15 minutes without a bound spool gets one
  notification, so no print runs on an unknown spool.
- **Slot re-send**: when the printer comes online (and 20 s after a spool is loaded), the
  material and colour of every bound slot are sent to the printer again. Skipped while it prints.

## What needs what

Spoolbook talks to two different things, and they have different requirements:

- **Bambu cloud filament library** (your Bambu account): storage, weighing, history,
  labels, low-stock warning, live usage and post-print booking, and binding a spool to a
  slot. This always works, whatever mode your printer is in, even when it is switched off.
- **The printer itself** (setting a slot's material and colour on the printer when you load a
  spool, and re-sending them when the printer comes online): Bambu firmware with
  authorization control rejects control commands from third-party tools in normal cloud mode.
  It works when
  - the printer is in **LAN-only mode with Developer mode** on, so
    [ha-bambulab](https://github.com/greghesp/ha-bambulab)'s `set_filament` gets through, or
  - you point the option "Action for the printer slot" at your own bridge that can talk to
    the printer (any action that accepts `befehl: ams/set_tray`, see Options).

  Without either, loading a spool still binds it in the cloud and everything in Spoolbook
  works; only the printer's own slot display keeps its old material and colour.
- **AMS slots and the active slot** are read from ha-bambulab, so that integration is needed
  for the AMS overview, the slot window and per-slot sensors (reading works in every mode).

## AMS overview card

```yaml
type: custom:spoolbook-card
mode: ams
# ams: [0]        # only these AMS units (ams_id from ha-bambulab); default: all
```

Tap a tile to open that slot's window (swap, weigh, unload); press and hold to weigh the
loaded spool straight away. A nice setup is a popup with `mode: ams` on top and
`mode: lager` with `hide_slots: true` below it.

## Card options (YAML)

The card has a visual editor (Add card → Spoolbook). In YAML it takes:

```yaml
type: custom:spoolbook-card
mode: fach                 # ams | lager | wiegen | neu | fach
tray: sensor.ams_1_tray_1  # fach (and optionally neu): slot sensor from ha-bambulab
# spool_id: 123456         # wiegen
# entity: sensor.…         # library sensor, found automatically otherwise
# hide_slots: true        # lager: hide the "Slots" section (e.g. when an ams card is right above)
# back: close              # wiegen/neu done: fire "spoolbook-close" (closes a browser_mod popup)
# back_script: my_script   # wiegen/neu done: call this script with {browser_id}
```

## Options (integration → Configure)

| Option | Meaning |
|---|---|
| Polling interval | Minutes between cloud library fetches (`spoolbook.refresh` fetches now). |
| Create one device per spool | One device with remaining % and grams per spool. Off = only the hub sensors. |
| Color name language | Language of the colour names derived from the hex code. |
| Auto-register new RFID spools | An official RFID spool in the AMS that is missing from the library is added automatically. Needs ha-bambulab. |
| Auto-remove manual duplicates | When the AMS registers a new official spool, one matching full, manually created spool is deleted, so pre-entered stock does not double up. |
| Sync remaining weight from the AMS | Pushes the AMS estimate of official RFID spools to the library whenever it changes (like Studio, without Studio running). 0 % is booked as empty. |
| Mark empty on runout | A spool taken out while a running print was drawing from it is set to 0 g. Removed after the print, or mid-print with more than 20 % left (a filament change), it is never touched. |
| Mark empty when removed below % | Optional extra rule: any spool removed at or below this percentage is set to 0 g. 0 = off. |
| Deduct print usage from manual spools | Books every finished print onto the manual (non-RFID) spool in each slot used, see "How usage is counted". |
| How loading a spool sets the printer slot | "Own command" (default): `bambu_lab.set_filament`. "Call an action": try the action below first. |
| Action for the printer slot | e.g. `rest_command.my_agent`; gets `befehl: ams/set_tray` and `daten: {ams_id, slot, filament_id, color, type}`. Response 200 = set, 409 = RFID slot (bind only), 404 or no response = own command. |
| Action for the low-stock message | Called with `title` and `message`, e.g. `notify.mobile_app_phone` or a script. Empty = a Home Assistant notification only. |
| Low-stock warning when this runs out | "the material" (default), "material in this colour" or "… from this brand". |
| Phones for label scans | Which Companion app devices open the spool page. Empty = all. |

The printer-related options show as "(unavailable)" with a link when ha-bambulab is not installed.

## How usage is counted

- **While printing** (display only): the active slot's tile and sensor sink by job grams ×
  progress, capped at what the job plans for that slot. Nothing is written to the cloud yet,
  so a crash or a restart cannot double-book; the running state survives a Home Assistant
  restart.
- **When the print ends**: the grams the Bambu cloud records per slot for that job are booked
  once onto the manual spool in each slot (option "Deduct print usage"). RFID spools are left
  to Bambu's own AMS estimate. Each job is booked only once.
- **Cancelled prints** (in the printer, the app or Studio) are booked proportionally to the
  last progress Spoolbook saw. If it never saw any progress, nothing is guessed: you get a
  notification to weigh the spool instead.
- **Weighing** always wins: a scale reading sets the true remaining weight and is kept in the
  spool's history.

## Entities

- **Spools** (`sensor.bambu_filament_library_spools`): number of active spools. Attributes
  carry the full inventory for the card (`spools`, `empty_spools`, `archived_spools`,
  `materials`, `brands`, `remaining_g_by_material`, `low_stock`, `trays_used`,
  `trays_live_g`); they are kept out of the recorder.
- **Total remaining filament**: grams across all active spools.
- **One sensor per AMS slot** (named after the AMS and slot, e.g. "AMS 1 · Fach 2"; "Fach" is German for slot): remaining
  grams of the bound spool minus live usage. Attributes: `spool_id`, `name`, `material`,
  `vendor`, `color`, `color_name`, `remaining_pct`, `total_g`, `filled`, `active` (the
  printer feeds from it now), `used_in_print`, `live_used_g`, `missing_spool` (filled but no
  spool bound), `ams_id`, `slot_id`, `ams_name`, `tray_entity` (the ha-bambulab slot sensor).
- **Per spool** (option "one device per spool"): remaining % and remaining grams, plus a
  "Delete from Bambu Cloud" button.

## Events

| Event | When | Data |
|---|---|---|
| `spoolbook_low_stock` | a sort is down to its last spool | `key`, `material`, `color`, `color_name`, `vendor`, `name`, `spool_id`, `remaining_g` of the last spool |
| `spoolbook_spool_empty` | a spool drops to 0 g | `spool_id`, `vendor`, `name`, `material` |

Use them for your own automations, e.g. a shopping list entry.

## Actions

All under `spoolbook.`; the ones that change data are admin-only. Field details are in
Developer tools → Actions.

| Action | What it does |
|---|---|
| `refresh` | Fetch the cloud library now. |
| `get_catalog` | Returns the vendor/product combinations the cloud accepts, plus your own slicer presets. |
| `create_spool` | Add a spool (also visible in Studio and the Bambu app); `count` adds several. |
| `update_spool` | Change fields of a spool; only the given fields are written. |
| `delete_spool` | Delete a spool from the cloud library. |
| `set_remaining` | Set the remaining grams. |
| `weigh_spool` | Set the remaining grams from a scale reading, with or without the empty spool. |
| `set_note` | Set the spool's note. |
| `set_filament_id` | Link a spool to a Bambu filament profile (e.g. `GFA00`), empty unlinks. |
| `set_color_name` | Your own colour name for display and low-stock sorts. |
| `archive_spool` | Archive an empty spool with its history, or bring it back. |
| `get_history` | Bookings of a spool (prints, weighing, create, archive), newest first. |
| `set_empty_spool` / `delete_empty_spool` | Manage empty-spool (tare) weights. |
| `load_spool` | Put a spool into a slot: printer idle check, free it elsewhere, set the printer slot, bind in the cloud; optionally weigh in the same step. |
| `unload_slot` | Clear a slot's spool binding (printer idle only). |
| `assign_spool` / `unassign_slot` | Lower-level bind / unbind in the cloud, without the checks of `load_spool`. |

## Troubleshooting

- Settings → Devices & services → Spoolbook → Download diagnostics: tokens, RFIDs and device
  ids are redacted, safe to attach to an issue.
- The printer's slot display does not change after loading a spool: see "What needs what".
- A changed card only reaches browsers after a Home Assistant restart (its URL carries a
  digest built at startup).

## Coming from ha-bambu-filaments

Spoolbook started as a fork of ha-bambu-filaments (domain `bambu_filaments`) and moves an
existing installation over once: login, options, spool book, entities (with their history,
labels and aliases) and devices. Install Spoolbook, remove the old integration's folder, add
one line `spoolbook:` to `configuration.yaml` and restart. Home Assistant only loads an
integration that has an entry or a YAML line, so this line starts the move. Afterwards
remove the line again. Entity ids `sensor.bambu_filament_library_*` become
`sensor.spoolbook_*`; printed `bambu_spule_` QR labels keep working.
