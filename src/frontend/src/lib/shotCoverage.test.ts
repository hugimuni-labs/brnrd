// The screenshot check's own claim, under test (#1944).
//
// The defect this guards: five identical pairs and a green tick, on a PR whose
// only changed component no driver opens. The distinction that fixes it is
// between *no captured surface changed* and *nothing looked*, and it is only
// worth anything if the second case is loud and the first is not overclaimed.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
	classifyCoverage,
	coverageHeadline,
	normalizeModule,
	isAppSource,
	FRONTEND_ROOT
} from '../../repro/shot-coverage.mjs';

const RECEIPT = `${FRONTEND_ROOT}/src/lib/RunLedgerReceipt.svelte`;
const FUEL = `${FRONTEND_ROOT}/src/lib/Fuel.svelte`;

test('normalizeModule maps a vite dev URL to its repo path', () => {
	assert.equal(
		normalizeModule('http://localhost:5197/src/lib/RunLedgerReceipt.svelte', {
			root: '/w/src/frontend'
		}),
		RECEIPT
	);
});

test('normalizeModule drops the query vite appends to a module', () => {
	assert.equal(
		normalizeModule('http://localhost:5197/src/lib/Fuel.svelte?svelte&type=style&lang.css', {
			root: '/w/src/frontend'
		}),
		FUEL
	);
});

test('normalizeModule relativises an /@fs/ escape against the vite root', () => {
	assert.equal(
		normalizeModule('http://localhost:5197/@fs/w/src/frontend/src/lib/Fuel.svelte', {
			root: '/w/src/frontend'
		}),
		FUEL
	);
});

test('normalizeModule drops everything that is not a repository source', () => {
	const root = '/w/src/frontend';
	for (const url of [
		'http://localhost:5197/node_modules/.vite/deps/svelte.js',
		'http://localhost:5197/@vite/client',
		'http://localhost:5197/.svelte-kit/generated/root.svelte',
		'http://localhost:5197/v1/dashboard/run-ledger',
		'http://localhost:5197/@fs/elsewhere/other/src/lib/Foo.svelte',
		'not a url'
	])
		assert.equal(normalizeModule(url, { root }), null, url);
});

test('normalizeModule honours an origin filter when one is given', () => {
	assert.equal(
		normalizeModule('http://cdn.example/src/lib/Fuel.svelte', {
			root: '/w/src/frontend',
			origin: 'http://localhost:5197'
		}),
		null
	);
});

test('isAppSource counts only what a page can load', () => {
	assert.equal(isAppSource(RECEIPT), true);
	assert.equal(isAppSource(`${FRONTEND_ROOT}/static/logo.svg`), true);
	assert.equal(isAppSource(`${FRONTEND_ROOT}/repro/drive-fuel.mjs`), false);
	assert.equal(isAppSource(`${FRONTEND_ROOT}/package-lock.json`), false);
});

// THE #1938 CASE, and the reason `loaded` is not the signal. That PR changed
// the run-ledger receipt; the drivers opened the fuel deck, whose Dashboard
// tree *imports* the receipt and never renders it. A check reading the network
// log calls this covered. A check reading execution counts calls it what it is.
test('imported by a captured page and never run is nothing-looked, loudly', () => {
	const coverage = classifyCoverage({
		changed: [RECEIPT],
		exercised: [FUEL],
		loaded: [FUEL, RECEIPT]
	});
	assert.equal(coverage.verdict, 'nothing-looked');
	assert.deepEqual(coverage.importedNotRun, [RECEIPT]);
	assert.deepEqual(coverage.neverLoaded, []);
	const headline = coverageHeadline(coverage, { allIdentical: true });
	assert.match(headline, /Nothing looked/);
	assert.match(headline, /imported it and never ran a line/);
	// The sentence a reviewer needs: the pixel table is not about their change.
	assert.match(headline, /not a statement about your change/);
});

test('never fetched at all reads differently from imported-and-idle', () => {
	const coverage = classifyCoverage({ changed: [RECEIPT], exercised: [FUEL], loaded: [FUEL] });
	assert.equal(coverage.verdict, 'nothing-looked');
	assert.deepEqual(coverage.neverLoaded, [RECEIPT]);
	assert.deepEqual(coverage.importedNotRun, []);
	assert.match(coverageHeadline(coverage, {}), /no captured surface even fetched it/);
});

test('a missing module record is unknown, never nothing-looked', () => {
	const coverage = classifyCoverage({ changed: [RECEIPT], loaded: [], coverageKnown: false });
	assert.equal(coverage.verdict, 'unknown');
	assert.match(coverageHeadline(coverage, {}), /Coverage unknown/);
});

test('an exercised change is covered, and the headline refuses to say visible', () => {
	const coverage = classifyCoverage({ changed: [RECEIPT], exercised: [RECEIPT, FUEL] });
	assert.equal(coverage.verdict, 'covered');
	const headline = coverageHeadline(coverage, { allIdentical: true });
	assert.match(headline, /\*Exercised\* is not \*visible\*/);
});

test('a partly exercised change names the half nobody saw', () => {
	const coverage = classifyCoverage({ changed: [RECEIPT, FUEL], exercised: [FUEL] });
	assert.equal(coverage.verdict, 'partial');
	assert.deepEqual(coverage.covered, [FUEL]);
	assert.deepEqual(coverage.uncovered, [RECEIPT]);
	assert.match(coverageHeadline(coverage, {}), /Partly looked/);
});

// A PR that only touches the harness itself must not accuse the harness of
// having missed something: there was nothing renderable to miss.
test('changes outside the app source are ignored, not counted as a miss', () => {
	const coverage = classifyCoverage({
		changed: [`${FRONTEND_ROOT}/repro/drive-fuel.mjs`, `${FRONTEND_ROOT}/package.json`],
		exercised: [FUEL]
	});
	assert.equal(coverage.verdict, 'no-app-source');
	assert.equal(coverage.ignored.length, 2);
	assert.equal(coverageHeadline(coverage, { allIdentical: true }), null);
});

test('a covered change with real pixel diffs gets no headline at all', () => {
	const coverage = classifyCoverage({ changed: [RECEIPT], exercised: [RECEIPT] });
	assert.equal(coverageHeadline(coverage, { allIdentical: false }), null);
});
