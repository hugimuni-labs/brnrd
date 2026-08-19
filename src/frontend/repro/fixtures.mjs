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
	pairing_command: 'cd brnrd\nbrnrd up',
	// brr/every-door-on-the-page — a mixed registry (one lit, one dark for
	// each reason) so a repro against this fixture exercises every branch
	// `MessengerDoors.svelte` renders without a second fixture object.
	messenger_doors: [
		{ platform: 'telegram', deep_link_available: true, reason: null },
		{ platform: 'whatsapp', deep_link_available: false, reason: 'not_configured' },
		{ platform: 'slack', deep_link_available: false, reason: 'not_built' },
		{ platform: 'signal', deep_link_available: false, reason: 'not_built' }
	]
};

// brr/every-door-on-the-page — `PairedChats.svelte`'s own endpoint; empty by
// default (the component renders nothing on an empty list, same "no state
// to fake" posture the rest of this file takes for zero-row endpoints
// above).
export const pairedChats = { paired_chats: [] };

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
	'/v1/dashboard/paired-chats': pairedChats,
	'/v1/dashboard/live-runs': liveRuns,
	'/v1/dashboard/activity': scheduledWakes,
	'/v1/dashboard/pr-review-queue': prReviewQueue,
	'/v1/dashboard/config-requests': configRequests,
	'/v1/dashboard/run-ledger': runLedger,
	'/v1/dashboard/surface': surface
};

// --- Scale-parameterized fixtures (repro/measure-rail.mjs) --------------
//
// The rail's unbounded sections grow with account data the fixed ROUTES
// above never varies: connected repos (project), environments per repo
// (environment), the runner catalog (fuel + spool rack, shells × cores),
// and quota windows per shell (fuel). `buildRoutes(scale)` produces a
// fresh ROUTES-shaped object at a given size, entirely additive — the
// exports above are untouched so drive.mjs/repro*.mjs keep their existing
// behaviour byte-for-byte.
//
// SHELL_NAMES/CORE_NAMES/WINDOW_LABELS are realistic-looking labels
// (matching the vocabulary `runner.py`'s catalog and `quota.py`'s windows
// actually use) so a screenshot doesn't read as obviously synthetic;
// content past what's needed to size the layout doesn't matter to a
// layout instrument.
const SHELL_NAMES = ['claude', 'codex', 'gemini', 'cursor-agent', 'aider'];
const CORE_NAMES = [
	'sonnet',
	'opus',
	'haiku',
	'fable',
	'gpt-5-codex',
	'gpt-5-mini',
	'o3',
	'default'
];
const WINDOW_LABELS = ['session', 'week', 'month', 'day', 'burst'];
const REPO_OWNERS = ['hugimuni-labs', 'acme-corp', 'north-star', 'fieldnotes', 'loomworks'];

/**
 * @param {{
 *   repos?: number,
 *   environments?: number,
 *   shells?: number,
 *   cores?: number,
 *   quotaWindows?: number,
 * }} scale
 */
