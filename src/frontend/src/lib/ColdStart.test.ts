import { deepEqual, equal, ok } from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { after, test } from 'node:test';
import { compile } from 'svelte/compiler';
import { render } from 'svelte/server';
import { DOCS_URL } from './publicStats.ts';
import type { ConnectedRepo, GitHubInstallation, MachinesSummary, MessengerDoor } from './repos.ts';

const here = dirname(fileURLToPath(import.meta.url));
const componentPath = join(here, 'ColdStart.svelte');
const generated = join(here, '.coldStart.generated.mjs');
const messengerDoorsPath = join(here, 'MessengerDoors.svelte');
const generatedMessengerDoors = join(here, '.coldStartMessengerDoors.generated.mjs');
const pairingCommandPath = join(here, 'PairingCommand.svelte');
const generatedPairingCommand = join(here, '.coldStartPairingCommand.generated.mjs');

// Same rendering dance as PublishConsentNotice.test.ts: compile the real
// component and render it with real props, so a claim that only becomes
// false in the rendered HTML still fails here.
async function renderColdStart(
	repos: ConnectedRepo[] | null,
	pairCommand: string | null = 'cd <repo>\nbrnrd',
	installations: GitHubInstallation[] | null = null,
	// #1365: defaults to `null`, same "an older/unwired caller reads as
	// unknown, not paired" contract the component itself gives the prop —
	// so every existing call site below still exercises the pre-#1365
	// repo-scoped-only gate unchanged.
	machines: MachinesSummary | null = null,
	// Origin-aware onboarding (2026-08-17): defaults to `null`, which reads
	// through to real client detection — `$effect` never runs under
	// `svelte/server`, so `null` here renders the desktop branch exactly
	// like every call site below always has. Tests below pass `true`/
	// `false` explicitly to pin the mobile branch without a browser.
	mobileOverride: boolean | null = null,
	// #1465: defaults to `null`, same "absent/empty reads as unknown, keep
	// today's copy" contract the component gives the prop — every existing
	// call site below still exercises the pre-#1465 honest-intermediate
	// mobile copy unchanged. Tests below pass a real registry array to pin
	// the tappable branch.
	messengerDoors: MessengerDoor[] | null = null
): Promise<string> {
	const source = readFileSync(componentPath, 'utf8');
	const messengerDoorsSource = readFileSync(messengerDoorsPath, 'utf8');
	const messengerDoorsCompiled = compile(messengerDoorsSource, {
		generate: 'server',
		runes: true,
		name: 'MessengerDoors'
	});
	writeFileSync(
		generatedMessengerDoors,
		messengerDoorsCompiled.js.code.replace(/'(\.\/[A-Za-z0-9_-]+)'/g, "'$1.ts'")
	);
	// PairingCommand is now imported by ColdStart — compile it the same way
	// MessengerDoors is compiled, so the SSR harness can resolve the import.
	const pairingCommandSource = readFileSync(pairingCommandPath, 'utf8');
	const pairingCommandCompiled = compile(pairingCommandSource, {
		generate: 'server',
		runes: true,
		name: 'PairingCommand'
	});
	writeFileSync(
		generatedPairingCommand,
		pairingCommandCompiled.js.code.replace(/'(\.\/[A-Za-z0-9_-]+)'/g, "'$1.ts'")
	);
	const compiled = compile(source, { generate: 'server', runes: true, name: 'ColdStart' });
	const runnable = compiled.js.code
		// Same generic rewrite: any bare relative import needs its `.ts`
		// extension for Node's loader. Generic from #1277a onward so new
		// imports don't each need their own regex line.
		.replace(/'(\.\/[A-Za-z0-9_-]+)'/g, "'$1.ts'")
		.replace("'./MessengerDoors.svelte'", "'./.coldStartMessengerDoors.generated.mjs'")
		.replace("'./PairingCommand.svelte'", "'./.coldStartPairingCommand.generated.mjs'")
		.replace(/import\s*\{[^}]*\}\s*from\s*'\$app\/paths';/, 'const resolve = (path) => path;');
	writeFileSync(generated, runnable);
	try {
		const module = await import(`${generated}?t=${process.pid}`);
		return render(module.default, {
			props: { repos, pairCommand, installations, machines, mobileOverride, messengerDoors }
		}).body;
	} finally {
		rmSync(generated, { force: true });
		rmSync(generatedMessengerDoors, { force: true });
		rmSync(generatedPairingCommand, { force: true });
	}
}

