// Capture dependency is external to the shipped page. No bundler required.
// node capture.mjs /absolute/path/to/playwright/index.mjs [base URL] [output dir]
// The base must serve the feed: live frames are shot against /loom/ as served.
import { mkdir, readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import assert from "node:assert/strict";
const { chromium } = await import(pathToFileURL(resolve(process.argv[2])).href);
const base = process.argv[3] || "http://127.0.0.1:7788";
const output = resolve(process.argv[4] || "media/loom/screen/opus");
// The first-load reveal lays six regions in 300 ms apart; frames wait past it.
const SETTLE = 2600;
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true });
const errors = [],
  requests = [],
  frameRates = [];
function observe(page) {
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => {
    // A bench page the feed has no answer for is a 404 the bench reads as
    // pending — an expected miss, not a page error.
    if (m.type() === "error" && !String(m.location()?.url || "").includes("/loom/page/")) errors.push(m.text());
  });
  page.on("request", (r) => {
    const u = new URL(r.url());
    if (u.protocol !== "data:") requests.push(u);
  });
}
for (const [w, h] of [
  [1920, 1080],
  [1280, 720],
]) {
  const page = await browser.newPage({
    viewport: { width: w, height: h },
    deviceScaleFactor: 1,
    reducedMotion: "no-preference",
  });
  observe(page);
  await page.goto(base + "/loom/?src=dev");
  await page.locator("#receipt h1").waitFor({ state: "attached" });
  await page.waitForTimeout(SETTLE);
  frameRates.push(await page.evaluate(()=>new Promise(resolve=>{
    const gaps=[];let previous;
    function sample(now){if(previous)gaps.push(now-previous);previous=now;if(gaps.length<90)requestAnimationFrame(sample);else{gaps.sort((a,b)=>a-b);resolve({width:innerWidth,median_ms:gaps[45],p95_ms:gaps[85],mean_fps:1000/(gaps.reduce((a,b)=>a+b,0)/gaps.length)});}}
    requestAnimationFrame(sample);
  })));
  await page.screenshot({ path: resolve(output, `fixture-${w}x${h}.png`) });
  await page.keyboard.press("Space");
  const still = await page.locator("canvas").screenshot();
  await page.waitForTimeout(700);
  assert.equal(
    still.equals(await page.locator("canvas").screenshot()),
    true,
    "Space freezes the canvas",
  );
  await page.keyboard.press("Space");
  await page.keyboard.press("1");
  await page.waitForTimeout(650);
  await page.screenshot({path:resolve(output,`fixture-lifted-${w}x${h}.png`)});
  await page.keyboard.press("2");
  await page.waitForTimeout(650);
  assert.match(
    await page.locator("#receipt").innerText(),
    /The shed is raised/,
  );
  assert.match(await page.locator("#receipt").innerText(), /the-shuttle/);
  await page.screenshot({
    path: resolve(output, `fixture-intersection-${w}x${h}.png`),
  });
  await page.keyboard.press("Escape");
  await page.keyboard.press('ArrowLeft');await page.waitForTimeout(650);
  await page.screenshot({path:resolve(output,`fixture-rail-mid-scroll-${w}x${h}.png`)});
  await page.keyboard.press('ArrowRight');await page.waitForTimeout(650);
  await page
    .getByRole("button", {
      name: "Open place src/brr/loom/static/loom.js",
      exact: true,
    })
    .focus();
  await page.keyboard.press("Enter");
  assert.equal(
    await page.locator("#focus").innerText(),
    "@src/brr/loom/static/loom.js",
  );
  await page.getByRole('button',{name:'explain',exact:true}).click();
  assert.equal(await page.locator('#console-note').innerText(),'reaches the shuttle in pass 2 — the local gate is not wired yet');
  await page.locator("#message").fill("hello");
  await page.locator("#message").press("Enter");
  assert.equal(
    await page.locator("#console-note").innerText(),
    "reaches the shuttle in pass 2 — the local gate is not wired yet",
  );
  await page.close();
}
// Live: the real state of this machine, as the feed serves it.
const data = (page) => page.evaluate(() => ({ ...document.querySelector("#loom").dataset }));
for (const [w, h] of [
  [1920, 1080],
  [1280, 720],
]) {
  const page = await browser.newPage({
    viewport: { width: w, height: h },
    deviceScaleFactor: 1,
    reducedMotion: "no-preference",
  });
  observe(page);
  await page.goto(base + "/loom/");
  await page.locator("#receipt h1").waitFor({ state: "attached" });
  await page.waitForTimeout(SETTLE);
  await page.screenshot({ path: resolve(output, `live-${w}x${h}.png`) });
  if (w === 1920) {
    // Lift is deterministic: one click = one toggle, the window rebuilt from
    // the intersection each time, a second click restores the counts exactly.
    const base0 = await data(page);
    const runes = JSON.parse(base0.runes);
    const live = (await (await fetch(base + "/loom/state.json")).json()).heddles
      .sort((a, b) => (b.lit || 0) - (a.lit || 0))
      .map((x) => x.slug);
    const [a, b] = live;
    const click = async (slug) => {
      const [x, y] = runes[slug];
      await page.mouse.click(x, y);
      await page.waitForTimeout(450);
      return data(page);
    };
    if (a) {
      const one = await click(a);
      assert.equal(one.lifted, a, "one click lifts one rune");
      await page.screenshot({ path: resolve(output, `live-lifted-${w}x${h}.png`) });
      assert.match(await page.locator("#receipt").innerText(), /The shed is raised/);
      await page.screenshot({ path: resolve(output, "live-page-heddle.png") });
      // No hover reflow while lifted.
      await page.mouse.move(700, 600);
      await page.waitForTimeout(700);
      assert.equal((await data(page)).places, one.places, "hover does not reflow a lifted window");
      if (b) {
        const both = await click(b);
        assert.equal(both.lifted, `${a},${b}`, "a second rune intersects");
        assert.ok(Number(both.places) <= Number(one.places), "an intersection never widens the window");
        await page.screenshot({ path: resolve(output, `live-intersection-${w}x${h}.png`) });
        const onlyB = await click(a);
        assert.equal(onlyB.lifted, b, "clicking a lifted rune drops only it");
        const none = await click(b);
        assert.equal(none.lifted, "", "every toggle is undone by its second click");
        assert.equal(none.passes, base0.passes);
      }
      if (!b) assert.equal((await click(a)).lifted, "", "a second click drops the lone rune");
      const again = await click(a);
      assert.equal(again.lifted, a);
      assert.equal(again.places, one.places, "the same lift rebuilds the same window");
      await page.keyboard.press("Escape");
      await page.waitForTimeout(300);
      assert.equal((await data(page)).lifted, "", "Esc drops all");
    }
    // The bench renders pages: pass, bead, item, place.
    await page.getByRole("button", { name: "Open shuttle card", exact: true }).focus();
    await page.keyboard.press("Enter");
    await page.waitForTimeout(400);
    await page.screenshot({ path: resolve(output, "live-page-pass.png") });
    const beadRow = page.locator("#receipt .bench-row").first();
    if (await beadRow.count()) {
      await beadRow.click();
      await page.waitForTimeout(400);
      assert.match(await page.locator("#receipt").innerText(), /command/i);
      await page.screenshot({ path: resolve(output, "live-page-bead.png") });
      await page.locator("#receipt .bench-back").first().click();
      await page.waitForTimeout(300);
      assert.match(await page.locator("#receipt").innerText(), /a pass:/, "‹ back returns to the pass");
    }
    const work = page.locator('#access button:text-matches("^Open work ")').first();
    if (await work.count()) {
      await work.focus();
      await page.keyboard.press("Enter");
      await page.waitForTimeout(400);
      await page.screenshot({ path: resolve(output, "live-page-item.png") });
    }
    const place = page.locator('#access button:text-matches("^Open place ")').first();
    if (await place.count()) {
      await place.focus();
      await page.keyboard.press("Enter");
      await page.waitForTimeout(600);
      await page.screenshot({ path: resolve(output, "live-page-place.png") });
    }
    await page.keyboard.press("Escape");
    for (let i = 0; i < 7; i++) await page.keyboard.press("ArrowLeft");
    await page.waitForTimeout(900);
    await page.screenshot({ path: resolve(output, `live-rail-mid-scroll-${w}x${h}.png`) });
    await page.keyboard.press("Escape");
  }
  await page.close();
}
// Close-ups at 2× for the face and the sweep.
{
  const page = await browser.newPage({
    viewport: { width: 1920, height: 1080 },
    deviceScaleFactor: 2,
    reducedMotion: "no-preference",
  });
  observe(page);
  await page.goto(base + "/loom/");
  await page.locator("#receipt h1").waitFor({ state: "attached" });
  await page.waitForTimeout(SETTLE);
  await page.screenshot({
    path: resolve(output, "live-face-close.png"),
    clip: { x: 1340, y: 8, width: 564, height: 470 },
  });
  await page.close();
}
// `cloth`: the line runs the weft, plaques raise threads, and it stays the
// topmost thing it passes.
{
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  observe(page);
  await page.goto(base + "/loom/?scan=cloth");
  await page.locator("#receipt h1").waitFor({ state: "attached" });
  await page.waitForTimeout(SETTLE);
  // Mid-traverse: the scan crosses a plaque and a thread rises from it.
  let lit = 0;
  for (let i = 0; i < 90 && lit < 1; i++) {
    await page.waitForTimeout(300);
    lit = Number(await page.evaluate(() => document.querySelector("#loom").dataset.trailsLit || 0));
  }
  assert.ok(lit >= 1, "a traverse raises a thread from the weft");
  await page.screenshot({ path: resolve(output, "live-mid-traverse-1920x1080.png") });
  // Entering from the left: the line runs the warp column's full height, over
  // everything it passes.
  let overWarp = false;
  for (let i = 0; i < 200 && !overWarp; i++) {
    await page.waitForTimeout(120);
    overWarp = await page.evaluate(() => {
      const el = document.querySelector("#loom");
      return Number(el.dataset.scanX || 1e9) < innerWidth * 0.12;
    });
  }
  assert.ok(overWarp, "the scan enters over the warp column");
  await page.screenshot({ path: resolve(output, "live-scan-over-warp-1920x1080.png"), clip: { x: 0, y: 150, width: 820, height: 800 } });
  await page.close();
}
// The two fronts: `up` measures depth from the weft, `root` radial distance.
for (const mode of ["up", "root"]) {
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  observe(page);
  await page.goto(`${base}/loom/?scan=${mode}`);
  await page.locator("#receipt h1").waitFor({ state: "attached" });
  let pings = 0;
  for (let i = 0; i < 80 && pings < 2; i++) {
    await page.waitForTimeout(250);
    pings = Number(await page.evaluate(() => document.querySelector("#loom").dataset.pings || 0));
  }
  assert.equal(await page.evaluate(() => document.querySelector("#loom").dataset.scan), mode);
  assert.ok(pings >= 2, `the ${mode} front lights nodes as it crosses them`);
  await page.screenshot({ path: resolve(output, `live-scan-${mode}-1920x1080.png`) });
  if (mode === "up" && (await page.evaluate(() => document.querySelector("#loom").dataset.watching)) === "true") {
    // The watch: the shuttle stands at the tower while the seat listens.
    assert.match(await page.locator("canvas").getAttribute("aria-label"), /loom/);
    await page.screenshot({ path: resolve(output, "live-watchtower-1920x1080.png") });
  }
  if (mode === "up") {
    // S cycles, and the choice is remembered on reload.
    await page.keyboard.press("S");
    await page.waitForTimeout(300);
    assert.equal(await page.evaluate(() => document.querySelector("#loom").dataset.scan), "root", "S cycles up → root");
    await page.goto(base + "/loom/");
    await page.locator("#receipt h1").waitFor({ state: "attached" });
    await page.waitForTimeout(1200);
    assert.equal(await page.evaluate(() => document.querySelector("#loom").dataset.scan), "root", "the choice persists");
  }
  await page.close();
}
// ?scan=off: no scan at all — the heartbeat on the face, a plaque's thread on
// hover — so the two readings can be compared.
{
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  observe(page);
  await page.goto(base + "/loom/?scan=off");
  await page.locator("#receipt h1").waitFor({ state: "attached" });
  await page.waitForTimeout(SETTLE + 1200);
  const dataset = () => page.evaluate(() => ({ ...document.querySelector("#loom").dataset }));
  const quiet = await dataset();
  assert.equal(quiet.scan, "off");
  assert.equal(quiet.trailsLit, "0", "nothing rises without the scan");
  // The focused plaque sits on the rail; hovering it shows its thread.
  const box = await page.locator("canvas").boundingBox();
  await page.mouse.move(box.width - 220, box.height - 190);
  await page.waitForTimeout(500);
  const hovered = await dataset();
  assert.equal(hovered.scan, "off");
  await page.screenshot({ path: resolve(output, "live-scan-off-1920x1080.png") });
  await page.close();
}
// Narrow drawer, reduced motion, and missing measurements are real edge states.
const narrow = await browser.newPage({
  viewport: { width: 820, height: 720 },
  reducedMotion: "reduce",
});
observe(narrow);
await narrow.goto(base + "/loom/?src=dev");
await narrow.locator("#receipt h1").waitFor({ state: "attached" });
assert.equal(await narrow.locator("#bench").isVisible(), false);
const reducedStill = await narrow.locator("canvas").screenshot();
await narrow.waitForTimeout(700);
assert.equal(
  reducedStill.equals(await narrow.locator("canvas").screenshot()),
  true,
  "Reduced motion freezes ambient pulse",
);
await narrow
  .getByRole("button", { name: "Open shuttle card", exact: true })
  .focus();
