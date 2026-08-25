// the-overlay-that-shows-the-room: source-text pins, the same grammar
// `dashboardPage.test.ts` speaks. The node panel imports sibling .svelte
// components, so the server-render dance can't compile it standalone —
// these pin the wiring and the ceremony constants instead.
import { test } from 'node:test';
import { match, ok, doesNotMatch } from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const page = readFileSync(join(here, '../routes/+page.svelte'), 'utf8');
const node = readFileSync(join(here, 'RunNodeInline.svelte'), 'utf8');
const card = readFileSync(join(here, 'LiveRuns.svelte'), 'utf8');
const overlay = readFileSync(join(here, 'RunOverlay.svelte'), 'utf8');

test('a run press opens the overlay stage and keeps selection when it closes', () => {
	// The stage state exists, an explicit press arms it, and closing only
	// clears the stage — never the loom selection underneath.
	match(page, /let runOverlayOpen = \$state\(false\)/);
	match(page, /runOverlayOpen = kind === 'run' && !same/);
	match(page, /onClose=\{\(\) => \(runOverlayOpen = false\)\}/);
	// The overlay renders the SAME node panel — one run, one panel; a
	// placement, not a fourth rendering.
	const overlayBlock = page.slice(page.indexOf('<RunOverlay'));
	match(overlayBlock, /<RunNodeInline/);
	match(overlayBlock, /liveRun=\{selectedLiveRun\}/);
	match(overlayBlock, /warpItems=\{selectedWarpItems\}/);
});

test('the in-flow node panel carries the same live enrichment as the overlay', () => {
	const laneBlock = page.slice(0, page.indexOf('<RunOverlay'));
	match(laneBlock, /liveRun=\{selectedLiveRun\}/);
	match(laneBlock, /warpItems=\{selectedWarpItems\}/);
});

test('AWAIT and CLOSING render specifically, and AWAIT slows the scan to a breath', () => {
	// The notice strip names the states out loud…
	match(node, /lifecycleNotice/);
	// …and AWAIT cools the motion: a 6s scan against the ordinary 1.4s —
	// a quiet state looks quiet, never busy.
	match(node, /loom-scan_6s/);
	match(node, /loom-scan_1\.4s/);
	match(card, /loom-scan_6s/);
});

test('the edge re-reveals slowly — the ceremony can afford to be watched', () => {
	// Keyed on the boundary's own timestamp: one reveal per attested
	// boundary, never ambient motion.
	match(node, /\{#key edge\?\.at\}/);
	match(node, /duration: 2400/);
	match(card, /\{#key run\.edge\?\.at\}/);
	match(card, /duration: 2000/);
});

test('the overlay closes on Escape and backdrop, and scrolls its own sheet', () => {
	match(overlay, /Escape/);
	match(overlay, /aria-label="close run detail"/);
	match(overlay, /max-h-\[92svh\]/);
	// aria-modal dialog semantics — a stage, not a decoration.
	match(overlay, /aria-modal="true"/);
});

test('absent facts render nothing — no fabricated room, edge, or course', () => {
	// Every strip is gated on the fact existing; a closed run or a
	// pre-upgrade daemon renders exactly what it renders today.
	match(node, /\{#if room \|\| edgeText \|\| course \|\| warpItems\.length > 0\}/);
	doesNotMatch(node, /room \?\? '/);
	ok(!node.includes("edge?.detail ?? '—'"));
});