function installation(over: Partial<GitHubInstallation> = {}): GitHubInstallation {
	return {
		id: 'inst-1',
		installation_id: '12345',
		target_login: 'Gurio',
		target_type: 'User',
		created_at: null,
		last_synced_at: null,
		last_synced_label: '',
		...over
	};
}

after(() => {
	rmSync(generated, { force: true });
	rmSync(generatedMessengerDoors, { force: true });
	rmSync(generatedPairingCommand, { force: true });
});

function repo(over: Partial<ConnectedRepo> = {}): ConnectedRepo {
	return {
		id: 'repo-1',
		dispatch_default: false,
		repo_full_name: 'Gurio/brr',
		forge: 'github',
		forge_repo_id: null,
		repo_owner: 'Gurio',
		repo_name: 'brr',
		default_branch: null,
		created_at: null,
		updated_at: null,
		created_label: '',
		updated_label: '',
		daemon_count: 0,
		daemon_status: 'missing',
		daemon_label: '',
		daemon_last_seen: '',
		daemon_last_seen_at: null,
		latest_daemon_name: '',
		gates: [],
		setup_command: '',
		telegram_paired: false,
		environment_default: null,
		environments: [],
		publish_layers: null,
		github_bot_collaborator: null,
		github_bot_checked_at: null,
		github_bot_checked_label: 'never',
		github_bot_status: null,
		github_bot_marker_notice: null,
		github_bot_notice: null,
		...over
	};
}

// The reported gap (2026-08-03 signup): six empty sections and no install
// line, no pointer at the page where a repo is enabled, no docs link.
// #1243 put `brnrd init` here; the 08-14 iMac trace found the board still
// teaching it four days after `decision-retire-init.md` folded it into the
// bare-`brnrd` front door. Two rungs now: install, then the guided door.
test('an account with nothing connected is told the two things that have to happen', async () => {
	const html = await renderColdStart([]);
	ok(html.includes('install the cli'), 'names the install step');
	ok(html.includes('npm install -g brnrd'), 'prints the headline install command');
	ok(html.includes('uv tool install brnrd'), 'offers the uv alternate');
	ok(html.includes('pipx install brnrd'), 'offers the pipx alternate');
	ok(html.includes('the guided setup'), 'names the guided-door step');
	ok(!html.includes('brnrd init'), 'the retired verb is off the wall (08-14 trace)');
	ok(!html.includes('account connect'), 'the pre-door pairing spell is off the wall too');
	ok(html.includes(`href="${DOCS_URL}"`), 'carries a docs link');
});

// This inverts the old pin here ("the block is gone the moment the account
// has a repo") — that assertion *was* the regression (#1084). A connected
// repo with no daemon is "connected but not connected": the block has to
// survive and the pairing step has to stay visible.
test('the block survives a connected repo until a daemon has ever paired', async () => {
	const html = await renderColdStart([repo({ daemon_status: 'missing' })]);
	ok(html.includes('the cold start'), 'a connected repo with no daemon is still the cold start');
	ok(html.includes('nothing is paired yet'));
	ok(
		html.includes('the guided setup'),
		'the setup step survives — this is exactly what used to vanish'
	);
});

// #1365, the fixture that named this gap: a machine paired at the account
// level with zero connected repos must not read "nothing is paired yet" —
// that was the literal screenshot (capabilities board listing the machine
// directly under this exact copy).
test('a paired account-level machine with no repos is not told nothing is paired', async () => {
	const html = await renderColdStart([], undefined, null, {
		paired: true,
		any_enabled_repo: false
	});
	ok(
		html.includes('the cold start'),
		'the block still renders — there is genuinely nothing to show yet'
	);
	ok(!html.includes('nothing is paired yet'), 'the account has, in fact, paired');
	ok(html.includes('machine paired, no repo enabled yet'), 'names the honest intermediate state');
	ok(html.includes('enable a repo'), 'points at the actual next step, not re-pairing');
});

