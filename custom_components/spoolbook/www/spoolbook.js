/* Spoolbook - card + sidebar page: wiegen, anlegen, Lager, Fach, Etiketten.
 *
 * One Lovelace card, five modes (config.mode):
 *   wiegen  - weigh window for one spool (config.spool_id); "Mit Spule" / "Nur Filament",
 *             empty-spool chips, live preview, archive prompt at 0 g.
 *   neu     - create a spool: brand, material, colour, profile, weight, "Ins Lager" /
 *             "Ins Fach" (config.tray preselects a slot).
 *   lager   - stock overview: low stock, empty spools, sorts, spool detail (history,
 *             weigh, archive, "In Fach legen", label), label sheet for several spools.
 *   fach    - window for one AMS slot (config.tray = slot sensor): spool in it, weigh,
 *             choose another spool, "+ Neue Spule", empty the slot.
 *   ams     - overview of all AMS units: one section per AMS, a row of 4 tiles that
 *             are fill gauges (spool colour, height = remaining %), weight underneath,
 *             the slot in use highlighted. Tap a tile = the fach window of that slot
 *             (with a Back button to the overview). Long press (~500 ms) = weigh window for
 *             the spool in that slot (Back returns to the overview; ignored without a spool).
 * All dialogs live inside the card (no browser_mod needed).
 *
 * Optional config:
 *   entity       library sensor (default: found automatically)
 *   back         'none' (stay, default) | 'close' (fire "spoolbook-close"; closes a
 *                browser_mod popup when browser_mod is there)
 *   back_script  script to call with {browser_id} when a wiegen/neu window is done
 *   browser_id   passed to back_script
 *   nach_tausch  wiegen: the cancel button reads "Überspringen"
 *   ams          ams: list of AMS ids to show, e.g. [0] (default: all AMS units)
 *
 * Data: the library sensor of this integration (attributes from sensor.py) and the
 * websocket command spoolbook/trays (AMS slots found from the Bambu Lab
 * printer integration). The sidebar page (<spoolbook-panel>, /spoolbook)
 * shows the card in mode lager; /spoolbook?spule=<id> opens that spool.
 */
