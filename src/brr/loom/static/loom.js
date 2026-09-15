/* The renderer owns pixels and selection. Only the feed owns facts. */
const canvas = document.querySelector("#loom");
const screen = canvas.getContext("2d");
let g = screen;
const sceneCanvas = document.createElement("canvas");
const sceneContext = sceneCanvas.getContext("2d");
let sceneDirty = true,
  sceneHits = [];
const bench = document.querySelector("#bench");
const receipt = document.querySelector("#receipt");
const access = document.querySelector("#access");
const legend = document.querySelector("#legend");
const reduced = matchMedia("(prefers-reduced-motion: reduce)");
const dev = new URLSearchParams(location.search).get("src") === "dev";
const replay = dev && new URLSearchParams(location.search).has("replay");
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
const compactNumber = (n) =>
  n == null
    ? "?"
    : Math.abs(n) >= 1e6
      ? (n / 1e6).toFixed(1) + "m"
      : Math.abs(n) >= 1000
        ? (n / 1000).toFixed(0) + "k"
        : String(n);
const askOf = (thread) => thread.ask_tokens ?? thread.ask?.tokens ?? null;

const GOLD = "#e7d3ac",
  WHITE = "#c9d1d9",
  DIM = "#5a6470";
const runeColors = {
  ᛗ: "#39e1cf",
  ᚱ: "#f39b48",
  ᛉ: "#ab82f6",
  ᚹ: "#f3cb53",
  ᛒ: "#83e565",
  ᚨ: "#f076a4",
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
const threadTravel=new Map();
let focusRun = null,
  actorRoute = [],
  radial = null;
let arrivals = [],
  sparks = [],
  actor = null,
  oldActor = null,
  actorAt = 0,
  seen = new Set(),
  currentRun = null;
let treeNodes = [],
  lastReceipt = "",
  lastAccess = "",
  pendingFold = 0;
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
const matches = (topics) =>
  [...lifted].every((topic) => list(topics).includes(topic));
function color(topic) {
  const rune = list(state?.heddles).find((h) => h.slug === topic)?.rune;
  if (!rune) return DIM;
  return (
    runeColors[rune] ||
    `hsl(${[...rune].reduce((n, c) => n * 31 + c.codePointAt(0), 0) % 360} 72% 66%)`
  );
}
const firstColor = (topics) => color(list(topics)[0]);
function text(value, x, y, fill = WHITE, size = 12, limit = Infinity) {
  g.font = `${size}px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace`;
  g.fillStyle = fill;
  let line = String(value ?? "unknown");
  if (Number.isFinite(limit)) {
    while (line.length && g.measureText(line).width > limit)
      line = line.slice(0, -1);
    if (line !== String(value ?? "unknown")) line = line.slice(0, -1) + "…";
  }
  g.fillText(line, x, y);
}
function line(x, y, x2, y2, stroke = "#2b3e44", alpha = 1) {
  g.globalAlpha = alpha;
  g.strokeStyle = stroke;
  g.lineWidth = 1;
  g.beginPath();
  g.moveTo(x, y);
  g.lineTo(x2, y2);
  g.stroke();
  g.globalAlpha = 1;
}
const blooms = new Map();
function dot(x, y, r, fill, glow = 0) {
  if (glow > 0) {
    // Blur once per glyph size/hue, then composite a small sprite. Blurring
    // each long branch every frame makes software Canvas miss its beat.
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
function hit(x, y, w, h, kind, data, label) {
  hits.push({ x, y, w, h, kind, data, label });
}
function wrap(value, x, y, maxWidth, size = 11, fill = DIM, maxLines = 3) {
  const words = String(value ?? "unknown").split(" ");
  let row = "",
    count = 0;
  g.font = `${size}px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace`;
  for (const word of words) {
    if (g.measureText(row + word).width > maxWidth && row) {
      text(row, x, y, fill, size, maxWidth);
      y += size * 1.6;
      row = "";
      if (++count >= maxLines) return y;
    }
    row += word + " ";
  }
  text(row, x, y, fill, size, maxWidth);
  return y + size * 1.6;
}
function resize() {
  width = innerWidth;
  height = innerHeight;
  const dpr = Math.min(devicePixelRatio || 1, 2);
  canvas.width = Math.round(width * dpr);
  canvas.height = Math.round(height * dpr);
  screen.setTransform(dpr, 0, 0, dpr, 0, 0);
  sceneCanvas.width = canvas.width;
  sceneCanvas.height = canvas.height;
  sceneContext.setTransform(dpr, 0, 0, dpr, 0, 0);
  sceneDirty = true;
  const right = width >= 1000 ? width * 0.7 : width - 16;
  layout = {
    warp: Math.min(280, Math.max(180, width * 0.17)),
    right,
    top: 200,
    cloth: height - 220,
  };
  layout.wx = layout.warp + 28;
  layout.ww = right - layout.wx - 25;
  rebuild();
}
function beadKey(b) {
  return JSON.stringify([b.at, b.act, b.detail, b.places, b.ctx_after]);
}
function clothRows() {
  return list(state?.cloth?.rows)
    .filter((r) => matches(r.topics))
    .slice()
    .sort((a,b)=>a.run===state?.run?.id?1:b.run===state?.run?.id?-1:String(a.started).localeCompare(String(b.started)));
}
function railLayout() {
  const rows = clothRows();
  let focus = rows.findIndex((r) => r.run === focusRun);
  if (focus < 0) focus = rows.length - 1;
  const center = (layout.right + 28) / 2,
    cardWidth = Math.min(360, (layout.right - 56) * 0.46);
  return rows.map((row, i) => {
    const d = i - focus,
      scale = Math.max(0.35, Math.pow(0.9, Math.abs(d)));
    let offset = cardWidth / 2 + 43;
    for (let j = 1; j < Math.abs(d); j++)
      offset += 75 * Math.max(0.35, Math.pow(0.9, j));
    return {
      row,
      focus: i === focus,
      scale,
      x: d ? center + Math.sign(d) * offset : center,
      w: i === focus ? cardWidth : 65 * scale,
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
        (r) => r.x + r.w / 2 > 28 && r.x - r.w / 2 < layout.right - 28,
      )
    : [];
  const mapped = visible.map((r) => passPlaces(r.row));
  const union =
    mapped.length ? new Set(mapped.filter(p=>p!==null).flat()) : null;
  return [...places.values()].filter(
    (p) => matches(p.topics) && (!union || union.has(p.path)),
  );
}
function rebuild() {
  sceneDirty = true;
  if (!state || !layout) return;
  const root = {
    path: "",
    name: state.repo || "repo",
    children: new Map(),
    depth: 0,
  };
  for (const place of mergedPlaces()) {
    const parts = place.path.split("/").filter(Boolean);
    const compact =
      parts.length > 4 ? [parts[0], parts[1], "…", parts.at(-1)] : parts;
    let node = root,
      path = "";
    compact.forEach((part, i) => {
      const leaf = i === compact.length - 1;
      path = leaf ? place.path : (path ? path + "/" : "") + part;
      const key = leaf ? place.path : part;
      if (!node.children.has(key))
        node.children.set(key, { path, name: part, children: new Map() });
      node = node.children.get(key);
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
      [...node.children].sort((a, b) => a[1].name.localeCompare(b[1].name)),
    );
    for (const child of node.children.values()) visit(child, node, depth + 1);
    if (!node.children.size) leaves.push(node);
    node.topics = node.place?.topics || [
      ...new Set([...node.children.values()].flatMap((n) => n.topics)),
    ];
  }
  visit(root);
  treeNodes = nodes;
  const bottom = layout.cloth - 62,
    top = layout.top + 91;
  const maxDepth = Math.max(1, ...nodes.map((n) => n.depth));
  const spacing = (layout.ww - 72) / Math.max(1, leaves.length - 1);
  const next = new Map();
  leaves.forEach((node, i) =>
    next.set(node.path, {
      x:
        leaves.length === 1
          ? layout.wx + layout.ww / 2
          : layout.wx + 36 + i * spacing,
      y: top + (i % 2) * Math.min(42, (bottom - top) * 0.17),
    }),
  );
  function place(node) {
    if (node.children.size) {
      const children = [...node.children.values()];
      children.forEach(place);
      next.set(node.path, {
        x:
          children.reduce((sum, n) => sum + next.get(n.path).x, 0) /
          children.length,
        y: bottom - ((bottom - top) * node.depth) / maxDepth,
      });
    }
  }
  place(root);
  // The root is an address, not another leaf, including an empty lifted set.
  next.set("", { x: layout.wx + layout.ww * 0.5, y: bottom });
  const changed =
    JSON.stringify([...next]) !== JSON.stringify([...targetPositions]);
  if (changed) {
    positions = new Map([...next].map(([path, p]) => [path, point(path) || p]));
    targetPositions = next;
    movesAt = clock;
  }
  const newest = [...list(state.beads)]
    .reverse()
    .find((b) => list(b.places).length);
  const path = newest?.places.at(-1);
  if (actor?.path !== path) {
    const from = beam(actor?.path),
      to = beam(path);
    let shared = 0;
    while (
      shared < from.length &&
      shared < to.length &&
      from[shared].x === to[shared].x &&
      from[shared].y === to[shared].y
    )
      shared++;
    actorRoute = from.length
      ? [...from.slice(Math.max(0, shared - 1)).reverse(), ...to.slice(shared)]
      : to;
    actorAt = clock;
  }
  actor = path && next.has(path) ? { path, ...next.get(path) } : null;
  updateAccess();
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
function actorPoint() {
  if (!actor) return null;
  const target = point(actor.path) || actor;
  const p =
    reduced.matches || !actorRoute.length
      ? target
      : along(actorRoute, smooth((clock - actorAt) / BEAT));
  return {
    x: Math.max(layout.wx + 8, Math.min(layout.right - 135, p.x - 42)),
    y: p.y + 7,
  };
}
function beam(path) {
  const chain = [];
  let node = treeNodes.find((n) => n.path === path);
  while (node) {
    const p = point(node.path);
    if (p) chain.unshift(p);
    node = node.parent;
  }
  return chain.flatMap((p, i) =>
    i
      ? [
          {
            x: chain[i - 1].x + (p.x - chain[i - 1].x) * 0.22,
            y: chain[i - 1].y + (p.y - chain[i - 1].y) * 0.55,
          },
          p,
        ]
      : [p],
  );
}
function strokePath(points, color, width = 1, alpha = 1, glow = 0) {
  if (points.length < 2) return;
  g.save();
  g.strokeStyle = color;
  g.lineJoin = "round";
  g.lineCap = "round";
  g.beginPath();
  points.forEach((p, i) => (i ? g.lineTo(p.x, p.y) : g.moveTo(p.x, p.y)));
  if (glow) {
    g.globalAlpha = alpha * 0.035;
    g.lineWidth = width + glow;
    g.stroke();
    g.globalAlpha = alpha * 0.09;
    g.lineWidth = width + glow * 0.45;
    g.stroke();
    g.globalAlpha = alpha * 0.16;
    g.lineWidth = width + glow * 0.16;
    g.stroke();
  }
  g.globalAlpha = alpha;
  g.lineWidth = width;
  g.stroke();
  g.restore();
}
function along(points, fraction) {
  const lengths = points
    .slice(1)
    .map((p, i) => Math.hypot(p.x - points[i].x, p.y - points[i].y));
  let distance = lengths.reduce((a, b) => a + b, 0) * fraction;
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
function halo(x, y, r, color, alpha = 0.18) {
  g.save();
  g.globalAlpha = alpha;
  const bloom = g.createRadialGradient(x, y, 0, x, y, r);
  bloom.addColorStop(0, color);
  bloom.addColorStop(1, "transparent");
  g.fillStyle = bloom;
  g.fillRect(x - r, y - r, r * 2, r * 2);
  g.restore();
}
function diamond(x, y, r, color) {
  strokePath(
    [
      { x, y: y - r },
      { x: x + r, y },
      { x, y: y + r },
      { x: x - r, y },
      { x, y: y - r },
    ],
    color,
    1.2,
    0.9,
    6,
  );
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
    arrivals = [];
    currentRun = runId;
  }
  const initial = state === null || state.run?.id !== runId;
  const fresh = list(next.beads).filter((b) => !seen.has(beadKey(b)));
  seen = new Set(list(next.beads).map(beadKey));
  state = next;
  for (const slug of lifted)
    if (!list(state.heddles).some((h) => h.slug === slug)) lifted.delete(slug);
  rebuild();
  if (!initial && !reduced.matches) {
    for (const b of fresh.filter((b) => matches(b.topics)).slice(-12)) {
      const p = point(list(b.places).at(-1));
      if (p)
        arrivals.push({
          path: list(b.places).at(-1),
          born: clock,
          color: firstColor(b.topics),
        });
      if (p)
        for (let i = 0; i < 18; i++)
          sparks.push({
            x: Math.min(layout.right - 100, p.x + 160),
            y: p.y,
            born: clock,
            angle: i * 2.399,
            reach: 28 + (i % 6) * 13,
            color: firstColor(b.topics),
          });
    }
  }
  for(const thread of list(state.hud?.strands)){
    const path=thread.status==='done'?'':list(thread.places).at(-1),prior=threadTravel.get(thread.id);
    if(!prior||prior.path!==path){const from=beam(prior?.path),to=beam(path);let shared=0;while(shared<from.length&&shared<to.length&&from[shared].x===to[shared].x&&from[shared].y===to[shared].y)shared++;
      threadTravel.set(thread.id,{path,born:clock,route:prior?[...from.slice(Math.max(0,shared-1)).reverse(),...to.slice(shared)]:[]});}
  }
  sourceStatus = dev ? "fixture" : "live";
  renderReceipt();
}
function toggle(slug) {
  radial = null;
  actions.lift();
  lifted.has(slug) ? lifted.delete(slug) : lifted.add(slug);
  warpScroll = 0;
  selected = { kind: "heddles" };
  rebuild();
  renderReceipt();
  document.querySelector("#focus").textContent = lifted.size
    ? "@" + [...lifted].join(" ∩ ")
    : "@shuttle";
}
function select(kind, data) {
  sceneDirty = true;
  if (kind === "cloth" && focusRun !== data.run) {
    focusRun = data.run;
    rebuild();
  }
  radial = kind === "place" ? { path: data.path } : null;
  selected = { kind, data };
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
    if (askOf(data) != null)
      button(`ask +${compactNumber(askOf(data))} · grant`, actions.grant);
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
    ...list(state?.hud?.strands).map(thread=>({label:"Open thread "+thread.title,action:()=>select("strand",thread)})),
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
function header(pulse) {
  text("brnrd", 28, 40, GOLD, 24);
  text("/  THE LOOM", 130, 39, DIM, 11);
  text(
    dev
      ? "FIXTURE · " + (replay ? "BOUNDARY REPLAY" : "ILLUSTRATIVE DATA")
      : sourceStatus.toUpperCase(),
    28,
    64,
    dev ? "#f3cb53" : DIM,
    9,
  );
  const gaugeX =
      width >= 1000 ? layout.right + 24 : Math.max(width * 0.57, 340),
    gw = width - gaugeX - 25;
  const railWidth = Math.max(180, gaugeX - 55),
    hed = list(state.heddles);
  text("01 / HEDDLES", 28, 96, DIM, 10);
  text(
    lifted.size
      ? `${lifted.size} LIFTED · INTERSECTION`
      : "LIFT A RUNE TO RAISE ITS WORK",
    160,
    96,
    DIM,
    9,
    railWidth - 150,
  );
  const cell = Math.min(150, railWidth / Math.max(hed.length, 1));
  hed.forEach((h, i) => {
    const x = 28 + i * cell,
      raised = lifted.has(h.slug),
      y = 146 - (raised ? 8 : 0),
      c = color(h.slug);
    g.globalAlpha = 0.3 + clamp(h.lit) * 0.7;
    g.shadowColor = c;
    g.shadowBlur = clamp(h.lit) * (18 + pulse * 12);
    text(h.rune || "?", x + 8, y, c, 50);
    g.shadowBlur = 0;
    g.globalAlpha = 1;
    text(
      `${i < 6 ? i + 1 + " " : ""}${h.slug.replace(/^the-/, "")}`,
      x + 7,
      166,
      raised ? WHITE : DIM,
      10,
      cell - 10,
    );
    if (raised)
      strokePath(
        [
          { x: x + 4, y: 174 },
          { x: x + cell - 15, y: 174 },
        ],
        c,
        2,
        1,
        10,
      );
    hit(x - 8, 103, cell - 3, 73, "heddle", h.slug, "Lift " + h.slug);
  });
  if (!hed.length) text("No heddles yet", 28, 132, DIM);
  line(28, 185, layout.right - 24, 185, "#202b3c");
  text("05 / GAUGE", gaugeX, 30, DIM, 10);
  const words = {
    awake: "weaving",
    listening: "at the shed",
    parked: "in the box",
    "handing-off": "a fresh shuttle threaded",
    released: "released",
  };
  text(
    words[state.shuttle?.state] || known(state.shuttle?.state),
    gaugeX,
    57,
    GOLD,
    24,
    gw,
  );
  // Wrap without elision: the chip is the feed's string, byte for byte.
  const chip = String(state.hud?.chip ?? "unknown");
  g.font = "10px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace";
  let cx = gaugeX,
    cy = 82;
  for (const ch of chip) {
    const w = g.measureText(ch).width;
    if (cx + w > gaugeX + gw) {
      cx = gaugeX;
      cy += 14;
    }
    g.fillStyle = "#a5b8bb";
    g.fillText(ch, cx, cy);
    cx += w;
  }
  const bars = [
    ["session", "session_pct_left"],
    ["week", "week_pct_left"],
    ["fable", "fable_pct_left"],
  ];
  const barY = Math.max(190, cy + 110),
    bw = (gw - 24) / 3;
  bars.forEach(([label, key], i) => {
    const x = gaugeX + i * (bw + 12),
      v = state.hud?.quota?.[key];
    text(label, x, barY, DIM, 9);
    const fuelColor =
      v == null
        ? DIM
        : v < 20
          ? "#f076a4"
          : ["#39e1cf", "#f3cb53", "#ab82f6"][i];
    text(v == null ? "?" : `${v}%`, x, barY + 17, fuelColor, 11);
    line(x, barY + 25, x + bw, barY + 25, "#2b383e");
    if (v != null)
      strokePath(
        [
          { x, y: barY + 25 },
          { x: x + bw * clamp(v / 100), y: barY + 25 },
        ],
        fuelColor,
        3,
        1,
        12,
      );
  });
  const full = state.hud?.full || {},
    resources = full.resources || {},
    allowance = resources.allowance || {};
  const elapsed = full.budget?.elapsed_seconds ?? state.run?.elapsed_s;
  const spent = allowance.spent ?? state.hud?.spend?.tokens,
    budget = allowance.tokens ?? state.hud?.spend?.allowance_tokens;
  const ratio = budget > 0 ? clamp(spent / budget) : null,
    arcY = Math.max(145, cy + 58),
    arcX = gaugeX + 35;
  g.strokeStyle = "#243044";
  g.lineWidth = 5;
  g.beginPath();
  g.arc(arcX, arcY, 28, Math.PI * 0.7, Math.PI * 2.3);
  g.stroke();
  if (ratio !== null) {
    g.strokeStyle = GOLD;
    g.beginPath();
    g.arc(arcX, arcY, 28, Math.PI * 0.7, Math.PI * 0.7 + Math.PI * 1.6 * ratio);
    g.stroke();
  }
  text(
    elapsed == null ? "?" : Math.floor(elapsed / 60) + "m",
    arcX - 18,
    arcY + 4,
    GOLD,
    13,
    40,
  );
  text(
    `${compactNumber(spent)} / ${compactNumber(budget)}`,
    gaugeX + 82,
    arcY - 10,
    WHITE,
    13,
    gw - 100,
  );
  text("SPEND / ALLOWANCE", gaugeX + 82, arcY + 8, DIM, 8);
  const pace = resources.quota?.pacing?.pace;
  text(
    "pace " +
      (pace?.ratio == null ? "unknown" : pace.ratio.toFixed(2) + "×") +
      (pace?.recommendation
        ? " · " + pace.recommendation.replaceAll("_", " ")
        : ""),
    gaugeX + 82,
    arcY + 26,
    pace?.ratio > 1 ? "#f3cb53" : DIM,
    9,
    gw - 88,
  );
  const ctx = state.hud?.ctx_tokens,
    cx2 = gaugeX + gw - 18,
    cy2 = barY + 97;
  for (let i = 0; i < 8; i++)
    line(cx2 - 4, cy2 - i * 7, cx2 + 7, cy2 - i * 7, "#273147");
  if (ctx != null)
    strokePath(
      [
        { x: cx2, y: cy2 },
        { x: cx2, y: cy2 - Math.min(56, (ctx / 50000) * 7) },
      ],
      "#ab82f6",
      5,
      0.9,
      10,
    );
  text(
    "ctx " + compactNumber(ctx),
    gaugeX + gw - 116,
    barY + 57,
    "#ab82f6",
    10,
    100,
  );
  text("50k / tick", gaugeX + gw - 116, barY + 73, DIM, 8, 100);
  const hold = full.resource_hold,
    wait = full.await || {},
    holdFacet = resources.quota?.hold;
  const lamp = hold?.active ? "#f3cb53" : wait.armed ? "#ab82f6" : "#39e1cf";
  dot(gaugeX + 4, barY + 49, 3, lamp, 13);
  const rest = hold?.active
    ? "hold · " + known(hold.reason)
    : wait.resolved
      ? "woke · " + known(wait.outcome)
      : wait.armed
        ? "at the shed"
        : "awake";
  text(rest, gaugeX + 15, barY + 53, WHITE, 9, gw - 130);
  text(
    "slept " +
      (wait.slept_seconds == null
        ? "?"
        : Math.round(wait.slept_seconds) + "s") +
      (holdFacet?.ratio == null ? "" : " · hold " + holdFacet.ratio + "× boot"),
    gaugeX,
    barY + 71,
    DIM,
    9,
    gw - 125,
  );
  const correspondent = resources.correspondent || {},
    quiet = correspondent.quiet_seconds;
  text(
    `quiet ${quiet == null ? "?" : Math.floor(quiet / 60) + "m"} · unread ${known(correspondent.unread_count)}`,
    gaugeX,
    barY + 91,
    WHITE,
    9,
    gw - 120,
  );
  const outbound = full.outbound,
    pending = full.attention?.pending_event_count;
  const delivered = outbound
    ? [
        outbound.replies_current,
        outbound.replies_other,
        outbound.outbound_messages,
      ].reduce((sum, v) => sum + (v || 0), 0)
    : null;
  text(
    `pending ${known(pending)} · deliveries ${known(delivered)}`,
    gaugeX,
    barY + 113,
    DIM,
    9,
    gw,
  );
  const plan = list(state.run?.card?.plan),
    lit =
      list(full.heddles).length ||
      list(state.heddles).filter((h) => h.lit > 0).length;
  text(
    `course ${plan.filter((p) => p.done).length}/${plan.length} · heddles lit ${lit}`,
    gaugeX,
    barY + 132,
    WHITE,
    9,
    gw,
  );
  const stake = allowance.stake;
  let foot = barY + 150;
  if (stake) {
    dot(gaugeX + 4, foot - 3, 3, "#f3cb53", 10);
    text(
      "stake · " +
        (stake.summary || compactNumber(stake.tokens ?? stake.budget_tokens)),
      gaugeX + 14,
      foot,
      GOLD,
      9,
      gw - 20,
    );
    foot += 20;
  }
  for (const thread of list(state.hud?.strands).slice(0, 3)) {
    const ask = askOf(thread),
      fuel =
        thread.allowance > 0
          ? clamp(1 - (thread.spent || 0) / thread.allowance)
          : null;
    text(thread.title, gaugeX, foot, WHITE, 9, gw - 125);
    line(gaugeX, foot + 8, gaugeX + 80, foot + 8, "#293245");
    if (fuel !== null)
      strokePath(
        [
          { x: gaugeX, y: foot + 8 },
          { x: gaugeX + 80 * fuel, y: foot + 8 },
        ],
        GOLD,
        2,
        0.7,
        5,
      );
    if (ask != null) {
      text("ask +" + compactNumber(ask), gaugeX + gw - 143, foot, GOLD, 9, 87);
      g.strokeStyle = GOLD;
      g.strokeRect(gaugeX + gw - 51, foot - 13, 49, 20);
      text("grant", gaugeX + gw - 45, foot + 1, WHITE, 9);
      hit(
        gaugeX + gw - 53,
        foot - 15,
        53,
        25,
        "command",
        { action: "grant" },
        "Grant thread allowance",
      );
    }
    foot += 24;
  }
  bench.style.top = Math.max(192, foot + 15) + "px";
}
function drawWarp() {
  const max = layout.warp - 40;
  text("02 / THE WARP", 28, layout.top, DIM, 10);
  text("INTENT, WAITING FOR A PASS", 28, layout.top + 21, DIM, 9, max);
  let y = layout.top + 62;
  for (const goal of list(state.warp?.goals).slice(0, 2)) {
    text("◎", 28, y, GOLD, 20);
    const end = wrap(goal.title, 55, y - 2, max - 27, 12, GOLD, 2);
    hit(24, y - 20, max, Math.max(38, end - y + 15), "goal", goal, goal.title);
    y = end + 18;
  }
  const items = list(state.warp?.items).filter(
    (w) => matches(w.topics) && !["done", "retired"].includes(w.state),
  );
  const coords = new Map(),
    start = y;
  g.save();
  g.beginPath();
  g.rect(
    16,
    start - 20,
    layout.warp - 16,
    Math.max(0, layout.cloth - start - 18),
  );
  g.clip();
  for (const [i, item] of items.entries()) {
    y = start + i * 84 - warpScroll * 84;
    coords.set(item.id, { x: 35, y });
    const c = firstColor(item.topics),
      held = item.state === "held";
    strokePath(
      [
        { x: 35, y: y + 6 },
        { x: 35, y: y + 67 },
      ],
      c,
      1.7,
      held ? 0.2 : 0.7,
      held ? 0 : 8,
    );
    dot(35, y, held ? 3 : 4, c, held ? 0 : 16);
    text(
      `${item.id} / ${known(item.state)}`,
      49,
      y - 2,
      held ? DIM : c,
      9,
      max - 28,
    );
    wrap(item.title, 49, y + 19, max - 28, 11, held ? DIM : WHITE, 2);
    if (y >= start - 10 && y < layout.cloth - 25)
      hit(25, y - 14, max, 68, "warp", item, item.title);
  }
  for (const item of items)
    for (const need of list(item.needs)) {
      const a = coords.get(item.id),
        b = coords.get(need);
      if (a && b) {
        line(22, a.y, 22, b.y, "#667278", 0.4);
        line(22, a.y, 32, a.y, "#667278", 0.4);
        line(22, b.y, 32, b.y, "#667278", 0.4);
      }
    }
  g.restore();
  if (!items.length) text("No work in this shed", 28, start, DIM, 11, max);
  line(layout.warp, 184, layout.warp, layout.cloth - 25, "#243239");
}
function drawTree(pulse) {
  const { wx, ww, cloth, right } = layout;
  text("03 / THE WINDOW", wx, layout.top + 13, WHITE, 10);
  text(
    `${mergedPlaces().length} PLACES / ${list(state.beads).filter((b) => matches(b.topics)).length} BOUNDARIES`,
    wx,
    layout.top + 34,
    DIM,
    9,
    ww,
  );
  const minY = layout.top + 52,
    maxY = cloth - 13;
  g.save();
  g.beginPath();
  g.rect(wx, minY, ww, maxY - minY);
  g.clip();
  const focusPaths = focusedPaths();
  const root = point("");
  if (root) halo(root.x, root.y, Math.min(ww * 0.45, 240), "#39e1cf", 0.075);
  for (const node of treeNodes) {
    const p = point(node.path);
    if (!p || !node.parent) continue;
    const parent = point(node.parent.path);
    if (!parent) continue;
    const c = firstColor(node.topics),
      elbow = {
        x: parent.x + (p.x - parent.x) * 0.22,
        y: parent.y + (p.y - parent.y) * 0.55,
      };
    const focused = [...focusPaths].some(
      (path) => path === node.path || path.startsWith(node.path + "/"),
    );
    strokePath([parent, elbow, p], c, 1.4, focused ? 0.46 : 0.09, 7);
  }
  const trail = list(state.beads)
    .filter(
      (b) => matches(b.topics) && targetPositions.has(list(b.places).at(-1)),
    )
    .slice(-8);
  trail.forEach((b, i) => {
    const path = list(b.places).at(-1),
      p = point(path),
      c = firstColor(b.topics),
      alpha = (i + 1) / trail.length;
    strokePath(beam(path), c, 2, alpha * 0.22, 11);
    halo(p.x, p.y, 17 + i * 2, c, alpha * 0.16);
  });
  const leaves = treeNodes.filter((n) => n.place),
    labelWidth = Math.max(
      45,
      Math.min(140, (ww - 50) / Math.max(1, Math.ceil(leaves.length / 2)) - 12),
    );
  for (const node of treeNodes) {
    const p = point(node.path);
    if (!p) continue;
    const focused =
      !node.path ||
      [...focusPaths].some(
        (path) => path === node.path || path.startsWith(node.path + "/"),
      );
    g.globalAlpha = focused ? 1 : 0.3;
    const c = node.path ? firstColor(node.topics) : GOLD;
    const radius = node.place
      ? 3 + clamp(node.place.heat) * 4
      : node.path
        ? 3
        : 7;
    halo(
      p.x,
      p.y,
      radius * 6,
      c,
      node.place?.heat == null ? 0.12 : 0.14 + clamp(node.place.heat) * 0.12,
    );
    dot(p.x, p.y, radius, c, 18);
    dot(p.x, p.y, Math.max(1, radius * 0.38), "#eefaff");
    if (node.place) {
      const ly = p.y - 18;
      text(
        node.name,
        Math.max(
          wx + 4,
          Math.min(right - labelWidth - 15, p.x - labelWidth * 0.48),
        ),
        ly,
        lifted.size ? c : WHITE,
        11,
        labelWidth,
      );
      hit(
        p.x - labelWidth * 0.5,
        p.y - 32,
        labelWidth,
        50,
        "place",
        node.place,
        node.path,
      );
      if (node.place.knots > 0) {
        const kx = p.x + 14,
          ky = p.y + 13;
        diamond(kx, ky, 4, c);
        g.strokeStyle = c;
        g.globalAlpha = 0.45;
        g.beginPath();
        g.arc(kx, ky, 8, 0, Math.PI * 2);
        g.stroke();
        g.globalAlpha = 1;
        text(node.place.knots, kx + 12, ky + 3, c, 9);
      }
      const beads = list(state.beads)
        .filter((b) => list(b.places).includes(node.path) && matches(b.topics))
        .slice(-5);
      beads.forEach((b, j) => {
        const bx = p.x - 12 - j * 7,
          by = p.y + 13;
        dot(bx, by, 1.8, firstColor(b.topics), 5);
        hit(bx - 3, by - 5, 6, 10, "bead", b, b.detail);
      });
    } else {
      text(
        node.path ? node.name + "/" : node.name,
        p.x - Math.min(100, node.name.length * 3),
        node.path ? p.y + 23 : p.y - 19,
        node.path ? DIM : GOLD,
        node.path ? 10 : 12,
        Math.min(220, ww),
      );
    }
    if (selected.kind === "place" && selected.data.path === node.path) {
      g.strokeStyle = GOLD;
      g.beginPath();
      g.arc(p.x, p.y, radius + 10, 0, Math.PI * 2);
      g.stroke();
    }
  }
  g.globalAlpha = 1;
  g.restore();
  if (root) {
    strokePath(
      [
        { x: root.x, y: root.y + 10 },
        { x: root.x, y: cloth - 14 },
        { x: right - 63, y: cloth - 14 },
        { x: right - 63, y: cloth },
      ],
      GOLD,
      1.5,
      0.65,
      8,
    );
  }
  if (!leaves.length)
    text(
      lifted.size ? "No places share these layers" : "No measured places yet",
      wx + 24,
      minY + 50,
      DIM,
      12,
      ww - 30,
    );
  const focused=railLayout().find(r=>r.focus)?.row;
  if(focused&&passPlaces(focused)===null)text('Place history not attested for this pass',wx,layout.top+49,DIM,9,ww);
}
function drawThreads(){
  const {wx,ww,cloth,right}=layout,root=point('')||{x:wx+ww*.5,y:cloth-62};
  const threads=list(state.hud?.strands).filter(thread=>[...lifted].every(slug=>list(list(state.heddles).find(h=>h.slug===slug)?.signature?.threads).includes(thread.id)));
  threads.forEach((thread,i)=>{
    const travel=threadTravel.get(thread.id),path=travel?.path,at=point(path),t=smooth((clock-(travel?.born||0))/BEAT);
    const p=at?(reduced.matches||!travel?.route.length?at:along(travel.route,t)):null;
    const x=p?p.x:wx+10+i*150,y=p?p.y+24:cloth-31,titleX=x>right-170?x-150:x+21;
    strokePath([root,{x,y:y-5}],GOLD,1,.1,0);halo(x+8,y-5,21,GOLD,.18);text('⌁',x,y,GOLD,17);text(thread.title,titleX,y,WHITE,9,145);
    const fuel=thread.allowance>0?clamp(1-(thread.spent||0)/thread.allowance):null;
    line(titleX,y+9,titleX+74,y+9,'#273144');if(fuel!==null)strokePath([{x:titleX,y:y+9},{x:titleX+74*fuel,y:y+9}],GOLD,2,.7,7);
    hit(Math.min(x,titleX)-4,y-18,165,34,'strand',thread,thread.title);
  });
}
function drawMotion(pulse) {
  g.save();
  g.beginPath();
  g.rect(layout.wx, layout.top + 52, layout.ww, layout.cloth - layout.top - 65);
  g.clip();
  for (const arrival of arrivals) {
    const route = beam(arrival.path),
      t = smooth((clock - arrival.born) / BEAT);
    if (!route.length) continue;
    strokePath(route, arrival.color, 2.5, (1 - t) * 0.7, 15);
    for (let i = 0; i < 7; i++) {
      const p = along(route, Math.max(0, t - i * 0.025));
      if (p) {
        g.globalAlpha = 1 - i / 7;
        dot(p.x, p.y, 4 - i * 0.35, arrival.color, 18);
        g.globalAlpha = 1;
      }
    }
  }
  arrivals = arrivals.filter((a) => clock - a.born < BEAT);
  const a = actorPoint();
  if (a && state.run) {
    const bob = reduced.matches ? 0 : (pulse - 0.5) * 4;
    const ay = a.y + bob;
    halo(a.x + 42, ay - 5, 48, GOLD, 0.23);
    g.shadowBlur = 18;
    g.shadowColor = GOLD;
    text(state.run.mood_glyph || "unknown", a.x, ay, GOLD, 17, 118);
    g.shadowBlur = 0;
    hit(a.x - 5, ay - 24, 120, 35, "run", null, "Open shuttle card");
    const blocks = list(state.beads)
      .filter((b) => matches(b.topics))
      .slice(-5);
    blocks.forEach((b, i) => {
      const x = a.x + 104,
        y = ay + 8 - i * 7;
      strokePath(
        [
          { x, y },
          { x: x + 18, y },
        ],
        firstColor(b.topics),
        3,
        0.45 + i * 0.1,
        10,
      );
      hit(x - 3, y - 4, 24, 7, "bead", b, b.detail);
    });
  }
  for (const spark of sparks) {
    const age = clamp((clock - spark.born) / BEAT),
      t = smooth(age);
    g.globalAlpha = (1 - age) * (1 - age);
    const x =
      (a ? a.x + 42 : spark.x) + Math.cos(spark.angle) * spark.reach * t;
    const y =
      (a ? a.y - 5 : spark.y) +
      Math.sin(spark.angle) * spark.reach * t +
      38 * age * age;
    strokePath(
      [
        { x, y },
        {
          x: x - Math.cos(spark.angle) * 5 * (1 - age),
          y: y - Math.sin(spark.angle) * 5 * (1 - age),
        },
      ],
      spark.color,
      2,
      1 - age,
      10,
    );
    dot(x, y, 1.7, spark.color, 9);
    g.globalAlpha = 1;
  }
  sparks = sparks.filter((s) => clock - s.born < BEAT);
  drawThreads();
  g.restore();
  const live = railLayout().find((r) => r.row.run === state.run?.id);
  if (live && !live.focus) {
    halo(
      live.x,
      layout.cloth + 56,
      23 + pulse * 8,
      firstColor(live.row.topics),
      0.18,
    );
  }
}
function drawCloth() {
  const y = layout.cloth,
    entries = railLayout();
  text("THE CLOTH / ← → / SCROLL TO FOCUS", 28, y - 5, DIM, 9);
  strokePath(
    [
      { x: 28, y: y + 55 },
      { x: layout.right - 28, y: y + 55 },
    ],
    "#416877",
    2,
    0.5,
    8,
  );
  g.save();
  g.beginPath();
  g.rect(24, y + 1, layout.right - 48, 119);
  g.clip();
  for (const entry of entries) {
    const { row, focus, x, w, scale } = entry;
    if (x + w / 2 < 25 || x - w / 2 > layout.right - 25) continue;
    const topics = list(row.topics),
      c = firstColor(topics);
    if (focus) {
      g.fillStyle = "#0e1524";
      g.fillRect(x - w / 2, y + 7, w, 105);
      g.strokeStyle = c;
      g.strokeRect(x - w / 2 + 0.5, y + 7.5, w, 105);
      strokePath(
        [
          { x: x - w / 2, y: y + 7 },
          { x: x + w / 2, y: y + 7 },
        ],
        c,
        2,
        0.9,
        9,
      );
      text(row.name || row.run, x - w / 2 + 14, y + 28, WHITE, 12, w - 28);
      topics.slice(0, 4).forEach((topic, i) => {
        const rune =
          list(state.heddles).find((h) => h.slug === topic)?.rune || "?";
        const tx = x - w / 2 + 14 + i * 76;
        g.strokeStyle = color(topic);
        g.strokeRect(tx, y + 38, 70, 21);
        text(rune, tx + 5, y + 54, color(topic), 16);
        text(topic.replace(/^the-/, ""), tx + 22, y + 52, WHITE, 8, 45);
      });
      text(
        `${known(row.shell)} / ${known(row.core)}`,
        x - w / 2 + 14,
        y + 75,
        DIM,
        10,
        w - 28,
      );
      const end = row.ended || state.at,
        start = Date.parse(row.started),
        seconds = (Date.parse(end) - start) / 1000;
      text(
        `${known(row.knots)} knots · PR ${
          list(row.prs)
            .map((n) => "#" + n)
            .join(", ") || "none"
        } · ${Number.isFinite(seconds) ? Math.max(0, Math.round(seconds / 60)) + "m" : "unknown"}`,
        x - w / 2 + 14,
        y + 96,
        GOLD,
        10,
        w - 28,
      );
      hit(x - w / 2, y + 7, w, 105, "cloth", row, row.name || row.run);
    } else {
      const cy = y + 55,
        r = 12 * scale;
      halo(x, cy, 26 * scale, c, 0.2);
      dot(x, cy, r * 0.45, c, 12);
      topics.forEach((topic, i) => {
        g.strokeStyle = color(topic);
        g.lineWidth = 2;
        g.beginPath();
        g.arc(
          x,
          cy,
          r + 4,
          (i * Math.PI * 2) / topics.length + 0.09,
          ((i + 1) * Math.PI * 2) / topics.length - 0.09,
        );
        g.stroke();
      });
      text(
        row.started ? row.started.slice(11, 16) : "?",
        x - 18,
        y + 84,
        DIM,
        9,
      );
      hit(x - 22, y + 28, 44, 66, "cloth", row, row.name || row.run);
    }
  }
  g.restore();
  if (!entries.length) text("No passes in this shed", 28, y + 52, DIM, 11);
}
function tooltip() {
  if (!hovered) return;
  const found = hits.find(
    (h) =>
      h.kind === hovered.kind &&
      (h.data === hovered.data ||
        (h.kind === "cloth" && h.data.run === hovered.data.run)),
  );
  if (!found) return;
  const d = found.data,
    lines =
      found.kind === "cloth"
        ? [
            d.name || d.run,
            `${known(d.shell)} / ${known(d.core)}`,
            `PR ${
              list(d.prs)
                .map((n) => "#" + n)
                .join(", ") || "none"
            } · ${known(d.knots)} knots`,
          ]
        : [found.label];
  const w = Math.min(330, width - 32),
    h = 24 + lines.length * 18 + (found.kind === "cloth" ? 32 : 0),
    x = Math.min(width - w - 16, Math.max(16, found.x)),
    y = Math.max(170, found.y - h - 12);
  g.shadowColor = "#000";
  g.shadowBlur = 24;
  g.fillStyle = "#0c1221";
  g.fillRect(x, y, w, h);
  g.shadowBlur = 0;
  g.strokeStyle = "#53647e";
  g.strokeRect(x + 0.5, y + 0.5, w, h);
  lines.forEach((value, i) =>
    text(value, x + 13, y + 23 + i * 18, i ? WHITE : GOLD, 11, w - 26),
  );
  if (found.kind === "cloth")
    list(d.topics)
      .slice(0, 4)
      .forEach((topic, i) => {
        const rune =
          list(state.heddles).find((h) => h.slug === topic)?.rune || "?";
        const cx = x + 14 + i * 72,
          cy = y + h - 12;
        g.strokeStyle = color(topic);
        g.strokeRect(cx - 4, cy - 18, 65, 24);
        text(rune, cx, cy, color(topic), 17);
        text(topic.replace(/^the-/, ""), cx + 18, cy - 2, WHITE, 8, 43);
      });
}
function drawRadial() {
  if (!radial) return;
  const p = point(radial.path);
  if (!p) return;
  const x = Math.max(layout.wx + 93, Math.min(layout.right - 98, p.x)),
    y = Math.max(layout.top + 125, Math.min(layout.cloth - 78, p.y));
  halo(x, y, 97, "#07080d", 0.98);
  g.fillStyle = "#0b1220ee";
  g.beginPath();
  g.arc(x, y, 73, 0, Math.PI * 2);
  g.fill();
  g.strokeStyle = "#576a81";
  g.beginPath();
  g.arc(x, y, 55, 0, Math.PI * 2);
  g.stroke();
  ["fold", "explain", "fix", "test", "split", "read"].forEach((action, i) => {
    const angle = -Math.PI / 2 + (i * Math.PI) / 3,
      ax = x + Math.cos(angle) * 61,
      ay = y + Math.sin(angle) * 61;
    g.fillStyle = "#111b2a";
    g.fillRect(ax - 27, ay - 11, 54, 22);
    g.strokeStyle = GOLD;
    g.strokeRect(ax - 27, ay - 11, 54, 22);
    text(action, ax - 21, ay + 4, WHITE, 10, 48);
    hit(ax - 29, ay - 13, 58, 26, "command", { action }, action);
  });
  text("@place", x - 21, y + 4, GOLD, 9);
}
function paintScene() {
  g.clearRect(0, 0, width, height);
  g.fillStyle = "#07080d";
  g.fillRect(0, 0, width, height);
  const glow = g.createRadialGradient(
    width * 0.44,
    height * 0.5,
    20,
    width * 0.44,
    height * 0.5,
    width * 0.52,
  );
  glow.addColorStop(0, "#111e3266");
  glow.addColorStop(1, "#07080d00");
  g.fillStyle = glow;
  g.fillRect(0, 0, width, height);
  if (state) {
    header(0.5);
    drawWarp();
    drawTree(0);
    drawCloth(0);
  } else {
    text("brnrd / the loom", 28, 42, GOLD, 23);
    text(
      sourceStatus === "connecting" ? "Waiting for the frame…" : sourceStatus,
      28,
      86,
      DIM,
    );
  }
}
function draw(timestamp) {
  if (!previous) previous = timestamp;
  if (!paused) clock += Math.min(timestamp - previous, 80);
  previous = timestamp;
  const pulse = reduced.matches
    ? 0
    : smooth(1 - Math.abs(((clock % BEAT) / BEAT) * 2 - 1));
  // The scene changes on measured updates or a camera transition. The body,
  // travelling light and sparks keep their own animation at display tempo.
  if (sceneDirty || (!reduced.matches && clock - movesAt < BEAT)) {
    g = sceneContext;
    hits = [];
    paintScene();
    sceneHits = hits;
    g = screen;
    sceneDirty = false;
  }
  g.clearRect(0, 0, width, height);
  g.drawImage(sceneCanvas, 0, 0, width, height);
  hits = [...sceneHits];
  if (state) {
    drawMotion(pulse);
    tooltip();
    drawRadial();
  }
  text(
    paused
      ? "Ⅱ BEAT PAUSED"
      : reduced.matches
        ? "REDUCED MOTION"
        : "600 ms / BEAT",
    28,
    height - 92,
    DIM,
    9,
  );
  text("? LEGEND", layout.right - 94, height - 92, DIM, 9);
  hit(layout.right - 100, height - 108, 90, 24, "legend", null, "Open legend");
  if (sourceStatus !== "live" && sourceStatus !== "fixture" && state)
    text(
      sourceStatus.toUpperCase() + " · LAST RECEIVED " + known(state.at),
      layout.wx,
      height - 92,
      "#f3cb53",
      9,
      layout.ww - 100,
    );
  requestAnimationFrame(draw);
}
reduced.addEventListener("change", () => {
  sceneDirty = true;
});
canvas.addEventListener("pointermove", (event) => {
  hovered =
    [...hits]
      .reverse()
      .find(
        (h) =>
          event.clientX >= h.x &&
          event.clientX <= h.x + h.w &&
          event.clientY >= h.y &&
          event.clientY <= h.y + h.h,
      ) || null;
  canvas.style.cursor = hovered ? "pointer" : "default";
});
canvas.addEventListener("pointerleave", () => (hovered = null));
canvas.addEventListener("click", (event) => {
  const h = [...hits]
    .reverse()
    .find(
      (h) =>
        event.clientX >= h.x &&
        event.clientX <= h.x + h.w &&
        event.clientY >= h.y &&
        event.clientY <= h.y + h.h,
    );
  if (!h) return;
  if (h.kind === "command") actions[h.data.action]();
  else if (h.kind === "heddle") toggle(h.data);
  else if (h.kind === "legend") legend.showModal();
  else select(h.kind, h.data);
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
    selected = { kind: "run" };
    document.querySelector("#focus").textContent = "@shuttle";
    rebuild();
    renderReceipt();
    bench.classList.remove("open");
    if (legend.open) legend.close();
  } else if (event.key === "?") {
    event.preventDefault();
    legend.open ? legend.close() : legend.showModal();
  } else if (event.key === " " && !event.target.matches("button")) {
    event.preventDefault();
    paused = !paused;
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
    stream.onerror = () => (sourceStatus = "reconnecting");
  }
}
start();
