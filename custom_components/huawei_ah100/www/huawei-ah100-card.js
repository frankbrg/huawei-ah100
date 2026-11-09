/* Dashboard cards for the Huawei AH100 integration, served by the integration itself. */

const DOMAIN = "huawei_ah100";
const HISTORY_DAYS = 30;
const STATISTICS_TTL_MS = 15 * 60 * 1000;
const REJECTION_VISIBLE_MS = 24 * 60 * 60 * 1000;

const TEXT = {
  en: {
    scale: "Huawei AH100",
    next_weighing: "Next weighing",
    automatic: "Automatic",
    automatic_hint: "Assigned by weight",
    active_by: "{name} · {method} since {time}",
    method_selection: "selected",
    method_confirmation: "confirmed",
    method_proximity: "phone nearby",
    no_measurement: "No weighing yet",
    body_fat: "Body fat",
    impedance: "Impedance",
    trend: "{days} days",
    rejected: "{time}: {weight} discarded – {reason}",
    reason_ambiguous_weight: "weight fits several profiles",
    reason_no_matching_profile: "weight fits no profile",
    reason_weight_mismatch: "weight does not fit the profile",
    weigh_now: "Weigh now",
    step_on: "Step on the scale now",
    cancel: "Cancel",
    detected_proximity: "Detected by phone nearby",
    detected_confirmation: "Confirmed for this weighing",
    sync_synced: "Sent to SparkyFitness",
    sync_pending: "Waiting for SparkyFitness",
    sync_error: "SparkyFitness sync failed",
    missing_scale: "Choose a Huawei AH100 scale in the card settings.",
    missing_profile: "Choose a Huawei AH100 profile in the card settings.",
    card_scale: "Huawei AH100 scale",
    card_scale_description: "All profiles of the scale with weight, trend and profile selection.",
    card_profile: "Huawei AH100 profile",
    card_profile_description: "One profile with weight, trend and a Weigh now button.",
  },
  de: {
    scale: "Huawei AH100",
    next_weighing: "Nächste Wägung",
    automatic: "Automatisch",
    automatic_hint: "Zuordnung nach Gewicht",
    active_by: "{name} · {method} seit {time}",
    method_selection: "ausgewählt",
    method_confirmation: "bestätigt",
    method_proximity: "Handy in der Nähe",
    no_measurement: "Noch keine Wägung",
    body_fat: "Körperfett",
    impedance: "Impedanz",
    trend: "{days} Tage",
    rejected: "{time}: {weight} verworfen – {reason}",
    reason_ambiguous_weight: "Gewicht passt zu mehreren Profilen",
    reason_no_matching_profile: "Gewicht passt zu keinem Profil",
    reason_weight_mismatch: "Gewicht passt nicht zum Profil",
    weigh_now: "Jetzt wiegen",
    step_on: "Bitte jetzt auf die Waage steigen",
    cancel: "Abbrechen",
    detected_proximity: "Per Handy in der Nähe erkannt",
    detected_confirmation: "Für diese Wägung bestätigt",
    sync_synced: "An SparkyFitness gesendet",
    sync_pending: "Wartet auf SparkyFitness",
    sync_error: "SparkyFitness-Synchronisierung fehlgeschlagen",
    missing_scale: "Wähle in den Karteneinstellungen eine Huawei-AH100-Waage.",
    missing_profile: "Wähle in den Karteneinstellungen ein Huawei-AH100-Profil.",
    card_scale: "Huawei-AH100-Waage",
    card_scale_description: "Alle Profile der Waage mit Gewicht, Verlauf und Profilauswahl.",
    card_profile: "Huawei-AH100-Profil",
    card_profile_description: "Ein Profil mit Gewicht, Verlauf und dem Knopf „Jetzt wiegen“.",
  },
  fr: {
    scale: "Huawei AH100",
    next_weighing: "Prochaine pesée",
    automatic: "Automatique",
    automatic_hint: "Attribution selon le poids",
    active_by: "{name} · {method} depuis {time}",
    method_selection: "sélectionné",
    method_confirmation: "confirmé",
    method_proximity: "téléphone proche",
    no_measurement: "Aucune pesée",
    body_fat: "Masse grasse",
    impedance: "Impédance",
    trend: "{days} jours",
    rejected: "{time} : {weight} ignorée – {reason}",
    reason_ambiguous_weight: "le poids correspond à plusieurs profils",
    reason_no_matching_profile: "le poids ne correspond à aucun profil",
    reason_weight_mismatch: "le poids ne correspond pas au profil",
    weigh_now: "Peser maintenant",
    step_on: "Montez sur la balance maintenant",
    cancel: "Annuler",
    detected_proximity: "Détecté par téléphone proche",
    detected_confirmation: "Confirmé pour cette pesée",
    sync_synced: "Envoyé à SparkyFitness",
    sync_pending: "En attente de SparkyFitness",
    sync_error: "Échec de la synchronisation SparkyFitness",
    missing_scale: "Choisissez une balance Huawei AH100 dans les réglages de la carte.",
    missing_profile: "Choisissez un profil Huawei AH100 dans les réglages de la carte.",
    card_scale: "Balance Huawei AH100",
    card_scale_description: "Tous les profils de la balance avec poids, tendance et choix du profil.",
    card_profile: "Profil Huawei AH100",
    card_profile_description: "Un profil avec poids, tendance et un bouton Peser maintenant.",
  },
};

