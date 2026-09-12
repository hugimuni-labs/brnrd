// Whether the screenshot check looked at what the PR changed (#1944).
//
// The measurement, on #1938: that PR rewrote a render condition in
// `RunLedgerReceipt.svelte` which was putting a green "observed model matches
// the configured core pin" tick on runs that had changed Runner mid-flight.
// `ui-shots` fired, compared its five pairs, and reported every one
// `0 px changed` — correctly, because no driver in the manifest opened the
// run-ledger receipt. A reviewer reads that table as *the UI did not change*.
// The truth was *nothing looked*.
//
// #1945 answered it by printing two lists and letting the reader intersect
// them. This module does the intersection — and the first signal it tried was
// wrong in the flattering direction, which is the whole reason the second one
// is worth writing down.
//
// **Loaded is not the signal.** Vite serves every source module as its own
// request, so the obvious receipt is the browser's network log. Measured on
// the deck route: 108 source modules fetched for one page, `Dashboard.svelte`
// importing most of the component tree — `RunLedgerReceipt.svelte` among
// them. A check built on that record would have reported #1938 as *covered*.
// The false green, rebuilt with more machinery.
//
// **Executed is.** Chromium's JS coverage reports per-function execution
// counts, and Vite's dev modules are one script per source file, so the two
// line up exactly. Same page, same capture: `RunLedgerReceipt.svelte` has one
// named function and **zero** of its ranges ran, while `Dashboard.svelte` ran
// 93 of 133 and `BillingPanel.svelte` 5 of 11. Imported and never invoked is
// precisely the state #1938 was in, and it is directly observable.
//
// So three groups, not two: *exercised* (code in it ran while the shots were
// taken), *imported but never run* (in the bundle, not on the screen), and
// *never fetched at all*. The last two are both silence; naming them apart is
// what tells an author whether to add a driver or change the fixture.
//
// **What it still may not claim.** Exercised is not *visible*: a component can
// run and render off-screen, below the fold, or into a region no shot crops.
// So the positive verdict stays weak and the negative one is the loud one — a
// false green here is the defect this exists to end, and a false alarm is
// merely noise. A missing record is `unknown`, never an accusation.

export const FRONTEND_ROOT = 'src/frontend';

// Repo-relative prefixes whose files the dev server can serve as modules.
// Everything else in `src/frontend/**` — the drivers themselves, configs,
// lockfiles — changes without any surface being able to show it, so it is
// reported as ignored rather than counted as a coverage miss.
export const APP_SOURCE_PREFIXES = [`${FRONTEND_ROOT}/src/`, `${FRONTEND_ROOT}/static/`];

// Files that live under the app source root and still cannot render: the unit
// tests beside the components they test, and the type declarations the browser
// never sees. Counting them would make every test-only PR read as *nothing
// looked*, and a headline that cries on a test file is a headline nobody reads
// on the day it is right.
const NOT_RENDERABLE = /(\.test\.[cm]?[jt]sx?|\.spec\.[cm]?[jt]sx?|\.d\.ts)$/;

/** Is this repo-relative path something a captured page could have run? */
export function isAppSource(file) {
	if (NOT_RENDERABLE.test(file)) return false;
	return APP_SOURCE_PREFIXES.some((prefix) => file.startsWith(prefix));
}

/**
 * A dev-server request URL → the repo-relative source file, or `null`.
 *
 * Vite serves each source module under its root-relative path in dev, so the
 * mapping is exact. Query strings (`?t=`, `?svelte&type=style`) are dropped by
 * `URL` itself; `/@fs/` escapes the root and is relativised against it;
 * dependency pre-bundles, `/@vite/`, `/@id/` and the SvelteKit scratch dir are
 * not repository sources and are dropped.
 */
export function normalizeModule(rawUrl, { origin, root }) {
	let url;
	try {
		url = new URL(rawUrl);
	} catch {
		return null;
	}
	if (origin && url.origin !== origin) return null;
	let rel = decodeURIComponent(url.pathname);
	if (rel.startsWith('/@fs/')) {
		const absolute = rel.slice('/@fs'.length);
		if (!root || !absolute.startsWith(`${root}/`)) return null;
		rel = absolute.slice(root.length + 1);
	} else if (rel.startsWith('/')) {
		rel = rel.slice(1);
	}
	if (!rel || !(rel.startsWith('src/') || rel.startsWith('static/'))) return null;
	return `${FRONTEND_ROOT}/${rel}`;
}

/**
 * The whole judgement, as data.
 *
 * `changed` — repo-relative paths the PR touched under `src/frontend`.
 * `loaded`  — repo-relative paths the captured pages fetched.
 * `coverageKnown` — false when no driver produced a module record.
 */
export function classifyCoverage({
	changed = [],
	exercised = [],
	loaded = [],
	coverageKnown = true
} = {}) {
	const appSource = changed.filter(isAppSource);
	const ignored = changed.filter((file) => !isAppSource(file));
	const exercisedSet = new Set(exercised);
	const loadedSet = new Set([...loaded, ...exercised]);
	const covered = appSource.filter((file) => exercisedSet.has(file));
	const importedNotRun = appSource.filter((file) => !exercisedSet.has(file) && loadedSet.has(file));
	const neverLoaded = appSource.filter((file) => !loadedSet.has(file));
	const uncovered = [...importedNotRun, ...neverLoaded];
	let verdict;
	if (!appSource.length) verdict = 'no-app-source';
	else if (!coverageKnown) verdict = 'unknown';
	else if (!covered.length) verdict = 'nothing-looked';
	else if (uncovered.length) verdict = 'partial';
	else verdict = 'covered';
	return {
		verdict,
		appSource,
		ignored,
		covered,
		importedNotRun,
		neverLoaded,
		uncovered,
		exercisedCount: exercisedSet.size,
		loadedCount: loadedSet.size
	};
}

