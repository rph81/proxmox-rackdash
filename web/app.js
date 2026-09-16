/* rackdash web UI.
 *
 * No framework and no build step: the Pi serves these three files straight
 * from disk and Chromium renders them. The DOM for each page is built once
 * and then only its readouts are refreshed, so polling never interrupts a
 * touch interaction or restarts a dial animation mid-sweep.
 */
'use strict';

const POLL_MS = 2000;        // /api/state
const LIVE_MS = 2000;        // /api/live sparklines
const RRD_MS = 30000;        // /api/rrd, which only advances once a minute
const GUEST_MS = 3000;       // per-guest detail while it is open

/* The dial arc is 80% of a circle: a 288 degree sweep leaves a 72 degree gap
 * at the bottom, which is where the secondary readout sits. */
const DIAL_SWEEP = 288;
const DIAL_START = -144;     // degrees from 12 o'clock, clockwise positive

/* A theme is a whole look, applied across every page at once: the palette
 * itself lives in style.css under [data-theme], and these carry the parts
 * canvas cannot read from CSS — the accent, the dial colour ramps and the
 * chart series palette. Picking one writes all of them into the config, and
 * any of them can then be fine-tuned individually below. */
const THEMES = [
  { id: 'midnight', label: 'Midnight', hint: 'Default slate blue',
    accent: '#4aa3ff', preview: ['#0e1116', '#161b22', '#4aa3ff'],
    usage: [[0, '#3fb950'], [55, '#d29922'], [75, '#db6d28'], [90, '#f85149']],
    temp:  [[30, '#3fb950'], [50, '#d29922'], [65, '#db6d28'], [80, '#f85149']],
    series: ['#4aa3ff', '#3fb950', '#d29922', '#f85149', '#a371f7', '#39c5cf', '#db6d28', '#db61a2'] },

  { id: 'deck', label: 'Flight Deck', hint: 'cv.hadife.com navy & cyan',
    accent: '#22d3ee', preview: ['#0b1020', '#0f1629', '#22d3ee'],
    usage: [[0, '#34d399'], [55, '#22d3ee'], [75, '#8b5cf6'], [90, '#f472b6']],
    temp:  [[30, '#34d399'], [50, '#22d3ee'], [65, '#8b5cf6'], [80, '#f472b6']],
    series: ['#22d3ee', '#8b5cf6', '#f472b6', '#34d399', '#fbbf24', '#60a5fa', '#f87171', '#a78bfa'] },

  { id: 'bambu', label: 'Bambu', hint: 'Black & yellow, like the rack',
    accent: '#ffd400', preview: ['#0a0a0a', '#141414', '#ffd400'],
    usage: [[0, '#a3e635'], [55, '#facc15'], [75, '#fb923c'], [90, '#f97316']],
    temp:  [[30, '#a3e635'], [50, '#facc15'], [65, '#fb923c'], [80, '#f97316']],
    series: ['#ffd400', '#a3e635', '#fb923c', '#f97316', '#fde047', '#84cc16', '#eab308', '#f59e0b'] },

  { id: 'ice', label: 'Ice', hint: 'Deep navy & pale cyan',
    accent: '#7dd3fc', preview: ['#071320', '#0d1f31', '#7dd3fc'],
    usage: [[0, '#5eead4'], [55, '#7dd3fc'], [75, '#fcd34d'], [90, '#fb7185']],
    temp:  [[30, '#5eead4'], [50, '#7dd3fc'], [65, '#fcd34d'], [80, '#fb7185']],
    series: ['#7dd3fc', '#5eead4', '#a5b4fc', '#fcd34d', '#fb7185', '#38bdf8', '#2dd4bf', '#c4b5fd'] },

  { id: 'synth', label: 'Synthwave', hint: 'Purple & magenta',
    accent: '#ff4fd8', preview: ['#120a1f', '#1b1030', '#ff4fd8'],
    usage: [[0, '#4ade80'], [55, '#c084fc'], [75, '#ff4fd8'], [90, '#ff2e88']],
    temp:  [[30, '#4ade80'], [50, '#c084fc'], [65, '#ff4fd8'], [80, '#ff2e88']],
    series: ['#ff4fd8', '#c084fc', '#22d3ee', '#4ade80', '#fbbf24', '#f472b6', '#818cf8', '#2dd4bf'] },

  { id: 'matrix', label: 'Terminal', hint: 'Phosphor green on black',
    accent: '#00e676', preview: ['#050806', '#0b120d', '#00e676'],
    usage: [[0, '#00e676'], [55, '#a5d63f'], [75, '#d4e157'], [90, '#ff5252']],
    temp:  [[30, '#00e676'], [50, '#a5d63f'], [65, '#d4e157'], [80, '#ff5252']],
    series: ['#00e676', '#76ff03', '#d4e157', '#ff5252', '#64ffda', '#b2ff59', '#ffd740', '#ff6e40'] },

  { id: 'nord', label: 'Nord', hint: 'Cool arctic blue-grey',
    accent: '#88c0d0', preview: ['#2e3440', '#3b4252', '#88c0d0'],
    usage: [[0, '#a3be8c'], [55, '#ebcb8b'], [75, '#d08770'], [90, '#bf616a']],
    temp:  [[30, '#a3be8c'], [50, '#ebcb8b'], [65, '#d08770'], [80, '#bf616a']],
    series: ['#88c0d0', '#a3be8c', '#ebcb8b', '#bf616a', '#b48ead', '#81a1c1', '#d08770', '#8fbcbb'] },

  { id: 'carbon', label: 'Carbon', hint: 'Neutral graphite',
    accent: '#78a9ff', preview: ['#161616', '#262626', '#78a9ff'],
    usage: [[0, '#42be65'], [55, '#f1c21b'], [75, '#ff832b'], [90, '#fa4d56']],
    temp:  [[30, '#42be65'], [50, '#f1c21b'], [65, '#ff832b'], [80, '#fa4d56']],
    series: ['#78a9ff', '#42be65', '#f1c21b', '#fa4d56', '#be95ff', '#3ddbd9', '#ff832b', '#ff7eb6'] },

  { id: 'ember', label: 'Ember', hint: 'Warm dark & orange',
    accent: '#ff8a3d', preview: ['#17110d', '#201812', '#ff8a3d'],
    usage: [[0, '#7ac74f'], [55, '#f0a202'], [75, '#ff8a3d'], [90, '#e4572e']],
    temp:  [[30, '#7ac74f'], [50, '#f0a202'], [65, '#ff8a3d'], [80, '#e4572e']],
    series: ['#ff8a3d', '#f0a202', '#7ac74f', '#e4572e', '#ffca7a', '#c98bdb', '#4ecdc4', '#ff6b6b'] },

  { id: 'slate', label: 'Slate', hint: 'Soft neutral dark',
    accent: '#60a5fa', preview: ['#11151a', '#1a2029', '#60a5fa'],
    usage: [[0, '#4ade80'], [55, '#facc15'], [75, '#fb923c'], [90, '#f87171']],
    temp:  [[30, '#4ade80'], [50, '#facc15'], [65, '#fb923c'], [80, '#f87171']],
    series: ['#60a5fa', '#4ade80', '#facc15', '#f87171', '#c084fc', '#2dd4bf', '#fb923c', '#f472b6'] },

  { id: 'daylight', label: 'Daylight', hint: 'Light, high contrast',
    accent: '#0969da', preview: ['#eef1f5', '#ffffff', '#0969da'],
    usage: [[0, '#1a7f37'], [55, '#9a6700'], [75, '#bc4c00'], [90, '#cf222e']],
    temp:  [[30, '#1a7f37'], [50, '#9a6700'], [65, '#bc4c00'], [80, '#cf222e']],
    series: ['#0969da', '#1a7f37', '#9a6700', '#cf222e', '#8250df', '#1b7c83', '#bc4c00', '#bf3989'] },

  { id: 'paper', label: 'Paper', hint: 'Warm light, easy on the eyes',
    accent: '#b45309', preview: ['#f3efe7', '#fffdf8', '#b45309'],
    usage: [[0, '#4d7c0f'], [55, '#b45309'], [75, '#c2410c'], [90, '#b91c1c']],
    temp:  [[30, '#4d7c0f'], [50, '#b45309'], [65, '#c2410c'], [80, '#b91c1c']],
    series: ['#b45309', '#4d7c0f', '#0f766e', '#b91c1c', '#7c3aed', '#0369a1', '#c2410c', '#a21caf'] },
];

/* Backdrops. The pattern itself lives in style.css on a layer behind the
 * content; this list only drives the picker and the labels. A pattern is
 * deliberately independent of the theme, so changing theme keeps it. */
const PATTERNS = [
  { id: 'none',      label: 'Flat' },
  { id: 'dots',      label: 'Dots' },
  { id: 'grid',      label: 'Grid' },
  { id: 'blueprint', label: 'Blueprint' },
  { id: 'plate',     label: 'Plate' },
  { id: 'hatch',     label: 'Hatch' },
  { id: 'crosshair', label: 'Crosshairs' },
  { id: 'hex',       label: 'Hex' },
  { id: 'circuit',   label: 'Circuit' },
  { id: 'topo',      label: 'Topo' },
  { id: 'scan',      label: 'Scanlines' },
  { id: 'carbon',    label: 'Carbon' },
  { id: 'grain',     label: 'Grain' },
  { id: 'glow',      label: 'Corner glow' },
  { id: 'vignette',  label: 'Vignette' },
];

