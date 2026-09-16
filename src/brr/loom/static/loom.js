/* The renderer owns pixels and selection. Only the feed owns facts.
 *
 * Layers, back to front, per frame:
 *   ground  — warm near-black, vignette, scanlines (cached; under everything)
 *   scene   — regions, branch lines, places, labels, instruments (cached)
 *   live    — scan along the weft and the trails it lights, the shuttle
 *             walking, sparks, threads, face
 *   glitch  — local slice + chroma split on the element that changed (live)
 *
 * Colour law: amber is the resident's light only; ice is the user's hand and
 * the settled; dark is the between; green only for receipts, red only for
 * walls; heddle hues only on chips, runes and thin halos.
 */
const canvas = document.querySelector("#loom");
const screen = canvas.getContext("2d");
let g = screen;
function layer() {
  const c = document.createElement("canvas");
  return [c, c.getContext("2d")];
}
const [groundCanvas, groundContext] = layer();
const [sceneCanvas, sceneContext] = layer();
const [glitchCanvas, glitchContext] = layer();
const [tintCanvas, tintContext] = layer();
let sceneDirty = true,
  groundDirty = true,
  sceneHits = [];
const bench = document.querySelector("#bench");
const receipt = document.querySelector("#receipt");
const access = document.querySelector("#access");
const legend = document.querySelector("#legend");
const reduced = matchMedia("(prefers-reduced-motion: reduce)");
const params = new URLSearchParams(location.search);
const dev = params.get("src") === "dev";
const replay = dev && params.has("replay");
const BEAT = 600;
const UNWIRED =
  "reaches the shuttle in pass 2 — the local gate is not wired yet";
function unwired() {
  document.querySelector("#console-note").textContent = UNWIRED;
}
const actions = Object.fromEntries(
  [
    "fold",
    "explain",
    "fix",
    "test",
    "split",
    "read",
    "grant",
    "keep",
    "drop",
    "write",
    "lift",
  ].map((name) => [name, () => unwired()]),
);

const INK = {
  ground: "#0b0906",
  vignette: "#15110b",
  bone: "#e8dcc0",
  boneDim: "#b3a78e",
  faint: "#7a6f5c",
  dark: "#3a3328",
  darker: "#241f18",
  amber: "#f2b134",
  ice: "#8fd3ff",
  receipt: "#7fcf8a",
  wall: "#e2574c",
  panel: "rgba(18, 14, 9, 0.78)",
};
// Six muted, mid-saturation hues, placed off amber (40°), ice (200°),
// receipt green (120°) and wall red (5°). Chips, runes and thin halos only.
const HUES = ["#6fb7a6", "#c8849c", "#9d8ad2", "#c38b6e", "#7c93d0", "#a3b46a"];
const FONT = 'ui-monospace, "SF Mono", SFMono-Regular, Menlo, Consolas, monospace';
const LEXICON = {
  awake: "weaving",
  listening: "at the shed",
  parked: "in the box",
  "handing-off": "handing off",
  released: "released",
};
const READINGS = {
  heddles: "the topics · lit = live work · click or 1–6 to lift",
  warp: "open items hanging on their topics",
  window:
    "the files the shuttle touched, grown from the root · brighter = more recent",
  cloth: "every pass · the plaque is the one in focus",
  face: "the resident: state, mood, fuel",
};
// Reading order of the first-load reveal, 300 ms apart; the face last.
const REVEAL = ["heddles", "warp", "window", "cloth", "bench", "face"];
const REVEAL_STEP = 300;
const PLACE_CAP = 30,
  LIFTED_CAP = 60;

const compactNumber = (n) =>
  n == null
    ? "?"
    : Math.abs(n) >= 1e6
      ? (n / 1e6).toFixed(1).replace(/\.0$/, "") + "m"
      : Math.abs(n) >= 1000
        ? (n / 1000).toFixed(0) + "k"
        : String(n);
const askOf = (thread) => thread.ask_tokens ?? thread.ask?.tokens ?? null;
const list = (value) => (Array.isArray(value) ? value : []);
const known = (value) =>
  value === null || value === undefined ? "unknown" : String(value);
const number = (value) =>
  value === null || value === undefined
    ? "unknown"
    : Number(value).toLocaleString("en-US");
const clamp = (value) => Math.max(0, Math.min(1, Number(value) || 0));
const smooth = (value) => {
  const t = clamp(value);
  return t * t * (3 - 2 * t);
};
// Deterministic noise: every flicker derives from the animation clock, so
// Space (which freezes the clock) freezes every pixel.
const rand = (n) => {
  const x = Math.sin(n * 12.9898 + 78.233) * 43758.5453;
  return x - Math.floor(x);
};
const lifted = new Set();
let state = null,
  hits = [],
  hovered = null,
  selected = { kind: "run" },
  sourceStatus = "connecting";
let width = 0,
  height = 0,
  layout,
  clock = 0,
  previous = 0,
  paused = false;
let positions = new Map(),
  targetPositions = new Map(),
  movesAt = 0,
  warpScroll = 0;
const threadTravel = new Map();
let focusRun = null,
  radial = null;
let sparks = [],
  actor = null,
  walk = { route: [], born: -1e9, dur: 1 },
  seen = new Set(),
  currentRun = null;
let treeNodes = [],
  lastReceipt = "",
  lastAccess = "",
  pendingFold = 0,
  hiddenCount = 0,
  shownCount = 0;
let revealAt = null,
  revealed = new Set();
let glitches = [],
  glitchSeed = 1;
let trails = new Map();
let prForge = null,
  forgeDrop = null;
let expanded = null,
  expandTimer = 0;
const runeRects = new Map(),
  threadRects = new Map();
const regionRects = {};

const matches = (topics) =>
  [...lifted].every((topic) => list(topics).includes(topic));
function color(topic) {
  const slugs = list(state?.heddles)
    .map((h) => h.slug)
    .sort();
  const i = slugs.indexOf(topic);
  return i < 0 ? INK.faint : HUES[i % HUES.length];
}
const firstColor = (topics) => color(list(topics)[0]);
const font = (size, weight = 400) => `${weight} ${size}px ${FONT}`;
function fit(value, limit, size, weight) {
  g.font = font(size, weight);
  let s = String(value ?? "unknown");
  if (Number.isFinite(limit) && g.measureText(s).width > limit) {
    while (s.length > 1 && g.measureText(s + "…").width > limit)
      s = s.slice(0, -1);
    s += "…";
  }
  return s;
}
function text(value, x, y, fill = INK.bone, size = 12, limit = Infinity, o = {}) {
  const s = fit(value, limit, size, o.weight);
  g.textAlign = o.align || "left";
  g.fillStyle = fill;
  if (o.glow) {
    g.save();
    g.shadowColor = o.glowColor || fill;
    g.shadowBlur = o.glow;
    g.fillText(s, x, y);
    g.restore();
  }
  g.fillText(s, x, y);
  const w = g.measureText(s).width;
  g.textAlign = "left";
  return w;
}
const glyphSprites = new Map();
// Glowing type that moves every frame is blurred once into a sprite.
function glowSprite(value, size, fill, glow, weight = 600) {
  const dpr = canvas.width / width || 1,
    key = `${value}/${size}/${fill}/${glow}/${weight}/${dpr}`;
  let sprite = glyphSprites.get(key);
  if (!sprite) {
    const [c, x] = layer();
    x.font = font(size, weight);
    const w = Math.ceil(x.measureText(value).width),
      pad = glow * 2 + 4;
    c.width = Math.ceil((w + pad * 2) * dpr);
    c.height = Math.ceil((size * 1.4 + pad * 2) * dpr);
    x.scale(dpr, dpr);
    x.font = font(size, weight);
    x.textBaseline = "middle";
    x.fillStyle = fill;
    x.shadowColor = INK.amber;
    x.shadowBlur = glow * dpr;
    x.fillText(value, pad, pad + size * 0.7);
    x.shadowBlur = 0;
    x.fillText(value, pad, pad + size * 0.7);
    sprite = { canvas: c, w, h: size * 1.4 + pad * 2, pad };
    if (glyphSprites.size > 64) glyphSprites.clear();
    glyphSprites.set(key, sprite);
  }
  return sprite;
}
function drawSprite(sprite, cx, baseline, size, scale = 1) {
  const w = (sprite.w + sprite.pad * 2) * scale,
    h = sprite.h * scale;
  g.drawImage(sprite.canvas, cx - w / 2, baseline - size * 0.35 - h / 2, w, h);
}
function line(x, y, x2, y2, stroke = INK.dark, alpha = 1, w = 1) {
  g.globalAlpha = alpha;
  g.strokeStyle = stroke;
  g.lineWidth = w;
  g.beginPath();
  g.moveTo(x, y);
  g.lineTo(x2, y2);
  g.stroke();
  g.globalAlpha = 1;
}
const blooms = new Map();
function dot(x, y, r, fill, glow = 0) {
  if (glow > 0) {
    // Blur once per glyph size/hue, then composite a small sprite.
    const radius = Math.round(r * 4) / 4,
      blur = Math.round(glow),
      key = `${radius}/${blur}/${fill}`;
    let sprite = blooms.get(key);
    if (!sprite) {
      const pad = Math.ceil(radius + blur * 2);
      sprite = document.createElement("canvas");
      sprite.width = sprite.height = pad * 2;
      const ink = sprite.getContext("2d");
      ink.shadowColor = fill;
      ink.shadowBlur = blur;
      ink.fillStyle = fill;
      ink.beginPath();
      ink.arc(pad, pad, radius, 0, Math.PI * 2);
      ink.fill();
      blooms.set(key, sprite);
    }
    g.drawImage(sprite, x - sprite.width / 2, y - sprite.height / 2);
  } else {
    g.fillStyle = fill;
    g.beginPath();
    g.arc(x, y, r, 0, Math.PI * 2);
    g.fill();
  }
}
function ring(x, y, r, stroke, alpha = 1, w = 1) {
  g.save();
  g.globalAlpha = alpha;
  g.strokeStyle = stroke;
  g.lineWidth = w;
  g.beginPath();
  g.arc(x, y, r, 0, Math.PI * 2);
  g.stroke();
  g.restore();
}
function hit(x, y, w, h, kind, data, label) {
  hits.push({ x, y, w, h, kind, data, label });
}
function wrap(value, x, y, maxWidth, size = 11, fill = INK.faint, maxLines = 3) {
  const words = String(value ?? "unknown").split(" ");
  let row = "",
    count = 0;
  g.font = font(size);
  for (const word of words) {
    if (g.measureText(row + word).width > maxWidth && row) {
      text(row, x, y, fill, size, maxWidth);
      y += size * 1.55;
      row = "";
      if (++count >= maxLines) return y;
    }
    row += word + " ";
  }
  text(row, x, y, fill, size, maxWidth);
  return y + size * 1.55;
}
function strokePath(points, stroke, w = 1, alpha = 1, glow = 0) {
  if (points.length < 2) return;
  g.save();
  g.strokeStyle = stroke;
  g.lineJoin = "round";
  g.lineCap = "round";
  g.beginPath();
  points.forEach((p, i) => (i ? g.lineTo(p.x, p.y) : g.moveTo(p.x, p.y)));
  if (glow) {
    g.globalAlpha = alpha * 0.05;
    g.lineWidth = w + glow;
    g.stroke();
    g.globalAlpha = alpha * 0.12;
    g.lineWidth = w + glow * 0.45;
    g.stroke();
  }
  g.globalAlpha = alpha;
  g.lineWidth = w;
  g.stroke();
  g.restore();
}
function along(points, fraction) {
  if (!points.length) return null;
  const lengths = points
    .slice(1)
    .map((p, i) => Math.hypot(p.x - points[i].x, p.y - points[i].y));
  let distance = lengths.reduce((a, b) => a + b, 0) * clamp(fraction);
  for (let i = 0; i < lengths.length; i++) {
    if (distance <= lengths[i]) {
      const t = lengths[i] ? distance / lengths[i] : 1;
      return {
        x: points[i].x + (points[i + 1].x - points[i].x) * t,
        y: points[i].y + (points[i + 1].y - points[i].y) * t,
      };
    }
    distance -= lengths[i];
  }
  return points.at(-1);
}
const routeLength = (points) =>
  points
    .slice(1)
    .reduce((sum, p, i) => sum + Math.hypot(p.x - points[i].x, p.y - points[i].y), 0);
function halo(x, y, r, fill, alpha = 0.18) {
  g.save();
  g.globalAlpha = alpha;
  const bloom = g.createRadialGradient(x, y, 0, x, y, r);
  bloom.addColorStop(0, fill);
  bloom.addColorStop(1, "transparent");
  g.fillStyle = bloom;
  g.fillRect(x - r, y - r, r * 2, r * 2);
  g.restore();
}
function diamond(x, y, r, stroke, alpha = 0.9) {
  strokePath(
    [
      { x, y: y - r },
      { x: x + r, y },
      { x, y: y + r },
      { x: x - r, y },
      { x, y: y - r },
    ],
    stroke,
    1.1,
    alpha,
    5,
  );
}
// An instrument bezel: a panel, a double rule and four corner ticks.
function bezel(x, y, w, h, o = {}) {
  g.save();
  if (o.fill !== false) {
    g.fillStyle = INK.panel;
    g.fillRect(x, y, w, h);
  }
  g.strokeStyle = o.edge || "#2e271d";
  g.lineWidth = 1;
  g.strokeRect(x + 0.5, y + 0.5, w - 1, h - 1);
  g.strokeStyle = "#1b1711";
  g.strokeRect(x + 4.5, y + 4.5, w - 9, h - 9);
  g.strokeStyle = o.tick || "#6b6050";
  const t = 7;
  for (const [cx, cy, sx, sy] of [
    [x, y, 1, 1],
    [x + w, y, -1, 1],
    [x, y + h, 1, -1],
    [x + w, y + h, -1, -1],
  ]) {
    g.beginPath();
    g.moveTo(cx + sx * 0.5, cy + sy * t);
    g.lineTo(cx + sx * 0.5, cy + sy * 0.5);
    g.lineTo(cx + sx * t, cy + sy * 0.5);
    g.stroke();
  }
  g.restore();
}
function regionTitle(key, label, x, y, w, o = {}) {
  const { compact } = layout;
  text(label.toUpperCase(), x, y, INK.bone, compact ? 9 : 10, w, { weight: 600 });
  text(READINGS[key], x, y + (compact ? 13 : 16), INK.boneDim, compact ? 9 : 10, o.readingWidth ?? w);
}
function isRevealed(key) {
  return reduced.matches || revealed.has(key);
}

