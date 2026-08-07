import { ok } from 'node:assert/strict';
import { readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { after, test } from 'node:test';
import { compile } from 'svelte/compiler';
import { render } from 'svelte/server';
import type { ConnectedRepo } from './repos.ts';

const here = dirname(fileURLToPath(import.meta.url));
const componentPath = join(here, 'PublishConsentNotice.svelte');
const generated = join(here, '.publishConsentNotice.generated.mjs');

// Same rendering dance as WithheldNotice.test.ts / pricing.test.ts: compile
// the real component and render it with real props. `$app/paths`'s `resolve`
// only exists inside a SvelteKit build, so it is swapped for the identity
// function it is at this app's root base path.
async function renderNotice(repos: ConnectedRepo[] | null): Promise<string> {
	const source = readFileSync(componentPath, 'utf8');
	const compiled = compile(source, {
		generate: 'server',
		runes: true,
		name: 'PublishConsentNotice'
	});
	const runnable = compiled.js.code
		.replace(/'\.\/publishScope'/g, "'./publishScope.ts'")
		.replace(/import\s*\{[^}]*\}\s*from\s*'\$app\/paths';/, 'const resolve = (path) => path;');
	writeFileSync(generated, runnable);
	try {
		const module = await import(`${generated}?t=${process.pid}`);
		return render(module.default, { props: { repos } }).body;
	} finally {
		rmSync(generated, { force: true });
	}
}

after(() => rmSync(generated, { force: true }));

function repo(over: Partial<ConnectedRepo>): ConnectedRepo {
	return {
		id: 'repo-1',
		dispatch_default: false,
		repo_full_name: 'Gurio/BeCenter',
		forge: 'github',
		forge_repo_id: null,
		repo_owner: 'Gurio',
		repo_name: 'BeCenter',
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

test('no repos, or none with a consent gap, renders nothing visible', async () => {
	// Svelte's SSR output for an empty `{#if}` is a handful of hydration
	// comment markers, not literally nothing — assert on the fragment's
	// meaning (no banner text) rather than pin those markers as a literal.
	for (const html of [
		await renderNotice(null),
		await renderNotice([repo({ publish_layers: 'corpus,quota' })])
	]) {
		ok(!html.includes('paused'));
		ok(!html.includes('set a scope'));
	}
});

// The bug the coordinator flagged: `publish_layers === null` proves only that
// no scope was ever recorded, not *why* — the old copy ("these repos were
// connected before the publish consent existed and have never been asked")
// asserted a history the data cannot support. A repo minted through the
// account API today lands `null` the same as a pre-existing one.
test('an unrecorded repo is named without inventing a connection history', async () => {
	const html = await renderNotice([repo({ publish_layers: null })]);
	ok(html.includes('Gurio/BeCenter'));
	ok(html.includes('never recorded a publish scope'));
	ok(!html.includes('connected before'), 'must not claim when the repo connected');
	ok(!html.includes('have never been asked'), 'must not claim a history of never being asked');
});

test('an opted-out repo is named as having chosen to publish nothing', async () => {
	const html = await renderNotice([repo({ id: 'repo-2', publish_layers: 'none' })]);
	ok(html.includes('Gurio/BeCenter'));
	ok(html.includes('chose to publish nothing'));
});

test('the deep link targets the specific repo id when one is known', async () => {
	const html = await renderNotice([repo({ id: 'repo-9', publish_layers: null })]);
	ok(html.includes('href="/repos?scope=repo-9#repo-repo-9"'));
});

test("the wording matches WithheldNotice's shared clause, not a restated copy", async () => {
	// Both components import unrecordedClause/optedOutClause from
	// publishScope.ts; this pins the fact that this banner's rendered text
	// actually contains that shared sentence, not a look-alike hand-copy.
	const html = await renderNotice([
		repo({ id: 'repo-1', repo_full_name: 'Gurio/BeCenter', publish_layers: null }),
		repo({ id: 'repo-2', repo_full_name: 'Gurio/other-repo', publish_layers: 'none' })
	]);
	ok(html.includes('Gurio/BeCenter never recorded a publish scope'));
	ok(html.includes('Gurio/other-repo chose to publish nothing'));
});