const GLOW_PRESETS = ['#4aa3ff', '#22d3ee', '#3fb950', '#ffd400',
                      '#ff8a3d', '#f85149', '#ff4fd8', '#a371f7'];

const LIGHT_THEMES = ['daylight', 'paper'];
const themeById = (id) => THEMES.find((t) => t.id === id) || THEMES[0];

const ACCENT_PRESETS = [
  ['#4aa3ff', 'Blue'], ['#39c5cf', 'Cyan'], ['#3fb950', 'Green'],
  ['#d29922', 'Amber'], ['#db6d28', 'Orange'], ['#f85149', 'Red'],
  ['#db61a2', 'Pink'], ['#a371f7', 'Purple'],
];
const MAX_FAVORITES = 10;

const PAGES = [
  { id: 'overview',    label: 'Overview',  icon: 'M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z' },
  { id: 'performance', label: 'Usage',     icon: 'M3 12h4l3 8 4-16 3 8h4' },
  { id: 'temps',       label: 'Temps',     icon: 'M14 14.76V5a2 2 0 1 0-4 0v9.76a4 4 0 1 0 4 0z' },
  { id: 'vms',         label: 'VMs',       icon: 'M3 5h18v6H3zM3 13h18v6H3zM7 8h.01M7 16h.01' },
  { id: 'proxmox',     label: 'Node',      icon: 'M12 2 2 7l10 5 10-5zM2 17l10 5 10-5M2 12l10 5 10-5' },
  { id: 'settings',    label: 'Settings',  icon: 'M4 6h16M4 12h16M4 18h16M8 4v4M16 10v4M11 16v4' },
];

const state = {
  snapshot: null,
  config: null,
  live: null,
  rrd: null,
  page: 'overview',
  dials: new Map(),
  charts: new Map(),
  guest: null,            // { type, id, timeframe, payload, prevNet }
  lastTouch: Date.now(),
  dimmed: false,
  rotateAt: 0,
};

/* --------------------------------------------------------------- utilities */

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const isNum = (v) => typeof v === 'number' && isFinite(v);

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else if (value !== null && value !== undefined && value !== false) {
      node.setAttribute(key, value === true ? '' : value);
    }
  }
  for (const child of children) if (child) node.append(child);
  return node;
}

function svgEl(tag, attrs = {}) {
  const node = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  return node;
}

async function api(path, options = {}) {
  const headers = Object.assign({}, options.headers);
  if (options.body) headers['Content-Type'] = 'application/json';
  const response = await fetch(path, Object.assign({}, options, { headers }));
  const text = await response.text();
  let payload = null;
  try { payload = text ? JSON.parse(text) : null; } catch (_) { /* non-JSON */ }
  if (!response.ok) {
    const error = new Error((payload && payload.error) || `HTTP ${response.status}`);
    error.status = response.status;
    throw error;
  }
  return payload;
}

/* Binary units, because that is what Proxmox reports and what df shows. */
function fmtBytes(bytes, digits) {
  if (!isNum(bytes) || bytes <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
  const index = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  const value = bytes / Math.pow(1024, index);
  const places = digits !== undefined ? digits : (value >= 100 || index === 0 ? 0 : 1);
  return `${value.toFixed(places)} ${units[index]}`;
}

function fmtRate(bytesPerSecond) {
  if (!isNum(bytesPerSecond)) return '—';
  return `${fmtBytes(bytesPerSecond, bytesPerSecond >= 1024 * 1024 * 100 ? 0 : 1)}/s`;
}

function fmtUptime(seconds) {
  if (!isNum(seconds) || seconds <= 0) return '—';
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (days) return `${days}d ${hours}h`;
  if (hours) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

function fmtClock(date, is24) {
  return date.toLocaleTimeString([], {
    hour: '2-digit', minute: '2-digit', hour12: !is24,
  });
}

function fmtTimeShort(epochSeconds) {
  if (!isNum(epochSeconds) || epochSeconds <= 0) return '—';
  return new Date(epochSeconds * 1000).toLocaleTimeString([], {
    hour: '2-digit', minute: '2-digit',
    hour12: !(state.config && state.config.ui.clock_24h),
  });
}

let toastTimer = null;
function toast(message, isError = false) {
  const node = document.getElementById('toast');
  node.textContent = message;
  node.classList.toggle('error', isError);
  node.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.remove('show'), 2400);
}

/* ------------------------------------------------------------------ colour */

const hexOk = (value) => /^#[0-9a-f]{6}$/i.test(value || '');
const toRgb = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));

function mixHex(a, b, amount) {
  const left = toRgb(a), right = toRgb(b);
  const out = left.map((v, i) => Math.round(v * (1 - amount) + right[i] * amount));
  return '#' + out.map((v) => v.toString(16).padStart(2, '0')).join('');
}

const rgba = (hex, alpha) => `rgba(${toRgb(hex).join(',')},${alpha})`;

/** Interpolate the configured stops so the arc sweeps through the colours
 *  rather than snapping between them. `at` is a percentage for usage dials
 *  and a temperature for thermal ones. */
function colorAt(stops, at) {
  if (!stops || !stops.length) return 'var(--accent)';
  if (!isNum(at)) return stops[0][1];
  if (at <= stops[0][0]) return stops[0][1];
  const last = stops[stops.length - 1];
  if (at >= last[0]) return last[1];
  for (let i = 0; i < stops.length - 1; i++) {
    const [lowAt, lowColor] = stops[i];
    const [highAt, highColor] = stops[i + 1];
    if (at >= lowAt && at <= highAt) {
      const span = highAt - lowAt;
      return mixHex(lowColor, highColor, span > 0 ? (at - lowAt) / span : 0);
    }
  }
  return last[1];
}

function usageColor(percent) {
  const ui = state.config.ui;
  if (ui.dial_color === 'accent') return ui.accent;
  return colorAt(ui.usage_stops, percent);
}

function tempColor(celsius) {
  const ui = state.config.ui;
  if (ui.dial_color === 'accent') return ui.accent;
  return colorAt(ui.temp_stops, celsius);
}

/* ------------------------------------------------------------------- theme */

function applyTheme() {
  const ui = state.config.ui;
  document.documentElement.dataset.theme = ui.theme;
  document.body.classList.toggle('no-anim', !ui.animate);

  const light = LIGHT_THEMES.includes(ui.theme);
  const accent = hexOk(ui.accent) ? ui.accent : '#4aa3ff';
  const root = document.documentElement.style;
  root.setProperty('--accent', accent);
  // On white a bright accent has too little contrast for text, so darken it
  // there; on the dark theme it is already legible.
  root.setProperty('--accent-strong', light ? mixHex(accent, '#000000', 0.3) : accent);
  root.setProperty('--accent-dim', light ? mixHex(accent, '#ffffff', 0.84)
                                         : mixHex(accent, '#0e1116', 0.7));
  root.setProperty('--accent-soft', rgba(accent, light ? 0.14 : 0.13));
  // One factor drives every size in the header bar, including its height, so
  // the whole strip grows together and stays centred.
  root.setProperty('--hscale', String(clamp(ui.header_scale || 130, 100, 200) / 100));
  applyPattern();
}

/** The backdrop is one fixed layer behind everything, so strength is just its
 *  opacity and the corner glow is a single custom property. */
function applyPattern() {
  const ui = state.config.ui;
  document.body.dataset.pattern = ui.pattern || 'none';
  document.documentElement.style.setProperty(
    '--pattern-strength', String(clamp(ui.pattern_strength, 0, 200) / 100));
  const glow = hexOk(ui.glow_color) ? ui.glow_color
    : (hexOk(ui.accent) ? ui.accent : '#4aa3ff');
  document.documentElement.style.setProperty(
    '--pat-glow', rgba(glow, LIGHT_THEMES.includes(ui.theme) ? 0.16 : 0.2));
}

/* -------------------------------------------------------------------- dial */

function polar(cx, cy, radius, degrees) {
  const radians = degrees * Math.PI / 180;
  return [cx + radius * Math.sin(radians), cy - radius * Math.cos(radians)];
}

function arcPath(cx, cy, radius, fromDeg, toDeg) {
  const [x1, y1] = polar(cx, cy, radius, fromDeg);
  const [x2, y2] = polar(cx, cy, radius, toDeg);
  const large = Math.abs(toDeg - fromDeg) > 180 ? 1 : 0;
  const sweep = toDeg >= fromDeg ? 1 : 0;
  return `M ${x1.toFixed(2)} ${y1.toFixed(2)} A ${radius} ${radius} 0 ${large} ${sweep} ${x2.toFixed(2)} ${y2.toFixed(2)}`;
}

