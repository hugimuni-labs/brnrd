// Fixture JSON for the dashboard's dozen-odd `/v1/dashboard/*` endpoints —
// enough shape to render THE STACK with a long warp list under it, without a
// real backend/account. Built by reading each `fetch*` function's response
// interface in src/lib/*.ts (see strand-report for the file list).

const now = new Date('2026-08-12T22:50:00Z').toISOString();

export const quota = {
	generated_at: now,
	runner_quotas: [
		{
			shell: 'claude',
			status: 'known',
			windows: [
				{ label: 'session', used: 3, limit: 100, percent: 3, reset: 'resets 5:50am' },
				{ label: 'week', used: 71, limit: 100, percent: 71, reset: 'resets Aug 14' }
			]
		}
	]
};

export const runners = {
	generated_at: now,
	reported_at: now,
	stale: false,
	default: 'claude-sonnet',
	profiles: [
		{ name: 'claude-sonnet', shell: 'claude', model: 'sonnet', class: 'balanced', selected: true }
	],
	wake_request: null,
	sticky: null
};

export const repos = {
	generated_at: now,
	account: { id: 'acc_1', github_login: 'hugimuni-labs' },
	connected_repos: [
		{
			id: 'repo_1',
			dispatch_default: true,
			repo_full_name: 'hugimuni-labs/brnrd',
			forge: 'github',
			forge_repo_id: '1',
			repo_owner: 'hugimuni-labs',
			repo_name: 'brnrd',
			default_branch: 'main',
			created_at: now,
			updated_at: now,
			created_label: 'connected 3w ago',
			updated_label: 'now',
			daemon_count: 1,
			daemon_status: 'online',
			daemon_label: 'online',
			daemon_last_seen: 'now',
			daemon_last_seen_at: now,
			latest_daemon_name: 'the-key-that-never-expired',
			gates: [],
			setup_command: 'cd brnrd && brnrd up',
			telegram_paired: true,
			environment_default: 'host · default',
			environments: [{ name: 'host · default', available: true }],
			publish_layers: 'all',
			github_bot_collaborator: true,
			github_bot_checked_at: now,
			github_bot_checked_label: 'now',
			github_bot_status: null,
			github_bot_marker_notice: null,
			github_bot_notice: null
		}
	],
	connected_count: 1,
	installations: [],
	installed_repos: [],
	github_sync_configured: true,
	oauth_ready: true,
	install_url: 'https://github.com/apps/brnrd/installations/new',
	github_app_slug: 'brnrd',
	github_bot_login: 'brnrd-bot',
	notice: null,
	setup_installation_id: '',
	pairing_command: 'cd brnrd\nbrnrd up'
};

export const liveRuns = {
	generated_at: now,
	runs: [],
	stale: false,
	reported_at: now,
	spawn_max_concurrent: 5,
	daemon_mood: null
};

export const scheduledWakes = { generated_at: now, rows: [], total: 0 };
export const prReviewQueue = { generated_at: now, prs: [], stale: false, reported_at: now };
export const configRequests = { generated_at: now, requests: [] };
export const runLedger = {
	generated_at: now,
	rows: [],
	stale: false,
	reported_at: now,
	span_seconds_served: 0
};

// Six topics (matches the evidence photo's six heddle glyphs), and ~49 warp
// items split decision/preparation so the cloth list is long enough to
// scroll several viewport-heights on a 390x844 layout — the repro's whole
// point is a *long list under a docking stack*, not the item content.
const TOPIC_IDS = ['mint', 'mcp', 'seed', 'schedule', 'runner', 'tos'];

function topicFile(id) {
	return { path: `surface/topics/${id}.md`, markdown: `# ${id}\n\nids: ${id}\n` };
}

function warpFile(n) {
	const type = n % 5 === 0 ? 'preparation' : 'decision';
	const topic = TOPIC_IDS[n % TOPIC_IDS.length];
	return {
		path: `surface/warp/w-${n}.md`,
		markdown: `# Item number ${n}\n\ntype: ${type}\ntopics: ${topic}\n`
	};
}

export const surfaceFiles = [
	...TOPIC_IDS.map(topicFile),
	...Array.from({ length: 49 }, (_, i) => warpFile(i + 1))
];

export const surface = {
	generated_at: now,
	files: surfaceFiles,
	reported_at: now
};

export const ROUTES = {
	'/v1/dashboard/quota': quota,
	'/v1/dashboard/runners': runners,
	'/v1/dashboard/repos': repos,
	'/v1/dashboard/live-runs': liveRuns,
	'/v1/dashboard/activity': scheduledWakes,
	'/v1/dashboard/pr-review-queue': prReviewQueue,
	'/v1/dashboard/config-requests': configRequests,
	'/v1/dashboard/run-ledger': runLedger,
	'/v1/dashboard/surface': surface
};