await narrow.keyboard.press("Enter");
assert.equal(await narrow.locator("#bench").isVisible(), true);
await narrow.locator("#close-bench").click();
assert.equal(await narrow.locator("#bench").isVisible(), false);
await narrow.close();
const empty = await browser.newPage({ viewport: { width: 1280, height: 720 } });
observe(empty);
await empty.route("**/loom/dev/state.sample.json", (r) =>
  r.fulfill({
    json: {
      at: null,
      repo: null,
      run: null,
      shuttle: { state: "released" },
      hud: { chip: null, quota: null, strands: [] },
      heddles: [],
      warp: { goals: [], items: [] },
      beads: [],
      cloth: { rows: [] },
      tree: { places: [] },
      bench: { folds: [] },
    },
  }),
);
await empty.goto(base + "/loom/?src=dev");
await empty.locator("#receipt h1").waitFor({ state: "attached" });
assert.equal(await empty.locator("#receipt h1").innerText(), "No live pass");
await empty.waitForTimeout(SETTLE);
await empty.screenshot({ path: resolve(output, "empty-1280x720.png") });
await empty.close();
// Exercise the live transport and a hostile-looking fold as text, without a daemon.
const fixture = JSON.parse(
  await readFile(new URL("./state.sample.json", import.meta.url), "utf8"),
);
// Tall enough that the bench is a column, not a drawer.
const transport = await browser.newPage({
  viewport: { width: 1440, height: 900 },
});
observe(transport);
const updated = structuredClone(fixture);
updated.run.name = "SSE receipt arrived";
updated.bench.folds = [
  {
    path: "fixture/fold",
    place: "src/brr/loom/static/loom.js",
    marks: ["keep"],
  },
];
await transport.route("**/loom/state.json", (r) =>
  r.fulfill({ json: fixture }),
);
await transport.route("**/loom/events", (r) =>
  r.fulfill({
    contentType: "text/event-stream",
    body: "event: state\ndata: " + JSON.stringify(updated) + "\n\n",
  }),
);
// A feed without bench pages: the bench must read pending, not break.
await transport.route("**/loom/page/**", (r) => r.fulfill({ status: 404, json: { error: "no page" } }));
await transport.route("**/loom/bench?*", (r) =>
  r.fulfill({
    contentType: "text/plain",
    body: '<script>throw Error("must stay text")</script>\nA measured fold.',
  }),
);
await transport.goto(base + "/loom/");
await transport
  .getByRole("heading", { name: "SSE receipt arrived", exact: true })
  .waitFor();