class Dial {
  constructor(label, options = {}) {
    this.options = options;
    this.shown = 0;
    this.target = 0;
    this.raf = null;
    this._offset = null;
    this._stroke = null;

    const path = arcPath(50, 50, 40, DIAL_START, DIAL_START + DIAL_SWEEP);
    const svg = svgEl('svg', { viewBox: '0 0 100 100' });
    svg.append(svgEl('path', {
      class: 'arc-track', d: path, fill: 'none',
      'stroke-width': options.thin ? 7 : 8.5, 'stroke-linecap': 'round',
    }));
    // pathLength normalises the arc to 100 units, so the dash offset is just
    // "100 minus percent" regardless of the radius.
    this.arc = svgEl('path', {
      class: 'arc-value', d: path, fill: 'none',
      'stroke-width': options.thin ? 7 : 8.5, 'stroke-linecap': 'round',
      pathLength: 100, 'stroke-dasharray': 100, 'stroke-dashoffset': 100,
    });
    svg.append(this.arc);

    this.valueText = svgEl('text', { class: 'dial-value', x: 50, y: 53, 'font-size': 20 });
    this.unitText = svgEl('tspan', { class: 'dial-unit', 'font-size': 10 });
    this.valueText.append(document.createTextNode('—'));
    this.valueText.append(this.unitText);
    svg.append(this.valueText);

    // The secondary readout lives outside the SVG: inside it, a string like
    // "75.2 GB / 128 GB" is wider than the gap in the arc and spills over the
    // stroke. In HTML it can simply ellipsis at the tile width.
    this.labelNode = el('div', { class: 'dial-label', text: label });
    this.subNode = el('div', { class: 'dial-sub' });
    this.node = el('div', { class: 'dial' }, svg, this.labelNode, this.subNode);
  }

  setLabel(label) {
    if (this.labelNode.textContent !== label) this.labelNode.textContent = label;
    this.labelNode.title = label;
  }

  /** percent drives the arc; value/unit/sub are what the user reads. */
  update({ percent, value, unit = '', sub = '', color, decimals = 0, blank = false }) {
    const pct = blank ? 0 : clamp(isNum(percent) ? percent : 0, 0, 100);
    const offset = (100 - pct).toFixed(2);
    const stroke = blank ? 'var(--track)' : (color || 'var(--accent)');
    // Writing the same value restarts the CSS transition, so the compositor
    // would animate a nothing-change every poll.
    if (offset !== this._offset) {
      this.arc.setAttribute('stroke-dashoffset', offset);
      this._offset = offset;
    }
    if (stroke !== this._stroke) {
      this.arc.setAttribute('stroke', stroke);
      this._stroke = stroke;
    }
    this.unitText.textContent = blank ? '' : unit;
    if (this.subNode.textContent !== (sub || '')) this.subNode.textContent = sub || '';
    this.subNode.title = sub || '';

    if (blank || !isNum(value)) {
      this.cancel();
      this.shown = 0;
      this.valueText.firstChild.nodeValue = blank ? '—' : '—';
      return;
    }
    this.animateTo(value, decimals);
  }

  animateTo(value, decimals) {
    const animate = !state.config || state.config.ui.animate;
    // Readings jitter by fractions constantly. Tweening those keeps a render
    // loop alive permanently for a change nobody can see, so snap instead.
    const trivial = Math.abs(value - this.shown) < 0.15 && this.raf === null;
    if (!animate || trivial) {
      this.cancel();
      this.shown = value;
      this.valueText.firstChild.nodeValue = value.toFixed(decimals);
      return;
    }
    this.target = value;
    if (this.raf) return;               // an in-flight tween picks up the new target
    const from = this.shown;
    const start = performance.now();
    const duration = 450;
    const step = (now) => {
      const t = clamp((now - start) / duration, 0, 1);
      const eased = 1 - Math.pow(1 - t, 3);
      this.shown = from + (this.target - from) * eased;
      this.valueText.firstChild.nodeValue = this.shown.toFixed(decimals);
      if (t < 1) {
        this.raf = requestAnimationFrame(step);
      } else {
        this.raf = null;
        this.shown = this.target;
        this.valueText.firstChild.nodeValue = this.shown.toFixed(decimals);
      }
    };
    this.raf = requestAnimationFrame(step);
  }

  cancel() {
    if (this.raf) { cancelAnimationFrame(this.raf); this.raf = null; }
  }
}

/** Dials are created lazily per container and reused across polls, so the
 *  arc keeps its position instead of snapping back to zero every refresh. */
function dialFor(containerId, key, label, options) {
  const mapKey = `${containerId}:${key}`;
  let dial = state.dials.get(mapKey);
  if (!dial) {
    dial = new Dial(label, options);
    state.dials.set(mapKey, dial);
  }
  dial.setLabel(label);
  return dial;
}

function layoutDials(container, dials) {
  const existing = Array.from(container.children);
  const wanted = dials.map((d) => d.node);
  const same = existing.length === wanted.length
    && existing.every((node, i) => node === wanted[i]);
  if (!same) container.replaceChildren(...wanted);
  const columns = Math.min(wanted.length || 1, wanted.length > 8 ? Math.ceil(wanted.length / 2) : wanted.length);
  container.style.setProperty('--dial-cols', String(Math.max(columns, 1)));
}

/* ------------------------------------------------------------------ charts */

function cssVar(name, fallback) {
  const value = getComputedStyle(document.body).getPropertyValue(name).trim();
  return value || fallback;
}

/** Multi-series line chart on a canvas. Nulls break the line rather than
 *  being drawn as zero, which matters for RRD buckets that have no data. */
function drawChart(canvas, options) {
  const series = (options.series || []).filter((s) => s && s.values && s.values.length);
  const dpr = window.devicePixelRatio || 1;
  const width = canvas.clientWidth;
  const height = canvas.clientHeight;
  if (!width || !height) return;
  // Assigning width/height reallocates the backing store and is far more
  // expensive than the drawing itself, so only do it when the size changed.
  // clearRect below handles wiping the previous frame.
  const backingW = Math.round(width * dpr);
  const backingH = Math.round(height * dpr);
  if (canvas.width !== backingW || canvas.height !== backingH) {
    canvas.width = backingW;
    canvas.height = backingH;
  }

  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);

  const line = cssVar('--line', '#2a323e');
  const muted = cssVar('--muted', '#8b98a9');
  const pad = { l: options.axis === false ? 4 : 32, r: 6, t: 6, b: options.times ? 15 : 6 };
  const plotW = width - pad.l - pad.r;
  const plotH = height - pad.t - pad.b;
  if (plotW <= 0 || plotH <= 0) return;

  let max = options.max;
  if (max === undefined) {
    max = 0;
    for (const item of series) {
      for (const value of item.values) if (isNum(value) && value > max) max = value;
    }
    max = max > 0 ? max * 1.15 : 1;
  }
  const min = options.min || 0;
  const span = Math.max(max - min, 1e-9);

  ctx.font = '10px ui-monospace, Menlo, monospace';
  ctx.strokeStyle = line;
  ctx.fillStyle = muted;
  ctx.lineWidth = 1;
  const ticks = options.ticks || 4;
  for (let i = 0; i <= ticks; i++) {
    const value = min + (span * i) / ticks;
    const y = pad.t + plotH - ((value - min) / span) * plotH;
    ctx.beginPath();
    ctx.moveTo(pad.l, Math.round(y) + 0.5);
    ctx.lineTo(width - pad.r, Math.round(y) + 0.5);
    ctx.stroke();
    if (options.axis !== false) {
      ctx.textAlign = 'right';
      ctx.fillText(options.format ? options.format(value) : String(Math.round(value)),
                   pad.l - 5, y + 3);
    }
  }

  if (!series.length || series.every((s) => s.values.every((v) => !isNum(v)))) {
    ctx.textAlign = 'center';
    ctx.fillStyle = muted;
    ctx.fillText(options.empty || 'no data yet', width / 2, height / 2);
    return;
  }

  const count = Math.max(...series.map((s) => s.values.length));
  const xAt = (i) => pad.l + (count <= 1 ? plotW / 2 : (i / (count - 1)) * plotW);
  const yAt = (v) => pad.t + plotH - (clamp((v - min) / span, 0, 1)) * plotH;

  if (options.times && options.times.length) {
    ctx.textAlign = 'center';
    ctx.fillStyle = muted;
    for (let i = 0; i <= 3; i++) {
      const index = Math.round((count - 1) * (i / 3));
      const stamp = options.times[index];
      if (!isNum(stamp)) continue;
      ctx.fillText(fmtTimeShort(stamp), clamp(xAt(index), pad.l + 16, width - pad.r - 16),
                   height - 4);
    }
  }

  const fill = options.fill !== undefined ? options.fill
    : (state.config ? state.config.ui.chart_fill : true);

  for (const item of series) {
    const points = [];
    item.values.forEach((value, i) => points.push(isNum(value) ? [xAt(i), yAt(value)] : null));

    if (fill && series.length <= 2 && item.fill !== false) {
      ctx.beginPath();
      let open = false;
      points.forEach((point) => {
        if (!point) { open = false; return; }
        if (!open) { ctx.moveTo(point[0], pad.t + plotH); ctx.lineTo(point[0], point[1]); open = true; }
        else ctx.lineTo(point[0], point[1]);
      });
      const lastPoint = points.filter(Boolean).pop();
      if (lastPoint) {
        ctx.lineTo(lastPoint[0], pad.t + plotH);
        ctx.closePath();
        ctx.fillStyle = rgba(item.color, 0.16);
        ctx.fill();
      }
    }

    ctx.strokeStyle = item.color;
    ctx.lineWidth = 1.8;
    ctx.lineJoin = 'round';
    ctx.setLineDash(item.dashed ? [4, 3] : []);
    ctx.beginPath();
    let started = false;
    points.forEach((point) => {
      if (!point) { started = false; return; }
      if (started) ctx.lineTo(point[0], point[1]);
      else { ctx.moveTo(point[0], point[1]); started = true; }
    });
    ctx.stroke();
    ctx.setLineDash([]);
  }

  if (options.legend !== false && series.length > 1) {
    let x = pad.l + 2;
    ctx.textAlign = 'left';
    ctx.font = '10px system-ui, sans-serif';
    for (const item of series) {
      if (!item.label) continue;
      ctx.fillStyle = item.color;
      ctx.fillRect(x, pad.t - 1, 8, 3);
      ctx.fillStyle = muted;
      ctx.fillText(item.label, x + 11, pad.t + 3);
      x += 11 + ctx.measureText(item.label).width + 12;
    }
  }
}

