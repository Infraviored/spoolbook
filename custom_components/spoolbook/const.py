"""Constants for the Spoolbook integration."""

from __future__ import annotations

DOMAIN = "spoolbook"

CONF_REGION = "region"
CONF_EMAIL = "email"
CONF_TOKEN = "token"

REGION_GLOBAL = "global"
REGION_CHINA = "china"
REGIONS = [REGION_GLOBAL, REGION_CHINA]

OPT_SCAN_INTERVAL = "scan_interval"
OPT_SPOOL_ENTITIES = "spool_entities"
OPT_AUTO_DEDUP = "auto_dedup_manual"
OPT_COLOR_LANG = "color_language"
OPT_AUTO_REGISTER = "auto_register_ams"
# Shown read-only in the options flow while the printer integration is missing.
OPT_AUTO_REGISTER_UNAVAILABLE = "auto_register_ams_unavailable"
OPT_SYNC_REMAINING = "sync_ams_remaining"
OPT_SYNC_REMAINING_UNAVAILABLE = "sync_ams_remaining_unavailable"
OPT_EMPTY_ON_RUNOUT = "empty_on_runout"
OPT_EMPTY_ON_RUNOUT_UNAVAILABLE = "empty_on_runout_unavailable"
OPT_EMPTY_PCT = "empty_on_removal_pct"
OPT_EMPTY_PCT_UNAVAILABLE = "empty_on_removal_pct_unavailable"
OPT_DEDUCT_USAGE = "deduct_print_usage"

DEFAULT_SCAN_INTERVAL_MIN = 15
DEFAULT_SPOOL_ENTITIES = True
DEFAULT_AUTO_DEDUP = False
DEFAULT_COLOR_LANG = "auto"
DEFAULT_AUTO_REGISTER = False
DEFAULT_SYNC_REMAINING = False
DEFAULT_EMPTY_ON_RUNOUT = False
DEFAULT_EMPTY_PCT = 0  # 0 = off (optional extra rule next to the runout rule)
# Sanity cap for the runout rule: a spool pulled mid-print with more than
# this left was a deliberate filament change, not a runout.
RUNOUT_MAX_REMAIN_PCT = 20
DEFAULT_DEDUCT_USAGE = False
# Studio pushes AMS weights at most every 10 min per spool while the printer is busy.
REMAINING_PUSH_COOLDOWN_S = 600

# Domain of the Bambu Lab printer integration (github.com/greghesp/ha-bambulab)
# whose AMS slot sensors feed the auto-register feature.
BAMBULAB_DOMAIN = "bambu_lab"
# ha-bambulab AMS device model string -> cloud amsType (see AMS_TYPE_NAMES in the card).
AMS_TYPE_BY_MODEL = {"AMS": 1, "AMS Lite": 2, "AMS 2 Pro": 3, "AMS HT": 4}
COLOR_LANGS = ["auto", "de", "en"]

SERVICE_REFRESH = "refresh"

CARD_URL_BASE = "/spoolbook_files"
CARD_FILENAME = "bambu-filaments-card.js"
# Spulen card (wiegen / neu / lager / fach) + sidebar page, loaded via add_extra_js_url.
SPULEN_JS_FILENAME = "spoolbook.js"

# ------------------------------------------------------------ Spoolbook (3.x)
# Loading a spool into an AMS slot (service load_spool): how the printer slot
# itself is set before the cloud binding.
OPT_TRAY_COMMAND = "tray_command"
TRAY_COMMAND_OWN = "own"  # bambu_lab.set_filament (local, as in assign_spool)
TRAY_COMMAND_SERVICE = "service"  # a configurable HA action, e.g. rest_command.x
TRAY_COMMANDS = [TRAY_COMMAND_OWN, TRAY_COMMAND_SERVICE]
DEFAULT_TRAY_COMMAND = TRAY_COMMAND_OWN
OPT_TRAY_SERVICE = "tray_service"
DEFAULT_TRAY_SERVICE = ""
# Low-stock message: an action called with {title, message}; empty = only a
# persistent notification.
OPT_NOTIFY_SERVICE = "notify_service"
# Low-stock: what counts as "the same filament" (warn only when that runs out)
OPT_STOCK_LEVEL = "stock_level"
STOCK_LEVELS = ["material", "color", "vendor"]  # material / + colour / + colour + brand
DEFAULT_STOCK_LEVEL = "material"
DEFAULT_NOTIFY_SERVICE = ""
# Phones (mobile_app device ids) that open the spool page on a label scan;
# empty = every phone.
OPT_TAG_DEVICES = "tag_devices"

SERVICE_LOAD_SPOOL = "load_spool"
SERVICE_UNLOAD_SLOT = "unload_slot"

# Sidebar page (panel_custom) and its web component.
PANEL_URL_PATH = "spoolbook"
PANEL_COMPONENT = "spoolbook-panel"
PANEL_TITLE = "Spulen"
PANEL_ICON = "mdi:printer-3d-nozzle"
QR_JS_FILENAME = "qrcodegen.js"

# Labels: one HA tag per spool; the QR holds the HA tag URL (opens the
# Companion app, which fires tag_scanned with the scanning phone's device_id).
TAG_PREFIX = "spoolbook_"
# Labels printed before the rename carry this prefix; scans stay accepted.
LEGACY_TAG_PREFIX = "bambu_spule_"
# Domain of the pre-rename integration (one-time migration, see migrate.py).
LEGACY_DOMAIN = "bambu_filaments"
TAG_URL_BASE = "https://www.home-assistant.io/tag/"
# A slot loaded this recently without a binding is offered first in the detail.
RECENT_LOAD_S = 600
