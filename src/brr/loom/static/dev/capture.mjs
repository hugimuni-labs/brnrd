// Capture dependency is external to the shipped page. No bundler required.
// node capture.mjs /absolute/path/to/playwright/index.mjs [base URL] [output dir]
import { mkdir, readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import assert from "node:assert/strict";
const { chromium } = await import(pathToFileURL(resolve(process.argv[2])).href);
const base = process.argv[3] || "http://127.0.0.1:7788";
const output = resolve(process.argv[4] || "media/loom/screen");
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true });
const errors = [],
  requests = [],
  frameRates = [];
function observe(page) {
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(m.text());
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
  await page.locator("#receipt h1").waitFor();
  await page.waitForTimeout(900);
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
  await page.screenshot({path:resolve(output,`lifted-${w}x${h}.png`)});
  await page.keyboard.press("2");
  await page.waitForTimeout(650);
  assert.match(
    await page.locator("#receipt").innerText(),
    /The shed is raised/,
  );
  assert.match(await page.locator("#receipt").innerText(), /the-shuttle/);
  await page.screenshot({
    path: resolve(output, `intersection-${w}x${h}.png`),
  });
  await page.keyboard.press("Escape");
  await page.keyboard.press('ArrowLeft');await page.waitForTimeout(650);
  await page.screenshot({path:resolve(output,`rail-mid-scroll-${w}x${h}.png`)});
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
await empty.locator("#receipt h1").waitFor();
assert.equal(await empty.locator("#receipt h1").innerText(), "No live pass");
await empty.screenshot({ path: resolve(output, "empty-1280x720.png") });
await empty.close();
// Exercise the live transport and a hostile-looking fold as text, without a daemon.
const fixture = JSON.parse(
  await readFile(new URL("./state.sample.json", import.meta.url), "utf8"),
);
const transport = await browser.newPage({
  viewport: { width: 1280, height: 720 },
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
const ctx = await browser.newContext({
  viewport: { width: 1280, height: 720 },
  reducedMotion: "no-preference",
  recordVideo: { dir: output, size: { width: 1280, height: 720 } },
});
const movie = await ctx.newPage();
observe(movie);
await movie.goto(base + "/loom/?src=dev&replay");
await movie.locator("#receipt h1").waitFor();
await movie.waitForTimeout(6000);
await movie.keyboard.press("1");
await movie.waitForTimeout(7000);
await movie.keyboard.press("Escape");
await movie.waitForTimeout(7000);
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
      screenshots: "two sizes, intersections, empty",
      video: "fixture-beat.webm, 20 seconds",
    },
    null,
    2,
  ),
);