function seriesPalette() {
  const series = state.config && state.config.ui.series;
  return (series && series.length) ? series : themeById('midnight').series;
}

/** Fixed roles (in/out, cpu/mem) still take their colour from the theme, so a
 *  Bambu or Terminal palette never leaves a stray default blue on a chart. */
function roleColor(index) {
  const palette = seriesPalette();
  return palette[index % palette.length];
}

function chartSeries(values, color, label, extra = {}) {
  return Object.assign({ values: values || [], color, label }, extra);
}

/* -------------------------------------------------------------------- nav */

function buildNav() {
  const nav = document.getElementById('nav');
  nav.replaceChildren(...PAGES.map((page) => {
    const icon = svgEl('svg', { viewBox: '0 0 24 24' });
    icon.append(svgEl('path', { d: page.icon }));
    const button = el('button', {
      type: 'button', onclick: () => showPage(page.id),
    }, icon, el('span', { text: page.label }));
    button.dataset.page = page.id;
    return button;
  }));
}

function showPage(id) {
  state.page = id;
  state.rotateAt = Date.now();
  for (const page of PAGES) {
    const section = document.getElementById(`page-${page.id}`);
    if (section) section.hidden = page.id !== id;
    const button = document.querySelector(`#nav button[data-page="${page.id}"]`);
    if (button) button.setAttribute('aria-current', String(page.id === id));
  }
  if (id !== 'vms') closeGuest();
  render();
  if (id === 'settings') renderSettings();
}

/* --------------------------------------------------------------- overview */

function systemDials() {
  const snapshot = state.snapshot;
  const system = snapshot.system || {};
  const dials = [];

  const cpu = isNum(system.cpu) ? system.cpu : null;
  const cpuDial = dialFor('overview', 'cpu', 'CPU');
  cpuDial.update({
    percent: cpu, value: cpu, unit: '%', decimals: cpu !== null && cpu < 10 ? 1 : 0,
    color: usageColor(cpu), blank: cpu === null,
    sub: system.cpus ? `${system.cpus} threads` : '',
  });
  dials.push(cpuDial);

  const memory = system.memory || {};
  const memDial = dialFor('overview', 'memory', 'MEMORY');
  memDial.update({
    percent: memory.percent, value: memory.percent, unit: '%',
    color: usageColor(memory.percent), blank: !isNum(memory.percent),
    sub: memory.total ? `${fmtBytes(memory.used)} / ${fmtBytes(memory.total)}` : '',
  });
  dials.push(memDial);

  const disk = system.disk || {};
  const diskDial = dialFor('overview', 'disk', 'ROOT DISK');
  diskDial.update({
    percent: disk.percent, value: disk.percent, unit: '%',
    color: usageColor(disk.percent), blank: !isNum(disk.percent),
    sub: disk.total ? `${fmtBytes(disk.used)} / ${fmtBytes(disk.total)}` : '',
  });
  dials.push(diskDial);

  const net = system.net || {};
  const total = (isNum(net.in) ? net.in : 0) + (isNum(net.out) ? net.out : 0);
  const known = isNum(net.in) || isNum(net.out);
  const linkBytes = (net.link_mbit || 1000) * 1e6 / 8;
  const netDial = dialFor('overview', 'net', 'NETWORK');
  netDial.update({
    percent: known ? (total / linkBytes) * 100 : null,
    value: known ? total / (1024 * 1024) : null,
    unit: ' MB/s', decimals: 1,
    color: usageColor(known ? (total / linkBytes) * 100 : 0), blank: !known,
    sub: known ? `↓${fmtRate(net.in)}  ↑${fmtRate(net.out)}` : '',
  });
  dials.push(netDial);

  return dials;
}

function tempDials(containerId) {
  const temps = state.snapshot.temps || {};
  const ui = state.config.ui;
  return (temps.sensors || []).map((sensor) => {
    const dial = dialFor(containerId, sensor.id, shortLabel(sensor.label));
    const value = sensor.value;
    const span = Math.max(ui.temp_max - ui.temp_min, 1);
    dial.update({
      percent: isNum(value) ? ((value - ui.temp_min) / span) * 100 : null,
      value, unit: '°', decimals: 1,
      color: tempColor(value), blank: !isNum(value),
      sub: sensor.fans && sensor.fans.length ? sensor.fans.join(', ') : '',
    });
    return dial;
  });
}

/** Sensor labels come from the fan app as "CPU (Intel) · Package id 0"; the
 *  dial is far too narrow for that, so keep the most specific part. */
function shortLabel(label, limit = 18) {
  if (!label) return '';
  let text = String(label);
  if (text.includes('·')) {
    const parts = text.split('·').map((p) => p.trim());
    text = parts[parts.length - 1] === 'Hottest drive' ? 'Hottest drive' : parts.join(' ');
    if (text.length > limit) text = parts[parts.length - 1];
  }
  return text.length > limit ? text.slice(0, limit - 1) + '…' : text;
}

function renderOverview() {
  const container = document.getElementById('overview-dials');
  layoutDials(container, [...systemDials(), ...tempDials('overview')]);

  const snapshot = state.snapshot;
  const system = snapshot.system || {};
  const guests = snapshot.guests || [];
  const running = guests.filter((g) => g.status === 'running').length;
  const load = system.load || [];
  const storage = snapshot.storage || [];
  const pools = storage.filter((p) => p.total > 0);
  const fullest = pools.slice().sort((a, b) => b.percent - a.percent)[0];
  const temps = snapshot.temps || {};
  const hottest = (temps.sensors || [])
    .filter((s) => isNum(s.value)).sort((a, b) => b.value - a.value)[0];
  const fans = (temps.fans || []).filter((f) => f.connected && isNum(f.rpm));

  const stat = (label, value, sub) => el('div', { class: 'stat' },
    el('div', { class: 'stat-label', text: label }),
    el('div', { class: 'stat-value', text: value }),
    el('div', { class: 'stat-sub', text: sub || '' }));

  document.getElementById('overview-strip').replaceChildren(
    stat('Load average', load.length ? load.map((v) => v.toFixed(2)).join('  ') : '—',
         system.cpus ? `${system.cpus} threads` : ''),
    stat('Guests', `${running} / ${guests.length}`,
         `${guests.filter((g) => g.type === 'VM').length} VM · ${guests.filter((g) => g.type === 'LXC').length} LXC`),
    stat('Fullest pool', fullest ? `${fullest.percent.toFixed(0)}%` : '—',
         fullest ? `${fullest.name} · ${fmtBytes(fullest.total - fullest.used)} free` : 'no pools'),
    stat('Hottest sensor', hottest ? `${hottest.value.toFixed(1)}°` : '—',
         hottest ? shortLabel(hottest.label, 26) : (temps.enabled ? 'no sensors' : 'fan app off')),
    stat('Fans', fans.length ? `${Math.round(fans.reduce((a, f) => a + f.rpm, 0) / fans.length)} rpm` : '—',
         fans.length ? `${fans.length} spinning` : 'not connected'),
  );
}

/* ------------------------------------------------------------ performance */

function renderPerformance() {
  const system = state.snapshot.system || {};
  const live = state.live || {};
  const liveSystem = live.system || {};
  const rrd = state.rrd || {};
  const accent = state.config.ui.accent;

  const cpu = isNum(system.cpu) ? system.cpu : null;
  const cpuDial = dialFor('perf', 'cpu', 'CPU');
  cpuDial.update({ percent: cpu, value: cpu, unit: '%', decimals: 1,
                   color: usageColor(cpu), blank: cpu === null });
  mountDial('perf-cpu-dial', cpuDial);
  document.getElementById('perf-cpu-tag').textContent = system.cpus ? `${system.cpus} threads` : '';

  const load = system.load || [];
  setKv('perf-cpu-kv', [
    ['Model', shortLabel(system.cpu_model || '—', 28)],
    ['Load 1 / 5 / 15', load.length ? load.map((v) => v.toFixed(2)).join(' / ') : '—'],
    ['I/O wait', lastOf(rrd.iowait) !== null ? `${lastOf(rrd.iowait).toFixed(1)}%` : '—'],
  ]);
  drawChart(document.getElementById('perf-cpu-chart'), {
    series: [chartSeries(liveSystem.cpu, accent, 'cpu %')],
    min: 0, max: 100, times: liveSystem.time, legend: false,
    format: (v) => `${Math.round(v)}`,
  });

  const memory = system.memory || {};
  const swap = system.swap || {};
  const memDial = dialFor('perf', 'mem', 'MEMORY');
  memDial.update({ percent: memory.percent, value: memory.percent, unit: '%', decimals: 1,
                   color: usageColor(memory.percent), blank: !isNum(memory.percent) });
  mountDial('perf-mem-dial', memDial);
  document.getElementById('perf-mem-tag').textContent = memory.total ? fmtBytes(memory.total) : '';
  setKv('perf-mem-kv', [
    ['Used', memory.total ? `${fmtBytes(memory.used)} / ${fmtBytes(memory.total)}` : '—'],
    ['Free', memory.total ? fmtBytes(memory.total - memory.used) : '—'],
    ['Swap', swap.total ? `${fmtBytes(swap.used)} / ${fmtBytes(swap.total)}` : 'none'],
  ]);
  drawChart(document.getElementById('perf-mem-chart'), {
    series: [chartSeries(liveSystem.memory, accent, 'mem %')],
    min: 0, max: 100, times: liveSystem.time, legend: false,
    format: (v) => `${Math.round(v)}`,
  });

  const net = system.net || {};
  const total = (isNum(net.in) ? net.in : 0) + (isNum(net.out) ? net.out : 0);
  const known = isNum(net.in) || isNum(net.out);
  const linkBytes = (net.link_mbit || 1000) * 1e6 / 8;
  const netDial = dialFor('perf', 'net', 'NETWORK');
  netDial.update({
    percent: known ? (total / linkBytes) * 100 : null,
    value: known ? (total * 8) / 1e6 : null, unit: ' Mb/s', decimals: 1,
    color: usageColor(known ? (total / linkBytes) * 100 : 0), blank: !known,
  });
  mountDial('perf-net-dial', netDial);

  const peakIn = maxOf(rrd.netin), peakOut = maxOf(rrd.netout);
  setKv('perf-net-kv', [
    ['Receive', fmtRate(net.in)],
    ['Transmit', fmtRate(net.out)],
    ['Peak (1 h)', peakIn !== null || peakOut !== null
      ? `↓${fmtRate(peakIn)} ↑${fmtRate(peakOut)}` : '—'],
  ]);
  drawChart(document.getElementById('perf-net-chart'), {
    series: [
      chartSeries(rrd.netin, roleColor(1), 'in'),
      chartSeries(rrd.netout, roleColor(3), 'out'),
    ],
    times: rrd.time, format: (v) => fmtBytes(v, 0),
    empty: 'waiting for Proxmox history',
  });
}

