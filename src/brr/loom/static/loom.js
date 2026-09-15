/* The renderer owns pixels and selection. Only the feed owns facts.
 *
 * Layers, back to front, per frame:
 *   ground  — warm near-black, vignette, scanlines (cached; under everything)
 *   sweep   — the sonar's phosphor wedge and speckle (live)
 *   scene   — regions, branch lines, places, labels, instruments (cached)
 *   live    — sweep line, pings, the shuttle walking, sparks, threads, face
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
const [sweepCanvas, sweepContext] = layer();
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
const pings = new Map(),
  pendingPing = new Set();
let sweepPrev = null,
  expanded = null,
  expandTimer = 0,
  noisePattern = null;
const runeRects = new Map(),
  threadRects = new Map();
const regionRects = {};

const matches = (topics) =>
  [...lifted].every((topic) => list(topics).includes(topic));
function color(topic) {
  const i = list(state?.heddles).findIndex((h) => h.slug === topic);
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
    clothH = compact ? 112 : 150;
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
function railLayout() {
  const rows = clothRows(),
    { m, right, compact } = layout;
  let focus = rows.findIndex((r) => r.run === focusRun);
  if (focus < 0) focus = rows.length - 1;
  const cardWidth = Math.min(compact ? 330 : 400, (right - m * 2) * 0.44),
    live = focus === rows.length - 1;
  // The live pass sits rightmost by default; a focused past pass centres.
  const center = live ? right - 20 - cardWidth / 2 : (right + m) / 2;
  return rows.map((row, i) => {
    const d = i - focus,
      scale = Math.max(0.35, Math.pow(0.9, Math.abs(d)));
    let offset = cardWidth / 2 + 34;
    for (let j = 1; j < Math.abs(d); j++)
      offset += 46 * Math.max(0.35, Math.pow(0.9, j));
    return {
      row,
      focus: i === focus,
      scale,
      x: d ? center + Math.sign(d) * offset : center,
      w: i === focus ? cardWidth : 30 * scale,
    };
  });
}
function passPlaces(row) {
  if (Array.isArray(row?.places))
    return row.places
      .map((p) => (typeof p === "string" ? p : p.path))
      .filter(Boolean);
  if (row?.run === state?.run?.id)
    return [...new Set(list(state.beads).flatMap((b) => list(b.places)))];
  const thread = list(state?.hud?.strands).find((t) => t.id === row?.run);
  return Array.isArray(thread?.places) ? thread.places : null;
}
function focusedPaths() {
  const row = railLayout().find((r) => r.focus)?.row;
  return new Set(passPlaces(row) || []);
}
function focusPass(run) {
  focusRun = run;
  const row = clothRows().find((r) => r.run === run);
  radial = null;
  rebuild();
  if (row) select("cloth", row);
}
function stepFocus(delta) {
  const rows = clothRows(),
    index = railLayout().findIndex((r) => r.focus);
  if (rows.length)
    focusPass(rows[Math.max(0, Math.min(rows.length - 1, index + delta))].run);
}
function mergedPlaces() {
  const places = new Map();
  for (const p of list(state?.tree?.places))
    if (p.path) places.set(p.path, { ...p, topics: list(p.topics) });
  for (const b of list(state?.beads))
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
  const mapped = visible.map((r) => passPlaces(r.row));
  const union = mapped.length
    ? new Set(mapped.filter((p) => p !== null).flat())
    : null;
  return [...places.values()].filter(
    (p) => matches(p.topics) && (!union || union.has(p.path)),
  );
}
function newestPlace() {
  const b = [...list(state?.beads)].reverse().find((b) => list(b.places).length);
  return b ? list(b.places).at(-1) : null;
}
function actorHistory() {
  const out = [];
  for (const b of list(state?.beads)) {
    if (!matches(b.topics)) continue;
    const p = list(b.places).at(-1);
    if (p && out.at(-1) !== p) out.push(p);
  }
  return out.slice(-8);
}
function rebuild() {
  sceneDirty = true;
  if (!state || !layout) return;
  const all = mergedPlaces();
  const actorPath = newestPlace();
  // Fewer things at once: the hottest places, plus everything the eye must
  // not lose — the shuttle, its trail, the threads, the selection.
  const pinned = new Set(actorHistory());
  if (actorPath) pinned.add(actorPath);
  for (const t of list(state.hud?.strands)) {
    const p = list(t.places).at(-1);
    if (p) pinned.add(p);
  }
  if (selected.kind === "place" && selected.data?.path)
    pinned.add(selected.data.path);
  const ranked = all
    .slice()
    .sort(
      (a, b) =>
        (b.heat ?? -1) - (a.heat ?? -1) ||
        String(b.last || "").localeCompare(String(a.last || "")) ||
        a.path.localeCompare(b.path),
    );
  const cap = lifted.size ? LIFTED_CAP : PLACE_CAP,
    shown = new Map();
  for (const p of ranked) if (pinned.has(p.path)) shown.set(p.path, p);
  for (const p of ranked) {
    if (shown.size >= cap) break;
    shown.set(p.path, p);
  }
  if (expanded)
    for (const p of ranked)
      if (p.path.startsWith(expanded + "/")) shown.set(p.path, p);
  shownCount = shown.size;
  hiddenCount = all.length - shown.size;
  const root = {
    path: "",
    name: state.repo || "repo",
    children: new Map(),
  };
  for (const place of shown.values()) {
    const parts = place.path.split("/").filter(Boolean);
    const compact =
      parts.length > 4 ? [parts[0], parts[1], "…", parts.at(-1)] : parts;
    let node = root,
      prefix = "";
    compact.forEach((part, i) => {
      const leaf = i === compact.length - 1;
      prefix = leaf ? place.path : (prefix ? prefix + "/" : "") + part;
      if (!node.children.has(prefix))
        node.children.set(prefix, {
          path: prefix,
          name: part,
          children: new Map(),
        });
      node = node.children.get(prefix);
    });
    node.place = place;
  }
  function compress(node) {
    for (const [key, child] of node.children)
      node.children.set(key, compress(child));
    while (node !== root && !node.place && node.children.size === 1) {
      const child = [...node.children.values()][0];
      node = { ...child, name: node.name + "/" + child.name };
    }
    return node;
  }
  compress(root);
  const nodes = [],
    leaves = [];
  function visit(node, parent = null, depth = 0) {
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
    if (!node.children.size) leaves.push(node);
    node.topics = node.place?.topics || [
      ...new Set([...node.children.values()].flatMap((n) => n.topics)),
    ];
  }
  visit(root);
  for (const node of nodes)
    node.hiddenBelow = node.path
      ? all.filter(
          (p) => !shown.has(p.path) && p.path.startsWith(node.path + "/"),
        ).length
      : hiddenCount;
  treeNodes = nodes;
  const T = layout.tree,
    bottom = T.y + T.h - 30,
    top = T.y + 26;
  const maxDepth = Math.max(1, ...nodes.map((n) => n.depth));
  const x0 = T.x + 64,
    x1 = T.x + T.w - 80,
    spacing = (x1 - x0) / Math.max(1, leaves.length - 1);
  const next = new Map();
  const yOf = (depth) => bottom - ((bottom - top) * depth) / maxDepth;
  leaves.forEach((node, i) =>
    next.set(node.path, {
      x: leaves.length === 1 ? T.x + T.w / 2 : x0 + i * spacing,
      // Crowded rows alternate half a step so labels can take turns.
      y:
        yOf(node.depth) +
        (spacing < 70 && node.depth > 0 && i % 2
          ? Math.min(22, (bottom - top) / maxDepth / 2.4)
          : 0),
    }),
  );
  function place(node) {
    if (!node.children.size) return;
    const children = [...node.children.values()];
    children.forEach(place);
    next.set(node.path, {
      x:
        children.reduce((sum, n) => sum + next.get(n.path).x, 0) /
        children.length,
      y: yOf(node.depth),
    });
  }
  place(root);
  const r0 = next.get("");
  next.set("", {
    x: Math.max(T.x + T.w * 0.3, Math.min(T.x + T.w * 0.7, r0?.x ?? T.x + T.w / 2)),
    y: bottom,
  });
  const changed =
    JSON.stringify([...next]) !== JSON.stringify([...targetPositions]);
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
    const route = from.length
      ? [...from.slice(Math.max(0, shared - 1)).reverse(), ...to.slice(shared)]
      : [];
    // The shuttle walks the branch lines: 300–600 ms, eased, never teleports.
    walk = {
      route,
      born: clock,
      dur: route.length ? Math.max(300, Math.min(600, routeLength(route) * 1.1)) : 0,
    };
  }
  actor = actorPath && next.has(actorPath) ? { path: actorPath } : null;
  planLabels();
  updateAccess();
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
  }
  const a = actor && targetPositions.get(actor.path);
  if (a) boxes.push({ x: a.x - 40, y: a.y - 36, w: 124, h: 30 });
  const rank = (n) =>
    !n.path
      ? 0
      : n.path === actor?.path
        ? 1
        : selected.kind === "place" && selected.data?.path === n.path
          ? 2
          : n.children.size
            ? 10 + n.depth
            : 100 - clamp(n.place?.heat) * 50;
  for (const n of treeNodes.slice().sort((x, y) => rank(x) - rank(y))) {
    const p = targetPositions.get(n.path);
    if (!p) continue;
    const dir = n.children.size > 0,
      size = !n.path ? 11 : 10;
    measure.font = font(size, !n.path || dir ? 500 : 400);
    let label = !n.path ? n.name : dir ? n.name + "/" : n.name;
    if (label.length > 28) label = label.slice(0, 26) + "…";
    const w = measure.measureText(label).width;
    const cands = !n.path
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
      if (!n.path || clear(b)) {
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
        for (const path of list(b.places)) pendingPing.add(path);
      });
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
  sourceStatus = dev ? "fixture" : "live";
  renderReceipt();
}
function glitch(rect, dur = 120, born = clock) {
  if (reduced.matches) return;
  glitches.push({ rect, dur, born, seed: glitchSeed++ });
  if (glitches.length > 24) glitches = glitches.slice(-24);
}
function toggle(slug) {
  radial = null;
  actions.lift();
  lifted.has(slug) ? lifted.delete(slug) : lifted.add(slug);
  warpScroll = 0;
  expanded = null;
  selected = { kind: "heddles" };
  rebuild();
  glitch(() => runeRects.get(slug), 140);
  renderReceipt();
  document.querySelector("#focus").textContent = lifted.size
    ? "@" + [...lifted].join(" ∩ ")
    : "@shuttle";
}
function select(kind, data) {
  sceneDirty = true;
  if (kind === "cloth" && focusRun !== data.run) {
    focusRun = data.run;
  }
  radial = kind === "place" ? { path: data.path } : null;
  selected = { kind, data };
  if (kind === "cloth" || kind === "place") rebuild();
  bench.classList.add("open");
  pendingFold++;
  if (kind === "place")
    document.querySelector("#focus").textContent = "@" + data.path;
  renderReceipt();
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
    const el = element("span", topic, "tag");
    el.style.color = color(topic);
    el.style.borderColor = color(topic) + "66";
    box.append(el);
  }
  receipt.append(box);
}
function button(label, callback) {
  const el = element("button", label, "bench-action");
  el.type = "button";
  el.onclick = callback;
  receipt.append(el);
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
  ]);
  if (signature === lastReceipt) return;
  lastReceipt = signature;
  receipt.replaceChildren();
  const { kind, data } = selected;
  receipt.append(
    element("p", dev ? "FIXTURE / illustrative data" : state.repo, "eyebrow"),
  );
  if (kind === "run") {
    const run = state.run;
    receipt.append(element("h1", run?.name || "No live pass"));
    if (!run) {
      section("The shuttle", known(state.shuttle?.state));
      return;
    }
    tags(run.topic ? [run.topic] : []);
    row("body", [run.shell, run.core].map(known).join(" / "));
    section("Now", run.card?.now);
    section("Plan");
    for (const item of list(run.card?.plan))
      receipt.append(
        element(
          "p",
          (item.done ? "✓ " : "· ") + item.text,
          item.done ? "tick" : "",
        ),
      );
    section("Vector");
    for (const v of list(run.card?.vector)) receipt.append(element("p", v));
    section("Ledger");
    receipt.append(
      element("pre", list(run.card?.ledger).join("\n") || "unknown"),
    );
    section("Context");
    row("in the scroll", number(state.hud?.ctx_tokens));
    button("Inspect the latest boundary", () => {
      const b = list(state.beads)
        .filter((b) => matches(b.topics))
        .at(-1);
      if (b) select("bead", b);
    });
  } else if (kind === "heddles") {
    receipt.append(
      element("h1", lifted.size ? "The shed is raised" : "The whole cloth"),
    );
    tags([...lifted]);
    section(
      "Selection",
      lifted.size
        ? "Only rows carrying every lifted topic are in the window."
        : "No layers lifted. All measured rows are visible.",
    );
    row("places", mergedPlaces().length);
    row(
      "passes",
      list(state.cloth?.rows).filter((r) => matches(r.topics)).length,
    );
    for (const h of list(state.heddles).filter((h) => lifted.has(h.slug))) {
      section(h.rune + " / " + h.slug);
      row("last lit", h.last_lit);
      for (const [key, value] of Object.entries(h.signature || {}))
        row(key, list(value).join(", ") || "none");
    }
    button("Open the shuttle’s card", () => select("run"));
  } else if (kind === "bead") {
    receipt.append(element("h1", known(data.act) + " / boundary"));
    tags(data.topics);
    section("Block", data.detail);
    row("at", data.at);
    row("context", number(data.ctx_after));
    row("delta", number(data.delta));
    section("Places");
    for (const path of list(data.places))
      button(path, () => select("place", { path }));
  } else if (kind === "cloth") {
    receipt.append(element("h1", data.name || data.run));
    tags(data.topics);
    row("body", [data.shell, data.core].map(known).join(" / "));
    row("started", data.started);
    row("ended", data.ended);
    row(
      "PRs",
      list(data.prs)
        .map((n) => "#" + n)
        .join(" · ") || "none",
    );
    row("knots", data.knots);
    row("pages", data.pages);
    row("tokens", number(data.tokens));
    row("parent", data.parent);
  } else if (kind === "place") {
    receipt.append(element("h1", data.path));
    const p = mergedPlaces().find((p) => p.path === data.path);
    tags(p?.topics);
    row("heat", p?.heat);
    row("knots", p?.knots);
    row("last", p?.last);
    section("At this place");
    for (const action of ["fold", "explain", "fix", "test", "split", "read"])
      button(action, () => actions[action]());
    const folds = list(state.bench?.folds).filter((f) => f.place === data.path);
    section("Fold", folds.length ? undefined : "No fold at this place yet.");
    for (const fold of folds)
      button(`${fold.path} · ${list(fold.marks).join(", ")}`, () =>
        openFold(fold),
      );
    section("Boundaries");
    for (const b of list(state.beads)
      .filter((b) => list(b.places).includes(data.path) && matches(b.topics))
      .slice(-12))
      button(`${known(b.act)} · ${known(b.at)}`, () => select("bead", b));
  } else if (kind === "warp") {
    receipt.append(element("h1", data.title));
    tags(data.topics);
    row("id", data.id);
    row("state", data.state);
    row("type", data.type);
    row("taken", data.taken);
    row("needs", list(data.needs).join(", ") || "none");
  } else if (kind === "goal") {
    receipt.append(element("h1", data.title));
    row("goal", data.id);
    section("Metric", data.metric);
  } else if (kind === "strand") {
    receipt.append(element("h1", data.title));
    row("thread", data.id);
    row("status", data.status);
    row("spent", number(data.spent));
    row("allowance", number(data.allowance));
    section("Places", list(data.places).join("\n") || "none attested");
    if (askOf(data) != null)
      button(`ask +${compactNumber(askOf(data))} → grant`, actions.grant);
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
  if (live) dot(right - 96, y - 4, 3, INK.receipt, 8);
  text(status, right - 20, y - 1, dev ? INK.amber : live ? INK.boneDim : INK.wall, 9, 300, {
    align: "right",
  });
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
    runeRects.set(h.slug, { x: x - 6, y: y - size - 2, w: size + 12, h: size + 10 });
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
    !node.path ||
    [...focusPaths].some(
      (path) => path === node.path || path.startsWith(node.path + "/"),
    );
  for (const node of treeNodes) {
    const p = point(node.path);
    if (!p || !node.parent) continue;
    const parent = point(node.parent.path);
    if (!parent) continue;
    const lit = inFocus(node);
    strokePath(edgePoints(parent, p), lit ? "#5d513d" : INK.dark, lit ? 1.4 : 1, lit ? 0.95 : 0.6);
  }
  const root = point("");
  if (root) {
    // Where the weft enters: the trunk runs down into the cloth.
    strokePath([{ x: root.x, y: root.y }, { x: root.x, y: T.y + T.h }], "#5d513d", 1.4, 0.9);
  }
  for (const node of treeNodes) {
    const p = point(node.path);
    if (!p) continue;
    const focused = inFocus(node),
      heat = node.place ? clamp(node.place.heat) : 0.35,
      c = node.path ? firstColor(node.topics) : INK.bone;
    const radius = !node.path ? 4.5 : node.place ? 2.2 + heat * 3.2 : 2.2;
    g.globalAlpha = focused ? 1 : 0.4;
    // Bloom twice: the hue, thin, then the bone core.
    if (node.path) dot(p.x, p.y, radius + 1.2, c, 12 + heat * 6);
    dot(p.x, p.y, radius, node.place?.heat == null && node.path ? INK.faint : INK.bone);
    if (node.place?.knots > 0) {
      diamond(p.x + 9, p.y + 9, 3.2, INK.ice, 0.85);
      ring(p.x + 9, p.y + 9, 6.5, INK.ice, 0.3);
      if (node.place.knots > 1) text(node.place.knots, p.x + 18, p.y + 13, INK.ice, 8);
    }
    if (node.label) {
      const dir = node.children.size > 0;
      const fill = !node.path
        ? INK.bone
        : dir
          ? INK.boneDim
          : `rgba(232, 220, 192, ${(focused ? 0.55 + heat * 0.45 : 0.4).toFixed(2)})`;
      text(node.label.text, p.x + node.label.dx, p.y + node.label.dy, fill, node.label.size, Infinity, {
        weight: !node.path || dir ? 500 : 400,
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
    else if (node.path) hit(p.x - 10, p.y - 10, 20, 20, "branch", node, node.path);
    if (selected.kind === "place" && selected.data?.path === node.path)
      ring(p.x, p.y, radius + 8, INK.ice, 0.9);
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
  const focused = railLayout().find((r) => r.focus)?.row;
  if (focused && passPlaces(focused) === null)
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
    lampStep = Math.min(62, ww / lamps.length);
  lamps.forEach(([label, on, hue], i) => {
    const lx = wx + 4 + i * lampStep;
    if (on) dot(lx, lampY - 3, 3.2, hue, 10);
    else ring(lx, lampY - 3, 3, INK.dark, 1, 1.2);
    text(label, lx + 8, lampY, on ? INK.bone : INK.faint, 8, lampStep - 10);
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
  const tx = gx + gr + 18,
    tw = (compact ? pw * 0.46 : pw * 0.44) - (tx - px);
  text(`${compactNumber(spent)} / ${compactNumber(budget)}`, tx, gy - (compact ? 6 : 10), INK.bone, compact ? 12 : 15, tw, {
    weight: 500,
  });
  text(
    budget > 0 ? `spend · ${Math.round((spent / budget) * 100)}% of allowance` : "spend · allowance unknown",
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
  const qx = px + pw * (compact ? 0.5 : 0.48),
    qw = pw * (compact ? 0.36 : 0.38) - 12;
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
    text(title, px + 16, ty, INK.bone, 9, pw - 190);
    text(known(thread.status), px + pw - 150, ty, thread.status === "live" ? INK.boneDim : INK.faint, 9, 50);
    line(px + pw - 96, ty - 3, px + pw - 42, ty - 3, "#2a241b", 1, 2);
    if (fuel !== null)
      strokePath(
        [
          { x: px + pw - 96, y: ty - 3 },
          { x: px + pw - 96 + 54 * fuel, y: ty - 3 },
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
function drawCloth() {
  const { m, right, cloth, compact, clothH } = layout,
    entries = railLayout();
  regionRects.cloth = { x: m - 8, y: cloth - 2, w: right - m + 4, h: clothH };
  regionTitle("cloth", "the cloth", m, cloth + 14, right - m - 200);
  text("← → · wheel to focus", right - 20, cloth + 14, INK.faint, 9, 200, { align: "right" });
  const lineY = cloth + (compact ? 64 : 84),
    ph = compact ? 62 : 84;
  strokePath([{ x: m, y: lineY }, { x: right - 20, y: lineY }], INK.dark, 1.5, 1);
  g.save();
  g.beginPath();
  g.rect(m - 4, cloth + 28, right - m, clothH - 22);
  g.clip();
  const visible = entries.filter((e) => e.x + e.w / 2 >= m && e.x - e.w / 2 <= right - 20);
  for (const entry of visible) {
    const { row, focus, x, w, scale } = entry;
    const topics = list(row.topics),
      live = row.run === state.run?.id;
    if (focus) {
      const x0 = x - w / 2,
        y0 = lineY - ph / 2;
      bezel(x0, y0, w, ph, { edge: "#3b3327" });
      strokePath([{ x: x0 + 1, y: y0 + 1 }, { x: x0 + w - 1, y: y0 + 1 }], INK.ice, 1.5, 0.9, 6);
      text(row.name || row.run, x0 + 12, y0 + (compact ? 17 : 21), INK.bone, compact ? 11 : 12, w - 24, { weight: 500 });
      let cx = x0 + 12;
      const chipY = y0 + (compact ? 24 : 30),
        chipH = compact ? 15 : 18;
      for (const topic of topics.slice(0, 4)) {
        const rune = list(state.heddles).find((h) => h.slug === topic)?.rune || "?";
        const label = topic.replace(/^the-/, "");
        g.font = font(9);
        const cw = Math.min(110, g.measureText(label).width + 26);
        if (cx + cw > x0 + w - 12) break;
        g.strokeStyle = color(topic) + "aa";
        g.strokeRect(cx + 0.5, chipY + 0.5, cw, chipH);
        text(rune, cx + 5, chipY + chipH - 4, color(topic), compact ? 11 : 12);
        text(label, cx + 19, chipY + chipH - 5, INK.bone, 9, cw - 22);
        cx += cw + 6;
      }
      if (!topics.length) text("no topic stamped", cx, chipY + chipH - 5, INK.faint, 9);
      const minutes = minutesOf(row);
      const when = live
        ? `now · this run · ${minutes ?? "?"} min`
        : `${clothTime(row.started)} → ${row.ended ? clothTime(row.ended) : "open"} · ${minutes ?? "?"} min`;
      const facts = `${known(row.shell)}/${known(row.core)} · ${known(row.knots)} knots · ${list(row.prs).length ? list(row.prs).length + " PR" + (list(row.prs).length > 1 ? "s" : "") : "no PR"}`;
      const by = y0 + ph - (compact ? 9 : 12);
      const ww = text(when, x0 + 12, by, live ? INK.amber : INK.bone, compact ? 9 : 10, w * 0.55);
      text(facts, x0 + 24 + ww, by, INK.faint, 9, w - ww - 36);
      hit(x0, y0, w, ph, "cloth", row, row.name || row.run);
    } else {
      const r = 7 * scale + 2;
      if (live) {
        halo(x, lineY, 22, INK.amber, 0.35);
        dot(x, lineY, r * 0.55, INK.amber, 10);
      } else {
        dot(x, lineY, Math.max(1.5, r * 0.4), INK.boneDim);
      }
      if (topics.length)
        topics.forEach((topic, i) => {
          g.strokeStyle = color(topic);
          g.globalAlpha = 0.85;
          g.lineWidth = 1.5;
          g.beginPath();
          g.arc(x, lineY, r + 2, (i * Math.PI * 2) / topics.length + 0.12, ((i + 1) * Math.PI * 2) / topics.length - 0.12);
          g.stroke();
          g.globalAlpha = 1;
        });
      else ring(x, lineY, r + 2, INK.dark, 0.9);
      if (list(row.prs).length) diamond(x, lineY - r - 8, 2.5, INK.ice, 0.7);
      hit(x - Math.max(8, r + 4), lineY - 22, Math.max(16, (r + 4) * 2), 44, "cloth", row, row.name || row.run);
    }
  }
  g.restore();
  // Time reads: three labels at most — the start, a middle, now.
  const focusEntry = entries.find((e) => e.focus);
  const small = visible.filter((e) => !e.focus);
  const picks = [];
  if (small.length) picks.push(small[0]);
  if (small.length > 2) picks.push(small[Math.floor(small.length / 2)]);
  const liveEntry = visible.find((e) => e.row.run === state.run?.id);
  if (liveEntry && !liveEntry.focus) picks.push(liveEntry);
  else if (small.length > 1) picks.push(small.at(-1));
  const labelY = lineY + ph / 2 + (compact ? 12 : 16);
  let lastEnd = -Infinity;
  for (const e of picks) {
    const label = e.row.run === state.run?.id ? "now" : clothTime(e.row.started);
    g.font = font(9);
    const lw = g.measureText(label).width;
    const lx = Math.max(m, Math.min(right - 20 - lw, e.x - lw / 2));
    const overPlaque = focusEntry && lx + lw > focusEntry.x - focusEntry.w / 2 - 6 && lx < focusEntry.x + focusEntry.w / 2 + 6;
    if (lx < lastEnd + 24 || overPlaque) continue;
    line(e.x, lineY + 8, e.x, labelY - 10, INK.dark);
    text(label, lx, labelY, label === "now" ? INK.amber : INK.boneDim, 9);
    lastEnd = lx + lw;
  }
  if (!entries.length) text("No passes in this shed", m, lineY + 4, INK.faint, 11);
}
function drawFooter() {
  const { m, right, consoleH } = layout;
  const y = height - consoleH - 8;
  text(paused ? "Ⅱ BEAT PAUSED" : reduced.matches ? "REDUCED MOTION" : "600 ms / beat", m, y, INK.faint, 9);
  text("? replay the reading · L legend · Space pause", right - 20, y, INK.faint, 9, 360, { align: "right" });
  hit(right - 300, y - 14, 280, 20, "legend", null, "Open legend");
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
function sweepAngle() {
  if (reduced.matches) return -Math.PI / 2;
  // One rotation every four beats, eased within each beat.
  const b = clock / BEAT,
    i = Math.floor(b);
  return -Math.PI / 2 + ((i + smooth(b - i)) * Math.PI) / 2;
}
function sweepCenter() {
  return actorPoint() || point("") || null;
}
function drawSweepUnder(center, angle) {
  const T = layout.tree;
  const R = Math.max(
    ...[
      [T.x, T.y],
      [T.x + T.w, T.y],
      [T.x, T.y + T.h],
      [T.x + T.w, T.y + T.h],
    ].map(([x, y]) => Math.hypot(x - center.x, y - center.y)),
  );
  g.save();
  g.beginPath();
  g.rect(T.x, T.y, T.w, T.h);
  g.clip();
  // Range rings: the scope's graticule, centred on the shuttle.
  for (let r = 80; r < R; r += 80) ring(center.x, center.y, r, INK.amber, r % 240 ? 0.035 : 0.06);
  if (!reduced.matches) {
    const w = Math.ceil(T.w),
      h = Math.ceil(T.h);
    if (sweepCanvas.width !== w || sweepCanvas.height !== h) {
      sweepCanvas.width = w;
      sweepCanvas.height = h;
      noisePattern = null;
    }
    const s = sweepContext;
    s.globalCompositeOperation = "source-over";
    s.clearRect(0, 0, w, h);
    if (!noisePattern) noisePattern = s.createPattern(noiseTexture(), "repeat");
    // Ultrasound speckle: the grain shimmers on its own clock, masked to the
    // swept sector and fading with the phosphor.
    const step = Math.floor(clock / 70),
      jx = Math.floor(rand(step) * 256),
      jy = Math.floor(rand(step + 0.37) * 256);
    s.save();
    s.translate(-jx, -jy);
    s.fillStyle = noisePattern;
    s.fillRect(jx, jy, w, h);
    s.restore();
    const cx = center.x - T.x,
      cy = center.y - T.y;
    const mask = s.createConicGradient(angle - Math.PI / 2, cx, cy);
    mask.addColorStop(0, "rgba(0,0,0,0)");
    mask.addColorStop(0.249, "rgba(0,0,0,0.95)");
    mask.addColorStop(0.25, "rgba(0,0,0,0)");
    mask.addColorStop(1, "rgba(0,0,0,0)");
    s.globalCompositeOperation = "destination-in";
    s.fillStyle = mask;
    s.fillRect(0, 0, w, h);
    const fall = s.createRadialGradient(cx, cy, 10, cx, cy, R);
    fall.addColorStop(0, "rgba(0,0,0,1)");
    fall.addColorStop(1, "rgba(0,0,0,0.25)");
    s.fillStyle = fall;
    s.fillRect(0, 0, w, h);
    g.globalAlpha = 0.6;
    g.drawImage(sweepCanvas, T.x, T.y, T.w, T.h);
    g.globalAlpha = 1;
    const wash = g.createConicGradient(angle - Math.PI / 2, center.x, center.y);
    wash.addColorStop(0, "rgba(242,177,52,0)");
    wash.addColorStop(0.249, "rgba(242,177,52,0.13)");
    wash.addColorStop(0.25, "rgba(242,177,52,0)");
    wash.addColorStop(1, "rgba(242,177,52,0)");
    g.fillStyle = wash;
    g.fillRect(T.x, T.y, T.w, T.h);
  }
  g.restore();
  return R;
}
function noiseTexture() {
  const [c, x] = layer();
  c.width = c.height = 256;
  for (let i = 0; i < 2600; i++) {
    const a = rand(i * 1.3);
    x.fillStyle = a > 0.82 ? "rgba(242,177,52,0.95)" : `rgba(232,220,192,${(0.25 + a * 0.4).toFixed(2)})`;
    x.fillRect(Math.floor(rand(i * 3.1) * 256), Math.floor(rand(i * 7.7) * 256), a > 0.95 ? 2 : 1, 1);
  }
  return c;
}
function drawSweepOver(center, angle, R) {
  const T = layout.tree;
  g.save();
  g.beginPath();
  g.rect(T.x, T.y, T.w, T.h);
  g.clip();
  if (!reduced.matches) {
    strokePath(
      [center, { x: center.x + Math.cos(angle) * R, y: center.y + Math.sin(angle) * R }],
      INK.amber,
      1.3,
      0.75,
      10,
    );
    // Pings: the line crossing a place brightens it and throws one ring.
    const span = sweepPrev === null ? 0 : angle - sweepPrev;
    if (span > 0 && span < Math.PI) {
      for (const node of treeNodes) {
        if (!node.path || node.path === actor?.path) continue;
        const p = point(node.path);
        if (!p) continue;
        const d = Math.hypot(p.x - center.x, p.y - center.y);
        if (d < 6 || d > R) continue;
        const theta = Math.atan2(p.y - center.y, p.x - center.x);
        const delta = (((theta - sweepPrev) % (Math.PI * 2)) + Math.PI * 2) % (Math.PI * 2);
        if (delta <= span) {
          const strong = pendingPing.delete(node.path);
          pings.set(node.path, { at: clock, strong });
          if (strong) glitch(() => {
            const q = point(node.path);
            return q && { x: q.x - 40, y: q.y - 22, w: 110, h: 34 };
          }, 120);
        }
      }
    }
    sweepPrev = angle;
  }
  for (const [path, ping] of pings) {
    const dur = ping.strong ? 520 : 300,
      age = (clock - ping.at) / dur;
    if (age >= 1 || age < 0) {
      if (age >= 1) pings.delete(path);
      continue;
    }
    const p = point(path);
    if (!p) continue;
    halo(p.x, p.y, ping.strong ? 30 : 16, INK.amber, (1 - age) * (ping.strong ? 0.55 : 0.35));
    dot(p.x, p.y, 2.2, "#fff4dc", 0);
    ring(p.x, p.y, 5 + age * (ping.strong ? 26 : 16), INK.amber, (1 - age) * 0.85, ping.strong ? 1.5 : 1);
  }
  g.restore();
}
function blockRect(i) {
  const a = actorPoint();
  if (!a || !state?.run) return null;
  const gw = regionRects.actorGlyphW || 44;
  return { x: a.x + gw / 2 + 6, y: a.y - 26 - i * 6, w: 40, h: 8 };
}
function drawTrail() {
  const history = actorHistory();
  for (let i = 1; i < history.length; i++) {
    const from = beam(history[i - 1]),
      to = beam(history[i]);
    if (!from.length || !to.length) continue;
    let shared = 0;
    while (shared < from.length && shared < to.length && Math.abs(from[shared].x - to[shared].x) < 0.01 && Math.abs(from[shared].y - to[shared].y) < 0.01) shared++;
    const route = [...from.slice(Math.max(0, shared - 1)).reverse(), ...to.slice(shared)];
    strokePath(route, INK.amber, 1.4, ((i + 1) / history.length) * 0.32, 6);
  }
  history.forEach((path, i) => {
    const p = point(path);
    if (p) dot(p.x, p.y, 1.8 + i * 0.15, INK.amber, 6 + i);
  });
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
  const glyph = state.run.mood_glyph || "unknown";
  const gw = text(glyph, a.x, gy, "#ffd27a", 15, 140, { align: "center", weight: 600, glow: 18, glowColor: INK.amber });
  regionRects.actorGlyphW = gw;
  const box = { x: a.x - gw / 2 - 8, y: gy - 17, w: gw + 16, h: 24 };
  regionRects.actor = box;
  hit(box.x, box.y, box.w, box.h, "actor", null, "the resident");
  // The context stack: one small glowing bar per recent block.
  const blocks = list(state.beads)
    .filter((b) => matches(b.topics))
    .slice(-6);
  blocks.reverse().forEach((b, i) => {
    const x = a.x + gw / 2 + 8,
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
    let lx = a.x - gw / 2 - 18 - lw;
    if (lx < T.x + 8) lx = a.x + gw / 2 + 60;
    const ly = gy - 18;
    g.save();
    g.fillStyle = "rgba(11,9,6,0.85)";
    g.fillRect(lx - 6, ly - 12, lw + 12, 17);
    g.restore();
    text(label, lx, ly, INK.bone, 10);
    line(lx + lw + 6, ly - 4, a.x - gw / 2 - 4, gy - 6, INK.amber, 0.6);
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
    const title = String(thread.title || thread.id || "thread").split(/:\s/)[0];
    const tx = x + 14 > T.x + T.w - 150 ? x - 150 : x + 14;
    text(title, tx, y - 1, done ? INK.faint : INK.boneDim, 9, 140);
    const fuel = thread.allowance > 0 ? clamp(1 - (thread.spent || 0) / thread.allowance) : null;
    line(tx, y + 5, tx + 56, y + 5, "#2a241b", 1, 2);
    if (fuel !== null) strokePath([{ x: tx, y: y + 5 }, { x: tx + 56 * fuel, y: y + 5 }], INK.amber, 2, 0.7 * alpha, 5);
    hit(Math.min(x, tx) - 4, y - 14, 160, 24, "strand", thread, thread.title);
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
      const size = (layout.compact ? 24 : 34) * (1 + (reduced.matches ? 0 : (pulse - 0.5) * 0.03));
      text(shown, cx, cy + size * 0.35, "#ffd27a", size, box.w - 12, { align: "center", weight: 600, glow: 20, glowColor: INK.amber });
    } else text("no pass", cx, cy + 4, INK.faint, 11, box.w, { align: "center" });
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
  let center = null,
    angle = 0,
    R = 0;
  const windowOn = state && isRevealed("window");
  if (windowOn) {
    center = sweepCenter();
    angle = sweepAngle();
    if (center) R = drawSweepUnder(center, angle);
  }
  g.drawImage(sceneCanvas, 0, 0, width, height);
  if (state) {
    if (windowOn) {
      if (center && !paused) drawSweepOver(center, angle, R);
      else if (center) drawSweepOver(center, sweepPrev ?? angle, R);
      g.save();
      g.beginPath();
      g.rect(layout.tree.x, layout.tree.y, layout.tree.w, layout.tree.h);
      g.clip();
      drawTrail();
      drawThreads();
      drawActor(pulse);
      drawSparks();
      g.restore();
      if (walk.route.length && clock - walk.born >= walk.dur && actor) pendingPing.delete(actor.path);
    }
    drawFaceLive(pulse);
    if (isRevealed("cloth")) {
      const live = railLayout().find((r) => r.row.run === state.run?.id && !r.focus);
      const lineY = layout.cloth + (layout.compact ? 64 : 84);
      if (live && live.x > layout.m && live.x < layout.right - 20) halo(live.x, lineY, 16 + pulse * 8, INK.amber, 0.2);
    }
    tooltip();
    drawRadial();
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
  if (node?.hiddenBelow > 0 && expanded !== node.path)
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
    document.querySelector("#focus").textContent = "@shuttle";
    rebuild();
    renderReceipt();
    bench.classList.remove("open");
    if (legend.open) legend.close();
  } else if (event.key === "?") {
    event.preventDefault();
    replayReveal();
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
