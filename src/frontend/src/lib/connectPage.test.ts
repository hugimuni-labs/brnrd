import { ok, strictEqual } from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

// Source-level, deliberately — same rationale as before: ConnectFlow reads
// onMount, history.replaceState, and browser APIs that the SSR harness cannot
// stub cheaply, and the claims here are structural (markup presence, link
// shapes, absence of wrong patterns), not derived state. The one exception is
// the initial-phase check, which is a source-level read of the $state init.
const here = dirname(fileURLToPath(import.meta.url));
const connectFlowPath = join(here, 'ConnectFlow.svelte');
const connectCodeRoutePath = join(here, '..', 'routes', 'connect', '[code]', '+page.svelte');
const connectEntryRoutePath = join(here, '..', 'routes', 'connect', '+page.svelte');

function flowSource(): string {
	return readFileSync(connectFlowPath, 'utf8');
}

// --- structural: all three phases live in one file ---

test('ConnectFlow contains all three phase blocks in one component', () => {
	const src = flowSource();
	ok(src.includes("phase === 'entry'"), 'entry phase block present');
	ok(src.includes("phase === 'confirm'"), 'confirm phase block present');
	ok(src.includes("phase === 'done'"), 'done phase block present');
});

test('ConnectFlow starts at entry when no initialCode is given', () => {
	const src = flowSource();
	// The $state initialiser must read: initialCode ? 'confirm' : 'entry'
	// so a no-props render starts at the entry form, not the confirm phase.
	ok(
		/let phase = \$state<Phase>\(\s*initialCode\s*\?\s*['"]confirm['"]\s*:\s*['"]entry['"]\s*\)/.test(
			src
		),
		'phase initialises to confirm when initialCode is set, entry otherwise'
	);
});

test('ConnectFlow starts at confirm when initialCode is provided (deep-link / reload)', () => {
	// Verified by the same $state initialiser above — when initialCode is truthy
	// the component opens at confirm and loads context in onMount.
	const src = flowSource();
	ok(src.includes('initialCode'), 'initialCode prop accepted');
	ok(src.includes('onMount'), 'onMount used to fetch context for deep-link entry');
});

// --- URL update: replaceState, not navigate ---

test('ConnectFlow uses history.replaceState on submit, not window.location.assign', () => {
	const src = flowSource();
	ok(src.includes('history.replaceState'), 'URL updated with replaceState on submit');
	ok(
		!src.includes('window.location.assign'),
		'no window.location.assign — that was the two-screen navigation'
	);
	// The fragment must be appended separately so it doesn't pass through resolve()
	ok(
		/history\.replaceState\(null,\s*['"]['"]\s*,\s*resolve\(/.test(src),
		'replaceState receives the resolved path'
	);
	ok(
		/['"]#['"]\s*\+\s*entered/.test(src),
		'the # fragment is appended after the resolved path, not folded through resolve()'
	);
});

// --- the "connect a repository" dead-end link carries the pairing back ---
// (live 2026-08-06: this was the link that used to strand the reader at /repos)

test('the "connect a repository" dead-end link carries the pairing back as next=', () => {
	const src = flowSource();
	const needsRepoEnableBlock = src.match(
		/\{#if needsRepoEnable\(context\)\}[\s\S]{0,400}?\{\/if\}/
	);
	ok(needsRepoEnableBlock, 'the needsRepoEnable branch exists');
	const block = needsRepoEnableBlock![0];
	ok(
		/href=\{resolve\(`\/repos\?next=/.test(block),
		'the link points at /repos with a next= query param, routed through resolve()'
	);
	ok(
		/encodeURIComponent\(connectNextUrl\(code, hash\)\)/.test(block),
		'next= carries this exact pairing code back via connectNextUrl'
	);
});

// --- A-1: every detour carries the approval proof ---

test('every detour off the approval screen rebuilds the link through connectNextUrl', () => {
	const src = flowSource();
	ok(/href=\{loginUrlForConnect\(code, hash\)\}/.test(src), 'the sign-in link carries the hash');
	ok(
		/encodeURIComponent\(connectNextUrl\(code, hash\)\)/.test(src),
		'the connect-a-repo detour carries the hash via connectNextUrl'
	);
	ok(
		!/`\/connect\/\$\{code\}`/.test(src),
		'no hand-rolled /connect/<code> path that would silently drop the fragment'
	);
});

// --- terminal statuses: all must be reachable ---

test('all terminal statuses are handled in the confirm phase', () => {
	const src = flowSource();
	// statusNotice covers unknown / expired / consumed / needsRepoEnable
	ok(src.includes('notice'), 'statusNotice output is rendered');
	ok(src.includes('needsRepoEnable'), 'needsRepoEnable branch present');
	ok(src.includes('linkIncomplete'), 'linkIncomplete (missing proof) branch present');
	ok(src.includes('canApprove'), 'canApprove gate present');
	ok(src.includes('unauthenticated'), 'unauthenticated state handled');
});

// --- local / synthesized repo name ---

test('suggestedIsLocal note renders for local forge, guarding the synthesized name', () => {
	const src = flowSource();
	ok(
		src.includes('suggestedIsLocal'),
		'suggestedIsLocal derived value is checked in the template'
	);
	ok(
		src.includes('no forge behind this one'),
		'the local-forge note renders to warn about the synthesized name'
	);
});

// --- no repo case falls back to picker ---

test('when the pairing reported no repo, phase 2 shows the picker not an invented name', () => {
	const src = flowSource();
	// showPicker is true when suggested_repo_full_name is '' — verified in loadContext.
	ok(/showPicker = context\.suggested_repo_full_name === ['"]/.test(src), 'showPicker gate');
	ok(src.includes('select'), 'the picker select element is present for the fallback case');
});

// --- routes are thin wrappers ---

test('/connect entry route delegates entirely to ConnectFlow with no props', () => {
	const src = readFileSync(connectEntryRoutePath, 'utf8');
	ok(src.includes('ConnectFlow'), 'entry route renders ConnectFlow');
	ok(!src.includes('window.location'), 'no navigation logic left in the entry route');
	ok(!src.includes('onMount'), 'no lifecycle logic left in the entry route');
});

test('/connect/[code] route passes initialCode and initialHash to ConnectFlow', () => {
	const src = readFileSync(connectCodeRoutePath, 'utf8');
	ok(src.includes('ConnectFlow'), 'deep-link route renders ConnectFlow');
	ok(src.includes('initialCode={code}'), 'initialCode prop is passed');
	ok(src.includes('initialHash={hash}'), 'initialHash prop is passed');
	ok(!src.includes('onMount'), 'no duplicate lifecycle logic in the route wrapper');
	// page.params.code and page.url.hash must still come from the route (not ConnectFlow)
	ok(src.includes('page.params.code'), 'code extracted from route params');
	ok(src.includes('page.url.hash'), 'hash extracted from URL');
});

// --- no logic duplication ---

test('ConnectFlow is the only file containing approval logic', () => {
	const entrySrc = readFileSync(connectEntryRoutePath, 'utf8');
	const codeSrc = readFileSync(connectCodeRoutePath, 'utf8');
	ok(!entrySrc.includes('fetchConnectContext'), 'no fetch logic in entry route');
	ok(!codeSrc.includes('fetchConnectContext'), 'no fetch logic in deep-link route');
	ok(!entrySrc.includes('approveConnect'), 'no approve logic in entry route');
	ok(!codeSrc.includes('approveConnect'), 'no approve logic in deep-link route');
});

// --- approval phase: the flow enters correctly, then exits to done ---

test('ConnectFlow transitions to done after a successful approve', () => {
	const src = flowSource();
	// On result.ok, phase must be set to 'done'
	ok(
		/phase = ['"]done['"]/.test(src),
		"phase is set to 'done' after a successful approve"
	);
	ok(src.includes("result.ok"), 'result.ok is the condition for the done transition');
});