function mountDial(containerId, dial) {
  const container = document.getElementById(containerId);
  if (container.firstChild !== dial.node) container.replaceChildren(dial.node);
}

function setKv(id, pairs) {
  const node = document.getElementById(id);
  const children = [];
  for (const [key, value] of pairs) {
    children.push(el('dt', { text: key }));
    const dd = el('dd', { text: value });
    dd.title = value;
    children.push(dd);
  }
  node.replaceChildren(...children);
}

const lastOf = (values) => {
  if (!Array.isArray(values)) return null;
  for (let i = values.length - 1; i >= 0; i--) if (isNum(values[i])) return values[i];
  return null;
};
const maxOf = (values) => {
  if (!Array.isArray(values)) return null;
  const numbers = values.filter(isNum);
  return numbers.length ? Math.max(...numbers) : null;
};

/* ------------------------------------------------------------------ temps */

function renderTemps() {
  const temps = state.snapshot.temps || {};
  layoutDials(document.getElementById('temps-dials'), tempDials('temps'));

  const live = (state.live || {}).temps || {};
  const sensors = temps.sensors || [];
  const palette = seriesPalette();
  const series = sensors.map((sensor, index) => chartSeries(
    live[sensor.id], palette[index % palette.length], shortLabel(sensor.label, 16)));

  drawChart(document.getElementById('temps-chart'), {
    series, times: live.time, min: state.config.ui.temp_min,
    max: state.config.ui.temp_max, format: (v) => `${Math.round(v)}°`,
    fill: series.length <= 1 && state.config.ui.chart_fill,
    empty: temps.enabled ? 'waiting for readings' : 'fan app is disabled',
  });
  document.getElementById('temps-range').textContent =
    live.time && live.time.length > 1
      ? `${Math.round((live.time[live.time.length - 1] - live.time[0]) / 60)} min` : 'live';

  const fans = temps.fans || [];
  const container = document.getElementById('temps-fans');
  document.getElementById('temps-fan-tag').textContent =
    temps.connected ? `${fans.filter((f) => f.connected).length} connected` : 'no device';

  if (!fans.length) {
    container.replaceChildren(el('div', { class: 'empty',
      text: temps.enabled ? 'fan controller unreachable' : 'fan app disabled' }));
    return;
  }
  container.replaceChildren(...fans.map((fan) => {
    const duty = isNum(fan.duty) ? fan.duty : 0;
    const bar = el('i');
    bar.style.width = `${clamp(duty, 0, 100)}%`;
    bar.style.background = usageColor(duty);
    const node = el('div', { class: `fan${fan.connected ? '' : ' off'}` },
      el('div', { class: 'fan-name', text: fan.name }),
      el('div', { class: 'fan-value' }),
      el('div', { class: 'fan-bar' }, bar));
    const value = node.querySelector('.fan-value');
    if (fan.connected && isNum(fan.rpm)) {
      value.append(document.createTextNode(String(fan.rpm)),
                   el('small', { text: ' rpm  ' }),
                   document.createTextNode(duty.toFixed(0)),
                   el('small', { text: '%' }));
    } else if (fan.connected) {
      value.append(document.createTextNode(duty.toFixed(0)), el('small', { text: '%' }));
    } else {
      value.append(el('small', { text: 'not connected' }));
    }
    node.title = `${fan.name} · ${fan.reason || ''}`;
    return node;
  }));
}

/* -------------------------------------------------------------------- vms */

let vmFilter = 'all';

function renderVms() {
  if (state.guest) { renderGuestDetail(); return; }
  document.getElementById('vm-list-view').hidden = false;
  document.getElementById('vm-detail-view').hidden = true;

  const guests = state.snapshot.guests || [];
  const visible = guests.filter((g) => vmFilter === 'all'
    || (vmFilter === 'running' ? g.status === 'running' : g.status !== 'running'));

  document.getElementById('vm-count').textContent =
    `${guests.filter((g) => g.status === 'running').length} running of ${guests.length}`;
  document.querySelectorAll('#vm-filter button').forEach((button) => {
    button.setAttribute('aria-pressed', String(button.dataset.filter === vmFilter));
  });

  const grid = document.getElementById('vm-grid');
  if (!visible.length) {
    grid.replaceChildren(el('div', { class: 'empty',
      text: guests.length ? 'nothing matches this filter' : 'no guests reported' }));
    return;
  }

  grid.replaceChildren(...visible.map((guest) => {
    const running = guest.status === 'running';
    const meter = (label, percent, text) => {
      const fill = el('b');
      fill.style.width = `${clamp(percent || 0, 0, 100)}%`;
      fill.style.background = usageColor(percent || 0);
      return el('div', { class: 'meter' },
        el('span', { text: label }), el('i', {}, fill), el('em', { text }));
    };
    const node = el('button', {
      type: 'button', class: `vm${running ? '' : ' stopped'}`,
      onclick: () => openGuest(guest),
    },
      el('div', { class: 'vm-top' },
        el('span', { class: `dot ${running ? 'on' : 'off'}` }),
        el('span', { class: 'vm-name', text: guest.name }),
        el('span', { class: 'vm-id', text: guest.id })),
      el('div', { class: 'vm-meters' },
        meter('CPU', guest.cpu, `${(guest.cpu || 0).toFixed(0)}%`),
        meter('RAM', guest.mem_percent, `${(guest.mem_percent || 0).toFixed(0)}%`)),
      el('div', { class: 'vm-id', text: `${guest.type} · ${running ? fmtUptime(guest.uptime) : guest.status}` }));
    node.title = `${guest.name} (${guest.id})`;
    return node;
  }));
}

function openGuest(guest) {
  const type = guest.type === 'VM' ? 'qemu' : 'lxc';
  state.guest = { type, id: guest.id, name: guest.name, timeframe: 'hour',
                  payload: null, prevNet: null, error: null };
  document.getElementById('vm-list-view').hidden = true;
  document.getElementById('vm-detail-view').hidden = false;
  document.getElementById('vm-detail-name').textContent = guest.name;
  document.getElementById('vm-detail-kind').textContent = `${guest.type} ${guest.id}`;
  document.getElementById('vm-detail-error').hidden = true;
  fetchGuest();
}

function closeGuest() {
  state.guest = null;
  const list = document.getElementById('vm-list-view');
  const detail = document.getElementById('vm-detail-view');
  if (list) list.hidden = false;
  if (detail) detail.hidden = true;
}

async function fetchGuest() {
  const guest = state.guest;
  if (!guest) return;
  try {
    const payload = await api(`/api/guest/${guest.type}/${guest.id}?timeframe=${guest.timeframe}`);
    if (!state.guest || state.guest.id !== guest.id) return;   // navigated away
    // Per-guest counters are cumulative, so the live rate is the difference
    // between consecutive samples.
    const now = Date.now() / 1000;
    const status = payload.status || {};
    const previous = state.guest.prevNet;
    let rate = null;
    // Require a real gap between samples: two reads inside the collector's
    // short cache would otherwise difference to a spurious zero.
    if (previous && now - previous.t >= 1.5
        && status.netin >= previous.in && status.netout >= previous.out) {
      const elapsed = now - previous.t;
      rate = { in: (status.netin - previous.in) / elapsed,
               out: (status.netout - previous.out) / elapsed };
    }
    state.guest.prevNet = { t: now, in: status.netin, out: status.netout };
    state.guest.rate = rate || state.guest.rate || null;
    state.guest.payload = payload;
    state.guest.error = null;
  } catch (error) {
    if (state.guest) state.guest.error = error.message;
  }
  if (state.page === 'vms') renderGuestDetail();
}