const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

const list = (files, limit = 25) => {
	const shown = files.slice(0, limit).map((file) => `- \`${file}\``);
	if (files.length > limit) shown.push(`- _…and ${files.length - limit} more._`);
	return shown;
};

/**
 * The one-line headline, placed where a skimming reviewer cannot miss it.
 * Returns `null` when there is nothing worth a headline.
 */
export function coverageHeadline(coverage, { allIdentical } = {}) {
	const them = coverage.appSource.length === 1 ? 'it' : 'them';
	const why =
		coverage.neverLoaded.length && coverage.importedNotRun.length
			? `no captured surface fetched or ran ${them}`
			: coverage.importedNotRun.length
				? `a captured page imported ${them} and never ran a line — in the bundle, not on the screen`
				: `no captured surface even fetched ${them}`;
	switch (coverage.verdict) {
		case 'nothing-looked':
			return (
				`> **Nothing looked.** ${plural(coverage.appSource.length, 'changed source file')} ` +
				`under \`${FRONTEND_ROOT}/src\`, and ${why}. Whatever this table says ` +
				`about pixels is not a statement about your change.`
			);
		case 'partial':
			return (
				`> **Partly looked.** ${coverage.covered.length} of ` +
				`${plural(coverage.appSource.length, 'changed source file')} ` +
				`${coverage.covered.length === 1 ? 'was' : 'were'} exercised by a captured ` +
				`surface; the other ${coverage.uncovered.length} ` +
				`${coverage.uncovered.length === 1 ? 'was' : 'were'} not, and this check ` +
				`is silent about ${coverage.uncovered.length === 1 ? 'it' : 'them'}.`
			);
		case 'unknown':
			return (
				`> **Coverage unknown.** No driver recorded which modules it ran, so this ` +
				`check cannot say whether it looked at your change. Treat the pixel ` +
				`result as a statement about the captured surfaces only.`
			);
		case 'covered':
			return allIdentical
				? '> Every changed source file ran while a captured surface was photographed, and no pair differs. *Exercised* is not *visible*: code can run and render below the fold, or outside the region a shot crops.'
				: null;
		default:
			return null;
	}
}

/** The `<details>` block: the receipts behind the headline. */
export function renderCoverage(coverage, { surfaces = [], allIdentical = false } = {}) {
	const lines = ['<details><summary>What this compared, and what it could not see</summary>', ''];
	if (surfaces.length)
		lines.push(`**Surfaces compared (${surfaces.length}):** ${surfaces.join(' · ')}`, '');
	if (coverage.verdict !== 'unknown')
		lines.push(
			`**Source modules the captured pages ran:** ${coverage.exercisedCount} of ` +
				`${coverage.loadedCount} imported — measured by Chromium's own execution ` +
				`counts during capture, not inferred from imports.`,
			''
		);
	if (coverage.covered.length)
		lines.push(
			`**Changed and exercised (${coverage.covered.length}):**`,
			...list(coverage.covered),
			''
		);
	if (coverage.importedNotRun.length)
		lines.push(
			`**Changed, imported by a captured page, never run (${coverage.importedNotRun.length}):** ` +
				'the component is in the bundle and was not on the screen — a fixture or a ' +
				'driver interaction is what would reach it.',
			...list(coverage.importedNotRun),
			''
		);
	if (coverage.neverLoaded.length)
		lines.push(
			`**Changed and never fetched (${coverage.neverLoaded.length}):** no captured ` +
				'page imports this at all — it needs a driver that opens the route it lives on.',
			...list(coverage.neverLoaded),
			''
		);
	if (coverage.ignored.length)
		lines.push(
			`<sub>${plural(coverage.ignored.length, 'changed file')} outside ` +
				`\`${FRONTEND_ROOT}/src\` and \`${FRONTEND_ROOT}/static\` ` +
				`${coverage.ignored.length === 1 ? 'is' : 'are'} not renderable and ` +
				`${coverage.ignored.length === 1 ? 'was' : 'were'} not counted either way.</sub>`,
			''
		);
	if (coverage.verdict === 'no-app-source')
		lines.push(
			`**No renderable source changed in this PR**, so the comparison above is`,
			'informational — there was nothing for a surface to show differently.',
			''
		);
	if (coverage.verdict === 'nothing-looked' || coverage.verdict === 'unknown')
		lines.push(
			'Say what you opened and judged in the PR instead',
			'(`workflow.md` §Orchestration), or add a driver to',
			'`repro/ci-shots.json` that opens the surface your change renders on.',
			''
		);
	else if (allIdentical && coverage.verdict !== 'covered')
		lines.push(
			'**Every compared pair is identical.** That means no *captured* surface',
			'changed — not that no surface changed.',
			''
		);
	lines.push('</details>', '');
	return lines;
}