// The same gap, but with a connected repo whose own daemon status is
// `missing` — the repo-scoped signal alone still says "not paired" here,
// exactly like the classic cold case above; only the account-level
// `machines.paired` fact tells them apart.
test('a paired machine with a repo connected but not yet enabled reads the same honest state', async () => {
	const html = await renderColdStart([repo({ daemon_status: 'missing' })], undefined, null, {
		paired: true,
		any_enabled_repo: false
	});
	ok(!html.includes('nothing is paired yet'));
	ok(html.includes('machine paired, no repo enabled yet'));
});

// Once any repo actually carries a daemon, `daemonEverPaired` alone already
// clears the block (pre-#1365 behavior) — `machines.paired` being true too
// must not resurrect either panel.
test('the block is gone once a repo is actually enabled, machines summary or not', async () => {
	const html = await renderColdStart([repo({ daemon_status: 'online' })], undefined, null, {
		paired: true,
		any_enabled_repo: true
	});
	ok(!html.includes('the cold start'));
	ok(!html.includes('machine paired, no repo enabled yet'));
});

// An older backend that predates the `machines` field must not regress
// toward the bug: `undefined`/`null` reads as unknown, never as paired, so
// this stays the classic cold-start ladder exactly as before #1365.
test('a backend that omits the machines summary keeps the pre-#1365 ladder', async () => {
	const html = await renderColdStart([], undefined, null, null);
	ok(html.includes('nothing is paired yet'));
	ok(!html.includes('machine paired, no repo enabled yet'));
});

// Step 01 is unobservable (no wire fact says "the CLI is installed") and
// the guided door reports its own rungs in the terminal — so no step here
// may render a done-marker a wire fact does not back.
test('no step claims done — the ladder is unobservable from here', async () => {
	const html = await renderColdStart([repo({ daemon_status: 'missing' })]);
	ok(!html.includes('— done'), 'no step in this ladder renders a done-marker');
});

// The finding `brr/one-sequence-two-surfaces` fixed was an ordering bug —
// pinned as a position assertion, not a substring check, since the wrong
// ordering also contained every phrase. Two rungs since the 08-14 trace:
// the CLI must exist before the one word that drives it.
test('the ladder reads install → run brnrd', async () => {
	const html = await renderColdStart([]);
	const installAt = html.indexOf('install the cli');
	const doorAt = html.indexOf('the guided setup');
	ok(installAt >= 0 && doorAt >= 0, 'both rungs render');
	ok(installAt < doorAt, 'install precedes the door — the CLI is a prerequisite');
});

// The failure mode the old pin was actually guarding, restated correctly:
// a first-run panel that never leaves is worse than the blank page it
// replaced. It leaves once a daemon has *ever* registered — 'offline' and
// 'never_started' both count (a laptop that's asleep, or a daemon crash-
// looping post-registration, both already did this setup step once) — not
// only 'online'; this is a setup checklist, not a live health monitor.
for (const daemon_status of ['online', 'offline', 'never_started']) {
	test(`the block leaves once a daemon has registered (daemon_status=${daemon_status})`, async () => {
		const html = await renderColdStart([repo({ daemon_status })]);
		ok(!html.includes('the cold start'));
		ok(!html.includes('npm install -g brnrd'));
		ok(!html.includes('nothing is paired yet'));
	});
}

// The predicate must be an allowlist of the three known-paired values, not a
// blocklist of 'missing' — a value the backend never sends (a future status,
// a malformed payload) is not evidence of pairing, and must not fail open
// and hide the pairing step the same way `!== 'missing'` used to.
test('an unrecognized daemon_status does not count as paired', async () => {
	const html = await renderColdStart([repo({ daemon_status: 'weird' })]);
	ok(html.includes('the cold start'), 'an unknown status is not silently treated as paired');
	ok(html.includes('nothing is paired yet'));
	ok(html.includes('the guided setup'), 'the setup step still renders');
});