function resize() {
  width = innerWidth;
  height = innerHeight;
  const dpr = Math.min(devicePixelRatio || 1, 2);
  for (const [c, ctx] of [
    [canvas, screen],
    [groundCanvas, groundContext],
    [sceneCanvas, sceneContext],
  ]) {
    c.width = Math.round(width * dpr);
    c.height = Math.round(height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  const compact = height < 820,
    wide = width >= 1000,
    m = compact ? 20 : 28,
    consoleH = 76,
    clothH = compact ? 128 : 178;
  const right = wide ? Math.round(width * 0.7) : width - m;
  layout = {
    m,
    compact,
    wide,
    right,
    consoleH,
    clothH,
    headerBottom: compact ? 124 : 170,
    warp: Math.round(Math.min(290, Math.max(170, width * 0.16))),
    cloth: height - consoleH - clothH,
  };
  layout.railY = layout.cloth + (compact ? 56 : 74);
  layout.top = layout.headerBottom + 10;
  layout.wx = layout.warp + 22;
  layout.ww = right - layout.wx - 12;
  layout.tree = {
    x: layout.wx,
    y: layout.top + (compact ? 38 : 50),
    w: layout.ww,
    h: layout.cloth - 16 - (layout.top + (compact ? 38 : 50)),
  };
  layout.face = wide
    ? {
        x: right + 18,
        y: m - 8,
        w: width - right - 18 - m,
        h: compact ? 316 : 446,
      }
    : null;
  bench.style.top = layout.face
    ? Math.round(layout.face.y + layout.face.h + 14) + "px"
    : "";
  groundDirty = sceneDirty = true;
  rebuild();
}
function beadKey(b) {
  return JSON.stringify([b.at, b.act, b.detail, b.places, b.ctx_after]);
}
function clothRows() {
  return list(state?.cloth?.rows)
    .filter((r) => matches(r.topics))
    .slice()
    .sort((a, b) =>
      a.run === state?.run?.id
        ? 1
        : b.run === state?.run?.id
          ? -1
          : String(a.started).localeCompare(String(b.started)),
    );
}
// Passes group under their parent: a strand never sits beside the pass that
// dispatched it on the time line.
function railGroups() {
  const rows = clothRows(),
    byRun = new Map(rows.map((r) => [r.run, r]));
  const topOf = (r) => {
    let cur = r,
      guard = 0;
    while (cur.parent && cur.parent !== cur.run && byRun.has(cur.parent) && guard++ < 12)
      cur = byRun.get(cur.parent);
    return cur;
  };
  const groups = new Map();
  for (const r of rows) {
    const top = topOf(r);
    if (!groups.has(top.run)) groups.set(top.run, { row: top, strands: [] });
    if (top !== r) groups.get(top.run).strands.push(r);
  }
  const live = state?.run?.id;
  const holdsLive = (gr) => gr.row.run === live || gr.strands.some((r) => r.run === live);
  for (const gr of groups.values())
    gr.strands.sort((a, b) => String(a.started).localeCompare(String(b.started)));
  return [...groups.values()].sort((a, b) =>
    holdsLive(a) ? 1 : holdsLive(b) ? -1 : String(a.row.started).localeCompare(String(b.row.started)),
  );
}
const groupOf = (run) =>
  railGroups().find((gr) => gr.row.run === run || gr.strands.some((r) => r.run === run));
function railLayout() {
  const groups = railGroups(),
    { m, right, compact } = layout;
  let focus = groups.findIndex((gr) => gr.row.run === focusRun);
  if (focus < 0) focus = groups.length - 1;
  const cardWidth = Math.min(compact ? 320 : 400, (right - m * 2) * 0.4),
    live = focus === groups.length - 1;
  const center = live ? right - 20 - cardWidth / 2 : (right + m) / 2;
  const side = Math.max(center - cardWidth / 2 - m, right - 20 - (center + cardWidth / 2)) - 12;
  // A wide shoulder: the ten nearest read as plaques in miniature.
  const miniBase = Math.max(44, Math.min(compact ? 92 : 124, (side - 60) / 7.1));
  const widthAt = (k) => (k <= 10 ? Math.max(40, miniBase * Math.pow(0.92, k - 1)) : 12);
  return groups.map((gr, i) => {
    const d = i - focus,
      ad = Math.abs(d);
    if (!d) return { ...gr, focus: true, kind: "plaque", scale: 1, x: center, w: cardWidth };
    let offset = cardWidth / 2 + 10;
    for (let k = 1; k < ad; k++) offset += widthAt(k) + (k <= 10 ? 6 : 3);
    const w = widthAt(ad);
    return {
      ...gr,
      focus: false,
      kind: ad <= 10 ? "mini" : "dot",
      scale: Math.pow(0.92, ad),
      x: center + Math.sign(d) * (offset + w / 2),
      w,
    };
  });
}
function passPlaces(row) {
  if (Array.isArray(row?.places))
    return row.places
      .map((p) => (typeof p === "string" ? p : p.path))
      .filter(Boolean);
  if (row?.run === state?.run?.id)
    return [...new Set(list(state.beads).filter(fileBead).flatMap((b) => list(b.places)))];
  const thread = list(state?.hud?.strands).find((t) => t.id === row?.run);
  return Array.isArray(thread?.places) ? thread.places : null;
}
function groupPlaces(entry) {
  const lists = [entry.row, ...entry.strands].map(passPlaces).filter((p) => p !== null);
  return lists.length ? [...new Set(lists.flat())] : null;
}
function focusedPaths() {
  const entry = railLayout().find((r) => r.focus);
  return new Set((entry && groupPlaces(entry)) || []);
}
function focusPass(run) {
  const gr = groupOf(run);
  focusRun = gr ? gr.row.run : run;
  radial = null;
  const row = clothRows().find((r) => r.run === run);
  if (row) select("cloth", row);
  else rebuild();
}
function stepFocus(delta) {
  const groups = railGroups(),
    index = railLayout().findIndex((r) => r.focus);
  if (groups.length)
    focusPass(groups[Math.max(0, Math.min(groups.length - 1, index + delta))].row.run);
}
function mergedPlaces() {
  const places = new Map();
  for (const p of list(state?.tree?.places))
    if (p.path) places.set(p.path, { ...p, topics: list(p.topics) });
  for (const b of list(state?.beads).filter(fileBead))
    for (const path of list(b.places)) {
      if (!places.has(path))
        places.set(path, {
          path,
          heat: null,
          knots: null,
          topics: [],
          last: b.at,
        });
      const p = places.get(path);
      p.topics = [...new Set([...p.topics, ...list(b.topics)])];
    }
  for (const thread of list(state?.hud?.strands))
    for (const path of list(thread.places))
      if (!places.has(path))
        places.set(path, {
          path,
          heat: null,
          knots: null,
          topics: list(state.heddles)
            .filter((h) => list(h.signature?.threads).includes(thread.id))
            .map((h) => h.slug),
          last: null,
        });
  const visible = layout
    ? railLayout().filter(
        (r) => r.x + r.w / 2 > layout.m && r.x - r.w / 2 < layout.right - 20,
      )
    : [];
  // The window is the union of places of the passes in view — counted only
  // over passes that attest places; none attesting ⇒ no filter, never empty.
  const mapped = visible.map(groupPlaces).filter((p) => p !== null);
  const union = mapped.length ? new Set(mapped.flat()) : null;
  return [...places.values()].filter(
    (p) => matches(p.topics) && (!union || union.has(p.path)),
  );
}
// Places beyond files: four fixed places around the roots.
const FIXED = ["forge", "wire", "shed", "crew", "clock"];
const FIXED_GLYPH = { forge: "◆", wire: "≋", shed: "⌂", crew: "⁂", clock: "◷" };
function beadPath(b) {
  const kind = b?.place_kind || "file";
  if (FIXED.includes(kind)) return kind + ":";
  if (kind === "home") {
    const h = list(b?.home_places).at(-1) || list(b?.places).at(-1);
    return h ? "home:" + h : null;
  }
  return list(b?.places).at(-1) || null;
}
// Files a bead named are repo places whatever its kind; home paths ride
// `home_places` (or `places` on a home bead from an older feed).
const fileBead = (b) => (b.place_kind || "file") !== "home" || list(b.home_places).length > 0;
function newestPlace() {
  return [...list(state?.beads)].reverse().map(beadPath).find(Boolean) || null;
}
function actorHistory() {
  const out = [];
  for (const b of list(state?.beads)) {
    if (!matches(b.topics)) continue;
    const p = beadPath(b);
    if (p && out.at(-1) !== p) out.push(p);
  }
  return out.slice(-8);
}
function homePlaces() {
  const places = new Map();
  for (const p of list(state?.tree?.home?.places))
    if (p.path) places.set("home:" + p.path, { ...p, topics: list(p.topics), path: "home:" + p.path, kind: "home" });
  for (const b of list(state?.beads))
    for (const path of b.place_kind === "home" && !list(b.home_places).length ? list(b.places) : list(b.home_places))
        if (!places.has("home:" + path))
          places.set("home:" + path, { path: "home:" + path, heat: null, knots: null, topics: list(b.topics), last: b.at, kind: "home" });
  return [...places.values()].filter((p) => matches(p.topics));
}
function prsInView() {
  const entry = layout && state ? railLayout().find((r) => r.focus) : null;
  if (!entry) return [];
  return [...new Set([entry.row, ...entry.strands].flatMap((r) => list(r.prs)))];
}
function cap(places, limit, pinned) {
  const ranked = places
    .slice()
    .sort(
      (a, b) =>
        (b.heat ?? -1) - (a.heat ?? -1) ||
        String(b.last || "").localeCompare(String(a.last || "")) ||
        a.path.localeCompare(b.path),
    );
  const shown = new Map();
  for (const p of ranked) if (pinned.has(p.path)) shown.set(p.path, p);
  for (const p of ranked) {
    if (shown.size >= limit) break;
    shown.set(p.path, p);
  }
  if (expanded)
    for (const p of ranked) if (p.path.startsWith(expanded + "/")) shown.set(p.path, p);
  return shown;
}
function buildTrie(places, rootPath, rootName) {
  const root = { path: rootPath, name: rootName, children: new Map(), root: true, home: rootPath === "home:" };
  for (const place of places) {
    const rel = place.path.slice(rootPath.length),
      parts = rel.split("/").filter(Boolean);
    const compact = parts.length > 4 ? [parts[0], parts[1], "…", parts.at(-1)] : parts;
    let node = root,
      prefix = "";
    compact.forEach((part, i) => {
      const leaf = i === compact.length - 1;
      prefix = leaf ? rel : (prefix ? prefix + "/" : "") + part;
      const key = rootPath + prefix;
      if (!node.children.has(key))
        node.children.set(key, { path: key, name: part, children: new Map(), home: root.home });
      node = node.children.get(key);
    });
    node.place = place;
  }
  function compress(node) {
    for (const [key, child] of node.children) node.children.set(key, compress(child));
    while (!node.root && !node.place && node.children.size === 1) {
      const child = [...node.children.values()][0];
      node = { ...child, name: node.name + "/" + child.name };
    }
    return node;
  }
  compress(root);
  const nodes = [],
    leaves = [];
  (function visit(node, parent = null, depth = 0) {
    node.parent = parent;
    node.depth = depth;
    nodes.push(node);
    node.children = new Map(
      [...node.children].sort(
        (a, b) =>
          Number(b[1].children.size > 0) - Number(a[1].children.size > 0) ||
          a[1].name.localeCompare(b[1].name),
      ),
    );
    for (const child of node.children.values()) visit(child, node, depth + 1);
    if (!node.children.size && !node.root) leaves.push(node);
    node.topics = node.place?.topics || [...new Set([...node.children.values()].flatMap((n) => n.topics))];
  })(root);
  return { root, nodes, leaves };
}
function layoutTrie(trie, x0, x1, top, bottom, next) {
  const { nodes, leaves, root } = trie;
  const maxDepth = Math.max(1, ...nodes.map((n) => n.depth));
  const spacing = (x1 - x0) / Math.max(1, leaves.length - 1);
  const yOf = (depth) => bottom - ((bottom - top) * depth) / maxDepth;
  leaves.forEach((node, i) =>
    next.set(node.path, {
      x: leaves.length === 1 ? (x0 + x1) / 2 : x0 + i * spacing,
      // Crowded rows alternate half a step so labels can take turns.
      y: yOf(node.depth) + (spacing < 70 && i % 2 ? Math.min(22, (bottom - top) / maxDepth / 2.4) : 0),
    }),
  );
  (function place(node) {
    if (!node.children.size) return;
    const children = [...node.children.values()];
    children.forEach(place);
    next.set(node.path, {
      x: children.reduce((sum, n) => sum + next.get(n.path).x, 0) / children.length,
      y: yOf(node.depth),
    });
  })(root);
  const r0 = next.get(root.path);
  next.set(root.path, {
    x: Math.max(x0 + (x1 - x0) * 0.25, Math.min(x0 + (x1 - x0) * 0.75, r0?.x ?? (x0 + x1) / 2)),
    y: bottom,
  });
}
function rebuild() {
  sceneDirty = true;
  if (!state || !layout) return;
  const all = mergedPlaces(),
    allHome = homePlaces();
  const actorPath = newestPlace();
  // Fewer things at once: the hottest places, plus everything the eye must
  // not lose — the shuttle, its trail, the threads, the selection.
  const pinned = new Set(actorHistory());
  if (actorPath) pinned.add(actorPath);
  for (const t of list(state.hud?.strands)) {
    const p = list(t.places).at(-1);
    if (p) pinned.add(p);
  }
  if (selected.kind === "place" && selected.data?.path) pinned.add(selected.data.path);
  const shown = cap(all, lifted.size ? LIFTED_CAP : PLACE_CAP, pinned),
    shownHome = cap(allHome, lifted.size ? 24 : 12, pinned);
  shownCount = shown.size + shownHome.size;
  hiddenCount = all.length + allHome.length - shownCount;
  const repo = buildTrie([...shown.values()], "", state.repo || "repo"),
    home = buildTrie([...shownHome.values()], "home:", "home");
  const hasHome = home.leaves.length > 0;
  for (const [trie, pool, set] of [
    [repo, all, shown],
    [home, allHome, shownHome],
  ])
    for (const node of trie.nodes)
      node.hiddenBelow = node.root
        ? pool.length - set.size
        : pool.filter((p) => !set.has(p.path) && p.path.startsWith(node.path + "/")).length;
  const T = layout.tree,
    bottom = T.y + T.h - 30,
    top = T.y + 46;
  const homeW = hasHome ? Math.min(T.w * 0.3, 380) : 0,
    rightStrip = 120;
  const next = new Map();
  layoutTrie(repo, T.x + homeW + 64, T.x + T.w - rightStrip, top, bottom, next);
  if (hasHome) layoutTrie(home, T.x + 44, T.x + homeW - 24, T.y + T.h * 0.42, bottom, next);
  // The fixed places: the forge upper-right, the shed beside the face, the
  // crew beside the strands, the wire lower-left.
  const fixedAt = {
    forge: { x: T.x + T.w - 64, y: T.y + 58 },
    shed: { x: T.x + T.w - 46, y: T.y + T.h * 0.34 },
    crew: { x: T.x + T.w - 64, y: T.y + T.h * 0.66 },
    wire: { x: T.x + 40, y: T.y + T.h - 74 },
    clock: { x: T.x + 46, y: T.y + 58 },
  };
  const fixedNodes = FIXED.map((kind) => ({
    path: kind + ":",
    name: kind,
    fixed: kind,
    children: new Map(),
    depth: 1,
    topics: [],
    parent: kind === "wire" && hasHome ? home.root : repo.root,
    hiddenBelow: 0,
  }));
  for (const n of fixedNodes) next.set(n.path, fixedAt[n.fixed]);
  treeNodes = [...repo.nodes, ...(hasHome ? home.nodes : []), ...fixedNodes];
  layout.hasHome = hasHome;
  const changed = JSON.stringify([...next]) !== JSON.stringify([...targetPositions]);
  if (changed) {
    positions = new Map([...next].map(([path, p]) => [path, point(path) || p]));
    targetPositions = next;
    movesAt = clock;
  }
  if (actor?.path !== actorPath && actorPath && next.has(actorPath)) {
    const from = actor ? beam(actor.path, true) : [],
      to = beam(actorPath, true);
    let shared = 0;
    while (
      shared < from.length &&
      shared < to.length &&
      from[shared].x === to[shared].x &&
      from[shared].y === to[shared].y
    )
      shared++;
    // Different roots share nothing: the walk runs down one tree, along the
    // weft, and up the other.
    const route = from.length ? [...from.slice(Math.max(0, shared - 1)).reverse(), ...to.slice(shared)] : [];
    walk = {
      route,
      born: clock,
      dur: route.length ? Math.max(300, Math.min(600, routeLength(route) * 1.1)) : 0,
    };
  }
  actor = actorPath && next.has(actorPath) ? { path: actorPath } : null;
  planLabels();
  updateAccess();
  // A readable receipt of what the window holds, for tests and assistive tools.
  Object.assign(canvas.dataset, {
    lifted: [...lifted].join(","),
    places: String(shownCount),
    placesTotal: String(all.length + allHome.length),
    passes: String(clothRows().length),
    groups: String(railGroups().length),
    warp: String(list(state.warp?.items).filter((w) => matches(w.topics) && !["done", "retired"].includes(w.state)).length),
    actor: actorPath || "",
  });
}
function planLabels() {
  const T = layout.tree,
    boxes = [],
    measure = sceneContext;
  const clear = (b) =>
    b.x >= T.x + 2 &&
    b.x + b.w <= T.x + T.w - 2 &&
    b.y >= T.y + 2 &&
    b.y + b.h <= T.y + T.h &&
    !boxes.some(
      (o) => b.x < o.x + o.w && b.x + b.w > o.x && b.y < o.y + o.h && b.y + b.h > o.y,
    );
  for (const n of treeNodes) {
    n.label = null;
    const p = targetPositions.get(n.path);
    if (p) boxes.push({ x: p.x - 6, y: p.y - 6, w: 12, h: 12 });
    if (p && n.place?.knots >= 2) boxes.push({ x: p.x + 4, y: p.y + 4, w: 10, h: 10 });
  }
  const a = actor && targetPositions.get(actor.path);
  if (a) boxes.push({ x: a.x - 40, y: a.y - 36, w: 124, h: 30 });
  const rank = (n) =>
    n.root
      ? 0
      : n.path === actor?.path
        ? 1
        : selected.kind === "place" && selected.data?.path === n.path
          ? 2
          : n.children.size
            ? 10 + n.depth
            : 100 - clamp(n.place?.heat) * 50;
  for (const n of treeNodes) {
    const p = n.fixed && targetPositions.get(n.path);
    if (p) boxes.push({ x: p.x - 30, y: p.y - 16, w: 60, h: n.fixed === "forge" ? 48 : 36 });
  }
  for (const n of treeNodes.slice().sort((x, y) => rank(x) - rank(y))) {
    const p = targetPositions.get(n.path);
    if (!p || n.fixed) continue;
    const dir = n.children.size > 0,
      size = n.root ? (n.home ? 10 : 11) : n.home ? 9 : 10;
    measure.font = font(size, n.root || dir ? 500 : 400);
    let label = n.root ? n.name : dir ? n.name + "/" : n.name;
    if (label.length > 28) label = label.slice(0, 26) + "…";
    const w = measure.measureText(label).width;
    const cands = n.root
      ? [[p.x - w / 2, p.y + 20]]
      : dir
        ? [
            [p.x - w - 10, p.y + 4],
            [p.x + 10, p.y + 4],
            [p.x - w / 2, p.y + 19],
          ]
        : [
            [p.x - w / 2, p.y - 11],
            [p.x + 9, p.y + 4],
            [p.x - w - 9, p.y + 4],
            [p.x - w / 2, p.y + 19],
            [p.x - w / 2, p.y - 25],
          ];
    for (const [lx, ly] of cands) {
      const b = { x: lx - 2, y: ly - size, w: w + 4, h: size + 4 };
      if (n.root || clear(b)) {
        boxes.push(b);
        n.label = { text: label, dx: lx - p.x, dy: ly - p.y, size, w };
        break;
      }
    }
  }
}
function point(path) {
  const end = targetPositions.get(path),
    start = positions.get(path) || end;
  if (!end) return null;
  const t = reduced.matches ? 1 : smooth((clock - movesAt) / BEAT);
  return {
    x: start.x + (end.x - start.x) * t,
    y: start.y + (end.y - start.y) * t,
  };
}
const EDGE_STEPS = 10;
// Branch lines are cubic curves; walkers sample the very same curve.
function edgePoints(parent, p) {
  const my = (parent.y + p.y) / 2,
    out = [];
  for (let i = 0; i <= EDGE_STEPS; i++) {
    const t = i / EDGE_STEPS,
      u = 1 - t;
    out.push({
      x: u * u * u * parent.x + 3 * u * u * t * parent.x + 3 * u * t * t * p.x + t * t * t * p.x,
      y: u * u * u * parent.y + 3 * u * u * t * my + 3 * u * t * t * my + t * t * t * p.y,
    });
  }
  return out;
}
function beam(path, target = false) {
  const chain = [];
  let node = treeNodes.find((n) => n.path === path);
  while (node) {
    const p = target ? targetPositions.get(node.path) : point(node.path);
    if (p) chain.unshift(p);
    node = node.parent;
  }
  return chain.flatMap((p, i) =>
    i ? edgePoints(chain[i - 1], p).slice(1) : [p],
  );
}
function actorPoint() {
  if (!actor) return null;
  const target = point(actor.path);
  if (!target) return null;
  if (reduced.matches || !walk.route.length) return target;
  const t = (clock - walk.born) / walk.dur;
  return t >= 1 ? target : along(walk.route, smooth(t));
}
function receive(next) {
  if (!next || typeof next !== "object" || Array.isArray(next)) {
    sourceStatus = "invalid state";
    return;
  }
  const runId = next.run?.id ?? null;
  if (runId !== currentRun) {
    seen = new Set();
    sparks = [];
    currentRun = runId;
  }
  const prior = state,
    initial = state === null || state.run?.id !== runId;
  const fresh = list(next.beads).filter((b) => !seen.has(beadKey(b)));
  seen = new Set(list(next.beads).map(beadKey));
  state = next;
  for (const slug of lifted)
    if (!list(state.heddles).some((h) => h.slug === slug)) lifted.delete(slug);
  rebuild();
  if (revealAt === null) revealAt = clock;
  if (!initial && prior && !reduced.matches) {
    const landing = walk.born + walk.dur;
    fresh
      .filter((b) => matches(b.topics))
      .slice(-3)
      .forEach((b, i) => {
        const born = Math.max(clock, landing) + i * 140;
        sparks.push({ born, seed: glitchSeed++, count: 12 + (glitchSeed % 9) });
        glitch(() => blockRect(0), 120, born);
      });
    const before = prForge;
    prForge = prsInView().length;
    if (before != null && prForge > before) {
      forgeDrop = { born: clock };
      glitch(() => regionRects.forge, 140, clock + 400);
    }
    if (prior.shuttle?.state !== next.shuttle?.state)
      glitch(() => regionRects.faceWord, 140);
    for (const h of list(next.heddles)) {
      const old = list(prior.heddles).find((x) => x.slug === h.slug);
      if (
        !old ||
        Math.abs(clamp(old.lit) - clamp(h.lit)) > 0.15 ||
        old.lit > 0.05 !== h.lit > 0.05
      )
        glitch(() => runeRects.get(h.slug), 120);
    }
    const oldKnots = new Map(list(prior.tree?.places).map((p) => [p.path, p.knots]));
    for (const p of list(next.tree?.places))
      if ((p.knots || 0) > (oldKnots.get(p.path) || 0))
        glitch(() => {
          const q = point(p.path);
          return q && { x: q.x - 16, y: q.y - 16, w: 32, h: 32 };
        }, 120);
    for (const t of list(next.hud?.strands)) {
      const o = list(prior.hud?.strands).find((x) => x.id === t.id);
      if (o && o.status !== t.status) glitch(() => threadRects.get(t.id), 140);
    }
  }
  for (const thread of list(state.hud?.strands)) {
    const path = thread.status === "done" ? "" : list(thread.places).at(-1),
      prior = threadTravel.get(thread.id);
    if (!prior || prior.path !== path) {
      const from = beam(prior?.path, true),
        to = beam(path, true);
      let shared = 0;
      while (
        shared < from.length &&
        shared < to.length &&
        from[shared].x === to[shared].x &&
        from[shared].y === to[shared].y
      )
        shared++;
      threadTravel.set(thread.id, {
        path,
        born: clock,
        route: prior
          ? [...from.slice(Math.max(0, shared - 1)).reverse(), ...to.slice(shared)]
          : [],
      });
    }
  }
  // cloth.rows[].trail is kept ready for the sonar's next shape; nothing
  // draws it yet.
  trails = new Map(list(state.cloth?.rows).filter((r) => Array.isArray(r.trail)).map((r) => [r.run, r.trail]));
  noteTick(state);
  sourceStatus = dev ? "fixture" : "live";
  renderReceipt();
}
function glitch(rect, dur = 120, born = clock) {
  if (reduced.matches) return;
  glitches.push({ rect, dur, born, seed: glitchSeed++ });
  if (glitches.length > 24) glitches = glitches.slice(-24);
}
function toggle(slug) {
  // One click = one toggle; everything below is rebuilt from the intersection.
  radial = null;
  actions.lift();
  clearTimeout(expandTimer);
  lifted.has(slug) ? lifted.delete(slug) : lifted.add(slug);
  warpScroll = 0;
  expanded = null;
  hovered = null;
  if (focusRun && !clothRows().some((r) => r.run === focusRun)) focusRun = null;
  if (selected.kind !== "heddles") pageHistory = [...pageHistory, selected].slice(-24);
  selected = { kind: "heddles" };
  lastReceipt = "";
  rebuild();
  glitch(() => runeRects.get(slug), 140);
  renderReceipt();
  document.querySelector("#focus").textContent = lifted.size
    ? "@" + [...lifted].join(" ∩ ")
    : "@shuttle";
}
let pageHistory = [];
function select(kind, data, o = {}) {
  sceneDirty = true;
  if (kind === "cloth") {
    const gr = groupOf(data.run);
    focusRun = gr ? gr.row.run : data.run;
  }
  if (!o.back && selected && !(selected.kind === kind && selected.data === data))
    pageHistory = [...pageHistory, selected].slice(-24);
  radial = kind === "place" ? { path: data.path } : null;
  selected = { kind, data };
  if (kind === "cloth" || kind === "place") rebuild();
  bench.classList.add("open");
  pendingFold++;
  if (kind === "place")
    document.querySelector("#focus").textContent = "@" + data.path;
  lastReceipt = "";
  renderReceipt();
  receipt.scrollTop = 0;
  bench.scrollTop = 0;
}
function back() {
  const prior = pageHistory.at(-1);
  if (!prior) return;
  pageHistory = pageHistory.slice(0, -1);
  select(prior.kind, prior.data, { back: true });
}
function element(tag, value, className) {
  const el = document.createElement(tag);
  if (value !== undefined) el.textContent = value;
  if (className) el.className = className;
  return el;
}
function section(title, value) {
  receipt.append(element("h2", title));
  if (value !== undefined) receipt.append(element("p", known(value)));
}
function row(key, value) {
  const el = element("div", undefined, "receipt-row");
  el.append(element("span", key), element("span", known(value)));
  receipt.append(el);
}
function tags(topics) {
  const box = element("div", undefined, "tags");
  for (const topic of list(topics)) {
    const rune = list(state?.heddles).find((h) => h.slug === topic)?.rune;
    const el = element("span", (rune ? rune + " " : "") + topic, "tag");
    el.style.color = color(topic);
    el.style.borderColor = color(topic) + "66";
    box.append(el);
  }
  receipt.append(box);
}
function button(label, callback, className = "bench-action") {
  const el = element("button", label, className);
  el.type = "button";
  el.onclick = callback;
  receipt.append(el);
  return el;
}
function link(label, callback) {
  return button(label, callback, "bench-link");
}
// The feed's page endpoint: read once per address; until it answers, the
// bench renders what the contract attests and says so — never invents.
const PENDING = "— reads more when the feed lands";
const pages = new Map();
const PAGE_PARAMS = {
  bead: (b) => (b?.n == null ? null : { run: b.run || state?.run?.id || "", n: b.n }),
  pass: (id) => (id ? { id } : null),
  item: (id) => (id ? { id } : null),
  place: (path) => (path && !/^(forge|wire|shed|crew|clock):$/.test(path) ? { path: String(path).replace(/^home:/, "") } : null),
  heddle: (slug) => (slug ? { slug } : null),
};
function page(kind, key, o = {}) {
  // Asked on selection; a 404 or an empty answer reads as pending. A frame
  // that lists `pages` and omits this kind is taken at its word.
  if (dev) return null;
  if (Array.isArray(state?.pages) && !state.pages.includes(kind)) return null;
  const params = PAGE_PARAMS[kind]?.(key);
  if (!params) return null;
  const url = `/loom/page/${kind}?${new URLSearchParams(params)}`;
  const seen = pages.get(url);
  if (seen) return seen.status === "ok" ? seen.body : null;
  pages.set(url, { status: "loading" });
  const asked = selected,
    generation = pendingFold;
  fetch(url)
    .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
    .then((body) => {
      pages.set(url, body && typeof body === "object" && !body.error ? { status: "ok", body } : { status: "absent" });
      // Re-render only the page that asked, and never over a fold being read.
      if (o.quiet || selected !== asked || pendingFold !== generation) return;
      lastReceipt = "";
      renderReceipt();
    })
    .catch(() => pages.set(url, { status: "absent" }));
  return null;
}
function pending(label) {
  receipt.append(element("p", label ? `${label} ${PENDING}` : PENDING, "pending"));
}
function head(eyebrow, title, reading) {
  const top = element("div", undefined, "page-top");
  const backButton = element("button", "‹ back", "bench-back");
  backButton.type = "button";
  backButton.disabled = !pageHistory.length;
  backButton.onclick = back;
  top.append(backButton, element("span", eyebrow, "eyebrow"));
  receipt.append(top);
  receipt.append(element("h1", title));
  receipt.append(element("p", reading, "reading"));
}
function extras(body, skip) {
  // Fields the feed page carries that this bench does not yet lay out.
  if (!body) return;
  const rest = Object.entries(body).filter(
    ([k, v]) => !skip.includes(k) && v != null && typeof v !== "object",
  );
  if (!rest.length) return;
  section("From the feed");
  for (const [k, v] of rest) row(k.replaceAll("_", " "), v);
}
const prUrl = (n) => `https://github.com/${state.repo}/pull/${n}`;
function beadList(beads) {
  const box = element("div", undefined, "compact-list");
  receipt.append(box);
  for (const b of beads.slice(-12).reverse()) {
    const el = element("button", undefined, "bench-row");
    el.type = "button";
    el.append(
      element("span", String(b.at || "?").slice(11, 19)),
      element("span", known(b.act)),
      element("span", list(b.places).at(-1) || b.place_kind || "—"),
    );
    el.onclick = () => select("bead", b);
    box.append(el);
  }
}
function card(run) {
  section("Now", run.card?.now);
  section("Plan");
  for (const item of list(run.card?.plan))
    receipt.append(element("p", (item.done ? "✓ " : "· ") + item.text, item.done ? "tick" : ""));
  section("Vector");
  for (const v of list(run.card?.vector)) receipt.append(element("p", v));
  section("Ledger");
  receipt.append(element("pre", list(run.card?.ledger).join("\n") || "unknown"));
}
function passPage(data) {
  const live = data.run === state.run?.id;
  const body = page("pass", data.run);
  head(live ? "pass · this run" : `pass${body?.status ? " · " + body.status : ""}`, body?.title || data.name || data.run, "a pass: one run's work, from its card to what it produced");
  tags(body?.topics?.length ? body.topics : data.topics);
  if (body?.contract) {
    const excerpt = body.contract.length > 700 ? body.contract.slice(0, 700) + "…" : body.contract;
    receipt.append(element("pre", excerpt, "excerpt"));
  } else pending("contract excerpt");
  row("body", [body?.shell || data.shell, body?.core || data.core].map(known).join(" / "));
  row("started → ended", `${known(body?.started || data.started)} → ${body?.ended || data.ended || "open"}`);
  const minutes = body?.duration_s != null ? Math.round(body.duration_s / 60) : minutesOf(data);
  row("duration", minutes == null ? "unknown" : `${minutes} min`);
  row("run", data.run);
  if (body?.branch) row("branch", body.branch);
  if (body?.mood) row("mood", body.mood);
  const parentId = body?.parent || data.parent;
  if (parentId) {
    const parent = list(state.cloth?.rows).find((r) => r.run === parentId);
    section("Dispatched by");
    parent ? link(parent.name || parent.run, () => select("cloth", parent)) : receipt.append(element("p", parentId));
  }
  const cardData = live ? state.run?.card : body?.card;
  if (cardData) card({ card: { now: cardData.now, plan: cardData.plan, vector: cardData.vector, ledger: cardData.ledger } });
  section("Produce");
  const prs = body?.produce?.prs || list(data.prs).map((n) => ({ number: n }));
  for (const pr of prs) {
    const a = element("a", `PR #${pr.number}${pr.state ? " · " + pr.state.toLowerCase() : ""}`, "bench-link");
    a.href = pr.url || prUrl(pr.number);
    a.target = "_blank";
    a.rel = "noreferrer";
    if (pr.state === "MERGED") a.classList.add("receipt");
    receipt.append(a);
  }
  if (!prs.length) receipt.append(element("p", "no PR"));
  if (Array.isArray(body?.produce?.commits)) {
    for (const c of body.produce.commits) receipt.append(element("p", `${String(c.sha).slice(0, 8)}  ${c.subject || ""}`, "mono"));
    if (!body.produce.commits.length) receipt.append(element("p", "no commits", "mono"));
  } else {
    row("knots", data.knots);
    pending("commit shas");
  }
  if (Array.isArray(body?.produce?.pages) && body.produce.pages.length)
    for (const pg of body.produce.pages) receipt.append(element("p", String(pg.path || pg.title || pg), "mono"));
  else row("pages", data.pages ?? (body ? 0 : undefined));
  if (body?.report_path) row("report", `${body.report_path}${body.report_exists === false ? " (missing)" : ""}`);
  row("tokens", number(data.tokens));
  const rows = list(state.cloth?.rows);
  const strands = body?.strands
    ? body.strands.map((t) => ({ ...t, row: rows.find((r) => r.run === t.id) }))
    : rows.filter((r) => r.parent === data.run && r.run !== data.run).map((r) => ({ id: r.run, title: r.name, status: strandStatus(r), row: r }));
  section(`Strands · ${strands.length}`);
  for (const t of strands) {
    const label = `⌁ ${String(t.title || t.id).split(/:\s/)[0]} · ${known(t.status)}`;
    t.row ? link(label, () => select("cloth", t.row)) : receipt.append(element("p", label, "mono"));
  }
  if (!strands.length) receipt.append(element("p", "none"));
  section("Last boundaries");
  const beads = (body?.beads?.length ? body.beads : live ? list(state.beads) : list(data.beads)).map((b) => ({ run: data.run, ...b }));
  if (beads.length) beadList(beads);
  else pending();
  extras(body, ["id", "title", "name", "mood", "status", "contract", "shell", "core", "started", "ended", "duration_s", "parent", "branch", "report_path", "report_exists"]);
}
function beadPage(data) {
  const beads = list(state.beads),
    index = beads.findIndex((b) => beadKey(b) === beadKey(data));
  const body = page("bead", data);
  const b = { ...data, ...(body || {}) };
  head(
    `bead${b.n != null ? " · n " + b.n : ""}`,
    `${known(b.act)} · ${String(b.at || "?").slice(11, 19)}Z · ${b.place_kind || "file"}`,
    "a boundary: what the shuttle did, where, and what it cost",
  );
  const nav = element("div", undefined, "page-nav");
  const step = (n) =>
    beads.find((x) => x.n === n) || (index >= 0 ? beads[index + (n > (b.n ?? 0) ? 1 : -1)] : null) || (n != null ? { run: b.run, n } : null);
  for (const [label, n, fallback] of [
    ["‹ prev", body?.prev, index - 1],
    ["next ›", body?.next, index + 1],
  ]) {
    const target = body ? (n == null ? null : step(n)) : beads[fallback];
    const btn = element("button", label, "bench-back");
    btn.type = "button";
    btn.disabled = !target;
    btn.onclick = () => target && select("bead", target, { back: true });
    nav.append(btn);
  }
  nav.append(element("span", index < 0 ? "" : `${index + 1} / ${beads.length} in this frame`, "eyebrow"));
  receipt.append(nav);
  tags(b.topics);
  section("Command");
  receipt.append(element("pre", b.detail_full || b.detail || "unknown", "code"));
  if (b.result_bytes != null) row("result", `${number(b.result_bytes)} bytes`);
  else pending("result size");
  if (Array.isArray(b.tools) && b.tools.length) row("tools", b.tools.join(", "));
  section("Context");
  const of = b.window_tokens || b.ctx_after;
  const bar = element("div", undefined, "delta-bar");
  const fill = element("span");
  fill.style.width = `${Math.max(1, Math.min(100, ((b.delta || 0) / (of || 1)) * 100))}%`;
  bar.append(fill);
  receipt.append(bar);
  receipt.append(
    element(
      "p",
      `+${number(b.delta)} → ${number(b.ctx_after)} in the scroll · bar against ${b.window_tokens ? `the ${compactNumber(b.window_tokens)} window` : "the scroll (no window size attested)"}`,
      "mono",
    ),
  );
  section("Places");
  for (const path of list(b.places)) link(path, () => select("place", { path }));
  for (const path of list(b.home_places)) link("home · " + path, () => select("place", { path: "home:" + path, kind: "home" }));
  if (!list(b.places).length && !list(b.home_places).length) receipt.append(element("p", b.place_kind && b.place_kind !== "file" ? `the ${b.place_kind}` : "none attested"));
  section("The chip at this boundary");
  if (b.chip) receipt.append(element("pre", b.chip, "code"));
  else pending();
  extras(body, ["run", "n", "at", "act", "place_kind", "detail_full", "result_bytes", "ctx_after", "delta", "window_tokens", "chip", "prev", "next"]);
}
function itemPage(data) {
  const body = page("item", data.id);
  head(`item · ${data.id}`, data.title, "a warp item: intent hanging on its topics, waiting for a pass");
  tags(data.topics);
  row("type", data.type);
  row("state", data.state);
  row("taken", data.taken);
  section("Needs");
  for (const need of list(data.needs)) {
    const item = list(state.warp?.items).find((w) => w.id === need);
    item ? link(`${need} · ${item.title}`, () => select("warp", item)) : receipt.append(element("p", need));
  }
  if (!list(data.needs).length) receipt.append(element("p", "none"));
  if (list(body?.advances).length) row("advances", body.advances.join(", "));
  section("Refs");
  if (body?.refs) for (const r of (Array.isArray(body.refs) ? body.refs : String(body.refs).split(" · "))) receipt.append(element("p", String(r.label || r.path || r), "mono"));
  else if (body) receipt.append(element("p", "none"));
  else pending();
  if (body?.metric) section("Metric", body.metric);
  section("Prompt");
  if (body?.prompt) receipt.append(element("pre", body.prompt));
  else if (body) receipt.append(element("p", "none"));
  else pending();
  if (body?.body) {
    section("Body");
    receipt.append(element("pre", body.body));
  }
  const topic = list(data.topics)[0];
  const siblings = body?.siblings || list(state.warp?.items).filter((w) => w.id !== data.id && topic && list(w.topics).includes(topic));
  section(`On ${topic || "no topic"} · ${siblings.length}`);
  for (const w of siblings.slice(0, 12)) {
    const item = list(state.warp?.items).find((x) => x.id === w.id) || w;
    link(`${w.id} · ${w.title} · ${known(w.state)}`, () => select("warp", item));
  }
  extras(body, ["id", "title", "type", "state", "taken", "refs", "prompt", "body", "metric"]);
}
// Attention, read tolerantly until the feed's key settles: a list of
// {from|start|line, to|end, kind|what, count}, or {read: [...], edit: [...]}
// with [from, to, count] tuples. Kinds other than read count as edits.
function attentionBands(raw) {
  const out = [];
  const push = (from, to, kind, count) => {
    from = Number(from);
    to = Number(to ?? from);
    if (!Number.isFinite(from) || !Number.isFinite(to)) return;
    out.push({ from: Math.min(from, to), to: Math.max(from, to), kind: /^read/.test(String(kind || "read")) ? "read" : "edit", count: Number(count) || 1 });
  };
  const one = (x, kind) =>
    Array.isArray(x) ? push(x[0], x[1], kind, x[2]) : x && typeof x === "object" && push(x.from ?? x.start ?? x.line, x.to ?? x.end ?? x.line, x.kind ?? x.what ?? kind, x.count ?? x.n);
  if (Array.isArray(raw)) raw.forEach((x) => one(x));
  else if (raw && typeof raw === "object")
    for (const [kind, ranges] of Object.entries(raw)) if (Array.isArray(ranges)) ranges.forEach((x) => one(x, kind));
  return out;
}
function fileView(body) {
  const rawText = typeof body.text === "string" ? body.text : typeof body.text?.text === "string" ? body.text.text : null;
  const bands = attentionBands(body.attention);
  if (rawText == null && !bands.length) return false;
  const lines = rawText == null ? [] : rawText.split("\n");
  const total = Math.max(lines.length, Number(body.line_count ?? body.lines ?? body.text?.total_lines) || 0, ...bands.map((b) => b.to), 1);
  const picker = element("div", undefined, "range-picker");
  const from = element("input"),
    to = element("input");
  for (const [input, value, label] of [
    [from, 1, "from line"],
    [to, total, "to line"],
  ]) {
    input.type = "number";
    input.min = 1;
    input.max = total;
    input.value = value;
    input.setAttribute("aria-label", label);
  }
  picker.append(element("span", "lines"), from, element("span", "–"), to, element("span", `of ${total}`, "eyebrow"));
  receipt.append(picker);
  const view = element("div", undefined, "file-view");
  const strip = element("div", undefined, "attention-strip");
  strip.setAttribute("aria-label", "attention: lines read (bone) and edited (amber)");
  const pre = element("pre", undefined, "file-text");
  view.append(strip, pre);
  receipt.append(view);
  const heat = new Map();
  for (const b of bands)
    for (let n = b.from; n <= Math.min(b.to, lines.length); n++) {
      const h = heat.get(n) || { read: 0, edit: 0 };
      h[b.kind] += b.count;
      heat.set(n, h);
    }
  const render = () => {
    const a = Math.max(1, Math.min(total, Number(from.value) || 1)),
      z = Math.max(a, Math.min(total, Number(to.value) || total));
    const frag = document.createDocumentFragment();
    for (let n = a; n <= Math.min(z, lines.length); n++) {
      const ln = element("span", undefined, "ln");
      ln.dataset.n = n;
      const h = heat.get(n);
      if (h) ln.classList.add(h.edit ? "edited" : "read");
      ln.append(element("i", String(n)), document.createTextNode(lines[n - 1] + "\n"));
      frag.append(ln);
    }
    pre.replaceChildren(frag);
    if (!lines.length) pre.append(element("span", rawText == null ? PENDING : "(empty file)", "pending"));
  };
  from.onchange = to.onchange = render;
  render();
  const max = Math.max(1, ...bands.map((b) => b.count));
  for (const b of bands) {
    const band = element("button", undefined, `band ${b.kind}`);
    band.type = "button";
    band.title = `${b.kind} · lines ${b.from}–${b.to} · ${b.count}×`;
    band.setAttribute("aria-label", band.title);
    band.style.top = `${((b.from - 1) / total) * 100}%`;
    band.style.height = `max(2px, ${((b.to - b.from + 1) / total) * 100}%)`;
    band.style.opacity = (0.3 + 0.7 * (b.count / max)).toFixed(2);
    band.onclick = () => {
      if (b.from < Number(from.value) || b.from > Number(to.value)) {
        from.value = 1;
        to.value = total;
        render();
      }
      const target = pre.querySelector(`.ln[data-n="${b.from}"]`);
      if (target) pre.scrollTop = target.offsetTop - pre.offsetTop - 24;
    };
    strip.append(band);
  }
  return true;
}
function placePage(data) {
  const body = page("place", data.path);
  const fixed = /^(forge|wire|shed|crew|clock):$/.test(data.path) ? data.path.slice(0, -1) : null;
  head(fixed ? "place · beyond files" : "place", fixed || data.path, fixed ? `a place beyond files: where the shuttle goes to ${{ forge: "make PRs and merges", wire: "speak on a channel", shed: "wait", crew: "tend its strands", clock: "keep time" }[fixed]}` : "a place: everything the cloth knows happened here");
  const p = mergedPlaces().find((p) => p.path === data.path) || homePlaces().find((p) => p.path === data.path) || data;
  tags(body?.topics?.length ? body.topics : p?.topics);
  row("kind", body?.kind || data.kind || fixed || (String(data.path).startsWith("home:") ? "home" : "file"));
  if (body?.tree) row("tree", body.tree);
  row("heat", body?.heat ?? p?.heat);
  row("knots", body?.knots ?? p?.knots);
  row("last", body?.last ?? p?.last);
  if (fixed === "forge") row("PRs in view", prsInView().map((n) => "#" + n).join(" · ") || "none");
  if (body?.gh_url) {
    const a = element("a", "open on GitHub ↗", "bench-link");
    a.href = body.gh_url;
    a.target = "_blank";
    a.rel = "noreferrer";
    receipt.append(a);
  } else if (!fixed) pending("the GitHub link");
  if (!fixed) {
    section("The file");
    if (!body || !fileView(body)) pending("text and attention");
  }
  section("At this place");
  const actionsRow = element("div", undefined, "action-row");
  for (const action of ["fold", "explain", "fix", "test", "split", "read"]) {
    const b = element("button", action, "bench-action inline");
    b.type = "button";
    b.onclick = () => actions[action]();
    actionsRow.append(b);
  }
  receipt.append(actionsRow);
  const rows = list(state.cloth?.rows);
  const passes = body?.passes || rows.filter((r) => (passPlaces(r) || []).includes(data.path)).map((r) => ({ run: r.run, name: r.name }));
  section(`Passes that touched it · ${passes.length}`);
  for (const ps of passes.slice(-12)) {
    const r = rows.find((x) => x.run === ps.run);
    const label = `${ps.name || ps.run}${ps.mutated ? " · changed it" : ""}${ps.last ? " · " + clothTime(ps.last) : ""}`;
    r ? link(label, () => select("cloth", r)) : receipt.append(element("p", label, "mono"));
  }
  if (!passes.length) body ? receipt.append(element("p", "none")) : pending();
  const beads = body?.beads || list(state.beads).filter(
    (b) => (beadPath(b) === data.path || list(b.places).includes(data.path)) && matches(b.topics),
  );
  section(`Beads · ${beads.length}`);
  if (beads.length) beadList(beads);
  else receipt.append(element("p", "none in this frame"));
  const folds = body?.folds || list(state.bench?.folds).filter((f) => f.place === data.path);
  section("Fold", folds.length ? undefined : "No fold at this place yet.");
  if (body?.fold?.text) {
    receipt.append(element("p", `${body.fold.path} · ${list(body.fold.marks).join(", ") || "no marks"}`, "mono"));
    receipt.append(element("pre", body.fold.text));
    button("keep", actions.keep);
    button("drop", actions.drop);
  } else for (const fold of folds) button(`${fold.path} · ${list(fold.marks).join(", ")}`, () => openFold(fold));
  extras(body, ["path", "kind", "tree", "heat", "last", "knots", "fold", "gh_url", "line_count", "lines"]);
}
function heddlePages() {
  const shown = list(state.heddles).filter((h) => lifted.has(h.slug));
  head(
    lifted.size ? (lifted.size > 1 ? "intersection" : "heddle") : "heddles",
    lifted.size ? "The shed is raised" : "The whole cloth",
    lifted.size ? "a heddle: a topic, its signature and what it has lit" : "no rune lifted — lift one to raise its work",
  );
  tags([...lifted]);
  section(
    "Selection",
    lifted.size
      ? "Only rows carrying every lifted topic are in the window."
      : "No layers lifted. All measured rows are visible.",
  );
  row("places", mergedPlaces().length);
  row("passes", clothRows().length);
  row("items", canvas.dataset.warp);
  row("beads", list(state.beads).filter((b) => matches(b.topics)).length);
  for (const h of shown) {
    const body = page("heddle", h.slug);
    section(`${h.rune} / ${h.slug}`);
    row("lit", h.lit);
    row("last lit", h.last_lit);
    const has = (x) => list(x.topics).includes(h.slug);
    row(
      "counts",
      `${list(state.tree?.places).filter(has).length} places · ${list(state.cloth?.rows).filter(has).length} passes · ${list(state.warp?.items).filter(has).length} items · ${list(state.beads).filter(has).length} beads`,
    );
    for (const [key, value] of Object.entries(h.signature || {})) row(key, list(value).join(", ") || "none");
    if (body?.counts && typeof body.counts === "object")
      row("index", Object.entries(body.counts).map(([k, v]) => `${v} ${k}`).join(" · ") + (body.total != null ? ` · ${body.total} rows` : ""));
    if (Array.isArray(body?.rows)) {
      receipt.append(element("h2", "The index's last rows"));
      for (const r of body.rows.slice(-10).reverse())
        receipt.append(element("p", typeof r === "string" ? r : `${String(r.at || "").slice(5, 16)}  ${known(r.kind)}  ${r.ref || ""}`, "mono"));
    } else pending("the index's last rows");
  }
  button("Open the shuttle’s card", () => select("run"));
}
function renderReceipt() {
  if (!state) return;
  const signature = JSON.stringify([
    selected,
    state.run,
    state.beads,
    state.hud,
    [...lifted],
    state.bench,
    pageHistory.length,
  ]);
  if (signature === lastReceipt) return;
  lastReceipt = signature;
  receipt.replaceChildren();
  const { kind, data } = selected;
  if (dev) receipt.append(element("p", "FIXTURE / illustrative data", "eyebrow"));
  if (kind === "run") {
    const run = state.run;
    if (!run) {
      head("pass", "No live pass", "nothing is weaving right now");
      section("The shuttle", known(state.shuttle?.state));
      return;
    }
    const liveRow = list(state.cloth?.rows).find((r) => r.run === run.id) || {
      run: run.id,
      name: run.name,
      shell: run.shell,
      core: run.core,
      started: run.started,
      topics: run.topic ? [run.topic] : [],
    };
    passPage({ ...liveRow, name: run.name || liveRow.name });
    section("Context");
    row("in the scroll", number(state.hud?.ctx_tokens));
  } else if (kind === "heddles") heddlePages();
  else if (kind === "bead") beadPage(data);
  else if (kind === "cloth") passPage(data);
  else if (kind === "place") placePage(data);
  else if (kind === "warp") itemPage(data);
  else if (kind === "goal") {
    head(`goal · ${data.id}`, data.title, "a goal: what the warp is for");
    section("Metric", data.metric);
  } else if (kind === "strand") {
    const row0 = list(state.cloth?.rows).find((r) => r.run === data.id);
    if (row0) passPage(row0);
    else head("thread", data.title, "a thread out from this pass");
    section("Thread");
    row("thread", data.id);
    row("status", data.status);
    row("spent", number(data.spent));
    row("allowance", number(data.allowance));
    if (askOf(data) != null) button(`ask +${compactNumber(askOf(data))} → grant`, actions.grant);
  }
}
async function openFold(fold) {
  const generation = ++pendingFold;
  section("Reading fold…");
  try {
    const response = await fetch(
      "/loom/bench?path=" + encodeURIComponent(fold.path),
    );
    if (!response.ok) throw Error(`fold unavailable (${response.status})`);
    const body = await response.text();
    if (generation === pendingFold) {
      section(fold.path);
      receipt.append(element("pre", body));
      button("keep", actions.keep);
      button("drop", actions.drop);
    }
  } catch (error) {
    if (generation === pendingFold) section("Fold", error.message);
  }
}
function updateAccess() {
  const entries = [
    ...list(state?.heddles).map((h) => ({
      label: "Lift " + h.slug,
      action: () => toggle(h.slug),
    })),
    { label: "Open shuttle card", action: () => select("run") },
    ...list(state?.hud?.strands).map((thread) => ({
      label: "Open thread " + thread.title,
      action: () => select("strand", thread),
    })),
    ...mergedPlaces().map((p) => ({
      label: "Open place " + p.path,
      action: () => select("place", p),
    })),
    ...list(state?.cloth?.rows)
      .filter((r) => matches(r.topics))
      .map((r) => ({
        label: "Open pass " + (r.name || r.run),
        action: () => select("cloth", r),
      })),
    ...list(state?.warp?.items)
      .filter((r) => matches(r.topics))
      .map((r) => ({
        label: "Open work " + r.title,
        action: () => select("warp", r),
      })),
  ];
  const signature = JSON.stringify([entries.map((e) => e.label), [...lifted]]);
  if (signature === lastAccess) return;
  lastAccess = signature;
  access.replaceChildren();
  for (const entry of entries) {
    const b = element("button", entry.label);
    if (entry.label.startsWith("Lift "))
      b.setAttribute("aria-pressed", String(lifted.has(entry.label.slice(5))));
    b.onclick = entry.action;
    access.append(b);
  }
}

/* ------------------------------------------------------------ ground ---- */
function paintGround() {
  const c = groundContext;
  c.setTransform(canvas.width / width, 0, 0, canvas.height / height, 0, 0);
  c.fillStyle = INK.ground;
  c.fillRect(0, 0, width, height);
  const glow = c.createRadialGradient(
    width * 0.42,
    height * 0.46,
    30,
    width * 0.42,
    height * 0.46,
    Math.max(width, height) * 0.75,
  );
  glow.addColorStop(0, INK.vignette);
  glow.addColorStop(0.55, "#0e0b07");
  glow.addColorStop(1, "#060403");
  c.fillStyle = glow;
  c.fillRect(0, 0, width, height);
  // Faint scanlines under everything — never over the type.
  c.fillStyle = "rgba(0, 0, 0, 0.28)";
  for (let y = 0; y < height; y += 3) c.fillRect(0, y, width, 1);
  c.fillStyle = "rgba(242, 177, 52, 0.012)";
  for (let y = 1; y < height; y += 3) c.fillRect(0, y, width, 1);
}

/* ------------------------------------------------------------- scene ---- */
function drawBrand() {
  const { m, compact, right } = layout;
  const y = compact ? 30 : 38;
  const w = text("brnrd", m, y, INK.bone, compact ? 18 : 22, Infinity, {
    weight: 600,
    glow: 12,
    glowColor: "#f2b13455",
  });
  text("/ the loom", m + w + 12, y - 1, INK.faint, compact ? 10 : 11);
  const status = dev
    ? "FIXTURE · " + (replay ? "BOUNDARY REPLAY" : "ILLUSTRATIVE DATA")
    : sourceStatus.toUpperCase();
  const live = sourceStatus === "live";
  const sw = text(status, right - 20, y - 1, dev ? INK.amber : live ? INK.boneDim : INK.wall, 9, 300, {
    align: "right",
  });
  if (live) dot(right - 30 - sw, y - 4, 3, INK.receipt, 8);
  if (!layout.face && state.run) {
    const word = LEXICON[state.shuttle?.state] || known(state.shuttle?.state);
    text(`${state.run.mood_glyph || ""}  ${word}`, m + w + 110, y - 1, INK.amber, 11, right - m - w - 140 - sw);
  }
}
function drawHeddles() {
  const { m, compact, right } = layout;
  const y0 = compact ? 54 : 68,
    railW = right - m - 20;
  regionTitle("heddles", "the heddles", m, y0, railW);
  const hed = list(state.heddles),
    size = compact ? 30 : 42,
    base = compact ? 100 : 136;
  const cell = Math.min(compact ? 118 : 156, railW / Math.max(1, hed.length));
  regionRects.heddles = {
    x: m - 8,
    y: y0 - 14,
    w: railW + 16,
    h: base + (compact ? 22 : 28) - y0 + 14,
  };
  if (lifted.size) {
    const runes = hed
      .filter((h) => lifted.has(h.slug))
      .map((h) => h.rune)
      .join(" ∩ ");
    text(
      `${runes} · ${lifted.size > 1 ? "intersection" : "lifted"} · Esc drops`,
      right - 20,
      y0,
      INK.ice,
      10,
      300,
      { align: "right" },
    );
  }
  hed.forEach((h, i) => {
    const x = m + i * cell,
      raised = lifted.has(h.slug),
      lit = clamp(h.lit),
      c = color(h.slug);
    const y = base - (raised ? 8 : 0);
    runeRects.set(h.slug, { x: x - 6, y: y - size - 2, w: size + 12, h: size + 10, cx: x + size / 2, cy: base - size / 2 });
    g.save();
    g.globalAlpha = lit > 0.05 ? 0.45 + lit * 0.55 : 0.55;
    text(h.rune || "?", x, y, lit > 0.05 ? c : INK.dark, size, Infinity, {
      glow: lit > 0.05 ? 4 + lit * 16 : 0,
    });
    g.restore();
    const label = `${i < 6 ? i + 1 + " " : ""}${h.slug.replace(/^the-/, "")}`;
    text(
      label,
      x + 1,
      base + (compact ? 14 : 17),
      raised ? INK.bone : lit > 0.05 ? INK.boneDim : INK.faint,
      compact ? 9 : 10,
      cell - 14,
    );
    if (raised)
      strokePath(
        [
          { x, y: base + (compact ? 21 : 25) },
          { x: x + Math.min(cell - 22, 90), y: base + (compact ? 21 : 25) },
        ],
        c,
        2,
        1,
        8,
      );
    hit(x - 6, base - size - 12, cell - 4, size + 36, "heddle", h.slug, "Lift " + h.slug);
  });
  if (!hed.length) text("No heddles yet", m, base - 10, INK.faint, 11);
  canvas.dataset.runes = JSON.stringify(Object.fromEntries([...runeRects].map(([k, r]) => [k, [Math.round(r.cx), Math.round(r.cy)]])));
}
function drawWarp() {
  const { m, top, warp, cloth, compact } = layout;
  const max = warp - m - 10;
  regionRects.warp = { x: m - 8, y: top - 6, w: warp - m + 10, h: cloth - top };
  text("THE WARP", m, top + 14, INK.bone, compact ? 9 : 10, max, { weight: 600 });
  const readEnd = wrap(READINGS.warp, m, top + (compact ? 27 : 30), max, compact ? 9 : 10, INK.boneDim, 2);
  let y = readEnd + (compact ? 14 : 22);
  for (const goal of list(state.warp?.goals).slice(0, compact ? 1 : 2)) {
    text("◎", m, y, INK.bone, compact ? 13 : 16);
    const end = wrap(goal.title, m + 22, y - 1, max - 22, compact ? 10 : 11, INK.bone, 2);
    hit(m - 4, y - 16, max, Math.max(30, end - y + 10), "goal", goal, goal.title);
    y = end + (compact ? 6 : 12);
  }
  const items = list(state.warp?.items).filter(
    (w) => matches(w.topics) && !["done", "retired"].includes(w.state),
  );
  const coords = new Map(),
    start = y + 8,
    step = compact ? 50 : 62;
  g.save();
  g.beginPath();
  g.rect(m - 12, start - 18, warp - m + 12, Math.max(0, cloth - start - 4));
  g.clip();
  for (const [i, item] of items.entries()) {
    y = start + i * step - warpScroll * step;
    coords.set(item.id, { x: m + 6, y });
    const c = firstColor(item.topics),
      held = item.state === "held";
    ring(m + 6, y - 4, 4, c, held ? 0.35 : 0.9, 1.2);
    if (!held) dot(m + 6, y - 4, 1.6, INK.bone);
    text(item.title, m + 20, y, held ? INK.faint : INK.bone, compact ? 10 : 11, max - 22);
    text(
      `${item.id} · ${known(item.state)}${list(item.topics).length ? " · " + item.topics[0].replace(/^the-/, "") : ""}`,
      m + 20,
      y + (compact ? 14 : 16),
      held ? INK.dark : INK.faint,
      9,
      max - 22,
    );
    if (y >= start - 10 && y < cloth - 20)
      hit(m - 2, y - 14, max, step - 8, "warp", item, item.title);
  }
  for (const item of items)
    for (const need of list(item.needs)) {
      const a = coords.get(item.id),
        b = coords.get(need);
      if (a && b) {
        line(m - 4, a.y - 4, m - 4, b.y - 4, INK.dark, 0.9);
        line(m - 4, a.y - 4, m + 1, a.y - 4, INK.dark, 0.9);
        line(m - 4, b.y - 4, m + 1, b.y - 4, INK.dark, 0.9);
      }
    }
  g.restore();
  const overflow = items.length - Math.floor((cloth - start) / step);
  if (overflow > 0 && warpScroll === 0)
    text(`+${overflow} below · wheel`, m + 20, cloth - 8, INK.faint, 9, max);
  if (!items.length) text("No work in this shed", m, start, INK.faint, 11, max);
  line(warp + 4, top, warp + 4, cloth - 12, INK.darker);
}
function drawTree() {
  const { wx, ww, top, compact } = layout,
    T = layout.tree;
  regionRects.window = { x: wx - 6, y: top - 6, w: ww + 12, h: layout.cloth - top };
  regionTitle("window", "the window", wx, top + 14, ww, { readingWidth: ww - 280 });
  const boundaries = list(state.beads).filter((b) => matches(b.topics)).length;
  text(
    `${shownCount} places${hiddenCount ? ` · +${hiddenCount} dim` : ""} · ${boundaries} boundaries`,
    wx + ww,
    top + 14,
    INK.boneDim,
    compact ? 9 : 10,
    280,
    { align: "right" },
  );
  if (hiddenCount)
    text("hover a branch or lift a rune for the rest", wx + ww, top + (compact ? 27 : 30), INK.faint, 9, 280, {
      align: "right",
    });
  bezel(T.x, T.y, T.w, T.h, { fill: false, edge: "#1d1913" });
  g.save();
  g.beginPath();
  g.rect(T.x, T.y, T.w, T.h);
  g.clip();
  const focusPaths = focusedPaths();
  const inFocus = (node) =>
    node.root ||
    node.home ||
    node.fixed ||
    [...focusPaths].some((path) => path === node.path || path.startsWith(node.path + "/"));
  const visited = new Set(actorHistory());
  const root = point(""),
    homeRoot = layout.hasHome ? point("home:") : null;
  // The weft: both roots stand on it; the trunk runs down into the cloth.
  if (root) strokePath([{ x: root.x, y: root.y }, { x: root.x, y: T.y + T.h }], "#5d513d", 1.4, 0.9);
  if (homeRoot && root) {
    strokePath([{ x: T.x, y: root.y }, { x: T.x + T.w, y: root.y }], INK.darker, 1, 1);
    strokePath([homeRoot, root], "#5d513d", 1.4, 0.9);
    strokePath([{ x: homeRoot.x, y: homeRoot.y }, { x: homeRoot.x, y: T.y + T.h }], "#4a5a63", 1.2, 0.7);
  }
  for (const node of treeNodes) {
    const p = point(node.path);
    if (!p || !node.parent) continue;
    const parent = point(node.parent.path);
    if (!parent) continue;
    if (node.fixed) {
      g.save();
      g.setLineDash([2, 5]);
      strokePath(edgePoints(parent, p), visited.has(node.path) ? "#6b5a3a" : INK.dark, 1, 0.8);
      g.restore();
      continue;
    }
    const lit = inFocus(node);
    strokePath(
      edgePoints(parent, p),
      node.home ? "#3f5260" : lit ? "#5d513d" : INK.dark,
      lit ? 1.3 : 1,
      node.home ? 0.8 : lit ? 0.95 : 0.6,
    );
  }
  for (const node of treeNodes) {
    const p = point(node.path);
    if (!p) continue;
    if (node.fixed) {
      const on = actor?.path === node.path,
        seen = visited.has(node.path),
        size = 22;
      g.save();
      g.fillStyle = "#0f0c08";
      g.fillRect(p.x - size / 2, p.y - size / 2, size, size);
      g.strokeStyle = on ? INK.amber : seen ? "#6b5a3a" : "#3a3328";
      g.strokeRect(p.x - size / 2 + 0.5, p.y - size / 2 + 0.5, size - 1, size - 1);
      g.restore();
      text(FIXED_GLYPH[node.fixed], p.x, p.y + 4, on || seen ? INK.bone : INK.faint, 12, Infinity, { align: "center" });
      text(node.fixed, p.x, p.y + size / 2 + 12, on || seen ? INK.boneDim : INK.faint, 9, Infinity, { align: "center" });
      if (node.fixed === "forge") {
        // PRs of the passes in focus: knots that are PRs, dropped at the forge.
        const prs = prsInView();
        prs.slice(-8).forEach((n, i) => diamond(p.x - 24 + (i % 8) * 7, p.y + size / 2 + 22, 2.4, INK.ice, 0.85));
        if (prs.length > 8) text(`+${prs.length - 8}`, p.x + 34, p.y + size / 2 + 25, INK.ice, 8);
        regionRects.forge = { x: p.x - 34, y: p.y - 18, w: 72, h: 56 };
      }
      hit(p.x - 18, p.y - 16, 36, 44, "place", { path: node.path, kind: node.fixed, heat: null, knots: null, topics: [] }, node.fixed);
      continue;
    }
    const focused = inFocus(node),
      heat = node.place ? clamp(node.place.heat) : 0.35,
      c = node.home ? INK.ice : !node.root ? firstColor(node.topics) : INK.bone;
    const radius = (node.root ? 4.5 : node.place ? 2.2 + heat * 3.2 : 2.2) * (node.home ? 0.8 : 1);
    g.globalAlpha = focused ? 1 : 0.4;
    // Bloom twice: the hue, thin, then the bone core. Home is ice-tinted.
    if (!node.root) dot(p.x, p.y, radius + 1.2, c, 12 + heat * 6);
    dot(p.x, p.y, radius, node.home ? "#cfe9f7" : node.place?.heat == null && !node.root ? INK.faint : INK.bone);
    if (node.place?.knots >= 2) diamond(p.x + 8, p.y + 8, 2.4, INK.ice, focused ? 0.55 : 0.3);
    if (node.label) {
      const dir = node.children.size > 0;
      const fill = node.home
        ? node.root
          ? INK.ice
          : "rgba(169, 203, 224, 0.8)"
        : node.root
          ? INK.bone
          : dir
            ? INK.boneDim
            : `rgba(232, 220, 192, ${(focused ? 0.55 + heat * 0.45 : 0.4).toFixed(2)})`;
      text(node.label.text, p.x + node.label.dx, p.y + node.label.dy, fill, node.label.size, Infinity, {
        weight: node.root || dir ? 500 : 400,
      });
      if (dir && node.hiddenBelow > 0 && node.label.dx < 0)
        text(`+${node.hiddenBelow}`, p.x + node.label.dx - 4, p.y + node.label.dy, INK.faint, 9, Infinity, {
          align: "right",
        });
    }
    g.globalAlpha = 1;
    const hx = node.label ? Math.min(p.x - 8, p.x + node.label.dx) : p.x - 8,
      hw = node.label ? Math.max(16, node.label.w + Math.abs(node.label.dx) + 8) : 16;
    if (node.place) hit(hx, p.y - 16, hw, 28, "place", node.place, node.path);
    else if (!node.root) hit(p.x - 10, p.y - 10, 20, 20, "branch", node, node.path);
    if (selected.kind === "place" && selected.data?.path === node.path) ring(p.x, p.y, radius + 8, INK.ice, 0.9);
  }
  g.restore();
  if (!treeNodes.some((n) => n.place))
    text(
      lifted.size ? "No places share these layers" : "No measured places yet",
      T.x + 24,
      T.y + 40,
      INK.faint,
      12,
      T.w - 30,
    );
  const focused = railLayout().find((r) => r.focus);
  if (focused && groupPlaces(focused) === null)
    text("Place history not attested for this pass", T.x + 12, T.y + 18, INK.faint, 9, T.w);
}
function drawFace() {
  const f = layout.face;
  if (!f) return;
  const { compact } = layout,
    full = state.hud?.full || {},
    resources = full.resources || {};
  regionRects.face = { x: f.x - 4, y: f.y - 4, w: f.w + 8, h: f.h + 8 };
  bezel(f.x, f.y, f.w, f.h);
  const px = f.x + 18,
    pw = f.w - 36;
  regionTitle("face", "the face", px, f.y + (compact ? 22 : 26), pw);
  // The glyph itself animates on the live layer; its plate is here.
  const faceBox = {
    x: px,
    y: f.y + (compact ? 40 : 50),
    w: compact ? 118 : 158,
    h: compact ? 58 : 82,
  };
  regionRects.faceGlyph = faceBox;
  g.save();
  g.strokeStyle = "#2a241b";
  g.strokeRect(faceBox.x + 0.5, faceBox.y + 0.5, faceBox.w, faceBox.h);
  g.restore();
  hit(faceBox.x, faceBox.y, faceBox.w, faceBox.h, "run", null, "the resident");
  const shuttleState = state.shuttle?.state,
    word = LEXICON[shuttleState] || known(shuttleState);
  const wx = faceBox.x + faceBox.w + 16,
    ww = f.x + f.w - 18 - wx;
  const wordY = faceBox.y + (compact ? 24 : 32);
  text(word, wx, wordY, INK.bone, compact ? 19 : 26, ww, {
    weight: 500,
    glow: 14,
    glowColor: "#f2b13466",
  });
  regionRects.faceWord = { x: wx - 4, y: wordY - (compact ? 20 : 26), w: Math.min(ww, 260), h: compact ? 26 : 34 };
  const since = state.shuttle?.since,
    sinceMin =
      since && state.at ? Math.max(0, Math.round((Date.parse(state.at) - Date.parse(since)) / 60000)) : null;
  text(
    `${state.run?.mood ? state.run.mood + " · " : ""}since ${since ? since.slice(11, 16) : "?"}${sinceMin == null ? "" : " · " + sinceMin + "m"}`,
    wx,
    wordY + (compact ? 16 : 20),
    INK.boneDim,
    compact ? 9 : 10,
    ww,
  );
  // A lamp per hold kind. Ice = at the shed (the user's turn); amber = the
  // resident's own parks; red = a wall.
  const hold = full.resource_hold,
    wait = full.await || {};
  const resume = hold?.active ? hold.resume || hold.kind : null;
  const lamps = [
    ["await", wait.armed && !wait.resolved, INK.ice],
    ["operator", resume === "operator", INK.amber],
    ["strands", resume === "strands", INK.amber],
    ["any", resume === "any", INK.amber],
    ["wall", resume === "refill" || resume === "reset", INK.wall],
  ];
  const lampY = faceBox.y + faceBox.h - (compact ? 6 : 8),
    litLamp = lamps.findIndex(([, on]) => on),
    lampStep = compact ? 14 : Math.min(62, ww / lamps.length);
  lamps.forEach(([label, on, hue], i) => {
    const lx = wx + 4 + i * lampStep + (compact && litLamp >= 0 && i > litLamp ? 56 : 0);
    if (on) dot(lx, lampY - 3, 3.2, hue, 10);
    else ring(lx, lampY - 3, 3, INK.dark, 1, 1.2);
    if (!compact || on) text(label, lx + 8, lampY, on ? INK.bone : INK.faint, 8, compact ? 60 : lampStep - 10);
  });
  // waiting on · the act · the correspondent
  const strandsLive = list(state.hud?.strands).filter((t) => t.status === "live").length;
  const armedAt = wait.armed_at,
    armedMin =
      armedAt && state.at ? Math.max(0, Math.round((Date.parse(state.at) - Date.parse(armedAt)) / 60000)) : null;
  const waiting = !hold?.active && !(wait.armed && !wait.resolved)
    ? shuttleState === "awake"
      ? "nothing — weaving"
      : "?"
    : resume === "refill" || resume === "reset"
      ? "a wall · " + known(hold.reason)
      : resume === "strands"
        ? "a strand"
        : `him${armedMin == null ? "" : " " + armedMin + "m"}${strandsLive ? ` · ${strandsLive} strand${strandsLive > 1 ? "s" : ""} out` : ""}`;
  const lastBead = list(state.beads).at(-1);
  const rows = [
    ["waiting on", waiting, resume === "refill" ? INK.wall : INK.bone],
    ["act", lastBead ? `${known(lastBead.act)} · ${known(lastBead.detail)}` : "unknown", INK.bone],
    [
      "correspondent",
      resources.correspondent?.summary || resources.correspondent?.note || "unknown",
      INK.boneDim,
    ],
  ];
  let y = faceBox.y + faceBox.h + (compact ? 18 : 24);
  const rowStep = compact ? 14 : 18,
    keyW = compact ? 92 : 104;
  for (const [k, v, fill] of rows) {
    text(k, px, y, INK.faint, compact ? 9 : 10, keyW - 6);
    text(v, px + keyW, y, fill, compact ? 9 : 10, pw - keyW);
    y += rowStep;
  }
  y += compact ? 0 : 2;
  line(px, y - 6, px + pw, y - 6, "#2a241b");
  // Instruments: the fuel bezel with its needle, pace, quota, ctx.
  const allowance = resources.allowance || {};
  const spent = allowance.spent ?? state.hud?.spend?.tokens,
    budget = allowance.tokens ?? state.hud?.spend?.allowance_tokens;
  const gr = compact ? 27 : 38,
    gx = px + gr + 4,
    gy = y + gr + (compact ? 6 : 12);
  regionRects.gauge = { x: gx, y: gy, r: gr, ratio: budget > 0 ? spent / budget : null };
  g.save();
  g.fillStyle = "#0d0a07";
  g.beginPath();
  g.arc(gx, gy, gr + 5, 0, Math.PI * 2);
  g.fill();
  ring(gx, gy, gr + 5, "#3b3327", 1, 1.5);
  ring(gx, gy, gr + 1, "#1f1a13", 1, 1);
  const a0 = Math.PI * 0.75,
    sweepA = Math.PI * 1.5;
  for (let i = 0; i <= 10; i++) {
    const a = a0 + (sweepA * i) / 10,
      r1 = gr - (i % 5 ? 4 : 8);
    line(gx + Math.cos(a) * r1, gy + Math.sin(a) * r1, gx + Math.cos(a) * gr, gy + Math.sin(a) * gr, i >= 9 ? INK.wall : INK.boneDim, 0.8);
  }
  g.restore();
  text("fuel", gx, gy + gr * 0.62, INK.faint, 8, Infinity, { align: "center" });
  const qx = px + pw * (compact ? 0.54 : 0.48),
    qw = pw * (compact ? 0.3 : 0.38) - 12;
  const tx = gx + gr + (compact ? 12 : 18),
    tw = qx - tx - 10;
  text(`${compactNumber(spent)} / ${compactNumber(budget)}`, tx, gy - (compact ? 6 : 10), INK.bone, compact ? 11 : 15, tw, {
    weight: 500,
  });
  text(
    budget > 0 ? `${Math.round((spent / budget) * 100)}% of allowance` : "allowance unknown",
    tx,
    gy + (compact ? 8 : 8),
    INK.boneDim,
    9,
    tw,
  );
  const pace = resources.quota?.pacing?.pace;
  text(
    "pace " +
      (pace?.ratio == null ? "?" : pace.ratio.toFixed(2) + "×") +
      (pace?.recommendation ? " · " + pace.recommendation.replaceAll("_", " ") : ""),
    tx,
    gy + (compact ? 21 : 25),
    pace?.ratio > 1 ? INK.amber : INK.faint,
    9,
    tw,
  );
  // Quota: three glowing bars, percentage left. Red only under a wall.
  const bars = [
    ["session", "session_pct_left"],
    ["week", "week_pct_left"],
    ["fable", "fable_pct_left"],
  ];
  bars.forEach(([label, key], i) => {
    const by = y + (compact ? 12 : 16) + i * (compact ? 18 : 24),
      v = state.hud?.quota?.[key];
    text(label, qx, by, INK.faint, 9, 50);
    text(v == null ? "?" : `${v}%`, qx + qw, by, v != null && v < 10 ? INK.wall : INK.bone, 9, 40, { align: "right" });
    line(qx, by + 5, qx + qw, by + 5, "#2a241b", 1, 3);
    if (v != null)
      strokePath(
        [
          { x: qx, y: by + 5 },
          { x: qx + qw * clamp(v / 100), y: by + 5 },
        ],
        v < 10 ? INK.wall : INK.bone,
        3,
        0.85,
        8,
      );
  });
  // ctx column: 50k a tick, the scroll's occupancy.
  const ctx = state.hud?.ctx_tokens,
    cx = px + pw - 10,
    colTop = y + 2,
    colBottom = y + (compact ? 60 : 84),
    ticks = 16,
    tickH = (colBottom - colTop) / ticks;
  for (let i = 0; i <= ticks; i++)
    line(cx - (i % 4 ? 3 : 6), colBottom - i * tickH, cx + 3, colBottom - i * tickH, "#2f281e");
  if (ctx != null)
    strokePath(
      [
        { x: cx + 7, y: colBottom },
        { x: cx + 7, y: colBottom - Math.min(ticks, ctx / 50000) * tickH },
      ],
      INK.amber,
      3,
      0.75,
      8,
    );
  text("ctx", cx - 8, colBottom + 12, INK.faint, 8, Infinity, { align: "center" });
  text(compactNumber(ctx), cx - 10, colTop + 8, INK.bone, 9, 60, { align: "right" });
  y = Math.max(gy + gr + (compact ? 14 : 20), colBottom + (compact ? 16 : 20));
  line(px, y - 8, px + pw, y - 8, "#2a241b");
  // Threads with fuel and ask → grant.
  threadRects.clear();
  const threadList = list(state.hud?.strands).slice(0, compact ? 2 : 3);
  for (const thread of threadList) {
    const ask = askOf(thread),
      fuel = thread.allowance > 0 ? clamp(1 - (thread.spent || 0) / thread.allowance) : null;
    const ty = y + 6;
    text("⌁", px, ty, thread.status === "live" ? INK.amber : INK.faint, 11);
    const title = String(thread.title || thread.id || "thread").split(/:\s/)[0];
    text(title, px + 16, ty, INK.bone, 9, pw - 210);
    text(known(thread.status), px + pw - 186, ty, thread.status === "live" ? INK.boneDim : INK.faint, 9, 54);
    line(px + pw - 128, ty - 3, px + pw - 84, ty - 3, "#2a241b", 1, 2);
    if (fuel !== null)
      strokePath(
        [
          { x: px + pw - 128, y: ty - 3 },
          { x: px + pw - 128 + 44 * fuel, y: ty - 3 },
        ],
        INK.amber,
        2,
        0.7,
        5,
      );
    if (ask != null) {
      g.strokeStyle = INK.ice;
      g.strokeRect(px + pw - 36.5, ty - 12.5, 36, 16);
      text("grant", px + pw - 18, ty, INK.ice, 8, 34, { align: "center" });
      hit(px + pw - 38, ty - 14, 40, 20, "command", { action: "grant" }, `ask +${compactNumber(ask)} → grant`);
      text(`+${compactNumber(ask)}`, px + pw - 40, ty, INK.ice, 8, 40, { align: "right" });
    }
    threadRects.set(thread.id, { x: px - 4, y: ty - 13, w: pw + 8, h: 18 });
    hit(px - 4, ty - 13, pw - 44, 18, "strand", thread, thread.title);
    y += compact ? 16 : 20;
  }
  if (!threadList.length) {
    text("no threads out", px, y + 6, INK.faint, 9);
    y += 20;
  }
  // Footer: pending · deliveries · course n/m · stake.
  const outbound = full.outbound,
    pending = full.attention?.pending_event_count;
  const delivered = outbound
    ? [outbound.replies_current, outbound.replies_other, outbound.outbound_messages].reduce((s, v) => s + (v || 0), 0)
    : null;
  const plan = list(state.run?.card?.plan),
    done = plan.filter((p) => p.done).length;
  const fy = f.y + f.h - (compact ? 12 : 16);
  let fx = px;
  fx += text(`pending ${known(pending)}`, fx, fy, pending > 0 ? INK.amber : INK.boneDim, 9) + 14;
  fx += text(`deliveries ${known(delivered)}`, fx, fy, INK.boneDim, 9) + 14;
  fx += text(`course ${done}/${plan.length}`, fx, fy, INK.boneDim, 9) + 6;
  const boxes = Math.min(plan.length, 16);
  for (let i = 0; i < boxes; i++) {
    g.fillStyle = i < done ? INK.receipt : "transparent";
    g.strokeStyle = i < done ? INK.receipt : INK.dark;
    g.fillRect(fx + i * 6, fy - 7, 4, 7);
    g.strokeRect(fx + i * 6 + 0.5, fy - 6.5, 3, 6);
  }
  fx += boxes * 6 + 12;
  const stake = allowance.stake;
  if (stake)
    text(
      "stake " + (stake.summary || compactNumber(stake.tokens ?? stake.budget_tokens)),
      fx,
      fy,
      INK.amber,
      9,
      f.x + f.w - 18 - fx,
    );
}
function clothTime(iso) {
  if (!iso) return "?";
  const sameDay = state.at && iso.slice(0, 10) === state.at.slice(0, 10);
  return sameDay ? iso.slice(11, 16) : iso.slice(5, 10) + " " + iso.slice(11, 16);
}
function minutesOf(row) {
  if (row.duration_s != null) return Math.round(row.duration_s / 60);
  const s = (Date.parse(row.ended || state.at) - Date.parse(row.started)) / 60000;
  return Number.isFinite(s) ? Math.max(0, Math.round(s)) : null;
}
function strandStatus(r) {
  const thread = list(state.hud?.strands).find((t) => t.id === r.run);
  if (thread?.status) return thread.status;
  return r.ended ? "ended " + clothTime(r.ended) : "open";
}
function drawRunes(topics, x, y, size, limitX) {
  let cx = x;
  for (const topic of topics) {
    const rune = list(state.heddles).find((h) => h.slug === topic)?.rune || "·";
    if (cx + size > limitX) break;
    text(rune, cx, y, color(topic), size);
    cx += size * 0.9 + 3;
  }
  return cx;
}
function drawCloth() {
  const { m, right, cloth, compact, clothH, railY } = layout,
    entries = railLayout();
  regionRects.cloth = { x: m - 8, y: cloth - 2, w: right - m + 4, h: clothH };
  regionTitle("cloth", "the cloth", m, cloth + 14, right - m - 500, { readingWidth: right - m - 40 });
  hit(right - 250, cloth + 2, 230, 18, "legend", null, "Open legend");
  text(
    `${paused ? "Ⅱ beat paused" : reduced.matches ? "reduced motion" : "600 ms / beat"} · S scan: ${scanMode} · ← → focus · ? replay · L legend · Space pause`,
    right - 20,
    cloth + 14,
    paused ? INK.amber : INK.faint,
    9,
    470,
    { align: "right" },
  );
  if (lifted.size) {
    const runes = list(state.heddles)
      .filter((h) => lifted.has(h.slug))
      .map((h) => h.rune)
      .join(" ∩ ");
    text(`lifted ${runes}`, right - 20, cloth + (compact ? 27 : 30), INK.ice, 9, 200, { align: "right" });
  }
  strokePath([{ x: m, y: railY }, { x: right - 20, y: railY }], INK.dark, 1.5, 1);
  g.save();
  g.beginPath();
  g.rect(m - 4, cloth + 34, right - m, clothH - 34);
  g.clip();
  const visible = entries.filter((e) => e.x + e.w / 2 >= m && e.x - e.w / 2 <= right - 20);
  const earlier = entries.filter((e) => e.x + e.w / 2 < m).length;
  const liveRun = state.run?.id;
  for (const entry of visible) {
    const { row, strands, focus, x, w, kind } = entry;
    const topics = list(row.topics),
      holdsLive = row.run === liveRun || strands.some((r) => r.run === liveRun);
    if (focus) {
      const x0 = x - w / 2,
        shownStrands = strands.slice(-(compact ? 2 : 3)),
        stackH = shownStrands.length ? shownStrands.length * (compact ? 12 : 14) + 6 : 0,
        ph = (compact ? 60 : 80) + stackH,
        y0 = railY - (compact ? 26 : 32);
      bezel(x0, y0, w, ph, { edge: "#3b3327" });
      strokePath([{ x: x0 + 1, y: y0 + 1 }, { x: x0 + w - 1, y: y0 + 1 }], INK.ice, 1.5, 0.9, 6);
      text(row.name || row.run, x0 + 12, y0 + (compact ? 16 : 20), INK.bone, compact ? 11 : 12, w - 24, { weight: 500 });
      let cx = x0 + 12;
      const chipY = y0 + (compact ? 22 : 28),
        chipH = compact ? 14 : 17;
      for (const topic of topics.slice(0, 4)) {
        const rune = list(state.heddles).find((h) => h.slug === topic)?.rune || "?";
        const label = topic.replace(/^the-/, "");
        g.font = font(9);
        const cw = Math.min(110, g.measureText(label).width + 26);
        if (cx + cw > x0 + w - 12) break;
        g.strokeStyle = color(topic) + "aa";
        g.strokeRect(cx + 0.5, chipY + 0.5, cw, chipH);
        text(rune, cx + 5, chipY + chipH - 4, color(topic), compact ? 11 : 12);
        text(label, cx + 19, chipY + chipH - 4, INK.bone, 9, cw - 22);
        cx += cw + 6;
      }
      if (!topics.length) text("no topic stamped", cx, chipY + chipH - 4, INK.faint, 9);
      const minutes = minutesOf(row);
      const when =
        row.run === liveRun
          ? `now · this run · ${minutes ?? "?"} min`
          : `${clothTime(row.started)} → ${row.ended ? clothTime(row.ended) : "open"} · ${minutes ?? "?"} min`;
      const prs = list(row.prs).length;
      const facts = `${known(row.shell)}/${known(row.core)} · ${known(row.knots)} knots · ${prs ? prs + " PR" + (prs > 1 ? "s" : "") : "no PR"}${strands.length ? ` · ${strands.length} strand${strands.length > 1 ? "s" : ""}` : ""}`;
      const by = y0 + (compact ? 50 : 66);
      const ww = text(when, x0 + 12, by, row.run === liveRun ? INK.amber : INK.bone, compact ? 9 : 10, w * 0.5);
      text(facts, x0 + 24 + ww, by, INK.faint, 9, w - ww - 36);
      hit(x0, y0, w, by - y0 + 6, "cloth", row, row.name || row.run);
      // Its strands, stacked under it.
      let sy = by + (compact ? 14 : 18);
      if (shownStrands.length) line(x0 + 12, sy - (compact ? 9 : 11), x0 + w - 12, sy - (compact ? 9 : 11), "#2a241b");
      const extra = strands.length - shownStrands.length;
      for (const r of shownStrands) {
        const st = strandStatus(r),
          on = r.run === liveRun || st === "live";
        text("⌁", x0 + 14, sy, on ? INK.amber : INK.faint, 10);
        text(String(r.name || r.run).split(/:\s/)[0], x0 + 28, sy, on ? INK.bone : INK.boneDim, 9, w - 150);
        text(st, x0 + w - 12, sy, on ? INK.boneDim : INK.faint, 9, 110, { align: "right" });
        hit(x0 + 8, sy - 10, w - 16, compact ? 12 : 14, "cloth", r, r.name || r.run);
        sy += compact ? 12 : 14;
      }
      // The facts line counts every strand; the stack shows the latest.
      void extra;
    } else if (kind === "mini") {
      const mh = compact ? 28 : 34,
        y0 = railY - mh / 2,
        x0 = x - w / 2;
      g.save();
      g.fillStyle = "rgba(16,13,9,0.92)";
      g.fillRect(x0, y0, w, mh);
      g.strokeStyle = holdsLive ? "#7a5a22" : "#2e271d";
      g.strokeRect(x0 + 0.5, y0 + 0.5, w - 1, mh - 1);
      g.restore();
      const alpha = 0.55 + 0.45 * entry.scale;
      g.globalAlpha = alpha;
      text(row.name || row.run, x0 + 5, y0 + (compact ? 11 : 13), INK.bone, compact ? 8 : 9, w - 10);
      const rx = drawRunes(topics.slice(0, 3), x0 + 5, y0 + mh - 5, compact ? 9 : 10, x0 + w - 18);
      if (!topics.length) text("·", x0 + 5, y0 + mh - 5, INK.faint, 9);
      if (strands.length)
        text(`⌁${strands.length}`, x0 + w - 5, y0 + mh - 5, holdsLive ? INK.amber : INK.faint, 8, 30, { align: "right" });
      else if (list(row.prs).length) diamond(x0 + w - 8, y0 + mh - 8, 2.5, INK.ice, 0.7);
      g.globalAlpha = 1;
      void rx;
      hit(x0, y0, w, mh, "cloth", row, row.name || row.run);
    } else {
      const r = 3;
      dot(x, railY, 1.6, holdsLive ? INK.amber : INK.boneDim);
      topics.forEach((topic, i) => {
        g.strokeStyle = color(topic);
        g.lineWidth = 1.2;
        g.beginPath();
        g.arc(x, railY, r + 1.5, (i * Math.PI * 2) / topics.length + 0.15, ((i + 1) * Math.PI * 2) / topics.length - 0.15);
        g.stroke();
      });
      // Strands as satellites on the collapsed halo.
      strands.slice(0, 5).forEach((_, i) => dot(x + Math.cos(-Math.PI / 2 + i * 1.2) * 7, railY + Math.sin(-Math.PI / 2 + i * 1.2) * 7, 0.9, INK.faint));
      hit(x - 6, railY - 12, 12, 24, "cloth", row, row.name || row.run);
    }
  }
  g.restore();

  // Time reads: three labels at most — the start, a middle, now.
  const focusEntry = entries.find((e) => e.focus);
  const small = visible.filter((e) => !e.focus);
  const picks = [];
  if (small.length) picks.push(small[0]);
  if (small.length > 2) picks.push(small[Math.floor(small.length / 2)]);
  const liveEntry = visible.find((e) => e.row.run === liveRun || e.strands.some((r) => r.run === liveRun));
  if (liveEntry && !liveEntry.focus) picks.push(liveEntry);
  else if (small.length > 1) picks.push(small.at(-1));
  const labelY = railY + (compact ? 26 : 32);
  let lastEnd = earlier ? m + text(`← +${earlier} earlier`, m, labelY, INK.faint, 9) : -Infinity;
  for (const e of picks) {
    const label = e === liveEntry ? "now" : clothTime(e.row.started);
    g.font = font(9);
    const lw = g.measureText(label).width;
    const lx = Math.max(m, Math.min(right - 20 - lw, e.x - lw / 2));
    const overPlaque = focusEntry && lx + lw > focusEntry.x - focusEntry.w / 2 - 6 && lx < focusEntry.x + focusEntry.w / 2 + 6;
    if (lx < lastEnd + 24 || overPlaque) continue;
    line(e.x, railY + (compact ? 15 : 18), e.x, labelY - 10, INK.dark);
    text(label, lx, labelY, label === "now" ? INK.amber : INK.boneDim, 9);
    lastEnd = lx + lw;
  }
  if (!entries.length) text("No passes in this shed", m, railY + 4, INK.faint, 11);
}
function drawFooter() {
  const { m, right, consoleH } = layout;
  const y = height - consoleH - 8;
  if (sourceStatus !== "live" && sourceStatus !== "fixture" && state)
    text(sourceStatus.toUpperCase() + " · LAST RECEIVED " + known(state.at), layout.wx, y, INK.wall, 9, layout.ww - 380);
}
function paintScene() {
  g.clearRect(0, 0, width, height);
  if (state) {
    drawBrand();
    if (isRevealed("heddles")) drawHeddles();
    if (isRevealed("warp")) drawWarp();
    if (isRevealed("window")) drawTree();
    if (isRevealed("cloth")) drawCloth();
    if (isRevealed("face")) drawFace();
    drawFooter();
  } else {
    text("brnrd / the loom", 28, 42, INK.bone, 23);
    text(
      sourceStatus === "connecting" ? "Waiting for the frame…" : sourceStatus,
      28,
      86,
      INK.faint,
    );
  }
}

/* -------------------------------------------------------------- live ---- */
/* The scan lives on the cloth — the weft is time; the tree has no time axis.
 * One traverse per heartbeat tick. Crossing a pass pulses its plaque and
 * raises a hairline thread from it into the tree, through that run's trail
 * places in order. Nothing on the tree lights unless a thread reaches it. */
const SCAN_FALLBACK_MS = 10000;
const SCAN_MODES = ["up", "root", "cloth", "off"];
// Default `up`; ?scan= selects, S cycles, the choice persists.
let scanMode = (() => {
  const asked = params.get("scan");
  if (SCAN_MODES.includes(asked)) return asked;
  try {
    const kept = localStorage.getItem("loom.scan");
    if (SCAN_MODES.includes(kept)) return kept;
  } catch {}
  return "up";
})();
function setScanMode(mode) {
  scanMode = mode;
  scan.prevX = scan.prevY = scan.prevR = null;
  trailLit.clear();
  nodePings.clear();
  sceneDirty = true;
  try {
    localStorage.setItem("loom.scan", mode);
  } catch {}
}
const nodePings = new Map();
const scan = { tick: null, tickAt: null, period: SCAN_FALLBACK_MS, prevX: null, prevY: null, prevR: null };
const trailLit = new Map();
let tickPulse = null;
function noteTick(next) {
  const tick = next?.shuttle?.tick ?? list(next?.shuttle?.transitions).at(-1)?.tick;
  if (tick == null || tick === scan.tick) return;
  if (scan.tickAt != null) scan.period = Math.max(2000, Math.min(60000, clock - scan.tickAt));
  scan.tick = tick;
  scan.tickAt = clock;
  tickPulse = { born: clock };
}
function scanPhase() {
  return scan.tickAt != null
    ? clamp((clock - scan.tickAt) / scan.period)
    : (((clock % scan.period) + scan.period) % scan.period) / scan.period;
}
function scanX() {
  const { m, right } = layout;
  return m + scanPhase() * (right - 20 - m);
}
function pingNode(path) {
  nodePings.set(path, clock);
  if (nodePings.size > 240) nodePings.delete(nodePings.keys().next().value);
}
// A node lights as the front crosses it, and decays behind.
function drawNodePings() {
  const T = layout.tree;
  g.save();
  g.beginPath();
  g.rect(T.x, T.y, T.w, T.h);
  g.clip();
  let lit = 0;
  for (const [path, at] of nodePings) {
    const age = (clock - at) / 450;
    if (age >= 1) {
      nodePings.delete(path);
      continue;
    }
    const p = point(path);
    if (!p) continue;
    lit++;
    const node = treeNodes.find((n) => n.path === path);
    const hue = node?.home ? INK.ice : INK.amber;
    g.globalAlpha = (1 - age) * 0.8;
    dot(p.x, p.y, 2.4, hue, 10);
    g.globalAlpha = 1;
    ring(p.x, p.y, 3 + age * 10, hue, (1 - age) * 0.5, 1);
  }
  g.restore();
  canvas.dataset.pings = String(lit);
}
// The lowest place a run's trail touches: where an upward front meets it.
function trailFloor(entry) {
  let y = -Infinity;
  for (const row of [entry.row, ...entry.strands])
    for (const path of trailPaths(row, false)) {
      const p = targetPositions.get(path);
      if (p && p.y > y) y = p.y;
    }
  return y;
}
// A run's trail: the feed's `trail` when it lands; until then a live
// strand's places, the seat's last eight, a row's attested places, or the
// places of the beads its pass page attests (asked once, on first crossing).
function trailPaths(row, ask = true) {
  if (Array.isArray(row.trail))
    return row.trail
      .map((p) => (typeof p === "string" ? p : p?.path ? (p.kind === "home" ? "home:" + p.path : p.path) : null))
      .filter(Boolean);
  if (row.run === state.run?.id) return actorHistory();
  const thread = list(state.hud?.strands).find((t) => t.id === row.run);
  if (list(thread?.places).length) return list(thread.places);
  if (Array.isArray(row.places)) return row.places.map((p) => (typeof p === "string" ? p : p.path)).filter(Boolean);
  const body = ask ? page("pass", row.run, { quiet: true }) : null;
  const out = [];
  for (const b of list(body?.beads).slice().reverse()) {
    const path = beadPath(b);
    if (path && out.at(-1) !== path) out.push(path);
  }
  return out.slice(-8);
}
function trailColor(row, entry) {
  const seat =
    row.run === state.run?.id ||
    (entry && !entry.row.parent && entry.strands.some((r) => r.run === state.run?.id) && row === entry.row);
  if (seat) return INK.amber;
  const topic = list(row.topics)[0];
  return topic ? color(topic) : INK.boneDim;
}
// At a heartbeat's pace a traverse crosses every group, so the tree would
// carry twenty threads at once: the newest six stay, the rest let go.
const THREADS_AT_ONCE = 6;
function raise(entry) {
  for (const row of [entry.row, ...entry.strands]) {
    trailLit.delete(row.run);
    trailLit.set(row.run, {
      at: clock,
      paths: trailPaths(row),
      color: trailColor(row, entry),
      x: entry.x,
      w: entry.w,
      focus: entry.focus,
    });
  }
  for (const run of [...trailLit.keys()].slice(0, Math.max(0, trailLit.size - THREADS_AT_ONCE))) trailLit.delete(run);
}
function lightCrossed(x0, x1) {
  for (const entry of railLayout()) if (entry.x >= x0 && entry.x <= x1) raise(entry);
}
function prefix(points, fraction) {
  if (fraction >= 1) return points;
  const lengths = points.slice(1).map((p, i) => Math.hypot(p.x - points[i].x, p.y - points[i].y));
  const total = lengths.reduce((a, b) => a + b, 0);
  let want = total * clamp(fraction);
  const out = [points[0]];
  for (let i = 0; i < lengths.length; i++) {
    if (want <= lengths[i]) {
      const t = lengths[i] ? want / lengths[i] : 1;
      out.push({ x: points[i].x + (points[i + 1].x - points[i].x) * t, y: points[i].y + (points[i + 1].y - points[i].y) * t });
      return out;
    }
    want -= lengths[i];
    out.push(points[i + 1]);
  }
  return out;
}
// A hairline: 1 px, dots r 2, never more than 0.7 alpha.
function hairline(points, stroke, alpha, dotsFrom = 1) {
  strokePath(points, stroke, 1, Math.min(0.7, alpha), 4);
  points.slice(dotsFrom).forEach((p) => {
    g.globalAlpha = Math.min(0.7, alpha);
    dot(p.x, p.y, 2, stroke, 6);
    g.globalAlpha = 1;
  });
}
function threadPoints(t) {
  const up = t.paths.map((path) => point(path)).filter(Boolean);
  return up.length ? [{ x: t.x, y: layout.railY - 8 }, ...up] : [];
}
function drawRisenThreads() {
  let lit = 0;
  const T = layout.tree;
  g.save();
  g.beginPath();
  g.rect(layout.m, T.y, layout.right - layout.m, layout.railY - T.y);
  g.clip();
  // At rest the seat keeps a faint hairline through its last places.
  const seatTrail = actorHistory().map((p) => point(p)).filter(Boolean);
  if (seatTrail.length > 1) hairline(seatTrail, INK.amber, 0.18, 0);
  if (scanMode === "off" || reduced.matches) {
    // No traverse: a plaque shows its thread on hover.
    const row = hovered?.kind === "cloth" ? hovered.data : null;
    const entry = row ? railLayout().find((e) => e.row.run === row.run || e.strands.some((r) => r.run === row.run)) : null;
    if (row && entry) {
      const points = threadPoints({ x: entry.x, paths: trailPaths(row, false) });
      if (points.length > 1) {
        hairline(points, trailColor(row, entry), 0.7);
        lit++;
      }
    }
  } else {
    for (const [run, t] of trailLit) {
      const age = clock - t.at;
      const rise = Math.min(1, age / 400);
      const fade = age <= 400 ? 0.7 : 0.7 * Math.max(0, 1 - (age - 400) / Math.max(400, scan.period - 400));
      if (fade <= 0.02) {
        trailLit.delete(run);
        continue;
      }
      const points = threadPoints(t);
      if (points.length < 2) continue;
      hairline(prefix(points, rise), t.color, fade);
      lit++;
    }
  }
  g.restore();
  canvas.dataset.trailsLit = String(lit);
  canvas.dataset.scan = scanMode;
}
let warpFlash = null;
function drawScanCloth() {
  if (reduced.matches || !isRevealed("cloth")) return;
  const x = scanX(),
    // Over the warp column the line runs its full height: it is the topmost
    // thing wherever it exists, and nothing it passes may occlude it.
    overWarp = x <= layout.warp + 6,
    y0 = overWarp ? layout.top - 6 : layout.cloth + 34,
    y1 = layout.railY + (layout.compact ? 20 : 26);
  if (!paused) {
    if (scan.prevX != null && scan.prevX < layout.m + 6 && x >= layout.m + 6) {
      // Entering: the warp's threads flicker once as the line touches them.
      warpFlash = { born: clock };
      glitch(() => regionRects.warp, 140);
    }
    if (scan.prevX != null && x >= scan.prevX) lightCrossed(scan.prevX, x);
    else if (scan.prevX != null) {
      lightCrossed(scan.prevX, Infinity);
      lightCrossed(-Infinity, x);
    }
    scan.prevX = x;
  }
  // The wake tints what it just crossed for ~200 ms.
  if (warpFlash) {
    const age = (clock - warpFlash.born) / 200;
    if (age < 1) {
      const w = regionRects.warp;
      if (w) {
        g.fillStyle = `rgba(242,177,52,${(0.12 * (1 - age)).toFixed(3)})`;
        g.fillRect(w.x, w.y, w.w, w.h);
      }
    } else warpFlash = null;
  }
  for (const t of trailLit.values()) {
    const age = (clock - t.at) / 200;
    if (age >= 1 || !t.w) continue;
    const h = t.focus ? (layout.compact ? 88 : 116) : layout.compact ? 28 : 34;
    g.fillStyle = `rgba(242,177,52,${(0.14 * (1 - age)).toFixed(3)})`;
    g.fillRect(t.x - t.w / 2, layout.railY - (t.focus ? (layout.compact ? 26 : 32) : h / 2), t.w, h);
  }
  canvas.dataset.scanX = String(Math.round(x));
  const wake = g.createLinearGradient(x - 70, 0, x, 0);
  wake.addColorStop(0, "rgba(242,177,52,0)");
  wake.addColorStop(1, "rgba(242,177,52,0.1)");
  g.fillStyle = wake;
  g.fillRect(x - 70, y0, 70, y1 - y0);
  strokePath([{ x, y: y0 }, { x, y: y1 }], INK.amber, 1, 0.7, 6);
  // The plaque it just crossed pulses once.
  for (const t of trailLit.values()) {
    const age = (clock - t.at) / 300;
    if (age >= 1) continue;
    ring(t.x, layout.railY, 6 + age * 14, INK.amber, (1 - age) * 0.7, 1);
  }
}
function weftY() {
  const root = point("") || { y: layout.tree.y + layout.tree.h - 30 };
  return root.y;
}
// `up` — a wavefront rising from the weft to the top of the window: what it
// measures is distance from the root.
function drawScanUp() {
  const T = layout.tree,
    phase = scanPhase(),
    base = weftY(),
    y = base - (base - T.y) * phase;
  if (!paused && scan.prevY != null && y < scan.prevY) {
    for (const node of treeNodes) {
      if (node.root) continue;
      const p = targetPositions.get(node.path);
      if (p && p.y <= scan.prevY && p.y > y) pingNode(node.path);
    }
    // A run's thread rises when the front reaches its trail's lowest place.
    for (const entry of railLayout()) {
      if (entry.x < layout.m || entry.x > layout.right - 20) continue;
      const floor = trailFloor(entry);
      if (floor > -Infinity && floor <= scan.prevY && floor > y) raise(entry);
    }
  }
  if (!paused) scan.prevY = y;
  g.save();
  g.beginPath();
  g.rect(T.x, T.y, T.w, T.h);
  g.clip();
  const wake = g.createLinearGradient(0, y + 60, 0, y);
  wake.addColorStop(0, "rgba(242,177,52,0)");
  wake.addColorStop(1, "rgba(242,177,52,0.1)");
  g.fillStyle = wake;
  g.fillRect(T.x, y, T.w, 60);
  strokePath([{ x: T.x, y }, { x: T.x + T.w, y }], INK.amber, 1, 0.7, 6);
  g.restore();
  canvas.dataset.scanY = String(Math.round(y));
}
// `root` — the same front as a half-circle expanding from the root.
function drawScanRoot() {
  const T = layout.tree,
    phase = scanPhase(),
    repo = point("") || { x: T.x + T.w / 2, y: T.y + T.h - 30 },
    home = layout.hasHome ? point("home:") : null;
  const reach = Math.hypot(T.w, T.h),
    r = reach * phase;
  const dist = (p, o) => Math.hypot(p.x - o.x, p.y - o.y);
  if (!paused && scan.prevR != null && r > scan.prevR) {
    for (const node of treeNodes) {
      if (node.root) continue;
      const p = targetPositions.get(node.path);
      if (!p || p.y > (node.home && home ? home.y : repo.y) + 4) continue;
      const d = dist(p, node.home && home ? home : repo) * (node.home ? 1 / 0.6 : 1);
      if (d > scan.prevR && d <= r) pingNode(node.path);
    }
  }
  if (!paused) scan.prevR = r;
  g.save();
  g.beginPath();
  g.rect(T.x, T.y, T.w, T.h);
  g.clip();
  for (const [origin, hue, scale] of [
    [repo, INK.amber, 1],
    ...(home ? [[home, INK.ice, 0.6]] : []),
  ]) {
    const rr = r * scale;
    g.save();
    g.globalAlpha = 0.6;
    g.strokeStyle = hue;
    g.lineWidth = 1;
    g.shadowColor = hue;
    g.shadowBlur = 6;
    g.beginPath();
    g.arc(origin.x, origin.y, Math.max(1, rr), Math.PI, Math.PI * 2);
    g.stroke();
    g.restore();
    // A short wake inside the front.
    const wake = g.createRadialGradient(origin.x, origin.y, Math.max(1, rr - 46), origin.x, origin.y, Math.max(2, rr));
    wake.addColorStop(0, "rgba(242,177,52,0)");
    wake.addColorStop(1, hue === INK.ice ? "rgba(143,211,255,0.08)" : "rgba(242,177,52,0.09)");
    g.fillStyle = wake;
    g.beginPath();
    g.arc(origin.x, origin.y, Math.max(2, rr), Math.PI, Math.PI * 2);
    g.fill();
  }
  g.restore();
  canvas.dataset.scanR = String(Math.round(r));
}
function drawScan() {
  if (scanMode === "off" || reduced.matches) return;
  if (scanMode === "cloth") drawScanCloth();
  else {
    if (scanMode === "up") drawScanUp();
    else drawScanRoot();
    drawNodePings();
  }
}
function blockRect(i) {
  const a = actorPoint();
  if (!a || !state?.run) return null;
  const gw = regionRects.actorGlyphW || 44;
  const T = layout.tree,
    gx = Math.max(T.x + gw / 2 + 6, Math.min(T.x + T.w - gw / 2 - 48, a.x));
  return { x: gx + gw / 2 + 6, y: a.y - 26 - i * 6, w: 40, h: 8 };
}
function drawTrail() {
  // The trace: the light the walk leaves on the line, fading after arrival.
  if (walk.route.length && !reduced.matches) {
    const t = (clock - walk.born) / walk.dur,
      fade = 1 - clamp((clock - walk.born - walk.dur) / 900);
    if (fade > 0) {
      const head = Math.max(2, Math.round(walk.route.length * clamp(t)));
      strokePath(walk.route.slice(0, head), INK.amber, 2.2, 0.9 * fade, 14);
    }
  }
}
function drawActor(pulse) {
  const a = actorPoint();
  regionRects.actor = null;
  if (!a || !state.run) return;
  const T = layout.tree;
  const bob = reduced.matches ? 0 : (pulse - 0.5) * 3;
  const gy = a.y - 15 + bob;
  // The brightest thing on screen: the resident's own light.
  halo(a.x, a.y - 8, 86, INK.amber, 0.22);
  halo(a.x, a.y - 8, 34, INK.amber, 0.38);
  dot(a.x, a.y, 3.2, "#fff1d0", 14);
  // No attested face ⇒ the shuttle's sign, never a word dressed as a face.
  const glyph = state.run.mood_glyph || "⌁";
  const sprite = glowSprite(glyph, 15, "#ffd27a", 14);
  const gw = sprite.w;
  // The glyph stays inside the window even at an edge place.
  const gx = Math.max(T.x + gw / 2 + 6, Math.min(T.x + T.w - gw / 2 - 48, a.x));
  drawSprite(sprite, gx, gy, 15);
  regionRects.actorGlyphW = gw;
  const box = { x: gx - gw / 2 - 8, y: gy - 17, w: gw + 16, h: 24 };
  regionRects.actor = box;
  hit(box.x, box.y, box.w, box.h, "actor", null, "the resident");
  // The context stack: one small glowing bar per recent block.
  const blocks = list(state.beads)
    .filter((b) => matches(b.topics))
    .slice(-6);
  blocks.reverse().forEach((b, i) => {
    const x = gx + gw / 2 + 8,
      y = a.y - 24 - i * 6,
      w = 8 + clamp((b.delta || 0) / 20000) * 26;
    strokePath([{ x, y }, { x: x + w, y }], INK.amber, 3, 0.9 - i * 0.12, 8);
    dot(x - 3, y, 1.1, firstColor(b.topics));
    hit(x - 4, y - 3, w + 8, 6, "bead", b, `${known(b.act)} · ${known(b.detail)}`);
  });
  const since = revealAt === null ? Infinity : clock - revealAt - REVEAL.length * REVEAL_STEP;
  const showLabel = reduced.matches || since < 6000 || hovered?.kind === "actor";
  if (showLabel) {
    const label = "you are here → the resident";
    g.font = font(10);
    const lw = g.measureText(label).width;
    let lx = gx - gw / 2 - 18 - lw;
    if (lx < T.x + 8) lx = gx + gw / 2 + 60;
    const ly = gy - 18;
    g.save();
    g.fillStyle = "rgba(11,9,6,0.85)";
    g.fillRect(lx - 6, ly - 12, lw + 12, 17);
    g.restore();
    text(label, lx, ly, INK.bone, 10);
    if (lx < gx) line(lx + lw + 6, ly - 4, gx - gw / 2 - 4, gy - 6, INK.amber, 0.6);
  }
}
function drawSparks() {
  const a = actorPoint();
  if (!a) {
    sparks = [];
    return;
  }
  for (const s of sparks) {
    const t = (clock - s.born) / 700;
    if (t < 0 || t >= 1) continue;
    for (let i = 0; i < s.count; i++) {
      const ang = -Math.PI / 2 + (rand(s.seed * 13 + i) - 0.5) * Math.PI * 1.5,
        v = 50 + rand(s.seed * 7 + i * 3) * 70,
        ms = t * 0.7;
      const x = a.x + Math.cos(ang) * v * ms,
        y = a.y - 12 + Math.sin(ang) * v * ms + 0.5 * 260 * ms * ms;
      const alpha = (1 - t) * (1 - t);
      g.globalAlpha = alpha;
      dot(x, y, 1.4, i % 4 ? INK.amber : "#fff1d0", 6);
      g.globalAlpha = 1;
    }
  }
  sparks = sparks.filter((s) => clock - s.born < 700);
}
function drawThreads() {
  const T = layout.tree,
    root = point("") || { x: T.x + T.w / 2, y: T.y + T.h - 30 };
  const threads = list(state.hud?.strands).filter((thread) =>
    [...lifted].every((slug) =>
      list(list(state.heddles).find((h) => h.slug === slug)?.signature?.threads).includes(thread.id),
    ),
  );
  let loose = 0;
  threads.forEach((thread) => {
    const travel = threadTravel.get(thread.id),
      at = point(travel?.path),
      t = smooth((clock - (travel?.born || 0)) / BEAT);
    let p = at ? (reduced.matches || !travel?.route.length ? at : along(travel.route, t)) : null;
    const done = thread.status === "done";
    if (!p) {
      // No attested place: the thread fans off the trunk.
      const side = loose++ % 2 ? 1 : -1;
      p = { x: root.x + side * (60 + loose * 24), y: root.y - 8 - loose * 14 };
      strokePath([root, p], INK.amber, 1, 0.18);
    }
    const x = p.x + 10,
      y = p.y + 18,
      alpha = done ? 0.35 : 0.75;
    halo(x + 4, y - 4, 16, INK.amber, 0.12 * alpha);
    g.globalAlpha = alpha;
    text("⌁", x, y, INK.amber, 12);
    g.globalAlpha = 1;
    const tx = x + 14;
    const fuel = thread.allowance > 0 ? clamp(1 - (thread.spent || 0) / thread.allowance) : null;
    line(tx, y - 4, tx + 22, y - 4, "#2a241b", 1, 2);
    if (fuel !== null) strokePath([{ x: tx, y: y - 4 }, { x: tx + 22 * fuel, y: y - 4 }], INK.amber, 2, 0.7 * alpha, 5);
    hit(x - 4, y - 14, 44, 20, "strand", thread, String(thread.title || thread.id || "thread").split(/:\s/)[0] + " · " + known(thread.status));
  });
}
function drawFaceLive(pulse) {
  if (!isRevealed("face")) return;
  const box = regionRects.faceGlyph;
  const glyph = state.run?.mood_glyph;
  if (box) {
    const cx = box.x + box.w / 2,
      cy = box.y + box.h / 2;
    halo(cx, cy, box.w * 0.55, INK.amber, glyph ? 0.16 : 0.05);
    if (glyph) {
      // Keyframes on the beat: a slow blink every eighth beat, a small breath.
      const beat = Math.floor(clock / BEAT);
      const shown = !reduced.matches && beat % 8 === 7 && (clock % BEAT) / BEAT < 0.45 ? glyph.replace(/[·•oᴗ^]/g, (ch) => (ch === "ᴗ" ? ch : "-")) : glyph;
      // Fit the feed's glyph whole — never elide the face.
      let size = layout.compact ? 24 : 34;
      g.font = font(size, 600);
      while (size > 12 && g.measureText(glyph).width > box.w - 16) g.font = font(--size, 600);
      size *= 1 + (reduced.matches ? 0 : (pulse - 0.5) * 0.03);
      const base = Math.round(size / (1 + (reduced.matches ? 0 : (pulse - 0.5) * 0.03)));
      drawSprite(glowSprite(shown, base, "#ffd27a", 16), cx, cy + size * 0.35, size, size / base);
    } else {
      if (state.run) drawSprite(glowSprite("⌁", layout.compact ? 22 : 30, "#ffd27a", 12), cx, cy + 6, layout.compact ? 22 : 30);
      text(state.run ? "no face attested" : "no pass", cx, box.y + box.h - 8, INK.faint, 9, box.w - 8, { align: "center" });
    }
  }
  // With the scan off, the heartbeat shows on the face instead.
  if (scanMode === "off" && tickPulse && box && !reduced.matches) {
    const age = (clock - tickPulse.born) / 700;
    if (age < 1) ring(box.x + box.w / 2, box.y + box.h / 2, box.w * 0.4 + age * 26, INK.amber, (1 - age) * 0.5, 1.2);
    else tickPulse = null;
  }
  const gauge = regionRects.gauge;
  if (gauge && gauge.ratio != null) {
    const jitter = reduced.matches ? 0 : Math.sin(clock / 97) * 0.006 + Math.sin(clock / 31) * 0.003;
    const a = Math.PI * 0.75 + Math.PI * 1.5 * clamp(gauge.ratio) + jitter;
    strokePath(
      [
        { x: gauge.x - Math.cos(a) * 6, y: gauge.y - Math.sin(a) * 6 },
        { x: gauge.x + Math.cos(a) * (gauge.r - 3), y: gauge.y + Math.sin(a) * (gauge.r - 3) },
      ],
      INK.amber,
      1.6,
      1,
      8,
    );
    dot(gauge.x, gauge.y, 3, INK.bone);
  }
}
function tooltip() {
  if (!hovered) return;
  const found = hits.find(
    (h) =>
      h.kind === hovered.kind &&
      (h.data === hovered.data ||
        (h.kind === "cloth" && h.data?.run === hovered.data?.run) ||
        (h.data?.path && h.data.path === hovered.data?.path)),
  );
  if (!found) return;
  const d = found.data;
  let lines;
  if (found.kind === "cloth")
    lines = [
      d.name || d.run,
      `${known(d.shell)} / ${known(d.core)} · started ${clothTime(d.started)}`,
      `PR ${list(d.prs).map((n) => "#" + n).join(", ") || "none"} · ${known(d.knots)} knots`,
    ];
  else if (found.kind === "place")
    lines = [
      d.path,
      `heat ${d.heat == null ? "?" : d.heat.toFixed(2)} · knots ${known(d.knots)} · last ${known(d.last)}`,
      "click: fold · explain · fix · test · split · read",
    ];
  else if (found.kind === "branch")
    lines = [d.path + "/", d.hiddenBelow ? `+${d.hiddenBelow} dim places below — hold to open` : "a directory on the way"];
  else if (found.kind === "actor")
    lines = ["the resident — the shuttle", `${state.run?.mood_glyph || "?"} · ${LEXICON[state.shuttle?.state] || known(state.shuttle?.state)} at ${known(actor?.path)}`];
  else lines = [found.label];
  const w = Math.min(420, width - 32),
    h = 16 + lines.length * 17,
    x = Math.min(width - w - 16, Math.max(16, found.x)),
    y = found.y - h - 10 < 150 ? found.y + found.h + 10 : found.y - h - 10;
  g.save();
  g.fillStyle = "rgba(13,11,8,0.94)";
  g.fillRect(x, y, w, h);
  g.strokeStyle = INK.ice + "88";
  g.strokeRect(x + 0.5, y + 0.5, w, h);
  g.restore();
  lines.forEach((value, i) => text(value, x + 12, y + 20 + i * 17, i ? INK.boneDim : INK.ice, i ? 10 : 11, w - 24));
}
function drawRadial() {
  if (!radial) return;
  const p = point(radial.path);
  if (!p) return;
  const T = layout.tree;
  const x = Math.max(T.x + 90, Math.min(T.x + T.w - 90, p.x)),
    y = Math.max(T.y + 80, Math.min(T.y + T.h - 80, p.y));
  halo(x, y, 110, "#050403", 0.95);
  ring(x, y, 58, INK.ice, 0.35, 1);
  ring(x, y, 20, INK.ice, 0.25, 1);
  ["fold", "explain", "fix", "test", "split", "read"].forEach((action, i) => {
    const angle = -Math.PI / 2 + (i * Math.PI) / 3,
      ax = x + Math.cos(angle) * 60,
      ay = y + Math.sin(angle) * 60;
    const over = hovered?.kind === "command" && hovered.data?.action === action;
    g.fillStyle = over ? "#15222b" : "#0f0d0a";
    g.fillRect(ax - 29, ay - 11, 58, 22);
    g.strokeStyle = over ? INK.ice : INK.ice + "77";
    g.strokeRect(ax - 28.5, ay - 10.5, 57, 21);
    text(action, ax, ay + 4, over ? INK.ice : INK.bone, 10, 52, { align: "center" });
    hit(ax - 29, ay - 13, 58, 26, "command", { action }, action);
  });
  text("@" + radial.path.split("/").at(-1), x, y + 4, INK.ice, 9, 36, { align: "center" });
}
function applyGlitches() {
  glitches = glitches.filter((gl) => clock - gl.born < gl.dur);
  if (reduced.matches || !glitches.length) return;
  const dpr = canvas.width / width;
  for (const gl of glitches) {
    const age = (clock - gl.born) / gl.dur;
    if (age < 0) continue;
    const r = typeof gl.rect === "function" ? gl.rect() : gl.rect;
    if (!r) continue;
    const sx = Math.max(0, Math.floor(r.x * dpr)),
      sy = Math.max(0, Math.floor(r.y * dpr));
    const sw = Math.min(canvas.width - sx, Math.ceil(r.w * dpr)),
      sh = Math.min(canvas.height - sy, Math.ceil(r.h * dpr));
    if (sw < 2 || sh < 2) continue;
    glitchCanvas.width = sw;
    glitchCanvas.height = sh;
    glitchContext.drawImage(canvas, sx, sy, sw, sh, 0, 0, sw, sh);
    tintCanvas.width = sw;
    tintCanvas.height = sh;
    const shift = Math.round((2 + 4 * (1 - age)) * dpr);
    screen.save();
    screen.setTransform(1, 0, 0, 1, 0, 0);
    for (const [tint, dx] of [
      ["#ff3b30", -shift],
      ["#30d5ff", shift],
    ]) {
      tintContext.globalCompositeOperation = "copy";
      tintContext.drawImage(glitchCanvas, 0, 0);
      tintContext.globalCompositeOperation = "source-in";
      tintContext.fillStyle = tint;
      tintContext.fillRect(0, 0, sw, sh);
      screen.globalCompositeOperation = "lighter";
      screen.globalAlpha = 0.45 * (1 - age);
      screen.drawImage(tintCanvas, sx + dx, sy);
    }
    screen.globalCompositeOperation = "source-over";
    screen.globalAlpha = 1;
    const bands = 5,
      bh = Math.ceil(sh / bands),
      frame = Math.floor(clock / 40);
    for (let i = 0; i < bands; i++) {
      const off = Math.round((rand(gl.seed * 31 + i * 7 + frame) - 0.5) * 16 * dpr * (1 - age));
      if (!off) continue;
      const by = i * bh,
        hh = Math.min(bh, sh - by);
      if (hh <= 0) continue;
      screen.fillStyle = INK.ground;
      screen.fillRect(sx, sy + by, sw, hh);
      screen.drawImage(glitchCanvas, 0, by, sw, hh, sx + off, sy + by, sw, hh);
    }
    screen.restore();
  }
}
function advanceReveal() {
  if (revealAt === null) return;
  const t = clock - revealAt;
  REVEAL.forEach((key, i) => {
    if (revealed.has(key)) return;
    if (reduced.matches || t >= i * REVEAL_STEP) {
      revealed.add(key);
      sceneDirty = true;
      if (key === "bench") bench.classList.remove("veiled");
      else glitch(() => regionRects[key], 240);
    }
  });
}
function replayReveal() {
  revealAt = clock;
  revealed = new Set();
  bench.classList.add("veiled");
  sceneDirty = true;
}
function draw(timestamp) {
  if (!previous) previous = timestamp;
  if (!paused) clock += Math.min(timestamp - previous, 80);
  previous = timestamp;
  const pulse = reduced.matches ? 0.5 : smooth(1 - Math.abs(((clock % BEAT) / BEAT) * 2 - 1));
  if (!paused) advanceReveal();
  if (groundDirty) {
    paintGround();
    groundDirty = false;
  }
  if (sceneDirty || (!reduced.matches && clock - movesAt < BEAT)) {
    g = sceneContext;
    hits = [];
    paintScene();
    sceneHits = hits;
    g = screen;
    sceneDirty = false;
  }
  g.clearRect(0, 0, width, height);
  g.drawImage(groundCanvas, 0, 0, width, height);
  hits = [...sceneHits];
  const windowOn = state && isRevealed("window");
  g.drawImage(sceneCanvas, 0, 0, width, height);
  if (state) {
    if (windowOn) {
      g.save();
      g.beginPath();
      g.rect(layout.tree.x, layout.tree.y, layout.tree.w, layout.tree.h);
      g.clip();
      drawTrail();
      drawThreads();
      drawActor(pulse);
      drawSparks();
      if (forgeDrop && regionRects.forge && !reduced.matches) {
        const t = (clock - forgeDrop.born) / 400,
          f = regionRects.forge;
        if (t < 1) diamond(f.x + 10 + Math.min(7, prsInView().length - 1) * 7, f.y - 30 + smooth(t) * 70, 3.5, INK.ice, 1);
        else forgeDrop = null;
      }
      g.restore();
    }
    drawFaceLive(pulse);
    if (isRevealed("cloth")) {
      const live = railLayout().find((r) => !r.focus && (r.row.run === state.run?.id || r.strands.some((x) => x.run === state.run?.id)));
      if (live && live.x > layout.m && live.x < layout.right - 20) halo(live.x, layout.railY, 16 + pulse * 8, INK.amber, 0.2);
    }
    if (windowOn) drawRisenThreads();
    tooltip();
    drawRadial();
    drawScan();
  }
  applyGlitches();
  requestAnimationFrame(draw);
}
reduced.addEventListener("change", () => {
  sceneDirty = true;
});
function hitAt(event) {
  return (
    [...hits]
      .reverse()
      .find(
        (h) =>
          event.clientX >= h.x &&
          event.clientX <= h.x + h.w &&
          event.clientY >= h.y &&
          event.clientY <= h.y + h.h,
      ) || null
  );
}
canvas.addEventListener("pointermove", (event) => {
  hovered = hitAt(event);
  canvas.style.cursor = hovered ? "pointer" : "default";
  // Hovering a branch with dim places below opens it after a short dwell.
  const node =
    hovered?.kind === "branch"
      ? hovered.data
      : hovered?.kind === "place"
        ? treeNodes.find((n) => n.path === hovered.data.path)
        : null;
  clearTimeout(expandTimer);
  if (!lifted.size && node?.hiddenBelow > 0 && expanded !== node.path)
    expandTimer = setTimeout(() => {
      expanded = node.path;
      rebuild();
    }, 280);
});
canvas.addEventListener("pointerleave", () => {
  hovered = null;
  clearTimeout(expandTimer);
});
canvas.addEventListener("click", (event) => {
  const h = hitAt(event);
  if (!h) return;
  if (h.kind === "command") actions[h.data.action]();
  else if (h.kind === "heddle") toggle(h.data);
  else if (h.kind === "legend") legend.showModal();
  else if (h.kind === "actor") select("run");
  else if (h.kind === "branch") {
    expanded = expanded === h.data.path ? null : h.data.path;
    rebuild();
  } else select(h.kind, h.data);
});
canvas.addEventListener(
  "wheel",
  (event) => {
    if (!state) return;
    event.preventDefault();
    if (event.clientY > layout.cloth) {
      stepFocus(Math.sign(event.deltaY || event.deltaX));
      return;
    }
    if (event.clientX < layout.warp) {
      const n = list(state.warp?.items).filter(
        (w) => matches(w.topics) && !["done", "retired"].includes(w.state),
      ).length;
      warpScroll = Math.max(
        0,
        Math.min(Math.max(0, n - 1), warpScroll + Math.sign(event.deltaY)),
      );
      sceneDirty = true;
    }
  },
  { passive: false },
);
document.addEventListener("keydown", (event) => {
  if (
    event.target.matches("input,textarea") ||
    event.metaKey ||
    event.ctrlKey ||
    event.altKey
  )
    return;
  if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
    event.preventDefault();
    stepFocus(event.key === "ArrowLeft" ? -1 : 1);
    return;
  }
  if (event.key === "Escape") {
    radial = null;
    lifted.clear();
    warpScroll = 0;
    expanded = null;
    selected = { kind: "run" };
    pageHistory = [];
    document.querySelector("#focus").textContent = "@shuttle";
    rebuild();
    renderReceipt();
    bench.classList.remove("open");
    if (legend.open) legend.close();
  } else if (event.key === "?") {
    event.preventDefault();
    replayReveal();
  } else if (event.key === "s" || event.key === "S") {
    event.preventDefault();
    setScanMode(SCAN_MODES[(SCAN_MODES.indexOf(scanMode) + 1) % SCAN_MODES.length]);
  } else if (event.key === "l" || event.key === "L") {
    event.preventDefault();
    legend.open ? legend.close() : legend.showModal();
  } else if (event.key === " " && !event.target.matches("button")) {
    event.preventDefault();
    paused = !paused;
    sceneDirty = true;
  } else if (/^[1-6]$/.test(event.key)) {
    const h = list(state?.heddles)[Number(event.key) - 1];
    if (h) toggle(h.slug);
  }
});
document.querySelector("#close-legend").onclick = () => legend.close();
document.querySelector("#close-bench").onclick = () =>
  bench.classList.remove("open");
document.querySelector("#console").onsubmit = (event) => {
  event.preventDefault();
  document.querySelector("#console-note").textContent = UNWIRED;
};
window.addEventListener("resize", resize);
bench.classList.add("veiled");
resize();
requestAnimationFrame(draw);
async function start() {
  try {
    const response = await fetch(
      dev ? "/loom/dev/state.sample.json" : "/loom/state.json",
    );
    if (!response.ok) throw Error(`state unavailable (${response.status})`);
    const initial = await response.json();
    receive(initial);
    if (replay) {
      // Explicit fixture-only playback. Live state is never synthesised.
      const originals = structuredClone(list(initial.beads));
      let i = 0;
      setInterval(() => {
        const next = structuredClone(state),
          b = structuredClone(originals[i++ % originals.length]);
        if (!b) return;
        b.at = new Date(Date.parse(initial.at) + i * 2400).toISOString();
        b.ctx_after = (next.hud.ctx_tokens || 0) + (b.delta || 0);
        next.at = b.at;
        next.hud.ctx_tokens = b.ctx_after;
        next.beads.push(b);
        next.beads = next.beads.slice(-240);
        receive(next);
      }, 2400);
    }
  } catch (error) {
    sourceStatus = error.message;
    sceneDirty = true;
  }
  if (!dev) {
    const stream = new EventSource("/loom/events");
    stream.addEventListener("state", (event) => {
      try {
        receive(JSON.parse(event.data));
      } catch {
        sourceStatus = "invalid state frame";
      }
    });
    stream.onerror = () => {
      sourceStatus = "reconnecting";
      sceneDirty = true;
    };
  }
}
start();
