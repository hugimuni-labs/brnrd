/* The renderer owns pixels and selection. Only the feed owns facts. */
const canvas = document.querySelector("#loom");
const g = canvas.getContext("2d");
const bench = document.querySelector("#bench");
const receipt = document.querySelector("#receipt");
const access = document.querySelector("#access");
const legend = document.querySelector("#legend");
const reduced = matchMedia("(prefers-reduced-motion: reduce)");
const dev = new URLSearchParams(location.search).get("src") === "dev";
const replay = dev && new URLSearchParams(location.search).has("replay");
const BEAT = 600;
const GOLD = "#e7d3ac",
  WHITE = "#dbe4e0",
  DIM = "#779097";
const runeColors = {
  ᛗ: "#8acdc3",
  ᚱ: "#c4a3e5",
  ᛉ: "#a8bbec",
  ᚹ: "#dfba7a",
  ᛒ: "#c5cd91",
  ᚨ: "#dba0af",
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
  treeScroll = 0,
  warpScroll = 0;
let sparks = [],
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
    `hsl(${[...rune].reduce((n, c) => n * 31 + c.codePointAt(0), 0) % 360} 42% 70%)`
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
function dot(x, y, r, fill, glow = 0) {
  g.fillStyle = fill;
  g.shadowColor = fill;
  g.shadowBlur = glow;
  g.beginPath();
  g.arc(x, y, r, 0, Math.PI * 2);
  g.fill();
  g.shadowBlur = 0;
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
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  const right = width >= 1000 ? width * 0.7 : width - 16;
  layout = {
    warp: Math.min(280, Math.max(180, width * 0.17)),
    right,
    top: 200,
    cloth: height - 150,
  };
  layout.wx = layout.warp + 28;
  layout.ww = right - layout.wx - 25;
  rebuild();
}
function beadKey(b) {
  return JSON.stringify([b.at, b.act, b.detail, b.places, b.ctx_after]);
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
  return [...places.values()].filter((p) => matches(p.topics));
}
function rebuild() {
  if (!state || !layout) return;
  const root = {
    path: "",
    name: state.repo || "repo",
    children: new Map(),
    depth: 0,
  };
  for (const p of mergedPlaces()) {
    let node = root,
      path = "";
    for (const part of p.path.split("/").filter(Boolean)) {
      path = path ? path + "/" + part : part;
      if (!node.children.has(part))
        node.children.set(part, {
          path,
          name: part,
          children: new Map(),
          depth: node.depth + 1,
          parent: node,
        });
      node = node.children.get(part);
    }
    node.place = p;
  }
  const nodes = [];
  function visit(node) {
    nodes.push(node);
    for (const child of [...node.children.values()].sort((a, b) =>
      a.name.localeCompare(b.name),
    ))
      visit(child);
  }
  visit(root);
  treeNodes = nodes;
  const row = height >= 900 ? 36 : 22,
    base = layout.cloth - 47;
  const maxVisible = Math.max(1, Math.floor((base - layout.top - 56) / row));
  treeScroll = Math.max(
    0,
    Math.min(treeScroll, Math.max(0, nodes.length - 1 - maxVisible)),
  );
  const next = new Map();
  nodes.forEach((node, i) =>
    next.set(node.path, {
      x: layout.wx + 24 + node.depth * 20,
      y: base - (i - treeScroll) * row,
    }),
  );
  positions = new Map([...next].map(([path, p]) => [path, point(path) || p]));
  targetPositions = next;
  movesAt = clock;
  const newest = [...list(state.beads)]
    .reverse()
    .find((b) => list(b.places).length);
  const path = newest?.places.at(-1);
  oldActor = actorPoint();
  actor = path && next.has(path) ? { path, ...next.get(path) } : null;
  actorAt = clock;
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
  const dest = point(actor.path) || actor;
  const end = {
    x: Math.min(
      layout.right - 128,
      dest.x + Math.min(220, actor.path.split("/").at(-1).length * 7 + 28),
    ),
    y: dest.y,
  };
  const start = oldActor || end,
    t = reduced.matches ? 1 : smooth((clock - actorAt) / BEAT);
  return {
    x: start.x + (end.x - start.x) * t,
    y: start.y + (end.y - start.y) * t,
  };
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
        for (let i = 0; i < 14; i++)
          sparks.push({
            x: Math.min(layout.right - 100, p.x + 160),
            y: p.y,
            born: clock,
            angle: i * 2.399,
            reach: 22 + (i % 5) * 9,
            color: firstColor(b.topics),
          });
    }
  }
  sourceStatus = dev ? "fixture" : "live";
  renderReceipt();
}
function toggle(slug) {
  lifted.has(slug) ? lifted.delete(slug) : lifted.add(slug);
  treeScroll = 0;
  warpScroll = 0;
  selected = { kind: "heddles" };
  rebuild();
  renderReceipt();
  document.querySelector("#focus").textContent = lifted.size
    ? "@" + [...lifted].join(" ∩ ")
    : "@shuttle";
}
function select(kind, data) {
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
    const beads = list(state.beads)
      .filter((b) => matches(b.topics))
      .slice(-5);
    section("Context / measured blocks");
    const stack = element("div", undefined, "stack");
    for (const b of beads) {
      const block = element(
        "button",
        `${known(b.act)}  +${number(b.delta)}  → ${number(b.ctx_after)}`,
        "block",
      );
      block.onclick = () => select("bead", b);
      stack.append(block);
    }
    receipt.append(stack);
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
    dev ? "#dfba7a" : DIM,
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
      y = 130 - (raised ? 6 : 0),
      c = color(h.slug);
    g.globalAlpha = 0.3 + clamp(h.lit) * 0.7;
    g.shadowColor = c;
    g.shadowBlur = clamp(h.lit) * (8 + pulse * 7);
    text(h.rune || "?", x, y, c, 27);
    g.shadowBlur = 0;
    g.globalAlpha = 1;
    text(
      `${i < 6 ? i + 1 + " " : ""}${h.slug.replace(/^the-/, "")}`,
      x + 29,
      y - 3,
      raised ? WHITE : DIM,
      10,
      cell - 34,
    );
    if (raised) line(x, y + 9, x + cell - 12, y + 9, c);
    hit(x - 8, 105, cell - 3, 46, "heddle", h.slug, "Lift " + h.slug);
  });
  if (!hed.length) text("No heddles yet", 28, 132, DIM);
  line(28, 163, layout.right - 24, 163);
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
    53,
    GOLD,
    14,
    gw,
  );
  // Wrap without elision: the chip is the feed's string, byte for byte.
  const chip = String(state.hud?.chip ?? "unknown");
  g.font = "10px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace";
  let cx = gaugeX,
    cy = 76;
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
  const barY = Math.max(112, cy + 20),
    bw = (gw - 24) / 3;
  bars.forEach(([label, key], i) => {
    const x = gaugeX + i * (bw + 12),
      v = state.hud?.quota?.[key];
    text(label, x, barY, DIM, 9);
    text(v == null ? "?" : `${v}%`, x, barY + 17, GOLD, 11);
    line(x, barY + 25, x + bw, barY + 25, "#2b383e");
    if (v != null) line(x, barY + 25, x + bw * clamp(v / 100), barY + 25, GOLD);
  });
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
    line(35, y + 6, 35, y + 67, c, held ? 0.2 : 0.45);
    dot(35, y, held ? 2 : 3, c, held ? 0 : 8);
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
  text("03 / THE WINDOW", wx, layout.top, DIM, 10);
  const places = mergedPlaces();
  text(
    `${places.length} PLACES  /  ${list(state.beads).filter((b) => matches(b.topics)).length} BOUNDARIES`,
    wx,
    layout.top + 21,
    DIM,
    9,
    ww,
  );
  const overflow = Math.max(
    0,
    treeNodes.length -
      1 -
      Math.floor((cloth - 47 - layout.top - 56) / (height >= 900 ? 36 : 22)),
  );
  if (overflow)
    text(
      `↑ ${overflow} more rows · scroll the tree${treeScroll ? " · offset " + treeScroll : ""}`,
      wx,
      layout.top + 39,
      DIM,
      9,
    );
  const minY = layout.top + 52,
    maxY = cloth - 25;
  g.save();
  g.beginPath();
  g.rect(wx, minY, ww, maxY - minY);
  g.clip();
  // A faint glyph grid supports reading; it carries no activity meaning.
  for (let y = minY + 12; y < maxY; y += 22)
    for (let x = wx + 4; x < right - 20; x += 20) dot(x, y, 0.5, "#17262c");
  for (const node of treeNodes) {
    const p = point(node.path);
    if (!p) continue;
    if (node.parent) {
      const parent = point(node.parent.path);
      if (parent) {
        line(parent.x + 3, parent.y - 7, parent.x + 3, p.y - 4, "#314b50", 0.7);
        line(parent.x + 3, p.y - 4, p.x - 7, p.y - 4, "#314b50", 0.7);
      }
    }
    if (p.y < minY + 10 || p.y > maxY) continue;
    const active =
      selected.kind === "place" && selected.data.path === node.path;
    if (active) {
      g.fillStyle = "#b3d6ca10";
      g.fillRect(p.x - 8, p.y - 17, right - p.x - 26, 24);
    }
    if (!node.place) {
      text(
        node.depth ? node.name + "/" : node.name,
        p.x,
        p.y,
        node.depth ? DIM : GOLD,
        node.depth ? 11 : 13,
        right - p.x - 28,
      );
    } else {
      const heat = node.place.heat;
      g.globalAlpha = heat == null ? 0.55 : 0.38 + clamp(heat) * 0.62;
      const c = firstColor(node.place.topics);
      text(node.name, p.x, p.y, c, 12, right - p.x - 160);
      g.globalAlpha = 1;
      const knots = node.place.knots;
      if (knots > 0) {
        text(`⊙ ${knots}`, right - 69, p.y, c, 10);
      }
      hit(
        p.x - 6,
        p.y - 16,
        Math.max(80, right - p.x - 25),
        22,
        "place",
        node.place,
        node.path,
      );
      const beads = list(state.beads)
        .filter((b) => list(b.places).includes(node.path) && matches(b.topics))
        .slice(-6);
      beads.forEach((b, i) => {
        const x = right - 98 - i * 9;
        dot(x, p.y - 4, 2, firstColor(b.topics));
        hit(x - 4, p.y - 10, 8, 12, "bead", b, b.detail);
      });
    }
  }
  const a = actorPoint();
  if (a && state.run) {
    const glyph = state.run.mood_glyph || "unknown";
    g.shadowBlur = reduced.matches ? 0 : 12 + pulse * 7;
    g.shadowColor = GOLD;
    text(glyph, a.x, a.y - 2, GOLD, 14, 120);
    g.shadowBlur = 0;
    hit(a.x - 5, a.y - 20, 120, 30, "run", null, "Open shuttle card");
    const blocks = list(state.beads)
      .filter((b) => matches(b.topics))
      .slice(-5);
    blocks.forEach((b, i) => {
      g.fillStyle = "#e7d3ac";
      g.globalAlpha = 0.16 + i * 0.06;
      g.fillRect(a.x + i * 17, a.y + 7, 13, 3);
      g.globalAlpha = 1;
    });
  }
  for (const s of sparks) {
    const t = smooth((clock - s.born) / BEAT);
    g.globalAlpha = 1 - t;
    dot(
      (a ? a.x + 32 : s.x) + Math.cos(s.angle) * s.reach * t,
      (a ? a.y - 8 : s.y) + Math.sin(s.angle) * s.reach * t,
      1.5,
      s.color,
      6,
    );
    g.globalAlpha = 1;
  }
  sparks = sparks.filter((s) => clock - s.born < BEAT);
  g.restore();
  const root = point("");
  if (root) {
    const nowX = right - 63;
    line(
      root.x + 3,
      Math.min(root.y + 8, cloth - 22),
      root.x + 3,
      cloth - 22,
      GOLD,
      0.5,
    );
    line(root.x + 3, cloth - 22, nowX, cloth - 22, GOLD, 0.25);
    line(nowX, cloth - 22, nowX, cloth, GOLD, 0.5);
  }
  if (!places.length)
    text(
      lifted.size ? "No places share these layers" : "No measured places yet",
      wx + 24,
      minY + 50,
      DIM,
      12,
      ww - 30,
    );
  // Threads have identities and allowance, but no place in this contract.
  // They branch from the pass root, never from a guessed file.
  const threads = list(state.hud?.strands).filter((thread) =>
    [...lifted].every((slug) =>
      list(
        list(state.heddles).find((h) => h.slug === slug)?.signature?.threads,
      ).includes(thread.id),
    ),
  );
  threads.slice(0, 3).forEach((thread, i) => {
    const x = wx + ww * 0.55,
      y = cloth - 67 - i * 39;
    line(wx + 28, cloth - 22, x - 12, y - 5, "#4e5b56", 0.35);
    text("⌁", x, y, GOLD, 14);
    text(
      thread.title,
      x + 19,
      y,
      thread.status === "live" ? WHITE : DIM,
      10,
      ww * 0.45 - 24,
    );
    const fraction =
      thread.allowance > 0
        ? clamp(1 - (thread.spent || 0) / thread.allowance)
        : null;
    line(x + 20, y + 9, x + 110, y + 9, "#324047");
    if (fraction !== null)
      line(x + 20, y + 9, x + 20 + 90 * fraction, y + 9, GOLD, 0.6);
    text(thread.status, x + 118, y + 12, DIM, 8, Math.max(0, ww * 0.45 - 120));
    hit(x - 5, y - 15, ww * 0.45, 31, "strand", thread, thread.title);
  });
  if (threads.length > 3)
    text(
      `+${threads.length - 3} threads · open card`,
      wx + ww * 0.55,
      cloth - 185,
      DIM,
      9,
    );
}
function drawCloth() {
  const y = layout.cloth,
    x = 28,
    end = layout.right - 28;
  const rows = list(state.cloth?.rows)
    .filter((r) => matches(r.topics))
    .slice()
    .sort((a, b) => String(a.started).localeCompare(String(b.started)));
  line(x, y, end, y, "#4d645f", 0.8);
  text("THE CLOTH", x, y + 34, DIM, 9);
  text("PAST → NOW", end - 87, y + 34, DIM, 9);
  rows.forEach((r, i) => {
    const px =
      x + 30 + (rows.length === 1 ? 1 : i / (rows.length - 1)) * (end - x - 65);
    const topics = list(r.topics),
      c = firstColor(topics);
    dot(px, y, r.ended ? 4 : 6, c, r.ended ? 0 : 13);
    topics.slice(1).forEach((topic, j) => {
      g.strokeStyle = color(topic);
      g.beginPath();
      g.arc(px, y, 7 + j * 3, Math.PI, Math.PI * 2);
      g.stroke();
    });
    if (r.prs?.length) text("◇", px - 4, y - 17, c, 12);
    if (rows.length <= 10)
      text(
        r.started ? String(r.started).slice(11, 16) : "?",
        px - 16,
        y + 19,
        DIM,
        9,
      );
    hit(px - 9, y - 26, 18, 50, "cloth", r, r.name || r.run);
  });
  const done = list(state.warp?.items).filter(
    (w) => w.state === "done" && matches(w.topics),
  );
  done.forEach((w, i) => {
    text("✓", 30 + i * 15, y - 10, firstColor(w.topics), 10);
    hit(28 + i * 15, y - 24, 14, 18, "warp", w, w.title);
  });
  if (!rows.length) text("No passes in this shed", x + 120, y + 34, DIM, 10);
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
    h = 24 + lines.length * 18,
    x = Math.min(width - w - 16, Math.max(16, found.x)),
    y = Math.max(170, found.y - h - 12);
  g.fillStyle = "#111c22";
  g.fillRect(x, y, w, h);
  g.strokeStyle = "#576a69";
  g.strokeRect(x + 0.5, y + 0.5, w, h);
  lines.forEach((value, i) =>
    text(value, x + 13, y + 23 + i * 18, i ? DIM : GOLD, 11, w - 26),
  );
}
function draw(timestamp) {
  if (!previous) previous = timestamp;
  if (!paused) clock += Math.min(timestamp - previous, 80);
  previous = timestamp;
  g.clearRect(0, 0, width, height);
  g.fillStyle = "#090e12";
  g.fillRect(0, 0, width, height);
  hits = [];
  const pulse = reduced.matches
    ? 0
    : smooth(1 - Math.abs(((clock % BEAT) / BEAT) * 2 - 1));
  const glow = g.createRadialGradient(
    width * 0.44,
    height * 0.5,
    20,
    width * 0.44,
    height * 0.5,
    width * 0.52,
  );
  glow.addColorStop(0, "#15272966");
  glow.addColorStop(1, "#090e1200");
  g.fillStyle = glow;
  g.fillRect(0, 0, width, height);
  if (state) {
    header(pulse);
    drawWarp();
    drawTree(pulse);
    drawCloth();
    tooltip();
  } else {
    text("brnrd / the loom", 28, 42, GOLD, 23);
    text(
      sourceStatus === "connecting" ? "Waiting for the frame…" : sourceStatus,
      28,
      86,
      DIM,
    );
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
      "#dfba7a",
      9,
      layout.ww - 100,
    );
  requestAnimationFrame(draw);
}
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
  if (h.kind === "heddle") toggle(h.data);
  else if (h.kind === "legend") legend.showModal();
  else select(h.kind, h.data);
});
canvas.addEventListener(
  "wheel",
  (event) => {
    if (!state) return;
    event.preventDefault();
    if (event.clientX < layout.warp) {
      const n = list(state.warp?.items).filter(
        (w) => matches(w.topics) && !["done", "retired"].includes(w.state),
      ).length;
      warpScroll = Math.max(
        0,
        Math.min(Math.max(0, n - 1), warpScroll + Math.sign(event.deltaY)),
      );
    } else {
      treeScroll += Math.sign(event.deltaY);
      rebuild();
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
  if (event.key === "Escape") {
    lifted.clear();
    treeScroll = 0;
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
  document.querySelector("#console-note").textContent =
    "the gate is not wired yet — write reaches the shuttle in pass 2";
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