function renderGuestDetail() {
  const guest = state.guest;
  if (!guest) return;
  document.getElementById('vm-list-view').hidden = true;
  document.getElementById('vm-detail-view').hidden = false;

  const banner = document.getElementById('vm-detail-error');
  banner.hidden = !guest.error;
  if (guest.error) banner.textContent = guest.error;

  document.querySelectorAll('#vm-timeframe button').forEach((button) => {
    button.setAttribute('aria-pressed', String(button.dataset.timeframe === guest.timeframe));
  });

  const payload = guest.payload;
  if (!payload) return;
  const status = payload.status || {};
  const history = payload.history || {};

  const statusTag = document.getElementById('vm-detail-status');
  statusTag.textContent = status.status === 'running'
    ? `running · ${fmtUptime(status.uptime)}` : (status.status || 'unknown');
  statusTag.className = `tag ${status.status === 'running' ? 'good' : ''}`;
  document.getElementById('vm-detail-name').textContent = status.name || guest.name;

  const running = status.status === 'running';
  const dials = [];

  const cpuDial = dialFor('vm', 'cpu', 'CPU');
  cpuDial.update({
    percent: status.cpu, value: status.cpu, unit: '%', decimals: 1,
    color: usageColor(status.cpu), blank: !running,
    sub: status.cpus ? `${status.cpus} vCPU` : '',
  });
  dials.push(cpuDial);

  const memDial = dialFor('vm', 'mem', 'MEMORY');
  memDial.update({
    percent: status.mem_percent, value: status.mem_percent, unit: '%', decimals: 1,
    color: usageColor(status.mem_percent), blank: !running,
    sub: status.maxmem ? `${fmtBytes(status.mem)} / ${fmtBytes(status.maxmem)}` : '',
  });
  dials.push(memDial);

  const diskDial = dialFor('vm', 'disk', status.disk_known ? 'DISK' : 'DISK (NO AGENT)');
  diskDial.update({
    percent: status.disk_percent, value: status.disk_percent, unit: '%', decimals: 1,
    color: usageColor(status.disk_percent), blank: !running || !status.disk_known,
    sub: status.maxdisk ? (status.disk_known
      ? `${fmtBytes(status.disk)} / ${fmtBytes(status.maxdisk)}`
      : `${fmtBytes(status.maxdisk)} provisioned`) : '',
  });
  dials.push(diskDial);

  const rate = guest.rate;
  const totalRate = rate ? rate.in + rate.out : null;
  const linkBytes = ((state.snapshot.system || {}).net || {}).link_mbit || 1000;
  const netDial = dialFor('vm', 'net', 'NETWORK');
  netDial.update({
    percent: totalRate !== null ? (totalRate / (linkBytes * 1e6 / 8)) * 100 : null,
    value: totalRate !== null ? totalRate / (1024 * 1024) : null,
    unit: ' MB/s', decimals: 2,
    color: usageColor(totalRate !== null ? (totalRate / (linkBytes * 1e6 / 8)) * 100 : 0),
    blank: !running || totalRate === null,
    sub: rate ? `↓${fmtRate(rate.in)} ↑${fmtRate(rate.out)}` : 'measuring…',
  });
  dials.push(netDial);

  layoutDials(document.getElementById('vm-detail-dials'), dials);

  drawChart(document.getElementById('vm-chart-cpu'), {
    series: [
      chartSeries(history.cpu, roleColor(0), 'cpu %'),
      chartSeries(history.memory, roleColor(4), 'mem %', { fill: false }),
    ],
    min: 0, max: 100, times: history.time, format: (v) => `${Math.round(v)}`,
    empty: 'no history for this guest',
  });
  drawChart(document.getElementById('vm-chart-net'), {
    series: [
      chartSeries(history.netin, roleColor(1), 'in'),
      chartSeries(history.netout, roleColor(3), 'out'),
    ],
    times: history.time, format: (v) => fmtBytes(v, 0),
    empty: 'no history for this guest',
  });
  document.getElementById('vm-net-tag').textContent = guest.timeframe;
}

/* ---------------------------------------------------------------- proxmox */

function renderProxmox() {
  const snapshot = state.snapshot;
  const system = snapshot.system || {};
  const pve = snapshot.proxmox || {};
  const load = system.load || [];

  document.getElementById('pve-node-tag').textContent = pve.node || '—';
  setKv('pve-node-kv', [
    ['Proxmox', pve.release ? `VE ${pve.release}` : (system.pveversion || '—')],
    ['Kernel', shortLabel(system.kversion || '—', 30)],
    ['CPU', shortLabel(system.cpu_model || '—', 30)],
    ['Threads', system.cpus ? String(system.cpus) : '—'],
    ['Uptime', fmtUptime(system.uptime)],
    ['Load', load.length ? load.map((v) => v.toFixed(2)).join(' / ') : '—'],
    ['Disks', (snapshot.disks || []).length ? `${snapshot.disks.length} physical` : '—'],
    ['Dashboard', `v${snapshot.version}`],
  ]);

  const pools = (snapshot.storage || []).filter((p) => p.total > 0);
  document.getElementById('pve-storage-tag').textContent = `${pools.length} pools`;
  const storageNode = document.getElementById('pve-storage');
  if (!pools.length) {
    storageNode.replaceChildren(el('div', { class: 'empty', text: 'no storage reported' }));
  } else {
    storageNode.replaceChildren(...pools.slice(0, 7).map((pool) => {
      const fill = el('i');
      fill.style.width = `${clamp(pool.percent, 0, 100)}%`;
      fill.style.background = usageColor(pool.percent);
      return el('div', { class: 'bar-row' },
        el('div', { class: 'bar-name', text: pool.name }),
        el('div', { class: 'bar-num',
                    text: `${fmtBytes(pool.used)} / ${fmtBytes(pool.total)}` }),
        el('div', { class: 'bar' }, fill));
    }));
  }

  const talkers = snapshot.talkers || [];
  const tasks = snapshot.tasks || [];
  const tasksNode = document.getElementById('pve-tasks');
  document.getElementById('pve-tasks-tag').textContent = talkers.length
    ? `top talker ${talkers[0].name}` : `${tasks.length} recent`;

  if (!tasks.length) {
    tasksNode.replaceChildren(el('div', { class: 'empty', text: 'no recent tasks' }));
  } else {
    tasksNode.replaceChildren(...tasks.slice(0, 7).map((task) => el('div', { class: 'task' },
      el('span', { class: `dot ${task.ok ? 'on' : 'off'}` }),
      el('span', { class: 'task-name', text: `${task.type} ${task.id}`.trim() }),
      el('span', { class: 'task-time', text: fmtTimeShort(task.start) }))));
  }
}

/* --------------------------------------------------------------- settings */

async function saveConfig(patch) {
  try {
    const saved = await api('/api/config', { method: 'PUT', body: JSON.stringify(patch) });
    state.config = saved;
    return saved;
  } catch (error) {
    toast(error.message, true);
    throw error;
  }
}

function patchUi(changes) {
  Object.assign(state.config.ui, changes);
  applyTheme();
  render();
  saveConfig({ ui: changes }).catch(() => {});
}

function renderSettings() {
  const ui = state.config.ui;

  renderThemeGallery();
  renderPatternGallery();
  document.querySelectorAll('#set-dialcolor button').forEach((button) => {
    button.setAttribute('aria-pressed', String(button.dataset.dialcolor === ui.dial_color));
  });
  document.querySelectorAll('#set-temp-source button').forEach((button) => {
    button.setAttribute('aria-pressed',
      String(button.dataset.source === state.config.fanctl.source));
  });

  const swatch = (colour, title, onRemove) => {
    const button = el('button', {
      class: 'swatch', type: 'button', title,
      onclick: () => selectAccent(colour),
    });
    button.style.background = colour;
    button.setAttribute('aria-pressed', String(colour === ui.accent));
    if (onRemove) {
      button.append(el('button', {
        class: 'remove', type: 'button', text: '×', title: `Remove ${colour}`,
        onclick: (event) => { event.stopPropagation(); onRemove(colour); },
      }));
    }
    return button;
  };

  document.getElementById('set-accent').replaceChildren(
    ...ACCENT_PRESETS.map(([colour, name]) => swatch(colour, name)));

  const favorites = document.getElementById('set-favorites');
  if (!ui.favorites.length) {
    favorites.replaceChildren(el('span', { class: 'favorites-empty',
      text: 'Pick a colour and press Save.' }));
  } else {
    favorites.replaceChildren(...ui.favorites.map((colour) =>
      swatch(colour, colour, removeFavorite)));
  }
  document.getElementById('set-fav-count').textContent = `${ui.favorites.length}/${MAX_FAVORITES}`;
  document.getElementById('set-accent-picker').value = ui.accent;
  document.getElementById('set-accent-hex').value = ui.accent;
  document.getElementById('set-fav-add').disabled =
    ui.favorites.includes(ui.accent) || ui.favorites.length >= MAX_FAVORITES;

  renderStops('set-usage-stops', 'usage_stops', 0, 100, '%');
  renderStops('set-temp-stops', 'temp_stops', -20, 150, '°C');

  const startPage = document.getElementById('set-start-page');
  if (!startPage.options.length) {
    for (const page of PAGES) startPage.append(el('option', { value: page.id, text: page.label }));
  }
  startPage.value = ui.start_page;
  document.getElementById('set-rotate').value = ui.rotate_seconds;
  document.getElementById('set-dim-after').value = ui.dim_after;
  document.getElementById('set-dim-level').value = ui.dim_level;
  document.getElementById('set-temp-min').value = ui.temp_min;
  document.getElementById('set-temp-max').value = ui.temp_max;
  document.getElementById('set-animate').checked = ui.animate;
  document.getElementById('set-chart-fill').checked = ui.chart_fill;
  document.getElementById('set-clock24').checked = ui.clock_24h;
  document.getElementById('set-show-pi').checked = ui.show_pi;
  document.getElementById('set-header-scale').value = ui.header_scale;
  document.getElementById('set-header-scale-out').textContent = `${ui.header_scale}%`;
  document.getElementById('set-link').value = state.config.proxmox.link_mbit;
  document.getElementById('set-pve-interval').value = state.config.proxmox.interval;

  const snapshot = state.snapshot || {};
  const pve = snapshot.proxmox || {};
  const temps = snapshot.temps || {};
  setKv('set-sources', [
    ['Proxmox', state.config.proxmox.host || 'not set'],
    ['Status', pve.error ? 'error' : (pve.ok ? `ok · ${pve.node}` : 'connecting')],
    ['Token', state.config.proxmox.token_secret === 'set' ? 'configured' : 'missing'],
    ['Fan app', state.config.fanctl.url || 'not set'],
    ['Sensors', temps.error ? 'error' : `${(temps.sensors || []).length} shown`],
  ]);
}