// #1243: the GitHub App is an optional identity upgrade, named once in the
// footer — never a gate, and never told to an account that already has it.
test('an installed App is named as already done, in the footer, not as a step', async () => {
	const html = await renderColdStart([], undefined, [installation()]);
	ok(html.includes('the cold start'));
	ok(html.includes('GitHub App installed'), 'names the fact already true');
	ok(
		!html.includes('Optional: install the GitHub App'),
		'does not re-ask for an install that already happened'
	);
	ok(html.includes('brnrd-dev[bot]'), 'says what the App actually buys — commits post as the bot');
});

// No installation at all still gets pointed at the App, but as an optional
// upgrade rather than a blocking step — this is the branch the very first
// test in this file also exercises with `installations` defaulted to
// `null`, pinned again here explicitly against the same fixture the
// "already installed" case above uses.
test('no installation at all still names the App as optional, not required', async () => {
	const html = await renderColdStart([], undefined, []);
	ok(html.includes('Optional: install the GitHub App'));
	ok(html.includes('Nothing here waits on it'));
	ok(!html.includes('GitHub App installed'));
});

// `null` is "the repos fetch has not landed", not "this account is empty".
// Rendering the cold start on it would flash a first-run panel at an account
// with fifteen repos on every page load.
test('an unlanded repo list renders nothing rather than guessing empty', async () => {
	const html = await renderColdStart(null);
	ok(!html.includes('the cold start'));
	ok(!html.includes('npm install -g brnrd'));
});

// The pairing lines are backend-owned (`_session.pairing_command`, served as
// `pairing_command` on /v1/dashboard/repos). This component must render what
// it is handed and hold no second copy of its own — a local fallback string
// is exactly the drift the single source exists to prevent.
test('the pairing command is rendered from the prop, never restated in the component', async () => {
	const html = await renderColdStart([], 'cd <repo>\nbrnrd account connect https://elsewhere');
	ok(html.includes('brnrd account connect https://elsewhere'));
	ok(!html.includes('https://brnrd.dev'), 'no hardcoded endpoint of its own');
	const source = readFileSync(componentPath, 'utf8');
	ok(!source.includes('brnrd account connect'), 'the command is not typed into the component');
});

// #1277a — the maintainer's own report: the COPY button next to step 02 used
// to hand over `cd <repo>` verbatim along with the runnable line beneath it,
// a literal placeholder no shell can run. The box (and, by construction, the
// button that copies its content) must hold only the runnable line; the `cd`
// step becomes prose above it instead.
test('the cd placeholder never appears inside the copyable command box', async () => {
	// A distinctive runnable line, so the assertion can find *it* rather
	// than the word "brnrd", which this page says everywhere.
	const html = await renderColdStart([], 'cd <repo>\nbrnrd-runnable-line');
	ok(!html.includes('cd <repo>'), 'the literal placeholder is not printed anywhere on the page');
	ok(html.includes('from your repo checkout:'), 'scene-setting prose replaces it');
	ok(
		html.includes('brnrd-runnable-line'),
		'the runnable line still renders, unconditionally copyable'
	);
});

// A pairing command that is a single line (never sent today, but the
// component must not assume two) has nothing to split out — no stray prose
// paragraph, and the whole string still renders and is still copyable.
test('a single-line pairing command renders whole, with no setup-line prose', async () => {
	const html = await renderColdStart([], 'brnrd account connect https://brnrd.dev');
	ok(html.includes('brnrd account connect https://brnrd.dev'));
	ok(!html.includes('from your repo checkout:'), 'nothing to split out of a single line');
});

// A missing command must not render an empty terminal box pretending to hold
// one; the step's prose still stands on its own.
test('a missing pairing command drops the code block, not the step', async () => {
	const html = await renderColdStart([], null);
	ok(html.includes('the guided setup'), 'the step survives');
	ok(html.includes('In the checkout'), 'its prose survives');
	// Exactly one terminal box on the page — step 01's install command; the
	// step-02 box is gone rather than rendered empty.
	equal(html.split('<pre').length - 1, 1, 'no empty box pretending to hold a command');
});

