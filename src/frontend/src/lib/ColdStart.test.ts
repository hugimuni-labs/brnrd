import { deepEqual, equal, ok } from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { after, test } from 'node:test';
import { compile } from 'svelte/compiler';
import { render } from 'svelte/server';
import { DOCS_URL } from './publicStats.ts';
import type { ConnectedRepo, GitHubInstallation } from './repos.ts';

const here = dirname(fileURLToPath(import.meta.url));
const componentPath = join(here, 'ColdStart.svelte');
const generated = join(here, '.coldStart.generated.mjs');

// Same rendering dance as PublishConsentNotice.test.ts: compile the real
// component and render it with real props, so a claim that only becomes
// false in the rendered HTML still fails here.
async function renderColdStart(
	repos: ConnectedRepo[] | null,
	pairCommand: string | null = 'cd <repo>\nbrnrd account connect https://brnrd.dev',
	installations: GitHubInstallation[] | null = null
): Promise<string> {
	const source = readFileSync(componentPath, 'utf8');
	const compiled = compile(source, { generate: 'server', runes: true, name: 'ColdStart' });
	const runnable = compiled.js.code
		.replace(/'\.\/publicStats'/g, "'./publicStats.ts'")
		.replace(/import\s*\{[^}]*\}\s*from\s*'\$app\/paths';/, 'const resolve = (path) => path;');
	writeFileSync(generated, runnable);
	try {
		const module = await import(`${generated}?t=${process.pid}`);
		return render(module.default, { props: { repos, pairCommand, installations } }).body;
	} finally {
		rmSync(generated, { force: true });
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

after(() => rmSync(generated, { force: true }));

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
test('an account with nothing connected is told the three things that have to happen', async () => {
	const html = await renderColdStart([]);
	ok(html.includes('install the cli'), 'names the install step');
	ok(html.includes('npm install -g brnrd'), 'prints the headline install command');
	ok(html.includes('uv tool install brnrd'), 'offers the uv alternate');
	ok(html.includes('pipx install brnrd'), 'offers the pipx alternate');
	ok(html.includes('enable a repository'), 'names the repo-enablement step');
	ok(html.includes('href="/repos"'), 'links the page that step lives on');
	ok(html.includes('pair the daemon'), 'names the pairing step');
	ok(html.includes('brnrd account connect'), 'prints the pairing command');
	ok(html.includes(`href="${DOCS_URL}"`), 'carries a docs link');
});

// This inverts the old pin here ("the block is gone the moment the account
// has a repo") — that assertion *was* the regression (#1084). An enabled
// repo with no daemon is "connected but not connected": the block has to
// survive, step 03 has to stay visible, and step 02 should read done rather
// than repeat itself.
test('the block survives an enabled repo until a daemon has ever paired', async () => {
	const html = await renderColdStart([repo({ daemon_status: 'missing' })]);
	ok(html.includes('the cold start'), 'an enabled repo with no daemon is still the cold start');
	ok(html.includes('nothing is paired yet'));
	ok(html.includes('pair the daemon'), 'step 03 survives — this is exactly what used to vanish');
	ok(html.includes('brnrd account connect'), 'the pairing command still renders');
	ok(html.includes('— done'), 'step 02 (enable a repository) reads done, not repeated');
});

// The failure mode the old pin was actually guarding, restated correctly:
// a first-run panel that never leaves is worse than the blank page it
// replaced. It leaves once a daemon has *ever* registered — 'offline' counts
// (a laptop that's asleep already did this setup step once) — not only
// 'online'; this is a setup checklist, not a live health monitor.
for (const daemon_status of ['online', 'offline']) {
	test(`the block leaves once a daemon has registered (daemon_status=${daemon_status})`, async () => {
		const html = await renderColdStart([repo({ daemon_status })]);
		ok(!html.includes('the cold start'));
		ok(!html.includes('npm install -g brnrd'));
		ok(!html.includes('nothing is paired yet'));
	});
}

// The predicate must be an allowlist of the two known-paired values, not a
// blocklist of 'missing' — a value the backend never sends (a future status,
// a malformed payload) is not evidence of pairing, and must not fail open
// and hide step 03 the same way `!== 'missing'` used to.
test('an unrecognized daemon_status does not count as paired', async () => {
	const html = await renderColdStart([repo({ daemon_status: 'weird' })]);
	ok(html.includes('the cold start'), 'an unknown status is not silently treated as paired');
	ok(html.includes('nothing is paired yet'));
	ok(html.includes('pair the daemon'), 'step 03 still renders');
});

// The other half of the regression: an account that already installed the
// GitHub App must not be told to install it again (#1084's "instructing a
// user who just installed it to install it").
test('an installed-but-unenabled App is not told to install the App again', async () => {
	const html = await renderColdStart([], undefined, [installation()]);
	ok(html.includes('the cold start'));
	ok(html.includes('GitHub App is installed'), 'names the fact already true');
	ok(
		!html.includes('Install the brnrd GitHub App where the repository lives'),
		'does not re-ask for an install that already happened'
	);
	ok(html.includes('enable a repository'), 'still points at the one thing left to do');
});

// No installation at all still gets the original two-part instruction —
// this is the branch the very first test in this file also exercises with
// `installations` defaulted to `null`, pinned again here explicitly against
// the same fixture the "already installed" case above uses.
test('no installation at all still asks to install the App', async () => {
	const html = await renderColdStart([], undefined, []);
	ok(html.includes('Install the brnrd GitHub App where the repository lives'));
	ok(!html.includes('GitHub App is installed'));
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

// A missing command must not render an empty terminal box pretending to hold
// one; the step's prose still stands on its own.
test('a missing pairing command drops the code block, not the step', async () => {
	const html = await renderColdStart([], null);
	ok(html.includes('pair the daemon'), 'the step survives');
	ok(html.includes('In the checkout'), 'its prose survives');
	ok(!html.includes('brnrd account connect'), 'no terminal box pretending to hold a command');
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