export function buildRoutes(scale = {}) {
	const nRepos = scale.repos ?? 3;
	const nEnvironments = scale.environments ?? 2;
	const nShells = scale.shells ?? 2;
	const nCores = scale.cores ?? 3;
	const nQuotaWindows = scale.quotaWindows ?? 2;

	const shellNames = Array.from(
		{ length: nShells },
		(_, i) => SHELL_NAMES[i % SHELL_NAMES.length] + (i >= SHELL_NAMES.length ? `-${i}` : '')
	);

	// runners: shells × cores profiles, first one the default/selected pin.
	const profiles = [];
	for (const shell of shellNames) {
		for (let c = 0; c < nCores; c++) {
			const core = CORE_NAMES[c % CORE_NAMES.length];
			const name = `${shell}-${core}`;
			profiles.push({
				name,
				shell,
				model: core,
				class: c === 0 ? 'balanced' : c % 3 === 0 ? 'strong' : 'economy',
				cost_rank: 10 + c * 10,
				quota_source: `${shell}-local`,
				capability_score: 60 + ((c * 7) % 35),
				availability: c % 5 === 4 ? 'shell-not-found' : null,
				available: c % 5 === 4 ? false : true,
				selected: shell === shellNames[0] && c === 0
			});
		}
	}
	const scaledRunners = {
		generated_at: now,
		reported_at: now,
		stale: false,
		default: profiles[0]?.name ?? null,
		profiles,
		wake_request: null,
		sticky: null
	};

	// quota: one row per shell, nQuotaWindows windows each.
	const scaledQuota = {
		generated_at: now,
		runner_quotas: shellNames.map((shell, si) => ({
			shell,
			status: 'known',
			windows: Array.from({ length: nQuotaWindows }, (_, wi) => {
				const label = WINDOW_LABELS[wi % WINDOW_LABELS.length];
				const percent = (si * 13 + wi * 29) % 100;
				return {
					label,
					used: percent,
					limit: 100,
					percent,
					reset: `resets in ${wi + 1}${label === 'session' ? 'h' : 'd'}`
				};
			})
		}))
	};

	// repos: nRepos connected repos, each with nEnvironments environments.
	const connectedRepos = Array.from({ length: nRepos }, (_, i) => {
		const owner = REPO_OWNERS[i % REPO_OWNERS.length];
		const repoName = `project-${i + 1}`;
		const environments = Array.from({ length: nEnvironments }, (_, ei) => ({
			name: ei === 0 ? 'host · default' : `env-${ei}`,
			available: ei % 4 !== 3,
			reason: ei % 4 === 3 ? 'daemon offline for this environment' : null
		}));
		return {
			id: `repo_${i + 1}`,
			dispatch_default: i === 0,
			repo_full_name: `${owner}/${repoName}`,
			forge: 'github',
			forge_repo_id: String(i + 1),
			repo_owner: owner,
			repo_name: repoName,
			default_branch: 'main',
			created_at: now,
			updated_at: now,
			created_label: `connected ${i + 1}w ago`,
			updated_label: i === 0 ? 'now' : `${i}h ago`,
			daemon_count: 1,
			daemon_status: i % 7 === 6 ? 'offline' : 'online',
			daemon_label: i % 7 === 6 ? 'offline' : 'online',
			daemon_last_seen: 'now',
			daemon_last_seen_at: now,
			latest_daemon_name: `daemon-${i + 1}`,
			gates: [],
			setup_command: 'cd repo && brnrd up',
			telegram_paired: i % 2 === 0,
			environment_default: environments[0]?.name ?? null,
			environments,
			publish_layers: 'all',
			github_bot_collaborator: true,
			github_bot_checked_at: now,
			github_bot_checked_label: 'now',
			github_bot_status: null,
			github_bot_marker_notice: null,
			github_bot_notice: null
		};
	});
	const scaledRepos = {
		generated_at: now,
		account: { id: 'acc_1', github_login: 'hugimuni-labs' },
		connected_repos: connectedRepos,
		connected_count: connectedRepos.length,
		installations: [],
		installed_repos: [],
		github_sync_configured: true,
		oauth_ready: true,
		install_url: 'https://github.com/apps/brnrd/installations/new',
		github_app_slug: 'brnrd',
		github_bot_login: 'brnrd-bot',
		notice: null,
		setup_installation_id: '',
		pairing_command: 'cd repo\nbrnrd up'
	};

	return {
		'/v1/dashboard/quota': scaledQuota,
		'/v1/dashboard/runners': scaledRunners,
		'/v1/dashboard/repos': scaledRepos,
		'/v1/dashboard/live-runs': liveRuns,
		'/v1/dashboard/activity': scheduledWakes,
		'/v1/dashboard/pr-review-queue': prReviewQueue,
		'/v1/dashboard/config-requests': configRequests,
		'/v1/dashboard/run-ledger': runLedger,
		'/v1/dashboard/surface': surface
	};
}

// Defaults reproduce a realistic account: a handful of projects, a couple
// of environments each, two shells with a few cores, two quota windows.
export const DEFAULT_SCALE = { repos: 3, environments: 2, shells: 2, cores: 3, quotaWindows: 2 };

// The pathological account the historical 977px/1502px figures were
// measured against by hand: many projects, many environments, a wide
// catalog across shells and cores, several quota windows in flight.
export const STRESS_SCALE = { repos: 12, environments: 5, shells: 4, cores: 6, quotaWindows: 4 };