// The product's only docs link used to be `gurio.github.io/brr/`, which
// returns 404 since the repo transfer — driven 2026-08-03, both hosts. It is
// one constant now precisely so it cannot rot per-surface again.
test('the docs constant points at the published site, not the retired host', async () => {
	ok(!DOCS_URL.includes('gurio.github.io'));
	equal(DOCS_URL, 'https://hugimuni-labs.github.io/brnrd/');
});

// Source-level, deliberately: the two signed-in routes are the whole
// dashboard and the whole repo manager, and compiling either to assert one
// header link would drag in forty components for a one-line claim. What is
// pinned is the property that was actually missing — that each signed-in
// surface reaches the docs at all, through the shared constant rather than
// its own literal.
const SIGNED_IN_ROUTES = [
	join(here, '..', 'routes', '+page.svelte'),
	join(here, '..', 'routes', 'repos', '+page.svelte')
];

test('every signed-in surface carries a docs link, bound to the shared constant', () => {
	for (const route of SIGNED_IN_ROUTES) {
		const source = readFileSync(route, 'utf8');
		ok(source.includes('href={DOCS_URL}'), `${route} links the docs`);
		ok(source.includes("from '$lib/publicStats'"), `${route} takes it from the constant`);
	}
});

// The sweep the fix was scoped by: no surface anywhere may re-acquire the
// dead host. It is the product's documentation link — a 404 there is the
// only answer a stuck reader gets.
test('no frontend source points at the retired docs host', () => {
	// This file names the dead host to assert against it, so the sweep reads
	// shipped sources only.
	let hits: string[] = [];
	try {
		hits = execFileSync(
			'grep',
			// `.*.generated.mjs` are the SSR compile harnesses' scratch files
			// (ControlStrip.test.ts etc.), written and unlinked *while this
			// sweep runs* under the concurrent test runner — grep enumerating
			// one and reading it after its unlink exits 2 ("No such file or
			// directory"), which this test then reported as a failure with no
			// dead-host hit anywhere (first tripped on #1076's CI, 2026-08-03).
			// They are generated from the very sources the sweep already
			// reads, so excluding them drops no coverage.
			[
				'-rl',
				'--exclude=*.test.ts',
				'--exclude=.*.generated.mjs',
				'gurio.github.io',
				join(here, '..')
			],
			{ encoding: 'utf8' }
		)
			.split('\n')
			.filter(Boolean);
	} catch (e) {
		// grep exits 1 with no output when nothing matches — the passing case.
		if ((e as { status?: number }).status !== 1) throw e;
	}
	deepEqual(hits, []);
});

// Origin-aware onboarding (2026-08-17): a phone arrival gets a different
// primary CTA — the sections below pin the desktop branch as an explicit
// no-op and the mobile branch's reordering, rather than relying only on
// every pre-existing test above defaulting `mobileOverride` to `null`.

// `mobileOverride: false` must render byte-identical to the omitted default
// (`null`) — the one proof that "on desktop, NOTHING changes" holds for the
// explicit as well as the implicit non-touch case, not just for whichever
// one the older tests above happen to exercise.
test('desktop (mobileOverride: false) renders identically to the default (null) case', async () => {
	const withDefault = await renderColdStart([repo({ daemon_status: 'missing' })]);
	const withExplicitFalse = await renderColdStart(
		[repo({ daemon_status: 'missing' })],
		undefined,
		null,
		null,
		false
	);
	equal(withExplicitFalse, withDefault);
	ok(!withDefault.includes('the messenger door'), 'the desktop branch never mentions the CTA');
});