await transport
  .getByRole("button", {
    name: "Open place src/brr/loom/static/loom.js",
    exact: true,
  })
  .focus();
await transport.keyboard.press("Enter");
await transport
  .getByRole("button", { name: "fixture/fold · keep", exact: true })
  .click();
await transport.getByText("A measured fold.", { exact: false }).waitFor();
await transport.getByRole("button",{name:"keep",exact:true}).click();
assert.equal(
  await transport.locator("#receipt script").count(),
  0,
  "folds are text, never markup",
);
await transport.close();
// The place page against the attention contract as the steer names it
// (gh_url · text · attention) — a mocked answer until the feed ships it.
{
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  observe(page);
  const text = Array.from({ length: 240 }, (_, i) => `line ${i + 1}: ${"x".repeat(i % 40)}`).join("\n");
  await page.route("**/loom/state.json", (r) => r.fulfill({ json: fixture }));
  await page.route("**/loom/events", (r) => r.fulfill({ contentType: "text/event-stream", body: "" }));
  await page.route("**/loom/page/place?*", (r) =>
    r.fulfill({
      json: {
        path: "src/brr/loom/static/loom.js",
        kind: "file",
        tree: "repo",
        gh_url: "https://github.com/hugimuni-labs/brnrd/blob/main/src/brr/loom/static/loom.js",
        text,
        attention: [
          { from: 10, to: 40, kind: "read", count: 3 },
          { from: 120, to: 126, kind: "edit", count: 1 },
          { from: 200, to: 230, kind: "read", count: 1 },
        ],
      },
    }),
  );
  await page.route("**/loom/page/**", (r) => r.fallback());
  await page.goto(base + "/loom/");
  await page.waitForTimeout(SETTLE);
  await page.getByRole("button", { name: "Open place src/brr/loom/static/loom.js", exact: true }).focus();
  await page.keyboard.press("Enter");
  await page.locator(".attention-strip .band.edit").waitFor();
  assert.equal(await page.locator(".attention-strip .band").count(), 3);
  assert.equal(await page.locator("#receipt a[href*='github.com']").count(), 1);
  await page.locator(".attention-strip .band.edit").click();
  await page.waitForTimeout(200);
  const scrolled = await page.locator("pre.file-text").evaluate((el) => el.scrollTop);
  assert.ok(scrolled > 1000, "a band scrolls the text to its lines");
  await page.locator(".range-picker input").first().fill("100");
  await page.locator(".range-picker input").first().dispatchEvent("change");
  assert.equal(await page.locator("pre.file-text .ln").first().getAttribute("data-n"), "100");
  await page.screenshot({ path: resolve(output, "contract-page-place-attention.png"), clip: { x: 1340, y: 440, width: 580, height: 640 } });
  await page.close();
}
const ctx = await browser.newContext({
  viewport: { width: 1280, height: 720 },
  reducedMotion: "no-preference",
  recordVideo: { dir: output, size: { width: 1280, height: 720 } },
});
const movie = await ctx.newPage();
observe(movie);
await movie.goto(base + "/loom/?src=dev&replay");
await movie.locator("#receipt h1").waitFor({ state: "attached" });
await movie.waitForTimeout(8000);
await movie.keyboard.press("1");
await movie.waitForTimeout(6000);
await movie.keyboard.press("Escape");
await movie.waitForTimeout(6000);
await ctx.close();
await movie.video().saveAs(resolve(output, "fixture-beat.webm"));
await movie.video().delete();
await browser.close();
assert.deepEqual(errors, [], "page must have zero console/page errors");
assert.equal(
  requests.every((u) => u.origin === base && u.pathname.startsWith("/loom/")),
  true,
  "all requests must stay under /loom/",
);
console.log(
  JSON.stringify(
    {
      errors,
      frameRates,
      requests: requests.length,
      network: "only /loom/*",
      screenshots: "fixture + live at two sizes, lift, intersection, rail, face, scan, empty",
      video: "fixture-beat.webm, 20 seconds",
    },
    null,
    2,
  ),
);