function renderThemeGallery() {
  const ui = state.config.ui;
  const active = themeById(ui.theme);
  document.getElementById('set-theme-tag').textContent = active.label;
  const container = document.getElementById('set-theme');

  container.replaceChildren(...THEMES.map((theme) => {
    const swatch = el('div', { class: 'theme-swatch' });
    theme.preview.forEach((colour) => {
      const layer = el('i');
      layer.style.background = colour;
      swatch.append(layer);
    });
    const card = el('button', {
      type: 'button', class: 'theme-card', title: theme.hint,
      onclick: () => applyThemePreset(theme.id),
    }, swatch, el('div', { class: 'theme-meta' },
      el('div', { class: 'theme-name', text: theme.label }),
      el('div', { class: 'theme-hint', text: theme.hint })));
    card.setAttribute('aria-pressed', String(theme.id === ui.theme));
    return card;
  }));
}

/** Applying a theme replaces the whole look at once — palette, accent, dial
 *  ramps and chart colours — which is what makes it a theme rather than a
 *  background swap. Individual pieces can still be tuned afterwards. */
function applyThemePreset(id) {
  const theme = themeById(id);
  patchUi({
    theme: theme.id,
    accent: theme.accent,
    usage_stops: theme.usage.map((s) => s.slice()),
    temp_stops: theme.temp.map((s) => s.slice()),
    series: theme.series.slice(),
  });
  renderSettings();
  toast(`${theme.label} theme applied`);
}

function renderPatternGallery() {
  const ui = state.config.ui;
  const active = PATTERNS.find((p) => p.id === ui.pattern) || PATTERNS[0];
  document.getElementById('set-pattern-tag').textContent = active.label;

  const container = document.getElementById('set-pattern');
  container.replaceChildren(...PATTERNS.map((pattern) => {
    // Each tile borrows the real pattern by carrying the same data attribute,
    // so a swatch can never drift from what the page actually draws.
    const art = el('div', { class: 'swatch-art' });
    art.dataset.pattern = pattern.id;
    const card = el('button', {
      type: 'button', class: 'pattern-card', title: pattern.label,
      onclick: () => selectPattern(pattern.id),
    }, art, el('div', { class: 'swatch-name', text: pattern.label }));
    card.setAttribute('aria-pressed', String(pattern.id === ui.pattern));
    return card;
  }));

  document.getElementById('set-pattern-strength').value = ui.pattern_strength;
  document.getElementById('set-pattern-strength-out').textContent =
    `${ui.pattern_strength}%`;

  // The glow colour only means anything for the one pattern that uses it.
  const glowRow = document.getElementById('set-glow-row');
  glowRow.hidden = ui.pattern !== 'glow';
  if (!glowRow.hidden) renderGlowRow();
}

function renderGlowRow() {
  const ui = state.config.ui;
  const effective = hexOk(ui.glow_color) ? ui.glow_color : ui.accent;
  document.getElementById('set-glow-presets').replaceChildren(
    ...GLOW_PRESETS.map((colour) => {
      const button = el('button', {
        class: 'swatch', type: 'button', title: colour,
        onclick: () => patchUi({ glow_color: colour }) || renderSettings(),
      });
      button.style.background = colour;
      button.setAttribute('aria-pressed', String(colour === ui.glow_color));
      return button;
    }));
  document.getElementById('set-glow-picker').value = effective;
  document.getElementById('set-glow-hex').value = ui.glow_color || effective;
  document.getElementById('set-glow-accent').disabled = !ui.glow_color;
}

function selectPattern(id) {
  patchUi({ pattern: id });
  renderSettings();
}

function renderStops(containerId, key, lo, hi, unit) {
  const stops = state.config.ui[key];
  const container = document.getElementById(containerId);
  container.replaceChildren(...stops.map((stop, index) => {
    const at = el('input', {
      type: 'number', class: 'stop-at', min: lo, max: hi, step: 1,
      onchange: (event) => {
        const next = state.config.ui[key].map((s) => s.slice());
        next[index][0] = clamp(Number(event.target.value) || 0, lo, hi);
        next.sort((a, b) => a[0] - b[0]);
        patchUi({ [key]: next });
        renderSettings();
      },
    });
    at.value = stop[0];
    const colour = el('input', {
      type: 'color',
      onchange: (event) => {
        const next = state.config.ui[key].map((s) => s.slice());
        next[index][1] = event.target.value.toLowerCase();
        patchUi({ [key]: next });
        renderSettings();
      },
    });
    colour.value = stop[1];
    const preview = el('div', { class: 'stop-preview' });
    preview.style.background = `linear-gradient(90deg, ${stop[1]}, ${
      stops[index + 1] ? stops[index + 1][1] : stop[1]})`;
    const row = el('div', { class: 'stop' }, colour, at, preview);
    at.title = `${stop[0]}${unit}`;
    return row;
  }));
}

function selectAccent(colour) {
  if (!hexOk(colour)) return;
  patchUi({ accent: colour.toLowerCase() });
  renderSettings();
}

function addFavorite() {
  const ui = state.config.ui;
  if (ui.favorites.includes(ui.accent) || ui.favorites.length >= MAX_FAVORITES) return;
  patchUi({ favorites: [ui.accent, ...ui.favorites].slice(0, MAX_FAVORITES) });
  renderSettings();
}

function removeFavorite(colour) {
  patchUi({ favorites: state.config.ui.favorites.filter((c) => c !== colour) });
  renderSettings();
}

function bindSettings() {
  document.getElementById('set-dialcolor').addEventListener('click', (event) => {
    const button = event.target.closest('button');
    if (button && button.dataset.dialcolor) {
      patchUi({ dial_color: button.dataset.dialcolor });
      renderSettings();
    }
  });
  document.getElementById('set-temp-source').addEventListener('click', async (event) => {
    const button = event.target.closest('button');
    if (!button || !button.dataset.source) return;
    state.config.fanctl.source = button.dataset.source;
    renderSettings();
    await saveConfig({ fanctl: { source: button.dataset.source } }).catch(() => {});
    toast('Sensor selection updated');
  });

  const picker = document.getElementById('set-accent-picker');
  const hex = document.getElementById('set-accent-hex');
  picker.addEventListener('input', () => {
    state.config.ui.accent = picker.value.toLowerCase();
    hex.value = state.config.ui.accent;
    applyTheme();
  });
  picker.addEventListener('change', () => selectAccent(picker.value));
  hex.addEventListener('change', () => {
    const value = hex.value.trim().toLowerCase();
    if (hexOk(value)) selectAccent(value);
    else { toast('Enter a colour as #rrggbb', true); hex.value = state.config.ui.accent; }
  });
  document.getElementById('set-fav-add').onclick = addFavorite;

  // Strength previews live while dragging and only saves when released, so a
  // slow drag does not post a config update per frame.
  const strength = document.getElementById('set-pattern-strength');
  strength.addEventListener('input', () => {
    state.config.ui.pattern_strength = Number(strength.value);
    document.getElementById('set-pattern-strength-out').textContent = `${strength.value}%`;
    applyPattern();
  });
  strength.addEventListener('change', () => {
    patchUi({ pattern_strength: Number(strength.value) });
  });

  // Previews live while dragging so the size can be judged on the panel
  // itself, and only saves on release.
  const headerScale = document.getElementById('set-header-scale');
  headerScale.addEventListener('input', () => {
    state.config.ui.header_scale = Number(headerScale.value);
    document.getElementById('set-header-scale-out').textContent = `${headerScale.value}%`;
    applyTheme();
  });
  headerScale.addEventListener('change', () => {
    patchUi({ header_scale: Number(headerScale.value) });
  });

  const glowPicker = document.getElementById('set-glow-picker');
  const glowHex = document.getElementById('set-glow-hex');
  glowPicker.addEventListener('input', () => {
    state.config.ui.glow_color = glowPicker.value.toLowerCase();
    glowHex.value = state.config.ui.glow_color;
    applyPattern();
  });
  glowPicker.addEventListener('change', () => {
    patchUi({ glow_color: glowPicker.value.toLowerCase() });
    renderSettings();
  });
  glowHex.addEventListener('change', () => {
    const value = glowHex.value.trim().toLowerCase();
    if (hexOk(value)) { patchUi({ glow_color: value }); renderSettings(); }
    else { toast('Enter a colour as #rrggbb', true); renderGlowRow(); }
  });
  document.getElementById('set-glow-accent').onclick = () => {
    patchUi({ glow_color: '' });          // blank means follow the accent
    renderSettings();
  };

  const number = (id, apply) => {
    document.getElementById(id).addEventListener('change', (event) => {
      apply(Number(event.target.value));
    });
  };
  number('set-rotate', (v) => patchUi({ rotate_seconds: v }));
  number('set-dim-after', (v) => patchUi({ dim_after: v }));
  number('set-dim-level', (v) => patchUi({ dim_level: v }));
  number('set-temp-min', (v) => patchUi({ temp_min: v }));
  number('set-temp-max', (v) => patchUi({ temp_max: v }));
  number('set-link', (v) => {
    state.config.proxmox.link_mbit = v;
    saveConfig({ proxmox: { link_mbit: v } }).catch(() => {});
  });
  number('set-pve-interval', (v) => {
    state.config.proxmox.interval = v;
    saveConfig({ proxmox: { interval: v } }).catch(() => {});
  });

  const check = (id, apply) => {
    document.getElementById(id).addEventListener('change', (event) => apply(event.target.checked));
  };
  check('set-animate', (v) => patchUi({ animate: v }));
  check('set-chart-fill', (v) => patchUi({ chart_fill: v }));
  check('set-clock24', (v) => patchUi({ clock_24h: v }));
  check('set-show-pi', (v) => patchUi({ show_pi: v }));

  document.getElementById('set-start-page').addEventListener('change', (event) => {
    patchUi({ start_page: event.target.value });
  });

  document.getElementById('vm-back').onclick = () => { closeGuest(); renderVms(); };
  document.getElementById('vm-filter').addEventListener('click', (event) => {
    const button = event.target.closest('button');
    if (button && button.dataset.filter) { vmFilter = button.dataset.filter; renderVms(); }
  });
  document.getElementById('vm-timeframe').addEventListener('click', (event) => {
    const button = event.target.closest('button');
    if (button && button.dataset.timeframe && state.guest) {
      state.guest.timeframe = button.dataset.timeframe;
      state.guest.payload = null;
      fetchGuest();
    }
  });
}