(() => {
  const DOMAIN = "spoolbook";
  const FILES = "/spoolbook_files";
  const MATERIAL_LABELS = ["PLA", "PLA Matte", "PLA+", "PLA Silk", "PETG", "PETG HF", "TPU", "ABS", "ASA", "PA", "PC"];
  const BASES = ["PETG", "PLA", "TPU", "ABS", "ASA", "PA", "PC", "PET", "PVA", "HIPS"];
  const GENERIC = { PLA: "GFL99", PETG: "GFG99", TPU: "GFU99", ABS: "GFB99", ASA: "GFB98", PA: "GFN99", PC: "GFC99", PVA: "GFS99" };
  const QUICK_COLORS = [
    ["black", "#161616"], ["white", "#FFFFFF"], ["gray", "#8A949E"], ["red", "#C12E1F"],
    ["blue", "#0A2989"], ["green", "#00AE42"], ["yellow", "#F4EE2A"], ["orange", "#FF6A13"],
  ];
  const KINDS = ["druck", "wiegen", "angelegt", "archiv", "zurueck", "korrektur", "leer"];

  // UI language: "de*" -> German, everything else -> English (from hass.locale / hass.language).
  // The helper is called T (not t): most render code uses `t` for a slot/tray.
  const I18N = {
    de: {
      colors_black: "Schwarz",
      colors_white: "Weiß",
      colors_gray: "Grau",
      colors_red: "Rot",
      colors_blue: "Blau",
      colors_green: "Grün",
      colors_yellow: "Gelb",
      colors_orange: "Orange",
      kind_druck: "Druck",
      kind_wiegen: "Gewogen",
      kind_angelegt: "Angelegt",
      kind_archiv: "Archiviert",
      kind_zurueck: "Zurückgeholt",
      kind_korrektur: "Korrigiert",
      kind_leer: "Leer",
      qr_missing: "QR-Bibliothek fehlt",
      qr_failed: "QR-Bibliothek nicht geladen",
      storage: "Lager",
      ams_slot: "AMS {a} · Fach {s}",
      slot: "Fach",
      in_slot: "Liegt jetzt in {label}.",
      rfid_note: " (RFID-Spule, der Drucker liest sie selbst.)",
      slot_emptied: "{label} ist geleert.",
      err_pick_empty: "Bitte eine Leerspule wählen (oder „Nur Filament“).",
      err_empty_name_weight: "Name und Gewicht der Leerspule angeben.",
      err_enter_weight: "Gewicht angeben.",
      err_pick_brand: "Marke wählen.",
      err_pick_material: "Material/Bezeichnung wählen.",
      err_scale_weight: "Gewicht von der Waage eintragen.",
      err_pick_or_create_empty: "Leerspule wählen oder anlegen (oder „Nur Filament“).",
      err_created_not_found: "Spule angelegt, aber nicht wiedergefunden - bitte über „Spule wählen“ einlegen.",
      err_created_not_loaded: "Spule angelegt (im Lager), aber nicht eingelegt: ",
      new_in_slot: "Neue Spule liegt in {label}.",
      spools_created: "{n} Spulen angelegt.",
      spool_created: "Spule angelegt.",
      saved: "Gespeichert.",
      err_tap_first: "Erst Spulen antippen.",
      err_colname: "Farbnamen eintragen.",
      ph_ne_name: "Name, z. B. Sunlu",
      ph_ne_weight: "g leer",
      add: "Anlegen",
      name_empty: "{name} leer:",
      apply: "Übernehmen",
      empty_estimate: "Leergewicht ist ein Schätzwert. ",
      empty_reweigh: "Leergewicht nachwiegen/ändern",
      new_chip: "+ Neue",
      prev_pick_empty: "Leerspule wählen",
      prev_enter_weight: "Gewicht eintragen",
      filament: "Filament",
      spool_nf_id: "Spule {id} nicht gefunden.",
      back: "Zurück",
      ams_empty: "Leer",
      ams_none: "Keine AMS-Fächer gefunden.",
      ams_unknown: "unbekannt",
      skip: "Überspringen",
      cancel: "Abbrechen",
      so_far: "bisher",
      scale_weight: "Gewicht laut Waage (g)",
      with_spool: "Mit Spule",
      filament_only: "Nur Filament",
      empty_spool: "Leerspule",
      saving: "Speichert…",
      save: "Speichern",
      empty_archive_q: "Leer, ins Archiv?",
      archive_hint: "Die Spule verschwindet aus Auswahl und Lager, Verlauf bleibt erhalten.",
      keep: "Behalten",
      to_archive: "Ins Archiv",
      ph_new_brand: "Neue Marke",
      no_profile: "Kein Profil (Fremdspule)",
      profile_custom: "{name} (eigenes)",
      profile_like: "{name} (wie vorhandene Spule)",
      new_spool: "Neue Spule",
      brand: "Marke",
      new_brand: "+ Neue Marke",
      material: "Material",
      ph_name: "Bezeichnung, z. B. High Speed Matte PETG",
      color: "Farbe",
      ph_colname: "Farbname",
      printer_profile: "Druckerprofil",
      catalog_loading: "Katalog lädt…",
      weight: "Gewicht",
      full: "Voll",
      weigh_now: "Gleich wiegen",
      ph_scale: "g laut Waage",
      filament_full: "Filament voll (g):",
      count: "Anzahl:",
      remember: " (merken)",
      creating: "Legt an…",
      to_storage: "Ins Lager",
      insert_slot: "Ins Fach einlegen",
      empty_banner: "Leer:",
      last: "letzte",
      spool_1: "{n} Spule",
      spool_n: "{n} Spulen",
      archive: "Archiv",
      archived_on: " · archiviert ",
      labels_selected: "Etiketten: {n} gewählt",
      all: "Alle",
      print: "Drucken",
      select_hint: "Spulen antippen, dann „Drucken“. Ein Etikett ist etwa 40 × 30 mm.",
      no_spools: "Keine Spulen.",
      storage_title: "Filament-Lager",
      print_labels: "Etiketten drucken",
      new_spool_btn: "+ Neue Spule",
      slots: "Fächer",
      empty: "leer",
      no_spool_sfx: "{m} · ohne Spule",
      goes_in: "Kommt in {label}?",
      yes_label: "Ja, {label}",
      here: "liegt hier",
      inside: "drin: {name}",
      no_spool_m: "{m} ohne Spule",
      printer_busy: "Drucker arbeitet",
      put_in_slot: "In Fach legen",
      take_out: "Aus {label} nehmen",
      slot_nf: "Fach {id} nicht gefunden (Sensor der Bambu-Lab-Integration?).",
      slots_loading: "Fächer laden…",
      now: "Jetzt:",
      last_of_kind: "letzte ihrer Sorte",
      no_spool_assigned: "{m}, keine Spule zugeordnet",
      reweigh: "Neu wiegen",
      clear_slot: "Fach leeren",
      spool: "Spule",
      busy_banner: "Der Drucker arbeitet gerade, Fächer erst danach tauschen.",
      choose_spool: "Spule wählen",
      creating_labels: "Etiketten werden erstellt…",
      sheet_title: "Spulen-Etiketten",
      sheet_hint: "Etiketten ausschneiden und auf die Spulen kleben. Scannen mit der Home-Assistant-App öffnet die Spule.",
      label_opened: "Etikett geöffnet.",
      labels_opened: "{n} Etiketten geöffnet.",
      spool_nf: "Spule nicht gefunden.",
      hist_loading: "Verlauf lädt…",
      no_entries: "Noch keine Einträge.",
      empty_spool_sfx: " · Leerspule ",
      now_paren: " (jetzt: {c})",
      name_color: "Farbe benennen",
      history: "Verlauf",
      print_label: "Etikett drucken",
      restore: "Zurückholen",
      panel_title: "Spulen",
      card_name: "Spoolbook",
      card_desc: "Spulen wiegen, anlegen, Lager, AMS-Übersicht, AMS-Fach und Etiketten (Spoolbook)",
      h_with: "{w} g mit {name}",
      h_without: "{w} g ohne Spule",
      h_weighed: "gewogen",
      h_removed: "Aus dem AMS genommen ({loc})",
      h_print: "Druck",
      h_aborted: "{title} (abgebrochen bei {p} %)",
      e_busy: "Der Drucker arbeitet gerade, Fächer erst danach tauschen.",
    },
    en: {
      colors_black: "Black",
      colors_white: "White",
      colors_gray: "Gray",
      colors_red: "Red",
      colors_blue: "Blue",
      colors_green: "Green",
      colors_yellow: "Yellow",
      colors_orange: "Orange",
      kind_druck: "Print",
      kind_wiegen: "Weighed",
      kind_angelegt: "Created",
      kind_archiv: "Archived",
      kind_zurueck: "Restored",
      kind_korrektur: "Corrected",
      kind_leer: "Empty",
      qr_missing: "QR library missing",
      qr_failed: "QR library failed to load",
      storage: "In storage",
      ams_slot: "AMS {a} · Slot {s}",
      slot: "Slot",
      in_slot: "Now in {label}.",
      rfid_note: " (RFID spool, the printer reads it itself.)",
      slot_emptied: "{label} emptied.",
      err_pick_empty: "Please choose an empty spool (or “Filament only”).",
      err_empty_name_weight: "Enter a name and weight for the empty spool.",
      err_enter_weight: "Enter a weight.",
      err_pick_brand: "Choose a brand.",
      err_pick_material: "Choose a material/name.",
      err_scale_weight: "Enter the weight from the scale.",
      err_pick_or_create_empty: "Choose or create an empty spool (or “Filament only”).",
      err_created_not_found: "Spool created, but not found again - please insert it via “Choose spool”.",
      err_created_not_loaded: "Spool created (in storage), but not inserted:",
      new_in_slot: "New spool is now in {label}.",
      spools_created: "{n} spools created.",
      spool_created: "Spool created.",
      saved: "Saved.",
      err_tap_first: "Tap some spools first.",
      err_colname: "Enter a color name.",
      ph_ne_name: "Name, e.g. Sunlu",
      ph_ne_weight: "g empty",
      add: "Add",
      name_empty: "{name} empty:",
      apply: "Apply",
      empty_estimate: "Empty weight is an estimate.",
      empty_reweigh: "Re-weigh/change empty weight",
      new_chip: "+ New",
      prev_pick_empty: "Choose empty spool",
      prev_enter_weight: "Enter weight",
      filament: "Filament",
      spool_nf_id: "Spool {id} not found.",
      back: "Back",
      ams_empty: "Empty",
      ams_none: "No AMS slots found.",
      ams_unknown: "unknown",
      skip: "Skip",
      cancel: "Cancel",
      so_far: "so far",
      scale_weight: "Weight on scale (g)",
      with_spool: "With spool",
      filament_only: "Filament only",
      empty_spool: "Empty spool",
      saving: "Saving…",
      save: "Save",
      empty_archive_q: "Empty, archive it?",
      archive_hint: "The spool disappears from the picker and storage; its history is kept.",
      keep: "Keep",
      to_archive: "Archive",
      ph_new_brand: "New brand",
      no_profile: "No profile (third-party spool)",
      profile_custom: "{name} (custom)",
      profile_like: "{name} (same as existing spool)",
      new_spool: "New spool",
      brand: "Brand",
      new_brand: "+ New brand",
      material: "Material",
      ph_name: "Name, e.g. High Speed Matte PETG",
      color: "Color",
      ph_colname: "Color name",
      printer_profile: "Printer profile",
      catalog_loading: "Loading catalog…",
      weight: "Weight",
      full: "Full",
      weigh_now: "Weigh now",
      ph_scale: "g on scale",
      filament_full: "Full filament (g):",
      count: "Count:",
      remember: " (remembered)",
      creating: "Creating…",
      to_storage: "Add to storage",
      insert_slot: "Insert into slot",
      empty_banner: "Empty:",
      last: "last one",
      spool_1: "{n} spool",
      spool_n: "{n} spools",
      archive: "Archive",
      archived_on: " · archived",
      labels_selected: "Labels: {n} selected",
      all: "All",
      print: "Print",
      select_hint: "Tap spools, then “Print”. A label is about 40 × 30 mm.",
      no_spools: "No spools.",
      storage_title: "Filament storage",
      print_labels: "Print labels",
      new_spool_btn: "+ New spool",
      slots: "Slots",
      empty: "empty",
      no_spool_sfx: "{m} · no spool",
      goes_in: "Is it in {label}?",
      yes_label: "Yes, {label}",
      here: "in this slot",
      inside: "inside: {name}",
      no_spool_m: "{m}, no spool",
      printer_busy: "Printer busy",
      put_in_slot: "Put in slot",
      take_out: "Take out of {label}",
      slot_nf: "Slot {id} not found (sensor of the Bambu Lab integration?).",
      slots_loading: "Loading slots…",
      now: "Now:",
      last_of_kind: "last of its kind",
      no_spool_assigned: "{m}, no spool assigned",
      reweigh: "Weigh again",
      clear_slot: "Empty slot",
      spool: "Spool",
      busy_banner: "The printer is busy, change slots afterwards.",
      choose_spool: "Choose spool",
      creating_labels: "Creating labels…",
      sheet_title: "Spool labels",
      sheet_hint: "Cut out the labels and stick them on the spools. Scanning with the Home Assistant app opens the spool.",
      label_opened: "Label opened.",
      labels_opened: "{n} labels opened.",
      spool_nf: "Spool not found.",
      hist_loading: "Loading history…",
      no_entries: "No entries yet.",
      empty_spool_sfx: " · empty spool",
      now_paren: " (now: {c})",
      name_color: "Name color",
      history: "History",
      print_label: "Print label",
      restore: "Restore",
      panel_title: "Spools",
      card_name: "Spoolbook",
      card_desc: "Weigh and add spools, storage, AMS overview, AMS slots and labels (Spoolbook)",
      h_with: "{w} g with {name}",
      h_without: "{w} g without spool",
      h_weighed: "weighed",
      h_removed: "Removed from the AMS ({loc})",
      h_print: "Print",
      h_aborted: "{title} (aborted at {p} %)",
      e_busy: "The printer is busy, change slots afterwards.",
    },
  };
  let LANG = "en";
  const isDe = (l) => String(l || "").toLowerCase().startsWith("de");
  const langOf = (hass) => (isDe(hass && ((hass.locale && hass.locale.language) || hass.language)) ? "de" : "en");
  const navLang = () => (isDe(navigator.language) ? "de" : "en");
  const T = (key, vars) => {
    const s = (I18N[LANG] && I18N[LANG][key]) || I18N.en[key] || key;
    return vars ? s.replace(/\{(\w+)\}/g, (m, k) => (vars[k] != null ? vars[k] : m)) : s;
  };
  // Slot labels come from the backend as "<AMS> · Fach <n>".
  const slotText = (l) => (LANG === "de" ? String(l || "") : String(l || "").replace(/ · Fach (\d+)$/, " · Slot $1"));
  // Service errors and history texts are German on the backend; the known ones are translated here.
  const errText = (e) => {
    const m = String((e && (e.message || e.error || (e.body && e.body.message))) || e);
    if (LANG === "de") return m;
    let x;
    if (m.includes("Fächer erst danach tauschen")) return T("e_busy");
    if ((x = m.match(/^(.+) ist kein AMS-Fach der Bambu-Lab-Integration/))) return `${x[1]} is not an AMS slot of the Bambu Lab integration (slot 1 to 4).`;
    if ((x = m.match(/^Spule (\S+) ist in keiner Filament-Bibliothek/))) return `Spool ${x[1]} is not in any filament library.`;
    if ((x = m.match(/^Spule (\S+) hat kein Druckerprofil/))) return `Spool ${x[1]} has no printer profile or material, the printer slot cannot be set.`;
    if ((x = m.match(/^Drucker-Fach nicht gesetzt, der Dienst (\S+) hat mit Status (\d+)/))) return `Printer slot not set: service ${x[1]} rejected it with status ${x[2]} (details in the log).`;
    if (m.startsWith("Leerspule unbekannt")) return "Unknown empty spool - please choose an empty spool or weigh “Filament only”.";
    if (m.startsWith("Dienst für das Drucker-Fach ungültig")) return "Invalid service for the printer slot (expected domain.service).";
    return m;
  };
  const histText = (h) => {
    const x = String(h.text || "");
    if (LANG === "de" || !x) return x;
    let m;
    if (x === "gewogen") return T("h_weighed");
    if ((m = x.match(/^(.+) g mit (.+)$/))) return T("h_with", { w: m[1], name: m[2] });
    if ((m = x.match(/^(.+) g ohne Spule$/))) return T("h_without", { w: m[1] });
    if ((m = x.match(/^Aus dem AMS genommen \((.*)\)$/))) return T("h_removed", { loc: m[1] });
    if ((m = x.match(/^(.*) \(abgebrochen bei (\d+) %\)$/))) return T("h_aborted", { title: m[1] === "Druck" ? T("h_print") : m[1], p: m[2] });
    return x === "Druck" ? T("h_print") : x;
  };

  const esc = (t) => String(t == null ? "" : t).replace(/[&<>"']/g, (c) => "&#" + c.charCodeAt(0) + ";");
  const hex = (c) => "#" + String(c || "#808080").replace(/^#/, "").slice(0, 6);
  const norm = (t) => String(t || "").trim().toLowerCase();
  const baseOf = (label) => {
    const up = String(label || "").toUpperCase();
    return BASES.find((b) => up.split(/[\s+\-_]/).includes(b)) || BASES.find((b) => up.includes(b)) || up.split(" ")[0] || "PLA";
  };
  const fmtDate = (iso) => {
    const d = new Date(iso);
    if (isNaN(d)) return "";
    return d.toLocaleDateString(LANG === "de" ? "de-DE" : "en-GB", { day: "2-digit", month: "2-digit" }) + " " +
      d.toLocaleTimeString(LANG === "de" ? "de-DE" : "en-GB", { hour: "2-digit", minute: "2-digit" });
  };
  const browserId = () =>
    (window.browser_mod && window.browser_mod.browserID) || localStorage.getItem("browser_mod-browser-id") || "";
  const spoolName = (s) => [s.vendor, s.display_name || s.name].filter(Boolean).join(" ");
  const sameSlot = (s, t) =>
    !!s && !!s.in_printer && String(s.ams_id) === String(t.ams_id) && String(s.slot_id) === String(t.slot_id) &&
    (!s.dev_id || !t.dev_id || s.dev_id === t.dev_id);

  // AMS slots, shared by every card on the page (one websocket call at a time).
  const TRAYS = { data: null, at: 0, promise: null, subs: new Set() };
  const loadTrays = (hass, force) => {
    if (TRAYS.promise) return TRAYS.promise;
    if (!force && TRAYS.data && Date.now() - TRAYS.at < 3000) return Promise.resolve(TRAYS.data);
    TRAYS.promise = hass.callWS({ type: DOMAIN + "/trays" })
      .then((d) => { TRAYS.data = d; TRAYS.at = Date.now(); return d; })
      .catch(() => TRAYS.data || { trays: [], pending: [] })
      .finally(() => { TRAYS.promise = null; TRAYS.subs.forEach((f) => f()); });
    return TRAYS.promise;
  };

  // QR codes: qrcodegen (Project Nayuki, MIT), shipped next to this file, loaded on demand.
  let qrPromise = null;
  const loadQr = () => {
    if (window.qrcodegen) return Promise.resolve(window.qrcodegen);
    if (!qrPromise) {
      qrPromise = new Promise((ok, fail) => {
        const sc = document.createElement("script");
        sc.src = FILES + "/qrcodegen.js";
        sc.onload = () => (window.qrcodegen ? ok(window.qrcodegen) : fail(new Error(T("qr_missing"))));
        sc.onerror = () => { qrPromise = null; fail(new Error(T("qr_failed"))); };
        document.head.appendChild(sc);
      });
    }
    return qrPromise;
  };
  const qrSvg = (qrlib, text) => {
    const qr = qrlib.QrCode.encodeText(text, qrlib.QrCode.Ecc.MEDIUM);
    const b = 2, n = qr.size + b * 2;
    let d = "";
    for (let y = 0; y < qr.size; y++) for (let x = 0; x < qr.size; x++) if (qr.getModule(x, y)) d += `M${x + b},${y + b}h1v1h-1z`;
    return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${n} ${n}" shape-rendering="crispEdges"><rect width="${n}" height="${n}" fill="#fff"/><path d="${d}" fill="#000"/></svg>`;
  };

  const CSS = `
    :host { display: block; }
    ha-card { padding: 12px; box-sizing: border-box; }
    .h { font-weight: 600; font-size: 15px; margin: 2px 0 8px; display: flex; align-items: center; gap: 8px; }
    .sub { font-size: 12px; color: var(--secondary-text-color); margin: 2px 0 8px; }
    .lbl { font-size: 12px; font-weight: 600; margin: 10px 0 4px; color: var(--secondary-text-color); }
    .row { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
    .chip { padding: 6px 11px; border-radius: 999px; font-size: 13px; cursor: pointer; white-space: nowrap;
      border: 1px solid var(--divider-color); background: none; color: var(--primary-text-color); font-family: inherit; }
    .chip.on { background: var(--accent-color, var(--primary-color)); border-color: var(--accent-color, var(--primary-color)); color: #000; }
    .chip.dash { border-style: dashed; }
    .btn { padding: 9px 14px; border-radius: 10px; font-size: 14px; cursor: pointer; border: 1px solid var(--divider-color);
      background: none; color: var(--primary-text-color); font-family: inherit; }
    .btn.pri { background: var(--accent-color, var(--primary-color)); border-color: transparent; color: #000; font-weight: 600; }
    .btn.warn { border-color: var(--error-color, #db4437); color: var(--error-color, #db4437); }
    .btn:disabled, .chip:disabled { opacity: .45; cursor: default; }
    .acts { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 14px; justify-content: flex-end; }
    input[type=number], input[type=text] { font: inherit; font-size: 16px; padding: 8px 10px; border-radius: 8px; width: 100%;
      box-sizing: border-box; border: 1px solid var(--divider-color); background: var(--card-background-color); color: var(--primary-text-color); }
    input.big { font-size: 24px; font-weight: 600; text-align: center; }
    input[type=color] { flex: 0 0 48px !important; width: 48px; height: 38px; border: none; padding: 0; background: none; cursor: pointer; }
    .grp { display: flex; align-items: center; gap: 8px; padding: 8px 10px; border-radius: 10px; cursor: pointer;
      background: rgba(127,127,127,.08); margin-top: 6px; font-size: 13px; }
    .grp .t { flex: 1; min-width: 0; }
    .grp .n { color: var(--secondary-text-color); font-size: 12px; white-space: nowrap; }
    select { font: inherit; padding: 8px; border-radius: 8px; width: 100%; border: 1px solid var(--divider-color);
      background: var(--card-background-color); color: var(--primary-text-color); }
    .inl { display: flex; gap: 6px; align-items: center; margin-top: 6px; }
    .inl input { flex: 1; }
    .prev { margin-top: 10px; padding: 10px; border-radius: 10px; background: rgba(127,127,127,.12); text-align: center; font-size: 16px; }
    .prev b { font-size: 20px; }
    .err { color: var(--error-color, #db4437); font-size: 13px; margin-top: 8px; white-space: pre-wrap; }
    .ok { color: var(--success-color, #43a047); font-size: 13px; margin-top: 8px; }
    .sw { flex-shrink: 0; width: 26px; height: 26px; border-radius: 50%; border: 2px solid rgba(255,255,255,.25); display: inline-block; }
    .list { display: flex; flex-direction: column; gap: 6px; }
    .item { display: flex; align-items: center; gap: 10px; padding: 8px 10px; border: 1px solid var(--divider-color);
      border-radius: 10px; cursor: pointer; }
    .item .t { flex: 1; min-width: 0; line-height: 1.3; }
    .item .t div:first-child { font-size: 13px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .item .t div + div { font-size: 12px; color: var(--secondary-text-color); }
    .item .g { font-size: 13px; font-weight: 600; white-space: nowrap; }
    .banner { padding: 8px 10px; border-radius: 10px; font-size: 13px; margin-bottom: 8px;
      background: rgba(255,152,0,.15); border: 1px solid rgba(255,152,0,.5); }
    .banner.red { background: rgba(219,68,55,.12); border-color: rgba(219,68,55,.5); }
    .hist { font-size: 12px; display: grid; grid-template-columns: auto auto 1fr auto; gap: 3px 10px; margin-top: 4px; }
    .hist .m { color: var(--secondary-text-color); }
    .muted { color: var(--secondary-text-color); font-size: 12px; }
    details summary { cursor: pointer; font-size: 13px; color: var(--secondary-text-color); margin-top: 10px; }
    .slots { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 6px; }
    .slot { display: flex; align-items: center; gap: 8px; padding: 8px 10px; border-radius: 10px; cursor: pointer; text-align: left;
      border: 1px solid var(--divider-color); background: none; color: var(--primary-text-color); font: inherit; font-size: 13px; min-width: 0; }
    .slot .t { flex: 1; min-width: 0; line-height: 1.25; }
    .slot .t div:first-child { font-weight: 600; }
    .slot .t div + div { font-size: 11px; color: var(--secondary-text-color); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .slot.here { border-color: var(--accent-color, var(--primary-color)); }
    .slot:disabled { opacity: .45; cursor: default; }
    .ask { display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 10px; border-radius: 10px;
      margin-bottom: 10px; background: rgba(3,169,244,.12); border: 1px solid rgba(3,169,244,.5); font-size: 14px; }
    .amsh { font-weight: 600; font-size: 15px; margin: 12px 0 8px; }
    .amsh:first-child { margin-top: 2px; }
    .amsrow { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }
    .amscol { min-width: 0; text-align: center; }
    .tile { position: relative; display: block; width: 100%; aspect-ratio: 1 / 1; padding: 0; overflow: hidden; cursor: pointer;
      border-radius: 14px; border: 2px solid rgba(127,127,127,.35); background: var(--card-background-color); font-family: inherit;
      -webkit-touch-callout: none; user-select: none; -webkit-user-select: none; }
    .tile.act { border-color: var(--accent-color, #f0b232); }
    .tile.used:not(.act) { border-style: dashed; border-color: var(--accent-color, #f0b232); }
    .tile .fill { position: absolute; left: 0; right: 0; bottom: 0; }
    .tile .nm { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; padding: 4px; box-sizing: border-box;
      text-align: center; font-size: 11px; line-height: 1.25; color: #fff; text-shadow: 0 0 3px rgba(0,0,0,.9), 0 1px 2px rgba(0,0,0,.9);
      word-break: break-word; overflow-wrap: anywhere; }
    .amsw { margin-top: 5px; font-weight: 700; font-size: 14px; }
    .grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; }
    .item.on { border-color: var(--accent-color, var(--primary-color)); background: rgba(127,127,127,.12); }
    .item.dash { border-style: dashed; justify-content: center; font-weight: 600; font-size: 13px; }
    .box { width: 18px; height: 18px; border-radius: 4px; border: 2px solid var(--divider-color); flex-shrink: 0; text-align: center; line-height: 16px; font-size: 13px; }
    .item.on .box { border-color: var(--accent-color, var(--primary-color)); background: var(--accent-color, var(--primary-color)); color: #000; }
  `;

  class BambuSpulenCard extends HTMLElement {
    setConfig(config) {
      this._config = { mode: "lager", back: "none", ...config };
      this._st = null; // view state, created on first render
      this._root = this._root || this.attachShadow({ mode: "open" });
      if (this._hass) this._render();
    }

    connectedCallback() {
      this._traySub = this._traySub || (() => {
        if (this._st && !this._st.busy && ["lager", "detail", "fach", "neu", "ams"].includes(this._st.view)) this._render();
      });
      TRAYS.subs.add(this._traySub);
    }

    disconnectedCallback() { if (this._traySub) TRAYS.subs.delete(this._traySub); }

    set hass(hass) {
      const first = !this._hass;
      this._hass = hass;
      const lang = langOf(hass);
      const langChanged = !first && lang !== LANG;
      LANG = lang;
      const lib = hass.states[this._libId];
      // Re-read the slots when the library or one of the slot sensors changed.
      const trays = (TRAYS.data && TRAYS.data.trays) || [];
      const sig = trays.map((t) => hass.states[t.entity_id]);
      const changed = first || lib !== this._lastLib || sig.some((s, i) => s !== (this._lastSig || [])[i]);
      this._lastSig = sig;
      if (changed) loadTrays(hass, !first);
      if (!this._st) {
        this._init();
        this._render();
      } else if (langChanged && !this._st.busy) {
        this._render();
      } else if (lib !== this._lastLib && ["lager", "detail", "fach", "ams"].includes(this._st.view) && !this._st.busy) {
        this._render();
      }
      this._lastLib = lib;
    }

    getCardSize() { return 6; }
    static getStubConfig() { return { mode: "ams" }; }

    // Visual editor: pick the mode (and the slot for "fach") without writing YAML.
    static getConfigForm() {
      const de = navLang() === "de";
      const opt = (value, d, e) => ({ value, label: de ? d : e });
      return {
        schema: [
          { name: "mode", required: true, selector: { select: { mode: "dropdown", options: [
            opt("ams", "AMS-Übersicht", "AMS overview"),
            opt("lager", "Lager", "Storage"),
            opt("fach", "Ein AMS-Fach", "One AMS slot"),
            opt("wiegen", "Wiegen", "Weigh"),
            opt("neu", "Neue Spule", "New spool"),
          ] } } },
          { name: "tray", selector: { entity: { domain: "sensor", integration: "bambu_lab" } } },
          { name: "hide_slots", selector: { boolean: {} } },
        ],
        computeLabel: (s) => ({
          mode: de ? "Ansicht" : "View",
          tray: de ? "AMS-Fach (für „Ein AMS-Fach“)" : "AMS slot (for \u201cOne AMS slot\u201d)",
          hide_slots: de ? "Fächer im Lager ausblenden" : "Hide slots in storage",
        })[s.name],
      };
    }

    // ------------------------------------------------------------ data
    get _libId() {
      if (this._config && this._config.entity) return this._config.entity;
      if (TRAYS.data && TRAYS.data.library) return TRAYS.data.library;
      if (this._libGuess && this._hass && this._hass.states[this._libGuess]) return this._libGuess;
      const states = (this._hass && this._hass.states) || {};
      this._libGuess = Object.keys(states).find((id) => id.startsWith("sensor.") &&
        Array.isArray(states[id].attributes.spools) && Array.isArray(states[id].attributes.empty_spools)) || "";
      return this._libGuess;
    }
    get _lib() { return ((this._hass && this._hass.states[this._libId]) || {}).attributes || {}; }
    get _spools() { return this._lib.spools || []; }
    get _empties() { return this._lib.empty_spools || []; }
    get _trays() { return (TRAYS.data && TRAYS.data.trays) || []; }
    _tray(id) { return this._trays.find((t) => t.entity_id === id); }
    _spool(id) { return this._spools.find((s) => s.spool_id === Number(id)); }
    _empty(id) { return this._empties.find((e) => e.id === id); }
    _emptyLabel(e) { return `${e.name} ${e.placeholder ? "~" : ""}${e.weight_g} g`; }
    _spoolTray(s) { return s && s.in_printer ? this._trays.find((t) => sameSlot(s, t)) : null; }
    _trayLabel(t) { return t ? slotText(t.label) : ""; }
    _where(s) {
      if (!s.in_printer) return T("storage");
      const tr = this._spoolTray(s);
      return tr ? slotText(tr.label) : T("ams_slot", { a: Number(s.ams_id) + 1, s: Number(s.slot_id) + 1 });
    }
    _inTray(t) { return t && t.spool_id ? this._spool(t.spool_id) : null; }

    async _svc(service, data, response = false, domain = DOMAIN) {
      if (!response) return this._hass.callService(domain, service, data);
      const r = await this._hass.callWS({ type: "call_service", domain, service, service_data: data, return_response: true });
      return r && r.response;
    }

    _init() {
      const c = this._config;
      const view = ["neu", "wiegen", "fach", "ams"].includes(c.mode) ? c.mode : "lager";
      this._st = { view, home: view === "fach" ? "fach" : view === "lager" ? "lager" : null, err: "", msg: "", busy: false };
      if (view === "wiegen") this._initWeigh(c.spool_id);
      if (view === "neu") this._initNew(c.tray);
      if (view === "fach") Object.assign(this._st, { tray: c.tray || "", filt: { mat: "", ven: "" } });
      if (view === "ams") this._st.fromAms = true;
    }

    // Sidebar page / deep link: show one spool's detail.
    openSpool(id) {
      if (!this._st) this._init();
      Object.assign(this._st, { view: "detail", detailId: Number(id), hist: null, err: "", msg: "", home: "lager" });
      this._loadHist();
      this._render();
    }

    _initWeigh(spoolId) {
      const s = this._spool(spoolId) || {};
      const esid = s.empty_spool || s.empty_spool_suggested || "";
      const tare = (this._empty(esid) || {}).weight_g || 0;
      Object.assign(this._st, {
        view: "wiegen", sid: Number(spoolId), withSpool: true, esid,
        weight: s.remaining_g != null ? Math.round(Number(s.remaining_g) + Number(tare)) : "",
        newEmpty: null, editEmpty: false, result: null, err: "",
      });
    }

    _initNew(tray) {
      Object.assign(this._st, {
        view: "neu", tray: tray || "", vendor: "", newVendor: false, label: "", name: "", nameTouched: false,
        colorName: "", color: "#FFFFFF", fid: "", fidTouched: false, weighMode: "voll", total: 1000, gross: "",
        withSpool: true, esid: "", count: 1, newEmpty: null, err: "", catalog: null,
      });
      this._loadCatalog();
    }

    async _loadCatalog() {
      try {
        const r = await this._svc("get_catalog", {}, true);
        this._st.catalog = r || { filaments: [], presets: [] };
      } catch (e) {
        this._st.catalog = { filaments: [], presets: [] };
      }
      if (this._st.view === "neu" && !this._st.fidTouched) this._st.fid = this._suggestFid();
      if (this._st.view === "neu") this._render();
    }

    // ------------------------------------------------------------ actions
    // A sub-view (wiegen, neu, leer) is done: back to where it was opened from.
    async _finish() {
      const st = this._st, c = this._config;
      st.err = "";
      st.nachTausch = false;
      if (st.home === "lager") {
        st.view = st.detailId ? "detail" : "lager";
        if (st.detailId) { st.hist = null; this._loadHist(); }
        return this._render();
      }
      if (st.home === "fach") { st.view = "fach"; return this._render(); }
      if (st.home === "ams") { st.view = "ams"; st.home = null; return this._render(); }
      const bid = c.browser_id || browserId();
      if (c.back_script) return this._svc(String(c.back_script).replace(/^script\./, ""), { browser_id: bid }, false, "script");
      if (c.back === "close") return this._close();
      st.view = "done";
      this._render();
    }

    _close() {
      this.dispatchEvent(new CustomEvent("spoolbook-close", { bubbles: true, composed: true }));
      if (window.browser_mod && this._hass.services.browser_mod) {
        const bid = this._config.browser_id || browserId();
        return this._hass.callService("browser_mod", "close_popup", bid ? { browser_id: [bid] } : {});
      }
    }

    // Load a spool into a slot (service load_spool), then optionally weigh it.
    _load(sid, trayId, after) {
      const st = this._st;
      return this._run(async () => {
        const r = await this._svc("load_spool", { spool_id: Number(sid), tray: trayId }, true);
        loadTrays(this._hass, true);
        const tr = this._tray(trayId);
        st.msg = T("in_slot", { label: slotText((r && r.tray_label) || (tr && tr.label) || trayId) }) +
          (r && r.printer === "RFID-Fach" ? T("rfid_note") : "");
        if (after) after(r);
      });
    }

    _unload(trayId) {
      const st = this._st;
      return this._run(async () => {
        await this._svc("unload_slot", { tray: trayId }, true);
        loadTrays(this._hass, true);
        const tr = this._tray(trayId);
        st.msg = T("slot_emptied", { label: tr ? slotText(tr.label) : T("slot") });
      });
    }

    async _run(fn) {
      if (this._st.busy) return;
      this._st.busy = true; this._st.err = ""; this._st.msg = ""; this._render();
      try { await fn(); } catch (e) { this._st.err = errText(e); this._st.msg = ""; }
      this._st.busy = false; this._render();
    }

    _net() {
      const st = this._st, w = Number(st.weight);
      if (st.weight === "" || isNaN(w)) return null;
      if (!st.withSpool) return Math.max(0, Math.round(w));
      const e = this._empty(st.esid);
      return e ? Math.max(0, Math.round(w - e.weight_g)) : null;
    }

    // A "+ Neue" empty spool that was filled in but not yet confirmed is
    // created on save instead of being silently ignored.
    async _pendingEmpty() {
      const st = this._st, ne = st.newEmpty;
      if (!ne || !ne.name || !Number(ne.weight)) return;
      const data = { name: ne.name, weight_g: Math.round(Number(ne.weight)) };
      if (ne.vendor) data.vendor = ne.vendor;
      const r = await this._svc("set_empty_spool", data, true);
      st.esid = r.id; st.newEmpty = null;
    }

    _saveWeigh() {
      const st = this._st;
      return this._run(async () => {
        if (st.withSpool) await this._pendingEmpty();
        const data = { spool_id: st.sid, weight_g: Math.round(Number(st.weight)), with_spool: st.withSpool };
        if (st.withSpool) {
          if (!st.esid) throw new Error(T("err_pick_empty"));
          data.empty_spool = st.esid;
        }
        const r = await this._svc("weigh_spool", data, true);
        st.result = r;
        if (r && r.empty) { st.view = "leer"; return; }
        st.busy = false;
        await this._finish();
      });
    }

    _archive(sid, archived = true, then) {
      return this._run(async () => {
        await this._svc("archive_spool", { spool_id: Number(sid), archived });
        if (then) await then();
      });
    }

    _addEmpty(after) {
      const st = this._st, ne = st.newEmpty || {};
      return this._run(async () => {
        const w = Math.round(Number(ne.weight));
        if (!ne.name || !w) throw new Error(T("err_empty_name_weight"));
        const data = { name: ne.name, weight_g: w };
        if (ne.vendor) data.vendor = ne.vendor;
        const r = await this._svc("set_empty_spool", data, true);
        st.esid = r.id; st.newEmpty = null;
        if (after) after(r);
      });
    }

    _updateEmptyWeight() {
      const st = this._st;
      return this._run(async () => {
        const w = Math.round(Number(st.editWeight));
        if (!w) throw new Error(T("err_enter_weight"));
        await this._svc("set_empty_spool", { empty_spool: st.esid, weight_g: w });
        st.editEmpty = false;
      });
    }

    // ------------------------------------------------------------ neu helpers
    _vendors() {
      const set = new Map();
      const add = (v) => { if (v && !set.has(norm(v))) set.set(norm(v), v); };
      (this._lib.brands || []).forEach((b) => add(b.vendor));
      this._spools.forEach((s) => add(s.vendor));
      (this._lib.archived_spools || []).forEach((s) => add(s.vendor));
      return [...set.values()].sort((a, b) => a.localeCompare(b));
    }
    _brand(v) { return (this._lib.brands || []).find((b) => norm(b.vendor) === norm(v)); }
    _labels() {
      const st = this._st, out = [...MATERIAL_LABELS];
      this._spools.filter((s) => norm(s.vendor) === norm(st.vendor)).forEach((s) => {
        if (s.name && !out.some((l) => norm(l) === norm(s.name))) out.push(s.name);
      });
      return out;
    }
    _profiles() {
      const st = this._st, base = baseOf(st.label || st.name);
      const cat = (st.catalog && st.catalog.filaments) || [];
      const pre = (st.catalog && st.catalog.presets) || [];
      const out = [], seen = new Set();
      const add = (id, text) => { if (id && !seen.has(id)) { seen.add(id); out.push([id, text]); } };
      cat.filter((f) => norm(f.vendor) === norm(st.vendor) && baseOf(f.material) === base).forEach((f) => add(f.filament_id, `${f.vendor} ${f.name}`));
      pre.filter((p) => baseOf(p.material) === base).forEach((p) => add(p.filament_id, T("profile_custom", { name: p.name })));
      this._spools.filter((s) => norm(s.vendor) === norm(st.vendor) && baseOf(s.material) === base && s.filament_id)
        .forEach((s) => add(s.filament_id, T("profile_like", { name: `${s.vendor} ${s.name}` })));
      cat.filter((f) => f.vendor === "Generic" && baseOf(f.material) === base).forEach((f) => add(f.filament_id, `Generic ${f.name}`));
      if (GENERIC[base]) add(GENERIC[base], `Generic ${base}`);
      return out;
    }
    _suggestFid() {
      const st = this._st, base = baseOf(st.label || st.name);
      const b = this._brand(st.vendor);
      if (b && b.filament_id && baseOf(b.material) === base && norm(b.name) === norm(st.name)) return b.filament_id;
      const cat = (st.catalog && st.catalog.filaments) || [];
      const pre = (st.catalog && st.catalog.presets) || [];
      const exact = cat.find((f) => norm(f.vendor) === norm(st.vendor) && norm(f.name) === norm(st.name));
      if (exact) return exact.filament_id;
      const same = this._spools.find((s) => norm(s.vendor) === norm(st.vendor) && norm(s.name) === norm(st.name) && s.filament_id);
      if (same) return same.filament_id;
      const p = pre.find((x) => norm(x.vendor) === norm(st.vendor) && baseOf(x.material) === base);
      if (p) return p.filament_id;
      const v = cat.find((f) => norm(f.vendor) === norm(st.vendor) && baseOf(f.material) === base);
      if (v) return v.filament_id;
      if (b && b.filament_id && baseOf(b.material) === base) return b.filament_id;
      const g = cat.find((f) => f.vendor === "Generic" && norm(f.name) === norm(base));
      return (g && g.filament_id) || GENERIC[base] || "";
    }
    _pickVendor(v) {
      const st = this._st;
      st.vendor = v; st.newVendor = false;
      const b = this._brand(v);
      if (b) {
        // Same brand again: material, profile, empty spool and size from last time.
        st.label = b.name || b.material || ""; st.name = b.name || ""; st.nameTouched = false;
        if (b.total_g) st.total = b.total_g;
      }
      const e = (b && b.empty_spool && this._empty(b.empty_spool)) ||
        this._empties.find((x) => norm(x.vendor || x.name) && (norm(v).includes(norm(x.vendor || x.name)) || norm(x.vendor || x.name).includes(norm(v))));
      st.esid = e ? e.id : "";
      if (!e && v) st.newEmpty = { name: v, weight: "", vendor: v };
      else st.newEmpty = null;
      if (!st.fidTouched) st.fid = this._suggestFid();
    }

    _create(tray) {
      const st = this._st;
      return this._run(async () => {
        if (!st.vendor) throw new Error(T("err_pick_brand"));
        if (!st.name) throw new Error(T("err_pick_material"));
        await this._pendingEmpty();
        const data = {
          vendor: st.vendor, material: baseOf(st.label || st.name), name: st.name,
          color: hex(st.color), filament_id: st.fid || "", total_g: Math.round(Number(st.total)) || 1000,
        };
        if (st.colorName) data.color_name = st.colorName;
        if (st.esid) data.empty_spool = st.esid;
        if (st.weighMode === "wiegen") {
          const w = Math.round(Number(st.gross));
          if (st.gross === "" || isNaN(w)) throw new Error(T("err_scale_weight"));
          if (st.withSpool) {
            if (!st.esid) throw new Error(T("err_pick_or_create_empty"));
            data.gross_g = w;
          } else data.remaining_g = Math.min(w, 20000);
        } else if (!tray) {
          data.count = Math.max(1, Math.min(50, Math.round(Number(st.count)) || 1));
        }
        const r = await this._svc("create_spool", data, true);
        const ids = (r && r.spool_ids) || [];
        if (tray) {
          if (!ids.length) throw new Error(T("err_created_not_found"));
          let r2;
          try {
            r2 = await this._svc("load_spool", { spool_id: ids[0], tray }, true);
          } catch (e) {
            throw new Error(T("err_created_not_loaded") + errText(e));
          }
          loadTrays(this._hass, true);
          const tr = this._tray(tray);
          st.msg = T("new_in_slot", { label: slotText((r2 && r2.tray_label) || (tr && tr.label) || tray) });
        } else {
          st.msg = ids.length > 1 ? T("spools_created", { n: ids.length }) : T("spool_created");
        }
        st.busy = false;
        const msg = st.msg;
        await this._finish();
        st.msg = msg;
        this._render();
      });
    }

    // ------------------------------------------------------------ render
    _render() {
      if (!this._root || !this._st) return;
      const v = this._st.view;
      const body =
        v === "wiegen" ? this._rWeigh() :
        v === "leer" ? this._rEmptyPrompt() :
        v === "neu" ? this._rNew() :
        v === "detail" ? this._rDetail() :
        v === "fach" ? this._rFach() :
        v === "ams" ? this._rAms() :
        v === "done" ? `<div class="ok">${esc(T("saved"))}</div>` : this._rLager();
      const err = (this._st.err ? `<div class="err">${esc(this._st.err)}</div>` : "") +
        (this._st.msg && !this._st.err ? `<div class="ok">${esc(this._st.msg)}</div>` : "");
      const active = this._root.activeElement;
      const focusKey = active && active.dataset ? active.dataset.k : null;
      this._root.innerHTML = `<style>${CSS}</style><ha-card>${body}${err}</ha-card>`;
      this._bind();
      if (focusKey) {
        const el = this._root.querySelector(`[data-k="${focusKey}"]`);
        if (el) { el.focus(); if (el.setSelectionRange && el.type === "text") el.setSelectionRange(el.value.length, el.value.length); }
      } else if (v === "wiegen") {
        const el = this._root.querySelector('[data-k="weight"]');
        if (el && !this._st.focused) { this._st.focused = true; setTimeout(() => { el.focus(); el.select(); }, 50); }
      }
    }

    // Long press (~500 ms, mouse and touch) on an AMS tile: weigh the spool in that slot.
    _bindLongPress(el) {
      let timer = null, x0 = 0, y0 = 0;
      const cancel = () => { if (timer) { clearTimeout(timer); timer = null; } };
      el.addEventListener("pointerdown", (ev) => {
        if (ev.pointerType === "mouse" && ev.button !== 0) return;
        el._lpFired = false;
        x0 = ev.clientX; y0 = ev.clientY;
        cancel();
        timer = setTimeout(() => {
          timer = null;
          const t = this._trays.find((x) => x.entity_id === el.dataset.v);
          if (!t || !t.spool_id || !this._spool(t.spool_id)) return; // nothing bound: ignore, tap still opens the slot
          el._lpFired = true;
          if (navigator.vibrate) { try { navigator.vibrate(30); } catch (e) { /* ignore */ } }
          const st = this._st;
          st.tray = t.entity_id;
          this._initWeigh(t.spool_id);
          st.home = "ams"; st.nachTausch = false;
          this._render();
        }, 500);
      });
      el.addEventListener("pointermove", (ev) => {
        if (timer && Math.hypot(ev.clientX - x0, ev.clientY - y0) > 10) cancel();
      });
      ["pointerup", "pointercancel", "pointerleave"].forEach((n) => el.addEventListener(n, cancel));
      el.addEventListener("contextmenu", (ev) => ev.preventDefault());
    }

    _bind() {
      const st = this._st;
      this._root.querySelectorAll("[data-a]").forEach((el) => {
        el.addEventListener("click", (ev) => {
          ev.stopPropagation(); ev.preventDefault();
          if (el._lpFired) { el._lpFired = false; return; } // the long press already acted
          this._act(el.dataset.a, el.dataset.v, el);
        });
        if (el.dataset.a === "ams-open") this._bindLongPress(el);
      });
      this._root.querySelectorAll("[data-k]").forEach((el) => {
        const k = el.dataset.k;
        const evName = el.tagName === "SELECT" || el.type === "color" ? "change" : "input";
        el.addEventListener(evName, () => {
          const val = el.value;
          if (k.startsWith("ne.")) { st.newEmpty = st.newEmpty || {}; st.newEmpty[k.slice(3)] = val; return; }
          st[k] = val;
          if (k === "weight") {
            const p = this._root.querySelector(".prev");
            if (p) p.innerHTML = this._previewHtml();
          }
          if (k === "name") { st.nameTouched = true; if (!st.fidTouched) { st.fid = this._suggestFid(); this._render(); } }
          if (k === "fid") st.fidTouched = true;
          if (k === "vendor") { st.vendor = val; }
          if (k === "color" || k === "gross" || k === "total") this._render();
        });
        el.addEventListener("keydown", (ev) => {
          ev.stopPropagation();
          if (ev.key === "Enter" && k === "weight") this._saveWeigh();
        });
      });
    }

    _act(a, v) {
      const st = this._st;
      switch (a) {
        case "mode": st.withSpool = v === "1"; break;
        case "es": st.esid = v; st.newEmpty = null; st.editEmpty = false; break;
        case "es-new": st.newEmpty = { name: "", weight: "", vendor: "" }; break;
        case "es-add": return this._addEmpty();
        case "es-edit": st.editEmpty = !st.editEmpty; st.editWeight = (this._empty(st.esid) || {}).weight_g || ""; break;
        case "es-save": return this._updateEmptyWeight();
        case "save": return this._saveWeigh();
        case "cancel": return this._finish();
        case "arch-yes": return this._archive(st.sid, true, () => this._finish());
        case "arch-no": return this._finish();
        case "ven": this._pickVendor(v); break;
        case "ven-new": st.newVendor = true; st.vendor = ""; st.esid = ""; st.newEmpty = null; break;
        case "ven-ok": if (st.vendor) this._pickVendor(st.vendor.trim()); break;
        case "lab":
          st.label = v; st.name = v; st.nameTouched = false;
          if (!st.fidTouched) st.fid = this._suggestFid();
          break;
        case "col": { const q = QUICK_COLORS[Number(v)]; st.colorName = T("colors_" + q[0]); st.color = q[1]; break; }
        case "wm": st.weighMode = v; break;
        case "create": return this._create("");
        case "create-tray": return this._create(v);
        case "new": this._initNew(""); break;
        case "detail":
          if (st.sel) { st.sel.has(Number(v)) ? st.sel.delete(Number(v)) : st.sel.add(Number(v)); break; }
          st.detailId = Number(v); st.view = "detail"; st.hist = null; st.msg = ""; this._loadHist(); break;
        case "back": st.view = "lager"; st.detailId = null; st.err = ""; st.msg = ""; break;
        case "load": return this._load(st.detailId, v);
        case "unload": return this._unload(v);
        case "label": return this._printLabels([Number(v)]);
        case "sel-start": st.sel = new Set(); st.msg = ""; break;
        case "sel-cancel": st.sel = null; break;
        case "sel-all": {
          const ids = this._spools.filter((s) => !s.archived).map((s) => s.spool_id);
          st.sel = st.sel && st.sel.size === ids.length ? new Set() : new Set(ids);
          break;
        }
        case "sel-print": {
          if (!st.sel || !st.sel.size) { st.err = T("err_tap_first"); break; }
          const ids = [...st.sel];
          return this._printLabels(ids).then(() => { if (!st.err) { st.sel = null; this._render(); } });
        }
        case "pick": return this._load(v, st.tray, () => { this._initWeigh(v); st.nachTausch = true; });
        case "ams-open": Object.assign(st, { view: "fach", home: "fach", tray: v, filt: { mat: "", ven: "" }, err: "", msg: "" }); break;
        case "ams-back": st.view = "ams"; st.home = null; st.err = ""; st.msg = ""; break;
        case "fach-new": this._initNew(st.tray); break;
        case "fach-weigh": this._initWeigh(v); break;
        case "fach-arch": return this._archive(v, true);
        case "fach-clear": return this._unload(st.tray);
        case "f": {
          const [k, val] = [v.slice(0, 3), v.slice(4)];
          st.filt[k] = st.filt[k] === val ? "" : val;
          break;
        }
        case "weigh": this._initWeigh(v); break;
        case "archive": return this._archive(v, true);
        case "unarchive": return this._archive(v, false);
        case "grp": { const o = st.open || (st.open = new Set()); o.has(v) ? o.delete(v) : o.add(v); break; }
        case "colname": return this._run(async () => {
          const name = String(st.colname || "").trim();
          if (!name) throw new Error(T("err_colname"));
          await this._svc("set_color_name", { spool_id: Number(v), color_name: name });
          st.colname = "";
        });
        default: return;
      }
      this._render();
    }

    async _loadHist() {
      const id = this._st.detailId;
      try {
        const r = await this._svc("get_history", { spool_id: id, limit: 15 }, true);
        if (this._st.detailId === id) { this._st.hist = r.history || []; this._render(); }
      } catch (e) { this._st.hist = []; this._render(); }
    }

    // ------------------------------------------------------------ views
    _emptyChips(allowEdit) {
      const st = this._st;
      const chips = this._empties.map((e) =>
        `<button class="chip ${st.esid === e.id ? "on" : ""}" data-a="es" data-v="${esc(e.id)}">${esc(this._emptyLabel(e))}</button>`).join("");
      let extra = "";
      if (st.newEmpty) {
        const ne = st.newEmpty;
        extra = `<div class="inl"><input type="text" data-k="ne.name" placeholder="${esc(T("ph_ne_name"))}" value="${esc(ne.name)}">
          <input type="number" inputmode="numeric" data-k="ne.weight" placeholder="${esc(T("ph_ne_weight"))}" value="${esc(ne.weight)}" style="max-width:90px">
          <button class="btn" data-a="es-add">${esc(T("add"))}</button></div>`;
      } else if (allowEdit && st.esid) {
        const e = this._empty(st.esid);
        extra = st.editEmpty
          ? `<div class="inl"><span class="muted">${esc(T("name_empty", { name: e.name }))}</span><input type="number" inputmode="numeric" data-k="editWeight" value="${esc(st.editWeight)}" style="max-width:100px">
             <button class="btn" data-a="es-save">${esc(T("apply"))}</button></div>`
          : `<div class="muted" style="margin-top:4px">${e && e.placeholder ? esc(T("empty_estimate")) : ""}<a href="#" data-a="es-edit" style="color:var(--primary-color)">${esc(T("empty_reweigh"))}</a></div>`;
      }
      return `<div class="row">${chips}<button class="chip dash" data-a="es-new">${esc(T("new_chip"))}</button></div>${extra}`;
    }

    _previewHtml() {
      const st = this._st, s = this._spool(st.sid) || {};
      const net = this._net();
      if (net == null) return st.withSpool && !st.esid ? T("prev_pick_empty") : T("prev_enter_weight");
      const total = Number(s.total_g) || 0;
      const pct = total > 0 ? Math.max(0, Math.min(100, Math.round((net / total) * 100))) : 0;
      return `${esc(T("filament"))}: <b>${net} g</b>${total ? `, ${pct} %` : ""}`;
    }

    _rWeigh() {
      const st = this._st, s = this._spool(st.sid);
      if (!s) return `<div class="err">${esc(T("spool_nf_id", { id: st.sid }))}</div><div class="acts"><button class="btn" data-a="cancel">${esc(T("back"))}</button></div>`;
      const pct = s.remaining_pct != null ? ` (${s.remaining_pct} %)` : "";
      const skip = this._st.home === "ams" ? T("back") : this._config.nach_tausch || this._st.nachTausch ? T("skip") : T("cancel");
      return `
        <div class="h"><span class="sw" style="background:${hex(s.color)}"></span>${esc(spoolName(s))} ${esc(s.material || "")}</div>
        <div class="sub">${esc(T("so_far"))} ${Math.round(Number(s.remaining_g || 0))} g${pct} · ${esc(this._where(s))}</div>
        <div class="lbl">${esc(T("scale_weight"))}</div>
        <input class="big" type="number" inputmode="numeric" data-k="weight" value="${esc(st.weight)}">
        <div class="row" style="margin-top:8px">
          <button class="chip ${st.withSpool ? "on" : ""}" data-a="mode" data-v="1">${esc(T("with_spool"))}</button>
          <button class="chip ${!st.withSpool ? "on" : ""}" data-a="mode" data-v="0">${esc(T("filament_only"))}</button>
        </div>
        ${st.withSpool ? `<div class="lbl">${esc(T("empty_spool"))}</div>${this._emptyChips(true)}` : ""}
        <div class="prev">${this._previewHtml()}</div>
        <div class="acts">
          <button class="btn" data-a="cancel" ${st.busy ? "disabled" : ""}>${skip}</button>
          <button class="btn pri" data-a="save" ${st.busy ? "disabled" : ""}>${esc(st.busy ? T("saving") : T("save"))}</button>
        </div>`;
    }

    _rEmptyPrompt() {
      const st = this._st, s = this._spool(st.sid) || {};
      return `
        <div class="h"><span class="sw" style="background:${hex(s.color)}"></span>${esc(spoolName(s))}</div>
        <div class="prev">${esc(T("empty_archive_q"))}</div>
        <div class="sub" style="margin-top:8px">${esc(T("archive_hint"))}</div>
        <div class="acts">
          <button class="btn" data-a="arch-no" ${st.busy ? "disabled" : ""}>${esc(T("keep"))}</button>
          <button class="btn pri" data-a="arch-yes" ${st.busy ? "disabled" : ""}>${esc(T("to_archive"))}</button>
        </div>`;
    }

    _rNew() {
      const st = this._st;
      const vendors = this._vendors();
      const venChips = vendors.map((v) => `<button class="chip ${norm(st.vendor) === norm(v) && !st.newVendor ? "on" : ""}" data-a="ven" data-v="${esc(v)}">${esc(v)}</button>`).join("");
      const venNew = st.newVendor
        ? `<div class="inl"><input type="text" data-k="vendor" placeholder="${esc(T("ph_new_brand"))}" value="${esc(st.vendor)}"><button class="btn" data-a="ven-ok">OK</button></div>`
        : "";
      const labs = this._labels().map((l) => `<button class="chip ${norm(st.label) === norm(l) ? "on" : ""}" data-a="lab" data-v="${esc(l)}">${esc(l)}</button>`).join("");
      const cols = QUICK_COLORS.map((q, i) => `<button class="chip ${st.colorName === T("colors_" + q[0]) ? "on" : ""}" data-a="col" data-v="${i}"><span class="sw" style="width:12px;height:12px;vertical-align:-1px;background:${q[1]}"></span> ${esc(T("colors_" + q[0]))}</button>`).join("");
      const profs = this._profiles();
      const fidIn = st.fid && !profs.some((p) => p[0] === st.fid) ? [[st.fid, st.fid]] : [];
      const opts = [...fidIn, ...profs, ["", T("no_profile")]]
        .map((p) => `<option value="${esc(p[0])}" ${p[0] === st.fid ? "selected" : ""}>${esc(p[1])}</option>`).join("");
      const weigh = st.weighMode === "wiegen";
      const net = (() => {
        const w = Number(st.gross);
        if (st.gross === "" || isNaN(w)) return null;
        if (!st.withSpool) return Math.round(w);
        const e = this._empty(st.esid);
        return e ? Math.max(0, Math.round(w - e.weight_g)) : null;
      })();
      const units = new Map();
      this._trays.forEach((t) => {
        const k = slotText(t.label).replace(/ · (Fach|Slot) \d+$/, "");
        if (!units.has(k)) units.set(k, []);
        units.get(k).push(t);
      });
      const trayBtns = [...units.entries()].map(([name, list]) =>
        `<div class="row" style="justify-content:flex-end"><span class="muted">${esc(name)}:</span>${list.map((t) =>
          `<button class="btn ${st.tray === t.entity_id ? "pri" : ""}" data-a="create-tray" data-v="${esc(t.entity_id)}" ${st.busy || t.busy ? "disabled" : ""}>${t.slot}</button>`).join("")}</div>`).join("");
      return `
        <div class="h">${esc(T("new_spool"))}</div>
        <div class="lbl">${esc(T("brand"))}</div>
        <div class="row">${venChips}<button class="chip dash" data-a="ven-new">${esc(T("new_brand"))}</button></div>${venNew}
        <div class="lbl">${esc(T("material"))}</div>
        <div class="row">${labs}</div>
        <div class="inl"><input type="text" data-k="name" placeholder="${esc(T("ph_name"))}" value="${esc(st.name)}"></div>
        <div class="lbl">${esc(T("color"))}</div>
        <div class="row">${cols}</div>
        <div class="inl"><input type="text" data-k="colorName" placeholder="${esc(T("ph_colname"))}" value="${esc(st.colorName)}"><input type="color" data-k="color" value="${esc(hex(st.color))}"></div>
        <div class="lbl">${esc(T("printer_profile"))}</div>
        ${st.catalog ? `<select data-k="fid">${opts}</select>` : `<div class="muted">${esc(T("catalog_loading"))}</div>`}
        <div class="lbl">${esc(T("weight"))}</div>
        <div class="row">
          <button class="chip ${!weigh ? "on" : ""}" data-a="wm" data-v="voll">${esc(T("full"))}</button>
          <button class="chip ${weigh ? "on" : ""}" data-a="wm" data-v="wiegen">${esc(T("weigh_now"))}</button>
        </div>
        ${weigh ? `
          <div class="inl"><input class="big" type="number" inputmode="numeric" data-k="gross" placeholder="${esc(T("ph_scale"))}" value="${esc(st.gross)}"></div>
          <div class="row" style="margin-top:6px">
            <button class="chip ${st.withSpool ? "on" : ""}" data-a="mode" data-v="1">${esc(T("with_spool"))}</button>
            <button class="chip ${!st.withSpool ? "on" : ""}" data-a="mode" data-v="0">${esc(T("filament_only"))}</button>
          </div>` : ""}
        <div class="inl"><span class="muted">${esc(T("filament_full"))}</span><input type="number" inputmode="numeric" data-k="total" value="${esc(st.total)}" style="max-width:110px">
          ${!weigh ? `<span class="muted">${esc(T("count"))}</span><input type="number" inputmode="numeric" data-k="count" value="${esc(st.count)}" style="max-width:70px">` : ""}</div>
        <div class="lbl">${esc(T("empty_spool"))}${weigh && st.withSpool ? "" : esc(T("remember"))}</div>
        ${this._emptyChips(false)}
        ${weigh ? `<div class="prev">${net == null ? (st.withSpool && !st.esid ? esc(T("prev_pick_empty")) : esc(T("prev_enter_weight"))) : `${esc(T("filament"))}: <b>${net} g</b>, ${Math.max(0, Math.min(100, Math.round((net / (Number(st.total) || 1000)) * 100)))} %`}</div>` : ""}
        <div class="acts"><button class="btn" data-a="cancel" ${st.busy ? "disabled" : ""}>${esc(T("cancel"))}</button>
          <button class="btn pri" data-a="create" ${st.busy ? "disabled" : ""}>${esc(st.busy ? T("creating") : T("to_storage"))}</button></div>
        ${trayBtns ? `<div class="lbl" style="text-align:right">${esc(T("insert_slot"))}</div>${trayBtns}` : ""}`;
    }

    _rLager() {
      const lib = this._lib;
      const spools = this._spools.filter((s) => !s.archived);
      const empty = spools.filter((s) => Number(s.remaining_g || 0) <= 0);
      const groups = new Map();
      spools.filter((s) => Number(s.remaining_g || 0) > 0).forEach((s) => {
        const k = s.sort_key || `${s.material}|${s.color}`;
        if (!groups.has(k)) groups.set(k, []);
        groups.get(k).push(s);
      });
      const sorted = [...groups.entries()].sort((a, b) => a[0].localeCompare(b[0]));
      const emptyHtml = empty.length ? `<div class="banner red">${esc(T("empty_banner"))}${empty.map((s) =>
        `<div class="row" style="justify-content:space-between;margin-top:4px"><span>${esc(spoolName(s))} ${esc(s.material || "")} · ${esc(this._where(s))}</span>
         <button class="chip" data-a="archive" data-v="${s.spool_id}">${esc(T("to_archive"))}</button></div>`).join("")}</div>` : "";
      const sel = this._st.sel;
      const item = (s) => `<div class="item ${sel && sel.has(s.spool_id) ? "on" : ""}" data-a="detail" data-v="${s.spool_id}">
        ${sel ? `<span class="box">${sel.has(s.spool_id) ? "✓" : ""}</span>` : ""}
        <span class="sw" style="background:${hex(s.color)}"></span>
        <div class="t"><div>${esc(spoolName(s))}</div><div>${esc(s.material || "")}${s.color_name ? " · " + esc(s.color_name) : ""} · ${esc(this._where(s))}</div></div>
        <div class="g">${Math.round(Number(s.remaining_g || 0))} g</div></div>`;
      // One row per sort (material + colour), tap to open its spools.
      const open = this._st.open || (this._st.open = new Set());
      const groupHtml = sorted.map(([k, list]) => {
        const s0 = list[0], sum = list.reduce((a, s) => a + Number(s.remaining_g || 0), 0);
        const lowMark = list.some((s) => s.low_stock) ? ` <span style="color:#ff9800">· ${esc(T("last"))}</span>` : "";
        const vendors = [...new Set(list.map((s) => s.vendor).filter(Boolean))].join(", ");
        const isOpen = open.has(k) || !!sel;
        return `<div class="grp" data-a="grp" data-v="${esc(k)}"><span class="sw" style="background:${hex(s0.color)}"></span>
          <div class="t"><b>${esc(s0.material || "?")} ${esc(s0.color_name || "")}</b><div class="muted">${esc(vendors)}</div></div>
          <div class="n">${esc(T(list.length === 1 ? "spool_1" : "spool_n", { n: list.length }))} · ${Math.round(sum)} g${lowMark} ${isOpen ? "▴" : "▾"}</div></div>
          ${isOpen ? `<div class="list" style="margin:6px 0 0 14px">${list.sort((a, b) => Number(b.remaining_g) - Number(a.remaining_g)).map(item).join("")}</div>` : ""}`;
      }).join("");
      const arch = lib.archived_spools || [];
      const archHtml = arch.length ? `<details><summary>${esc(T("archive"))} (${arch.length})</summary><div class="list" style="margin-top:6px">${arch.map((s) =>
        `<div class="item" data-a="detail" data-v="${s.spool_id}"><span class="sw" style="background:${hex(s.color)}"></span>
         <div class="t"><div>${esc([s.vendor, s.name].filter(Boolean).join(" "))}</div><div>${esc(s.material || "")}${s.archived_at ? esc(T("archived_on")) + esc(fmtDate(s.archived_at)) : ""}</div></div></div>`).join("")}</div></details>` : "";
      if (sel) {
        return `
          <div class="h" style="justify-content:space-between"><span>${esc(T("labels_selected", { n: sel.size }))}</span>
            <span class="row"><button class="btn" data-a="sel-all">${esc(T("all"))}</button><button class="btn" data-a="sel-cancel">${esc(T("cancel"))}</button>
            <button class="btn pri" data-a="sel-print" ${this._st.busy || !sel.size ? "disabled" : ""}>${esc(T("print"))}</button></span></div>
          <div class="sub">${esc(T("select_hint"))}</div>
          ${groupHtml || `<div class="muted">${esc(T("no_spools"))}</div>`}`;
      }
      return `
        <div class="h" style="justify-content:space-between"><span>${esc(T("storage_title"))}</span>
          <span class="row"><button class="btn" data-a="sel-start">${esc(T("print_labels"))}</button><button class="btn pri" data-a="new">${esc(T("new_spool_btn"))}</button></span></div>
        ${this._rSlotsOverview()}
        ${emptyHtml}${groupHtml || `<div class="muted">${esc(T("no_spools"))}</div>`}${archHtml}`;
    }

    // Compact list of AMS slots on top of the stock view (tap = that spool's detail).
    _rSlotsOverview() {
      const trays = this._trays;
      if (!trays.length || this._config.slots === false || this._config.hide_slots) return "";
      return `<div class="lbl">${esc(T("slots"))}</div><div class="slots" style="margin-bottom:8px">${trays.map((t) => {
        const s = this._inTray(t);
        const sub = s ? `${spoolName(s)} · ${Math.round(Number(s.remaining_g || 0))} g` : t.empty ? T("empty") : T("no_spool_sfx", { m: t.material || "?" });
        const col = s ? hex(s.color) : t.empty ? "transparent" : hex(t.color);
        return `<button class="slot" ${s ? `data-a="detail" data-v="${s.spool_id}"` : "disabled"}><span class="sw" style="background:${col}"></span>
          <div class="t"><div>${esc(slotText(t.label))}</div><div>${esc(sub)}</div></div></button>`;
      }).join("")}</div>`;
    }

    // Detail: "Kommt in …?" (slot just loaded without a spool) and one button per slot.
    _rSlotButtons(s) {
      const st = this._st, trays = this._trays;
      if (!trays.length) return "";
      const here = this._spoolTray(s);
      const pend = ((TRAYS.data && TRAYS.data.pending) || []).map((id) => this._tray(id)).filter((t) => t && (!here || t.entity_id !== here.entity_id));
      const ask = pend.length ? `<div class="ask"><span>${T("goes_in", { label: `<b>${esc(slotText(pend[0].label))}</b>` })}</span>
        <button class="btn pri" data-a="load" data-v="${esc(pend[0].entity_id)}" ${st.busy || pend[0].busy ? "disabled" : ""}>${esc(T("yes_label", { label: slotText(pend[0].label) }))}</button></div>` : "";
      const btns = trays.map((t) => {
        const inside = this._inTray(t);
        const isHere = here && here.entity_id === t.entity_id;
        const sub = isHere ? T("here") : inside ? T("inside", { name: spoolName(inside) }) : t.empty ? T("empty") : T("no_spool_m", { m: t.material || "?" });
        return `<button class="slot ${isHere ? "here" : ""}" data-a="load" data-v="${esc(t.entity_id)}" ${st.busy || isHere || t.busy ? "disabled" : ""}>
          <span class="sw" style="background:${inside ? hex(inside.color) : t.empty ? "transparent" : hex(t.color)}"></span>
          <div class="t"><div>${esc(slotText(t.label))}</div><div>${esc(t.busy ? T("printer_busy") : sub)}</div></div></button>`;
      }).join("");
      return `${ask}<div class="lbl">${esc(T("put_in_slot"))}</div><div class="slots">${btns}</div>
        ${here ? `<div class="row" style="margin-top:6px"><button class="chip" data-a="unload" data-v="${esc(here.entity_id)}" ${st.busy || here.busy ? "disabled" : ""}>${esc(T("take_out", { label: slotText(here.label) }))}</button></div>` : ""}`;
    }

    // Fach window: one slot, its spool, and a chooser for another spool.
    _rFach() {
      const st = this._st, t = this._tray(st.tray);
      if (!t) {
        return TRAYS.data ? `<div class="err">${esc(T("slot_nf", { id: st.tray }))}</div>` : `<div class="muted">${esc(T("slots_loading"))}</div>`;
      }
      const cur = this._inTray(t);
      const busy = st.busy || t.busy;
      const head = `<div class="h"><span class="sw" style="background:${cur ? hex(cur.color) : t.empty ? "transparent" : hex(t.color)}"></span>${esc(slotText(t.label))}</div>`;
      const now = cur
        ? `<div class="sub">${esc(T("now"))} <b>${esc(spoolName(cur))} ${esc(cur.material || "")}</b> · ${Math.round(Number(cur.remaining_g || 0))} g${cur.remaining_pct != null ? ` (${cur.remaining_pct} %)` : ""}${cur.low_stock ? ` · <span style="color:#ff9800">${esc(T("last_of_kind"))}</span>` : ""}</div>`
        : `<div class="sub">${esc(T("now"))} ${t.empty ? esc(T("empty")) : esc(T("no_spool_assigned", { m: t.material || "?" }))}</div>`;
      const leer = cur && !cur.archived && Number(cur.remaining_g || 0) <= 0
        ? `<div class="banner red row" style="justify-content:space-between">${esc(T("empty_archive_q"))}<button class="btn" data-a="fach-arch" data-v="${cur.spool_id}" ${busy ? "disabled" : ""}>${esc(T("to_archive"))}</button></div>` : "";
      const acts = `<div class="row" style="margin-bottom:6px">
        ${cur ? `<button class="btn" data-a="fach-weigh" data-v="${cur.spool_id}" ${st.busy ? "disabled" : ""}>${esc(T("reweigh"))}</button>` : ""}
        ${cur || !t.empty ? `<button class="btn" data-a="fach-clear" ${busy ? "disabled" : ""}>${esc(T("clear_slot"))}</button>` : ""}</div>`;
      const f = st.filt;
      const pool = this._spools.filter((s) => !s.archived && (!cur || s.spool_id !== cur.spool_id));
      const uniq = (k) => [...new Set(pool.map((s) => (k === "mat" ? s.material : s.vendor)).filter(Boolean))].sort();
      const chips = (k) => `<div class="row" style="margin-bottom:6px">${uniq(k).map((v) =>
        `<button class="chip ${f[k] === v ? "on" : ""}" data-a="f" data-v="${k}:${esc(v)}">${esc(v)}</button>`).join("")}</div>`;
      const shown = pool.filter((s) => (!f.mat || s.material === f.mat) && (!f.ven || s.vendor === f.ven))
        .sort((a, b) => (a.material + a.vendor + a.name).localeCompare(b.material + b.vendor + b.name) || b.remaining_g - a.remaining_g);
      const item = (s) => `<div class="item" data-a="${busy ? "" : "pick"}" data-v="${s.spool_id}">
        <span class="sw" style="background:${hex(s.color)}"></span>
        <div class="t"><div>${esc(s.display_name || s.name || T("spool"))}</div><div>${esc(s.vendor || "")} · ${esc(s.material || "")} · ${Math.round(Number(s.remaining_g || 0))} g${s.low_stock ? " · " + esc(T("last")) : ""}</div>
        <div>${esc(this._where(s))}</div></div></div>`;
      const back = st.fromAms ? `<div class="row" style="margin-bottom:8px"><button class="btn" data-a="ams-back">${esc(T("back"))}</button></div>` : "";
      return `${back}${head}${now}${leer}${acts}
        ${t.busy ? `<div class="banner">${esc(T("busy_banner"))}</div>` : ""}
        <div class="lbl">${esc(T("choose_spool"))}</div>${chips("mat")}${chips("ven")}
        <div class="grid2"><div class="item dash" data-a="fach-new">${esc(T("new_spool_btn"))}</div>${shown.map(item).join("")}</div>`;
    }

    // AMS overview: one section per AMS unit, tiles = fill gauges (height = remaining %).
    _rAms() {
      const only = Array.isArray(this._config.ams) ? this._config.ams.map(Number) : null;
      const trays = this._trays.filter((t) => !only || only.includes(Number(t.ams_id)));
      if (!trays.length) return TRAYS.data ? `<div class="muted">${esc(T("ams_none"))}</div>` : `<div class="muted">${esc(T("slots_loading"))}</div>`;
      const groups = [];
      trays.forEach((t) => {
        const key = t.dev_id + "/" + t.ams_id;
        let g = groups.find((x) => x.key === key);
        if (!g) groups.push(g = { key, name: t.ams_name, list: [] });
        g.list.push(t);
      });
      const tile = (t) => {
        const s = this._inTray(t);
        const attrs = ((this._hass.states[t.entity_id]) || {}).attributes || {};
        let pct = null, grams = null;
        if (s && s.remaining_pct != null) pct = Number(s.remaining_pct);
        else if (!s && attrs.remaining != null && !isNaN(Number(attrs.remaining))) pct = Number(attrs.remaining);
        if (s && s.remaining_g != null) grams = Math.round(Number(s.remaining_g));
        const empty = !s && t.empty;
        if (empty) { pct = null; grams = 0; }
        // Running print: subtract what it used so far (estimate, the booking comes after the print).
        const live = s ? Number(t.live_used_g) || 0 : 0;
        if (live > 0 && grams != null) {
          grams = Math.max(0, Math.round(Number(s.remaining_g) - live));
          const total = Number(s.total_g);
          if (total > 0) pct = grams / total * 100;
        }
        pct = pct == null ? null : Math.max(0, Math.min(100, pct));
        const color = s ? hex(s.color) : hex(t.color);
        const fill = !empty && pct ? `<div class="fill" style="height:${pct}%;background:${color}"></div>` : "";
        const name = empty ? T("ams_empty") : s
          ? [s.display_name || s.name, s.material && !String(s.display_name || s.name || "").includes(s.material) ? s.material : "", ].filter(Boolean).join(" ") + (s.vendor ? " · " + s.vendor : "")
          : (t.material || T("ams_unknown"));
        const w = grams != null ? grams + " g" : pct != null ? Math.round(pct) + " %" : "– g";
        return `<div class="amscol"><button class="tile ${t.active ? "act" : ""} ${t.used_in_print ? "used" : ""}" data-a="ams-open" data-v="${esc(t.entity_id)}" title="${esc(slotText(t.label))}">${fill}<span class="nm">${esc(name)}</span></button>
          <div class="amsw">${esc(w)}</div></div>`;
      };
      return groups.map((g) => `<div class="amsh">${esc(g.name)}</div><div class="amsrow">${g.list.map(tile).join("")}</div>`).join("");
    }

    // Label sheet: tags are created first, QR = HA tag URL, printed from a new window.
    _printLabels(ids) {
      const st = this._st;
      // Open the window right in the tap (popup blockers), fill it after the awaits.
      let w = null;
      try { w = window.open("", "_blank"); } catch (e) { w = null; }
      if (w && w.document) w.document.write("<p style='font-family:sans-serif'>" + T("creating_labels") + "</p>");
      return this._run(async () => {
        try {
          await this._labelsInto(w, ids);
        } catch (e) {
          if (w) w.close();
          throw e;
        }
      });
    }

    async _labelsInto(w, ids) {
      const st = this._st;
      {
        let tagUrl = (TRAYS.data && TRAYS.data.tag_url) || "https://www.home-assistant.io/tag/";
        let tags = {};
        try {
          const r = await this._hass.callWS({ type: DOMAIN + "/ensure_tags", spool_ids: ids.map(Number) });
          tags = (r && r.tags) || {};
          tagUrl = (r && r.tag_url) || tagUrl;
        } catch (e) { /* not admin or older backend: QR still works, the tag is created on first scan */ }
        const qrlib = await loadQr();
        const all = [...this._spools, ...(this._lib.archived_spools || [])];
        const labels = ids.map((id) => {
          const s = all.find((x) => x.spool_id === Number(id));
          if (!s) return "";
          const tid = tags[String(id)] || `spoolbook_${Number(id)}`;
          return `<div class="lab"><div class="qr">${qrSvg(qrlib, tagUrl + tid)}</div><div class="tx">
            <div class="b">${esc(s.vendor || "")}</div><div>${esc(s.material || "")}</div>
            <div class="c"><span style="background:${hex(s.color)}"></span>${esc(s.color_name || "")}</div>
            <div class="id">#${esc(s.spool_id)}</div></div></div>`;
        }).join("");
        const html = `<!DOCTYPE html><html lang="${LANG}"><head><meta charset="utf-8"><title>${esc(T("sheet_title"))}</title><style>
          @page { margin: 8mm; } body { margin: 0; font-family: Arial, sans-serif; color: #000; }
          .sheet { display: flex; flex-wrap: wrap; gap: 2mm; }
          .lab { width: 40mm; height: 30mm; box-sizing: border-box; border: 0.2mm dashed #999; display: flex; align-items: center;
            gap: 1mm; padding: 1mm; overflow: hidden; break-inside: avoid; page-break-inside: avoid; }
          .qr { width: 24mm; height: 24mm; flex-shrink: 0; } .qr svg { width: 100%; height: 100%; display: block; }
          .tx { flex: 1; min-width: 0; font-size: 2.5mm; line-height: 1.2; overflow: hidden; }
          .tx div { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
          .b { font-weight: bold; } .id { font-size: 2.2mm; color: #444; margin-top: 0.5mm; }
          .c { display: flex; align-items: center; gap: 0.8mm; white-space: normal !important; }
          .c span { display: inline-block; width: 4mm; height: 4mm; border-radius: 50%; border: 0.2mm solid #000; flex-shrink: 0;
            -webkit-print-color-adjust: exact; print-color-adjust: exact; }
          .hint { font-size: 12px; margin: 0 0 4mm; } @media print { .hint { display: none; } }
          </style></head><body><p class="hint">${esc(T("sheet_hint"))}</p>
          <div class="sheet">${labels}</div><script>setTimeout(function(){window.print();},400);<\/script></body></html>`;
        if (w && !w.closed && w.document) {
          w.document.open(); w.document.write(html); w.document.close();
        } else {
          // Popup blocked (e.g. in the app): print from a hidden frame.
          const fr = document.createElement("iframe");
          fr.style.cssText = "position:fixed;right:0;bottom:0;width:0;height:0;border:0";
          document.body.appendChild(fr);
          fr.contentDocument.open(); fr.contentDocument.write(html.replace(/<script>[\s\S]*?<\/script>/, "")); fr.contentDocument.close();
          setTimeout(() => { fr.contentWindow.focus(); fr.contentWindow.print(); setTimeout(() => fr.remove(), 60000); }, 400);
        }
        st.msg = ids.length === 1 ? T("label_opened") : T("labels_opened", { n: ids.length });
      }
    }

    _rDetail() {
      const st = this._st, id = st.detailId;
      const s = this._spool(id) || (this._lib.archived_spools || []).find((x) => x.spool_id === id);
      if (!s) return `<div class="muted">${esc(T("spool_nf"))}</div><div class="acts"><button class="btn" data-a="back">${esc(T("back"))}</button></div>`;
      const archived = !!(s.archived || s.archived_at);
      const e = this._empty(s.empty_spool);
      const hist = st.hist == null ? `<div class="muted">${esc(T("hist_loading"))}</div>` :
        !st.hist.length ? `<div class="muted">${esc(T("no_entries"))}</div>` :
        `<div class="hist">${st.hist.map((h) => `<span class="m">${esc(fmtDate(h.t))}</span><span>${esc(KINDS.includes(h.typ) ? T("kind_" + h.typ) : h.typ)}</span>
          <span class="m" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(histText(h))}</span>
          <span>${h.g != null ? (h.g > 0 ? "+" : "") + Math.round(h.g) + " g" : ""}${h.rest != null ? " → " + h.rest + " g" : ""}</span>`).join("")}</div>`;
      return `
        <div class="h"><span class="sw" style="background:${hex(s.color)}"></span>${esc(spoolName(s))} ${esc(s.material || "")}</div>
        <div class="sub">${s.remaining_g != null ? Math.round(Number(s.remaining_g)) + " g" + (s.remaining_pct != null ? ` (${s.remaining_pct} %)` : "") + " · " : ""}${esc(s.in_printer != null ? this._where(s) : T("archive"))}${e ? esc(T("empty_spool_sfx")) + esc(this._emptyLabel(e)) : ""}${s.low_stock ? ` · <span style="color:#ff9800">${esc(T("last_of_kind"))}</span>` : ""}</div>
        ${archived ? "" : this._rSlotButtons(s)}
        ${archived ? "" : `<div class="inl"><input type="text" data-k="colname" placeholder="${esc(T("ph_colname"))}${s.color_name ? esc(T("now_paren", { c: s.color_name })) : ""}" value="${esc(st.colname || "")}">
          <button class="btn" data-a="colname" data-v="${id}">${esc(T("name_color"))}</button></div>`}
        <div class="lbl">${esc(T("history"))}</div>${hist}
        <div class="acts">
          <button class="btn" data-a="back">${esc(T("back"))}</button>
          <button class="btn" data-a="label" data-v="${id}" ${st.busy ? "disabled" : ""}>${esc(T("print_label"))}</button>
          ${archived ? `<button class="btn" data-a="unarchive" data-v="${id}">${esc(T("restore"))}</button>` :
            `<button class="btn ${Number(s.remaining_g || 0) <= 0 ? "pri" : "warn"}" data-a="archive" data-v="${id}">${esc(T("to_archive"))}</button>
             <button class="btn pri" data-a="weigh" data-v="${id}">${esc(T("reweigh"))}</button>`}
        </div>`;
    }
  }

  // Sidebar page /spoolbook (panel_custom): the card in mode lager, full width.
  // ?spule=<id> opens that spool (QR label scan -> command_webview).
  class BambuSpulenPanel extends HTMLElement {
    set hass(hass) {
      this._hass = hass;
      LANG = langOf(hass);
      this._build();
      this._title.textContent = T("panel_title");
      this._card.hass = hass;
      if (this._menu) { this._menu.hass = hass; }
      this._checkUrl();
    }
    set narrow(v) { this._narrow = v; if (this._menu) this._menu.narrow = v; }
    set panel(p) { this._panel = p; }
    set route(r) { this._route = r; this._checkUrl(); }

    connectedCallback() {
      this._onLoc = () => this._checkUrl();
      window.addEventListener("location-changed", this._onLoc);
      window.addEventListener("popstate", this._onLoc);
      this._checkUrl();
    }
    disconnectedCallback() {
      window.removeEventListener("location-changed", this._onLoc);
      window.removeEventListener("popstate", this._onLoc);
      this._lastSpule = null;
    }

    _build() {
      if (this._card) return;
      const root = this.attachShadow({ mode: "open" });
      root.innerHTML = `<style>
        :host { display: block; height: 100%; background: var(--primary-background-color); overflow: auto; }
        .bar { display: flex; align-items: center; gap: 8px; height: var(--header-height, 56px); padding: 0 12px;
          background: var(--app-header-background-color, var(--primary-color)); color: var(--app-header-text-color, #fff);
          font-size: 20px; position: sticky; top: 0; z-index: 2; }
        .wrap { max-width: 900px; margin: 0 auto; padding: 12px; box-sizing: border-box; }
      </style><div class="bar"><ha-menu-button></ha-menu-button><span class="ttl"></span></div><div class="wrap"></div>`;
      this._menu = root.querySelector("ha-menu-button");
      this._title = root.querySelector(".ttl");
      this._menu.hass = this._hass;
      this._menu.narrow = this._narrow;
      this._card = document.createElement("spoolbook-card");
      this._card.setConfig({ mode: "lager" });
      root.querySelector(".wrap").appendChild(this._card);
    }

    _checkUrl() {
      if (!this._card || !this._hass) return;
      if (!location.pathname.startsWith("/spoolbook")) return;
      const id = new URLSearchParams(location.search).get("spule");
      if (id && id !== this._lastSpule) {
        this._lastSpule = id;
        this._card.openSpool(Number(id));
      } else if (!id) {
        this._lastSpule = null;
      }
    }
  }

  if (!customElements.get("spoolbook-card")) {
    customElements.define("spoolbook-card", BambuSpulenCard);
    window.customCards = window.customCards || [];
    window.customCards.push({
      type: "spoolbook-card",
      name: I18N[navLang()].card_name,
      description: I18N[navLang()].card_desc,
      preview: true,
      documentationURL: "https://github.com/Infraviored/spoolbook",
    });
  }
  if (!customElements.get("spoolbook-panel")) customElements.define("spoolbook-panel", BambuSpulenPanel);
})();
