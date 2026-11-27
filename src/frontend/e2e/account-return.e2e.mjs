// /account after setup (letter 11758): tier and readiness are two answers, and the GitHub App's return lands here.
import { test } from 'node:test';
import { equal, ok } from 'node:assert/strict';
import { open } from './harness.mjs';

const cloud = {
	deployment: 'cloud',
	tree: null,
	features: ['account', 'consent', 'dashboard', 'settings']
};
const json = (body) => ({
	status: 200,
	contentType: 'application/json',
	body: JSON.stringify(body)
});
const free = {
	tier: 'free',
	status: null,
	cohort: null,
	cadence: null,
	cancel_at_period_end: false
};

async function account(path, { sub, repos = {}, pairedChats = [] }) {
	// The default parts: this account's loom has sent its board, so readiness is ready.
	const B = await open({ site: cloud, ready: '.brd header' });
	const asked = [];
	await B.page.route('**/v1/dashboard/repos*', (r) => {
		asked.push(new URL(r.request().url()).search);
		r.fulfill(json({ account: { github_login: 'ada' }, connected_repos: [], ...repos }));
	});
	await B.page.route('**/v1/dashboard/paired-chats', (r) =>
		r.fulfill(json({ paired_chats: pairedChats }))
	);
	await B.page.route('**/v1/stats/pricing', (r) => r.fulfill(json({})));
	await B.page.route('**/v1/accounts/subscription', (r) => r.fulfill(json(sub)));
	await B.page.goto(B.base + path.replace(/^\//, ''));
	await B.page.waitForSelector('.acct .panel');
	return { B, asked };
}

test('a free account whose loom is connected is pointed at its board, not at setup', async () => {
	const { B } = await account('/account', { sub: free });
	try {
		await B.page.getByRole('link', { name: 'Open your board' }).waitFor();
		equal(await B.page.getByRole('link', { name: 'Set up brnrd' }).count(), 0);
	} finally {
		await B.close();
	}
});

test('a member reads as member, with no subscribe pitch', async () => {
	const { B } = await account('/account', { sub: { ...free, access: 'member' } });
	try {
		await B.page.getByRole('link', { name: 'Open your board' }).waitFor();
		const panel = await B.page.locator('.acct .panel').innerText();
		ok(/member/.test(panel) && /Hosted access is included/.test(panel), panel);
		equal(await B.page.getByRole('button', { name: /subscribe/ }).count(), 0);
	} finally {
		await B.close();
	}
});

test('the GitHub App return shows its outcome and repos here, once', async () => {
	const { B, asked } = await account('/account?installation_id=42&notice=github-synced#github', {
		sub: free,
		repos: {
			notice: 'GitHub installations synced.',
			installed_repos: [{ repo_full_name: 'ada/app' }],
			install_url: 'https://github.com/apps/brnrd-dev/installations/new'
		}
	});
	try {
		const section = B.page.locator('#github');
		await section.waitFor();
		const text = await section.innerText();
		ok(/GitHub installations synced\./.test(text) && /ada\/app/.test(text), text);
		ok(
			asked[0].includes('notice=github-synced') && asked[0].includes('installation_id=42'),
			asked[0]
		);
		const url = new URL(B.page.url());
		equal(url.pathname + url.search, '/account');
	} finally {
		await B.close();
	}
});

const messengerDoors = [
	{ platform: 'telegram', deep_link_available: true, paired: false },
	{ platform: 'whatsapp', deep_link_available: true, paired: false }
];

test('a loom approval offers both phone chats while setup continues', async () => {
	const B = await open({ site: cloud, ready: '.brd header' });
	try {
		await B.page.route('**/v1/connect/BR-12345678', (r) =>
			r.fulfill(
				json(
					r.request().method() === 'POST'
						? { ok: true, notice: 'Approved' }
						: {
								code: 'BR-12345678',
								status: 'pending',
								kind: 'loom',
								repos: [],
								suggested_repo_full_name: 'ada/self',
								suggested_forge: 'github'
							}
				)
			)
		);
		await B.page.route('**/v1/dashboard/repos?view=account', (r) =>
			r.fulfill(json({ messenger_doors: messengerDoors }))
		);
		await B.page.route('**/v1/dashboard/paired-chats', (r) =>
			r.fulfill(json({ paired_chats: [] }))
		);
		await B.page.goto(B.base + 'connect/BR-12345678#BR-12345678');
		await B.page.getByRole('button', { name: 'approve this computer' }).click();
		await B.page.getByTestId('back-to-terminal').waitFor();
		await B.page.getByTestId('connect-telegram').waitFor();
		await B.page.getByTestId('connect-whatsapp').waitFor();
		equal(B.errors.length, 0);
	} finally {
		await B.close();
	}
});

test('Account shows the bound handle and the unconnected messenger', async () => {
	const { B } = await account('/account#messengers', {
		sub: free,
		repos: { messenger_doors: messengerDoors },
		pairedChats: [{ id: 'chat-1', platform: 'telegram', paired: true, principal_display: '@ada' }]
	});
	try {
		await B.page.getByTestId('paired-telegram').getByText('@ada', { exact: true }).waitFor();
		await B.page.getByTestId('door-whatsapp').getByText('Not connected').waitFor();
		equal(B.errors.length, 0);
	} finally {
		await B.close();
	}
});