// The reordering itself: on mobile, the messenger-door panel renders before
// the demoted install ladder, and the ladder's own numbered "01"/"02" rungs
// (and their copy buttons) are gone — replaced by plain reference text, not
// a second copy of the same interactive ladder.
test('mobile arrival leads with the messenger door, install ladder demoted to reference', async () => {
	const html = await renderColdStart([], undefined, null, null, true);
	ok(html.includes('the cold start'));
	const doorAt = html.indexOf('the messenger door');
	const computerAt = html.indexOf('on your computer');
	ok(doorAt >= 0 && computerAt >= 0, 'both the CTA and the demoted note render');
	ok(doorAt < computerAt, 'the messenger door leads — install is demoted beneath it');
	ok(
		!html.includes('opens once a machine pairs'),
		'the CTA states the real unlock condition (a repo), not a stale one'
	);
	ok(html.includes('npm install -g brnrd'), 'the install command still renders, as reference');
	ok(!html.includes('text-amber-200/80">01<'), 'the numbered ladder rung is gone on mobile');
	ok(!html.includes('>copy<'), 'the demoted note is informational — no copy affordance');
});

// Same reordering for the account-paired-but-repo-less middle state: the
// messenger door still can't be wired (repo, not machine pairing, is what
// `telegram_pair_core` actually gates on), so mobile gets the same honest
// framing here too, ahead of the demoted "enable a repo" command.
test('mobile arrival in the paired-no-repo state also leads with the messenger door', async () => {
	const html = await renderColdStart(
		[],
		undefined,
		null,
		{ paired: true, any_enabled_repo: false },
		true
	);
	ok(html.includes('machine paired, no repo enabled yet'));
	const doorAt = html.indexOf('the messenger door');
	const computerAt = html.indexOf('on your computer');
	ok(doorAt >= 0 && computerAt >= 0);
	ok(doorAt < computerAt);
	ok(html.includes('still waits on a repo'), 'names the actual remaining gap, not a fake link');
	ok(!html.includes('>copy<'), 'no copy affordance in the demoted reference note');
});

// A mobile arrival with a still-cold account and no `pairCommand` at all
// (backend hasn't landed the fetch yet) must not render an empty terminal
// box pretending to hold the pairing line — same contract the desktop
// "missing pairing command" test above pins, carried into the mobile branch.
test('mobile arrival with no pairCommand drops the second reference box, not the CTA', async () => {
	const html = await renderColdStart([], null, null, null, true);
	ok(html.includes('the messenger door'));
	equal(html.split('<pre').length - 1, 1, 'only the install command renders — no empty pair box');
});

// #1457 (generalized #1465) — the link becomes constructible: a backend
// that carries an available messenger door flips the mobile CTA from the
// honest-intermediate copy to a tappable door. Three renderings of the
// wire contract, pinned separately since each means something different:
// an available door, a door the registry declares unavailable, and an
// older backend that never sends the field at all.

test('a backend with an available telegram door renders the tappable door, not the honest intermediate', async () => {
	const html = await renderColdStart([], undefined, null, null, true, [
		{ platform: 'telegram', deep_link_available: true }
	]);
	ok(html.includes('the messenger door'));
	ok(html.includes('data-testid="connect-telegram"'), 'the tap affordance renders');
	ok(html.includes('connect telegram'), 'the canonical button carries the CTA copy');
	ok(
		!html.includes('once a repo is enabled'),
		'the honest-intermediate copy is gone once a real door exists'
	);
});

test('a registry with no available door keeps the honest-intermediate fallback', async () => {
	const html = await renderColdStart([], undefined, null, null, true, [
		{ platform: 'telegram', deep_link_available: false },
		{ platform: 'whatsapp', deep_link_available: false }
	]);
	ok(html.includes('the messenger door'));
	ok(
		!html.includes('data-testid="connect-telegram"'),
		'no tap affordance without an available door'
	);
	ok(html.includes('once a repo is enabled'), 'the pre-#1457 copy still renders');
	ok(!html.includes('Telegram or WhatsApp'), '#1465: no longer promises a platform nothing backs');
});