/* ------------------------------------------------------------ dim + cycle */

function noteActivity() {
  state.lastTouch = Date.now();
  if (state.dimmed) {
    state.dimmed = false;
    const dim = document.getElementById('dim');
    dim.style.opacity = '0';
    setTimeout(() => { if (!state.dimmed) dim.hidden = true; }, 600);
  }
}

function tickIdle() {
  if (!state.config) return;
  const ui = state.config.ui;
  const dim = document.getElementById('dim');
  if (ui.dim_after > 0 && !state.dimmed
      && Date.now() - state.lastTouch > ui.dim_after * 1000) {
    state.dimmed = true;
    dim.hidden = false;
    // A CSS overlay, not a backlight change: this panel is driven over HDMI,
    // so there is no brightness control to reach from the browser.
    requestAnimationFrame(() => { dim.style.opacity = String(1 - ui.dim_level / 100); });
  }
  if (ui.rotate_seconds > 0 && !state.guest
      && Date.now() - state.rotateAt > ui.rotate_seconds * 1000
      && state.page !== 'settings') {
    const order = PAGES.filter((p) => p.id !== 'settings').map((p) => p.id);
    const next = order[(order.indexOf(state.page) + 1) % order.length];
    showPage(next);
  }
}

/* ---------------------------------------------------------------- polling */

function render() {
  if (!state.snapshot || !state.config) return;
  const snapshot = state.snapshot;

  const pve = snapshot.proxmox || {};
  const dot = document.getElementById('conn-dot');
  dot.className = `dot ${pve.ok ? 'on' : 'off'}`;
  document.getElementById('conn-host').textContent =
    pve.node || (state.config.proxmox.host || 'not configured').replace(/^https?:\/\//, '');
  document.getElementById('conn-sub').textContent = pve.release ? `VE ${pve.release}` : '';

  const guests = snapshot.guests || [];
  document.getElementById('chip-guests').textContent =
    guests.length ? `${guests.filter((g) => g.status === 'running').length}/${guests.length} up` : '';
  document.getElementById('chip-uptime').textContent =
    isNum((snapshot.system || {}).uptime) ? fmtUptime(snapshot.system.uptime) : '';

  renderPi(snapshot.pi || {});

  const banner = document.getElementById('banner');
  const problem = snapshot.warning || pve.error
    || (snapshot.temps && snapshot.temps.enabled && snapshot.temps.error);
  banner.hidden = !problem;
  if (problem) banner.textContent = problem;

  switch (state.page) {
    case 'overview': renderOverview(); break;
    case 'performance': renderPerformance(); break;
    case 'temps': renderTemps(); break;
    case 'vms': renderVms(); break;
    case 'proxmox': renderProxmox(); break;
    case 'settings': renderSettings(); break;
  }
}

/** The Pi's own temperature, CPU and GPU. Distinct from the Proxmox numbers,
 *  so it carries a PI badge; anything unreadable shows a dash rather than a
 *  zero, because 0% and "no source for this" are very different things. */
function renderPi(pi) {
  const node = document.getElementById('pi-stats');
  const show = state.config.ui.show_pi
    && (isNum(pi.temp) || isNum(pi.cpu) || isNum(pi.gpu));
  node.hidden = !show;
  if (!show) return;

  const stat = (label, value, unit, colour, title) => {
    const readout = el('div', { class: 'pi-value' });
    if (isNum(value)) {
      const digits = unit === '°' ? 1 : 0;
      readout.append(document.createTextNode(value.toFixed(digits)),
                     el('small', { text: unit === 'MHz' ? ' MHz' : unit }));
      readout.style.color = colour;
    } else {
      readout.append(document.createTextNode('—'));
      readout.style.color = 'var(--muted)';
    }
    const group = el('div', { class: 'pi-stat' },
      el('span', { class: 'pi-label', text: label }), readout);
    group.title = title;
    return group;
  };

  // The GPU figure is a percentage only when something actually measures
  // load. On a stock Pi the only reading available is the V3D clock, so it is
  // labelled and coloured as a clock rather than dressed up as utilisation.
  const gpuIsLoad = (pi.gpu_unit || '%') === '%';
  const gpuTitle = pi.gpu_source
    ? (gpuIsLoad ? `GPU load via ${pi.gpu_source}`
                 : `V3D clock via ${pi.gpu_source}. This kernel exposes no GPU `
                   + `utilisation figure, so the clock is shown instead.`)
    : 'no GPU reading available on this machine';

  node.replaceChildren(
    el('span', { class: 'pi-badge', text: 'PI' }),
    stat('TEMP', pi.temp, '°', tempColor(pi.temp), pi.model || 'this machine'),
    stat('CPU', pi.cpu, '%', usageColor(pi.cpu), 'CPU busy since the last poll'),
    stat(gpuIsLoad ? 'GPU' : 'V3D', pi.gpu, pi.gpu_unit || '%',
         gpuIsLoad ? usageColor(pi.gpu) : 'var(--text)', gpuTitle),
  );
}

async function poll() {
  // Deliberately no document.hidden guard here. It looks like free savings,
  // but a kiosk browser can report hidden in states where the panel is very
  // much lit, and then the screen stays blank forever. A permanently black
  // rack display is a far worse outcome than the CPU this would save.
  try {
    const snapshot = await api('/api/state');
    const first = !state.config;
    state.snapshot = snapshot;
    state.config = snapshot.config;
    if (first) {
      applyTheme();
      showPage(state.config.ui.start_page);
    }
    render();
  } catch (error) {
    const banner = document.getElementById('banner');
    banner.hidden = false;
    banner.textContent = `Cannot reach the dashboard service: ${error.message}`;
  }
}

async function pollLive() {
  if (!state.config) return;
  if (!['overview', 'performance', 'temps'].includes(state.page)) return;
  try {
    state.live = await api('/api/live');
    if (state.page === 'performance' || state.page === 'temps') render();
  } catch (_) { /* the state poll already surfaces connection errors */ }
}

async function pollRrd() {
  if (!state.config) return;
  try {
    state.rrd = await api('/api/rrd');
  } catch (_) { /* charts fall back to "waiting" */ }
}

function tickClock() {
  const is24 = state.config ? state.config.ui.clock_24h : true;
  document.getElementById('clock').textContent = fmtClock(new Date(), is24);
}

/* ------------------------------------------------------------------- main */

function main() {
  if (new URLSearchParams(location.search).get('kiosk') === '1') {
    document.body.classList.add('kiosk');
  }
  buildNav();
  bindSettings();

  for (const event of ['pointerdown', 'touchstart', 'keydown', 'wheel']) {
    window.addEventListener(event, noteActivity, { passive: true });
  }
  // A kiosk has no browser chrome to reach, so suppress the gestures that
  // would otherwise expose it or zoom the layout.
  window.addEventListener('contextmenu', (event) => event.preventDefault());
  window.addEventListener('dblclick', (event) => event.preventDefault());
  window.addEventListener('resize', () => render());

  poll().then(() => { pollRrd(); pollLive(); });
  setInterval(poll, POLL_MS);
  setInterval(pollLive, LIVE_MS);
  setInterval(pollRrd, RRD_MS);
  setInterval(() => { if (state.guest) fetchGuest(); }, GUEST_MS);
  setInterval(tickClock, 1000);
  setInterval(tickIdle, 1000);
  tickClock();
}

main();