function language(hass) {
  const lang = ((hass && ((hass.locale && hass.locale.language) || hass.language)) || "en").split("-")[0];
  return TEXT[lang] ? lang : "en";
}

function translate(hass, key, vars) {
  let text = TEXT[language(hass)][key] || TEXT.en[key] || key;
  Object.keys(vars || {}).forEach((name) => {
    text = text.split("{" + name + "}").join(String(vars[name]));
  });
  return text;
}

function escapeHtml(value) {
  return String(value === undefined || value === null ? "" : value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function locale(hass) {
  return (hass && hass.locale && hass.locale.language) || (hass && hass.language) || "en";
}

function formatNumber(hass, value, digits) {
  return new Intl.NumberFormat(locale(hass), {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(value);
}

function formatTime(hass, date) {
  return new Intl.DateTimeFormat(locale(hass), { hour: "2-digit", minute: "2-digit" }).format(date);
}

function formatRelative(hass, date) {
  const seconds = Math.round((date.getTime() - Date.now()) / 1000);
  const units = [
    ["day", 86400],
    ["hour", 3600],
    ["minute", 60],
  ];
  const format = new Intl.RelativeTimeFormat(locale(hass), { numeric: "auto" });
  for (let index = 0; index < units.length; index += 1) {
    const unit = units[index];
    if (Math.abs(seconds) >= unit[1]) {
      return format.format(Math.round(seconds / unit[1]), unit[0]);
    }
  }
  return format.format(0, "minute");
}

function numericState(hass, entityId) {
  const state = entityId && hass.states[entityId];
  if (!state) {
    return null;
  }
  const value = parseFloat(state.state);
  return Number.isFinite(value) ? value : null;
}

function deviceName(hass, deviceId) {
  const device = hass.devices && hass.devices[deviceId];
  return device ? device.name_by_user || device.name || "" : "";
}

function ownEntities(hass) {
  return Object.values(hass.entities || {}).filter((entry) => entry.platform === DOMAIN);
}

/* Find a profile's entities by translation key, with a fallback on the state's unit and class. */
function profileEntities(hass, deviceId) {
  const result = { deviceId: deviceId };
  ownEntities(hass)
    .filter((entry) => entry.device_id === deviceId)
    .forEach((entry) => {
      const state = hass.states[entry.entity_id];
      const attributes = (state && state.attributes) || {};
      const key = entry.translation_key;
      if (key === "weight" || (!key && attributes.unit_of_measurement === "kg")) {
        result.weight = entry.entity_id;
      } else if (key === "body_fat" || (!key && attributes.unit_of_measurement === "%")) {
        result.bodyFat = entry.entity_id;
      } else if (key === "impedance" || (!key && attributes.unit_of_measurement === "Ω")) {
        result.impedance = entry.entity_id;
      } else if (key === "last_measurement" || (!key && attributes.device_class === "timestamp")) {
        result.lastMeasurement = entry.entity_id;
      } else if (key === "sparky_sync") {
        result.sync = entry.entity_id;
      }
    });
  return result;
}

function scaleOfProfile(hass, profileDeviceId) {
  const device = hass.devices && hass.devices[profileDeviceId];
  return device ? device.via_device_id : null;
}

function scaleEntities(hass, scaleDeviceId) {
  const result = { deviceId: scaleDeviceId };
  ownEntities(hass)
    .filter((entry) => entry.device_id === scaleDeviceId)
    .forEach((entry) => {
      if (entry.entity_id.indexOf("select.") === 0) {
        result.select = entry.entity_id;
      } else if (entry.translation_key === "assigned_profile") {
        result.assigned = entry.entity_id;
      }
    });
  result.profiles = Object.values(hass.devices || {})
    .filter((device) => device.via_device_id === scaleDeviceId)
    .filter((device) => ownEntities(hass).some((entry) => entry.device_id === device.id))
    .map((device) => Object.assign(profileEntities(hass, device.id), { name: deviceName(hass, device.id) }))
    .sort((first, second) => first.name.localeCompare(second.name));
  return result;
}

function firstDevice(hass, model) {
  const deviceIds = new Set(ownEntities(hass).map((entry) => entry.device_id));
  const device = Object.values(hass.devices || {}).find(
    (candidate) => deviceIds.has(candidate.id) && candidate.model === model,
  );
  if (device) {
    return device.id;
  }
  /* The scale device only has its own entities if it has profiles, so look through via_device too. */
  const viaDevice = Object.values(hass.devices || {}).find(
    (candidate) => deviceIds.has(candidate.id) && candidate.via_device_id,
  );
  return model === "AH100" && viaDevice ? viaDevice.via_device_id : null;
}

/* Daily mean weights of the last days, shared by all cards and refreshed after new weighings. */
const statisticsCache = {};

function weightHistory(hass, entityId, onUpdate) {
  const state = hass.states[entityId];
  const changed = state ? state.last_changed : "";
  const cached = statisticsCache[entityId];
  if (cached && cached.loading) {
    cached.listeners.add(onUpdate);
    return cached.points;
  }
  if (cached && cached.changed === changed && Date.now() - cached.fetchedAt < STATISTICS_TTL_MS) {
    return cached.points;
  }
  const entry = {
    changed: changed,
    fetchedAt: Date.now(),
    points: cached ? cached.points : [],
    loading: true,
    listeners: new Set([onUpdate]),
  };
  statisticsCache[entityId] = entry;
  hass
    .callWS({
      type: "recorder/statistics_during_period",
      start_time: new Date(Date.now() - HISTORY_DAYS * 86400 * 1000).toISOString(),
      statistic_ids: [entityId],
      period: "day",
      types: ["mean"],
    })
    .then((result) => {
      entry.points = (result[entityId] || [])
        .map((row) => row.mean)
        .filter((value) => typeof value === "number");
      entry.loading = false;
      entry.listeners.forEach((listener) => listener());
      entry.listeners.clear();
    })
    .catch(() => {
      entry.loading = false;
      entry.listeners.clear();
    });
  return entry.points;
}

function sparkline(points, width, height) {
  if (points.length < 2) {
    return "";
  }
  const minimum = Math.min.apply(null, points);
  const maximum = Math.max.apply(null, points);
  const range = maximum - minimum || 1;
  const step = width / (points.length - 1);
  const path = points
    .map((value, index) => {
      const x = (index * step).toFixed(1);
      const y = (height - 2 - ((value - minimum) / range) * (height - 4)).toFixed(1);
      return (index === 0 ? "M" : "L") + x + " " + y;
    })
    .join(" ");
  return (
    '<svg class="spark" viewBox="0 0 ' + width + " " + height + '" preserveAspectRatio="none" aria-hidden="true">' +
    '<path d="' + path + '" /></svg>'
  );
}

function trend(hass, current, points) {
  if (current === null || points.length < 2) {
    return "";
  }
  const delta = current - points[0];
  const arrow = delta > 0.05 ? "▲" : delta < -0.05 ? "▼" : "▬";
  return (
    '<span class="trend" title="' + escapeHtml(translate(hass, "trend", { days: HISTORY_DAYS })) + '">' +
    arrow + " " + escapeHtml(formatNumber(hass, Math.abs(delta), 1)) + " kg</span>"
  );
}

function syncIcon(hass, entityId) {
  const state = entityId && hass.states[entityId];
  if (!state || state.state === "disabled") {
    return "";
  }
  const icons = { synced: "mdi:cloud-check-outline", pending: "mdi:cloud-sync-outline", error: "mdi:cloud-alert-outline" };
  const icon = icons[state.state];
  if (!icon) {
    return "";
  }
  return (
    '<ha-icon class="sync ' + escapeHtml(state.state) + '" icon="' + icon + '" title="' +
    escapeHtml(translate(hass, "sync_" + state.state)) + '"></ha-icon>'
  );
}

function lastMeasurement(hass, profile) {
  const state = profile.lastMeasurement && hass.states[profile.lastMeasurement];
  const date = state ? new Date(state.state) : null;
  return date && !isNaN(date.getTime()) ? date : null;
}

function rejectionNotice(hass, assignedEntityId) {
  const state = assignedEntityId && hass.states[assignedEntityId];
  const attributes = (state && state.attributes) || {};
  if (!attributes.last_rejected_at) {
    return "";
  }
  const at = new Date(attributes.last_rejected_at);
  if (isNaN(at.getTime()) || Date.now() - at.getTime() > REJECTION_VISIBLE_MS) {
    return "";
  }
  const text = translate(hass, "rejected", {
    time: formatTime(hass, at),
    weight: formatNumber(hass, attributes.last_rejected_weight_kg, 1) + " kg",
    reason: translate(hass, "reason_" + attributes.last_rejected_reason),
  });
  return '<div class="notice"><ha-icon icon="mdi:alert-outline"></ha-icon><span>' + escapeHtml(text) + "</span></div>";
}

function assignmentOf(hass, assignedEntityId) {
  const state = assignedEntityId && hass.states[assignedEntityId];
  if (!state || !state.attributes.profile_id || !state.attributes.method) {
    return null;
  }
  return {
    name: state.state,
    method: state.attributes.method,
    since: state.attributes.activated_at ? new Date(state.attributes.activated_at) : null,
  };
}

function moreInfo(element, entityId) {
  if (!entityId) {
    return;
  }
  element.dispatchEvent(
    new CustomEvent("hass-more-info", { detail: { entityId: entityId }, bubbles: true, composed: true }),
  );
}

const STYLES = `
  ha-card { padding: 16px; display: flex; flex-direction: column; gap: 12px; }
  .header { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
  .title { font-size: 1.2em; font-weight: 500; }
  .muted { color: var(--secondary-text-color); font-size: 0.9em; }
  label.next { display: flex; align-items: center; gap: 8px; color: var(--secondary-text-color); font-size: 0.9em; }
  select { font: inherit; color: var(--primary-text-color); background: var(--card-background-color, transparent);
    border: 1px solid var(--divider-color); border-radius: 8px; padding: 4px 8px; }
  .status { display: flex; align-items: center; gap: 8px; color: var(--secondary-text-color); }
  .status ha-icon { --mdc-icon-size: 20px; color: var(--primary-color); }
  .rows { display: flex; flex-direction: column; }
  .row { display: grid; grid-template-columns: 1fr auto; gap: 2px 12px; padding: 10px 0; cursor: pointer;
    border-top: 1px solid var(--divider-color); }
  .row.active .name { color: var(--primary-color); }
  .name { font-weight: 500; display: flex; align-items: center; gap: 6px; }
  .value { font-size: 1.3em; font-weight: 500; text-align: right; white-space: nowrap; }
  .details { color: var(--secondary-text-color); font-size: 0.85em; display: flex; flex-wrap: wrap; gap: 4px 10px; }
  .side { display: flex; align-items: center; justify-content: flex-end; gap: 8px; }
  .trend { color: var(--secondary-text-color); font-size: 0.85em; white-space: nowrap; }
  .spark { width: 100%; height: 28px; overflow: visible; }
  .spark path { fill: none; stroke: var(--primary-color); stroke-width: 2; vector-effect: non-scaling-stroke;
    stroke-linejoin: round; stroke-linecap: round; }
  .big .spark { height: 48px; }
  ha-icon.sync { --mdc-icon-size: 18px; color: var(--secondary-text-color); }
  ha-icon.sync.error { color: var(--error-color); }
  .notice { display: flex; gap: 8px; align-items: flex-start; padding: 8px 10px; border-radius: 8px;
    background: rgba(var(--rgb-warning-color, 255, 166, 0), 0.12); color: var(--primary-text-color); font-size: 0.9em; }
  .notice ha-icon { --mdc-icon-size: 20px; color: var(--warning-color); flex: none; }
  .profile-head { display: flex; align-items: center; gap: 12px; }
  .avatar { width: 40px; height: 40px; border-radius: 50%; flex: none; display: flex; align-items: center;
    justify-content: center; background: var(--primary-color); color: var(--text-primary-color, #fff);
    font-weight: 500; background-size: cover; background-position: center; }
  .headline { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; cursor: pointer; }
  .weight { font-size: 2.2em; font-weight: 500; line-height: 1.1; }
  .weight small { font-size: 0.5em; color: var(--secondary-text-color); margin-left: 2px; }
  .actions { display: flex; gap: 8px; flex-wrap: wrap; }
  button { font: inherit; font-weight: 500; border: none; border-radius: 18px; padding: 8px 18px; cursor: pointer;
    display: inline-flex; align-items: center; gap: 6px; }
  button ha-icon { --mdc-icon-size: 20px; }
  button.primary { background: var(--primary-color); color: var(--text-primary-color, #fff); }
  button.secondary { background: transparent; color: var(--primary-color); border: 1px solid var(--divider-color); }
  .waiting { display: flex; align-items: center; gap: 8px; font-weight: 500; color: var(--primary-color); }
  .waiting ha-icon { animation: pulse 1.6s ease-in-out infinite; }
  @keyframes pulse { 50% { opacity: 0.35; } }
  @media (prefers-reduced-motion: reduce) { .waiting ha-icon { animation: none; } }
`;

class HuaweiAH100BaseCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._signature = "";
  }

  setConfig(config) {
    this._config = Object.assign({}, config);
    this._signature = "";
    if (this._hass) {
      this._update();
    }
  }

  set hass(hass) {
    this._hass = hass;
    this._update();
  }

  getCardSize() {
    return 3;
  }

  _update() {
    if (!this._config) {
      return;
    }
    const signature = this._buildSignature();
    if (signature === this._signature) {
      return;
    }
    this._signature = signature;
    this.shadowRoot.innerHTML = "<style>" + STYLES + "</style>" + this._render();
    this._bind();
  }

  _refresh() {
    this._signature = "";
    this._update();
  }

  _stateSignature(entityIds) {
    const hass = this._hass;
    return entityIds
      .filter(Boolean)
      .map((entityId) => {
        const state = hass.states[entityId];
        return state ? entityId + "=" + state.state + JSON.stringify(state.attributes) : entityId;
      })
      .join("|");
  }

  _selectOption(option) {
    const scale = this._scale();
    if (!scale.select) {
      return;
    }
    this._hass.callService("select", "select_option", { entity_id: scale.select, option: option });
  }
}

class HuaweiAH100Card extends HuaweiAH100BaseCard {
  static getStubConfig(hass) {
    return { device_id: firstDevice(hass, "AH100") || "" };
  }

  static getConfigForm() {
    return {
      schema: [
        { name: "device_id", required: true, selector: { device: { filter: { integration: DOMAIN, model: "AH100" } } } },
        { name: "title", selector: { text: {} } },
      ],
    };
  }

  _scale() {
    return scaleEntities(this._hass, this._config.device_id);
  }

  _buildSignature() {
    const scale = this._scale();
    const ids = [scale.select, scale.assigned];
    scale.profiles.forEach((profile) => {
      ids.push(profile.weight, profile.bodyFat, profile.lastMeasurement, profile.sync);
    });
    const points = scale.profiles.map((profile) =>
      profile.weight ? weightHistory(this._hass, profile.weight, () => this._refresh()).join(",") : "",
    );
    return [language(this._hass), this._config.device_id, this._config.title, this._stateSignature(ids), points.join(";"), Math.floor(Date.now() / 60000)].join("#");
  }

  _render() {
    const hass = this._hass;
    if (!this._config.device_id || !hass.devices || !hass.devices[this._config.device_id]) {
      return '<ha-card><div class="muted">' + escapeHtml(translate(hass, "missing_scale")) + "</div></ha-card>";
    }
    const scale = this._scale();
    const title = this._config.title || deviceName(hass, scale.deviceId) || translate(hass, "scale");
    const selectState = scale.select && hass.states[scale.select];
    const options = selectState ? selectState.attributes.options || [] : [];
    const current = selectState ? selectState.state : "automatic";
    const select = selectState
      ? '<label class="next">' + escapeHtml(translate(hass, "next_weighing")) + '<select class="choose">' +
        options
          .map((option) => {
            const label = option === "automatic" ? translate(hass, "automatic") : option;
            return '<option value="' + escapeHtml(option) + '"' + (option === current ? " selected" : "") + ">" + escapeHtml(label) + "</option>";
          })
          .join("") +
        "</select></label>"
      : "";
    const assignment = assignmentOf(hass, scale.assigned);
    const status = assignment
      ? '<div class="status"><ha-icon icon="' + (assignment.method === "proximity" ? "mdi:cellphone-wireless" : "mdi:account-check-outline") + '"></ha-icon><span>' +
        escapeHtml(
          translate(hass, "active_by", {
            name: assignment.name,
            method: translate(hass, "method_" + assignment.method),
            time: assignment.since ? formatTime(hass, assignment.since) : "",
          }),
        ) + "</span></div>"
      : '<div class="status"><ha-icon icon="mdi:scale-bathroom"></ha-icon><span>' + escapeHtml(translate(hass, "automatic_hint")) + "</span></div>";
    const rows = scale.profiles
      .map((profile, index) => {
        const weight = numericState(hass, profile.weight);
        const fat = numericState(hass, profile.bodyFat);
        const last = lastMeasurement(hass, profile);
        const points = profile.weight ? weightHistory(hass, profile.weight, () => this._refresh()) : [];
        const details = [];
        if (fat !== null) {
          details.push(escapeHtml(translate(hass, "body_fat") + " " + formatNumber(hass, fat, 1) + " %"));
        }
        details.push(escapeHtml(last ? formatRelative(hass, last) : translate(hass, "no_measurement")));
        const active = assignment && assignment.name === profile.name;
        return (
          '<div class="row' + (active ? " active" : "") + '" data-index="' + index + '">' +
          '<div class="name">' + escapeHtml(profile.name) + syncIcon(hass, profile.sync) + "</div>" +
          '<div class="value">' + (weight === null ? "–" : escapeHtml(formatNumber(hass, weight, 1)) + " kg") + "</div>" +
          '<div class="details">' + details.join("<span>·</span>") + "</div>" +
          '<div class="side">' + trend(hass, weight, points) + "</div>" +
          '<div style="grid-column: 1 / -1">' + sparkline(points, 300, 28) + "</div>" +
          "</div>"
        );
      })
      .join("");
    return (
      "<ha-card>" +
      '<div class="header"><div class="title">' + escapeHtml(title) + "</div>" + select + "</div>" +
      status + rejectionNotice(hass, scale.assigned) +
      '<div class="rows">' + rows + "</div>" +
      "</ha-card>"
    );
  }

  _bind() {
    const scale = this._scale();
    const choose = this.shadowRoot.querySelector("select.choose");
    if (choose) {
      choose.addEventListener("change", (event) => this._selectOption(event.target.value));
    }
    this.shadowRoot.querySelectorAll(".row").forEach((row) => {
      row.addEventListener("click", () => moreInfo(this, scale.profiles[Number(row.dataset.index)].weight));
    });
  }

  getCardSize() {
    return 2 + this._scale().profiles.length * 2;
  }
}

class HuaweiAH100ProfileCard extends HuaweiAH100BaseCard {
  static getStubConfig(hass) {
    return { device_id: firstDevice(hass, "AH100 profile") || "" };
  }

  static getConfigForm() {
    return {
      schema: [
        { name: "device_id", required: true, selector: { device: { filter: { integration: DOMAIN, model: "AH100 profile" } } } },
        { name: "title", selector: { text: {} } },
      ],
    };
  }

  _profile() {
    return Object.assign(profileEntities(this._hass, this._config.device_id), {
      name: deviceName(this._hass, this._config.device_id),
    });
  }

  _scale() {
    return scaleEntities(this._hass, scaleOfProfile(this._hass, this._config.device_id));
  }

  _person(profile) {
    const state = profile.weight && this._hass.states[profile.weight];
    const personId = state && state.attributes.person_entity_id;
    return personId ? this._hass.states[personId] : null;
  }

  _buildSignature() {
    const profile = this._profile();
    const scale = this._scale();
    const person = this._person(profile);
    const points = profile.weight ? weightHistory(this._hass, profile.weight, () => this._refresh()).join(",") : "";
    return [
      language(this._hass),
      this._config.device_id,
      this._config.title,
      profile.name,
      this._stateSignature([profile.weight, profile.bodyFat, profile.impedance, profile.lastMeasurement, profile.sync, scale.select, scale.assigned]),
      person ? person.attributes.entity_picture : "",
      points,
      Math.floor(Date.now() / 60000),
    ].join("#");
  }

  _render() {
    const hass = this._hass;
    if (!this._config.device_id || !hass.devices || !hass.devices[this._config.device_id]) {
      return '<ha-card><div class="muted">' + escapeHtml(translate(hass, "missing_profile")) + "</div></ha-card>";
    }
    const profile = this._profile();
    const scale = this._scale();
    const name = this._config.title || profile.name;
    const person = this._person(profile);
    const picture = person && person.attributes.entity_picture;
    const avatar = picture
      ? '<div class="avatar" style="background-image:url(\'' + escapeHtml(picture) + '\')"></div>'
      : '<div class="avatar">' + escapeHtml((name || "?").trim().charAt(0).toUpperCase()) + "</div>";
    const weight = numericState(hass, profile.weight);
    const fat = numericState(hass, profile.bodyFat);
    const impedance = numericState(hass, profile.impedance);
    const last = lastMeasurement(hass, profile);
    const points = profile.weight ? weightHistory(hass, profile.weight, () => this._refresh()) : [];
    const details = [];
    if (fat !== null) {
      details.push(escapeHtml(translate(hass, "body_fat") + " " + formatNumber(hass, fat, 1) + " %"));
    }
    if (impedance !== null) {
      details.push(escapeHtml(translate(hass, "impedance") + " " + formatNumber(hass, impedance, 0) + " Ω"));
    }
    const selectState = scale.select && hass.states[scale.select];
    const selected = selectState && selectState.state === profile.name;
    const assignment = assignmentOf(hass, scale.assigned);
    let detected = "";
    if (!selected && assignment && assignment.name === profile.name && assignment.method !== "selection") {
      detected =
        '<div class="status"><ha-icon icon="' + (assignment.method === "proximity" ? "mdi:cellphone-wireless" : "mdi:account-check-outline") + '"></ha-icon><span>' +
        escapeHtml(translate(hass, "detected_" + assignment.method)) + "</span></div>";
    }
    let actions = "";
    if (selectState && selected) {
      actions =
        '<div class="waiting"><ha-icon icon="mdi:scale-bathroom"></ha-icon><span>' + escapeHtml(translate(hass, "step_on")) + "</span></div>" +
        '<div class="actions"><button class="secondary cancel">' + escapeHtml(translate(hass, "cancel")) + "</button></div>";
    } else if (selectState) {
      actions =
        '<div class="actions"><button class="primary weigh"><ha-icon icon="mdi:scale-bathroom"></ha-icon>' +
        escapeHtml(translate(hass, "weigh_now")) + "</button></div>";
    }
    return (
      '<ha-card class="big">' +
      '<div class="profile-head">' + avatar +
      '<div><div class="title">' + escapeHtml(name) + "</div>" +
      '<div class="muted">' + escapeHtml(last ? formatRelative(hass, last) : translate(hass, "no_measurement")) + "</div></div>" +
      '<div style="margin-left:auto">' + syncIcon(hass, profile.sync) + "</div></div>" +
      '<div class="headline">' +
      '<div class="weight">' + (weight === null ? "–" : escapeHtml(formatNumber(hass, weight, 1)) + "<small>kg</small>") + "</div>" +
      trend(hass, weight, points) + "</div>" +
      (details.length ? '<div class="details">' + details.join("<span>·</span>") + "</div>" : "") +
      sparkline(points, 300, 48) +
      detected + actions +
      "</ha-card>"
    );
  }

  _bind() {
    const profile = this._profile();
    const weigh = this.shadowRoot.querySelector("button.weigh");
    if (weigh) {
      weigh.addEventListener("click", () => this._selectOption(profile.name));
    }
    const cancel = this.shadowRoot.querySelector("button.cancel");
    if (cancel) {
      cancel.addEventListener("click", () => this._selectOption("automatic"));
    }
    const headline = this.shadowRoot.querySelector(".headline");
    if (headline) {
      headline.addEventListener("click", () => moreInfo(this, profile.weight));
    }
  }

  getCardSize() {
    return 4;
  }
}

function defineCards() {
  if (!customElements.get("huawei-ah100-card")) {
    customElements.define("huawei-ah100-card", HuaweiAH100Card);
  }
  if (!customElements.get("huawei-ah100-profile-card")) {
    customElements.define("huawei-ah100-profile-card", HuaweiAH100ProfileCard);
  }
}

// The frontend may install a scoped element registry later; elements defined before it are invisible.
customElements.whenDefined("home-assistant").then(defineCards);

window.customCards = window.customCards || [];
const pickerLanguage = (navigator.language || "en").split("-")[0];
const pickerText = TEXT[pickerLanguage] || TEXT.en;
[
  ["huawei-ah100-card", "card_scale", "card_scale_description"],
  ["huawei-ah100-profile-card", "card_profile", "card_profile_description"],
].forEach((card) => {
  if (!window.customCards.some((entry) => entry.type === card[0])) {
    window.customCards.push({
      type: card[0],
      name: pickerText[card[1]],
      description: pickerText[card[2]],
      preview: true,
      documentationURL: "https://github.com/frankbrg/huawei-ah100#dashboard-cards",
    });
  }
});