test("an absent messenger_doors field (older backend) renders exactly today's copy", async () => {
	// `undefined` — the same shape a caller gets from `repos.messenger_doors`
	// on a response that predates #1465 and omits the key entirely.
	const withAbsent = await renderColdStart([], undefined, null, null, true, undefined as never);
	const withExplicitNull = await renderColdStart([], undefined, null, null, true, null);
	equal(withAbsent, withExplicitNull, 'an omitted key renders identically to the explicit default');
	ok(!withAbsent.includes('data-testid="connect-telegram"'));
	ok(withAbsent.includes('once a repo is enabled'));
});

test('a registry with an available whatsapp door renders its own tappable button', async () => {
	const html = await renderColdStart([], undefined, null, null, true, [
		{ platform: 'telegram', deep_link_available: false },
		{ platform: 'whatsapp', deep_link_available: true }
	]);
	ok(html.includes('data-testid="connect-whatsapp"'), 'the whatsapp tap affordance renders');
	ok(html.includes('connect whatsapp'));
	ok(
		!html.includes('data-testid="connect-telegram"'),
		'telegram stays unavailable, no button for it'
	);
});

test('both doors available render two tappable buttons, no hand-picked primary', async () => {
	const html = await renderColdStart([], undefined, null, null, true, [
		{ platform: 'telegram', deep_link_available: true },
		{ platform: 'whatsapp', deep_link_available: true }
	]);
	ok(html.includes('data-testid="connect-telegram"'));
	ok(html.includes('data-testid="connect-whatsapp"'));
});

// Same flip, same reasoning, in the paired-no-repo state: #1457 mints
// account-level, so this state's door unlocks on `messengerDoors` (#1465)
// alone too, not on enabling a repo first.
test('paired-no-repo state also renders the tappable door once one is available', async () => {
	const html = await renderColdStart(
		[],
		undefined,
		null,
		{ paired: true, any_enabled_repo: false },
		true,
		[{ platform: 'telegram', deep_link_available: true }]
	);
	ok(html.includes('machine paired, no repo enabled yet'));
	ok(html.includes('data-testid="connect-telegram"'));
	ok(!html.includes('still waits on a repo'), 'the stale repo-gated copy is gone');
});

// The constraint: "the desktop path stays byte-identical." `messengerDoors`
// is read only inside the `isMobile` branches, so a desktop render must not
// change at all regardless of its value.
test('desktop rendering is unaffected by messengerDoors', async () => {
	const withoutDoors = await renderColdStart([repo({ daemon_status: 'missing' })]);
	const withDoors = await renderColdStart(
		[repo({ daemon_status: 'missing' })],
		undefined,
		null,
		null,
		false,
		[{ platform: 'telegram', deep_link_available: true }]
	);
	equal(withDoors, withoutDoors, 'desktop HTML is byte-identical either way');
});

// Door copy cleanup (brr/the-board-that-said-it-twice): when doors are
// available the mobile panel shows a shared intro sentence once, above the
// tiles, so the "opens the app directly" concept is said once rather than
// repeated per tile inside MessengerDoors (which is outside this file's
// ownership). Pin: intro appears before the first door's tap button.
test('mobile arrival with available doors shows shared intro above the door tiles', async () => {
	const html = await renderColdStart([], undefined, null, null, true, [
		{ platform: 'telegram', deep_link_available: true },
		{ platform: 'whatsapp', deep_link_available: true }
	]);
	ok(html.includes('opens the app directly'), 'shared intro renders');
	const introAt = html.indexOf('opens the app directly');
	const tileAt = html.indexOf('data-testid="connect-telegram"');
	ok(introAt >= 0 && tileAt >= 0, 'both intro and first tile render');
	ok(introAt < tileAt, 'shared intro is above the door tiles');
});

// The shared intro must not render when no door is available — the
// honest-intermediate copy fills that slot instead.
test('shared intro is absent when no doors are available', async () => {
	const html = await renderColdStart([], undefined, null, null, true, [
		{ platform: 'telegram', deep_link_available: false }
	]);
	ok(!html.includes('opens the app directly'), 'no intro without an available door');
	ok(html.includes('once a repo is enabled'), 'honest-intermediate copy still renders');
});
