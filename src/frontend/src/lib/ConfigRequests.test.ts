import { ok } from 'node:assert/strict';
import { readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { after, test } from 'node:test';
import { compile } from 'svelte/compiler';
import { render } from 'svelte/server';
import type { ConfigChangeRequestItem } from './configRequests.ts';

const here = dirname(fileURLToPath(import.meta.url));
const componentPath = join(here, 'ConfigRequests.svelte');
const generated = join(here, '.configRequests.generated.mjs');

// The dashboard's sole surviving "needs you" surface (2026-09-01, the
// needs-you strip's PR-review half retired). The caller (Dashboard.svelte)
// only mounts this when there is something to show, so this file only
// needs to cover what it renders once mounted — the absent-when-empty
// half of the contract lives in Dashboard.svelte's own mount guard, not
// here. Same compile-to-server-target dance as the deleted
// BackchannelQueue.test.ts (`svelte/compiler` + `svelte/server`, no
// bundler in this test's toolchain).
async function renderRequests(props: {
	requests?: ConfigChangeRequestItem[];
	error?: string | null;
}): Promise<string> {
	const source = readFileSync(componentPath, 'utf8');
	const compiled = compile(source, {
		generate: 'server',
		runes: true,
		name: 'ConfigRequests'
	});
	const runnable = compiled.js.code
		.replace(/'\.\/runLedger'/g, "'./runLedger.ts'")
		.replace(/'\.\/statusPalette'/g, "'./statusPalette.ts'");
	writeFileSync(generated, runnable);
	try {
		const module = await import(`${generated}?t=${process.pid}-${Math.random()}`);
		return render(module.default, {
			props: {
				requests: [],
				now: Date.parse('2026-09-01T12:00:00Z'),
				error: null,
				...props
			}
		}).body;
	} finally {
		rmSync(generated, { force: true });
	}
}

after(() => rmSync(generated, { force: true }));

function req(overrides: Partial<ConfigChangeRequestItem>): ConfigChangeRequestItem {
	return {
		id: 'cfg-1',
		repo_label: 'x/y',
		config_key: 'runner.shell',
		current_value: 'claude',
		requested_value: 'codex',
		reason: '',
		created_at: null,
		expires_at: null,
		approve_url: 'https://example.test/config/cfg-1',
		...overrides
	};
}

test('the block is labeled for what it now carries — no "needs you" wording remains', async () => {
	const html = await renderRequests({ requests: [req({})] });
	ok(html.includes('config approvals'));
	ok(!/needs you/i.test(html));
});

test('a pending request renders its row', async () => {
	const html = await renderRequests({ requests: [req({ config_key: 'runner.shell' })] });
	ok(html.includes('runner.shell: claude → codex'));
});

test('a fetch error renders instead of the rows, not alongside them', async () => {
	const html = await renderRequests({
		requests: [req({})],
		error: 'config-requests fetch failed: 500'
	});
	ok(html.includes('config-requests fetch failed: 500'));
	ok(!html.includes('runner.shell: claude → codex'));
});
