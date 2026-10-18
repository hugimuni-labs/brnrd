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
export const configRequests = { generated_at: now, requests: [] };
export const runLedger = {
	generated_at: now,
	rows: [],
	stale: false,
	reported_at: now,
	span_seconds_served: 0
};

// A closed run that crossed the runner boundary during dispatch. The
// fallback rewrites `core_expected` to the substitute, so `core_mismatch` is
// deliberately false; `runner_substituted_from` is the load-bearing signal
// that keeps the receipt from claiming the run matched its original author.
// Kept separate from the empty default so ordinary repros remain unchanged.
export const runLedgerFallback = {
	generated_at: now,
	rows: [
		{
			run_id: 'run-fallback-receipt',
			event_id: 'evt-fallback-receipt',
			started_at: '2026-08-12T22:10:00Z',
			ended_at: '2026-08-12T22:30:10Z',
			wall_clock_seconds: 1210,
			runner_shell: 'codex',
			runner_core: 'gpt-5.6-luna',
			core_expected: 'gpt-5.6-luna',
			core_mismatch: false,
			substitution_reason: 'dispatch fallback: claude-opus -> codex-gpt-5.6-luna after auth_error',
			runner_substituted_from: 'claude-opus',
			repo_label: 'hugimuni-labs/brnrd',
			source_system: 'telegram',
			name: 'the fallback that still finished',
			external_refs: [
				{ kind: 'summary', text: 'fallback completed with the substitute runner' },
				{ kind: 'commit', sha: 'a8b92c4d42' },
				{ kind: 'branch', name: 'brr/the-fallback-that-still-finished' }
			],
			parent_run_id: null,
			is_subspawn: false,
			tokens_input: 18400,
			tokens_output: 2100,
			tokens_cache_read: 7200,
			tokens_cache_creation: 400,
			context_window_used: 18.2,
			weekly_pct_delta: 1.4,
			five_hour_pct_delta: 3.1,
			usd_subscription_attributed: 0.42,
			usd_credits_equivalent: null,
			estimate_vs_actual: null
		}
	],
	stale: false,
	reported_at: now,
	span_seconds_served: 7 * 24 * 60 * 60
};

// Six topics (matches the evidence photo's six heddle glyphs), and ~49 warp
// items split decision/preparation so the cloth list is long enough to
// scroll several viewport-heights on a 390x844 layout — the repro's whole
// point is a *long list under a docking stack*, not the item content.
const TOPIC_IDS = ['mint', 'mcp', 'seed', 'schedule', 'runner', 'tos'];

// Two of six carry an authored heddle signature rune (move 5b's
// frontmatter, design-the-loom §20) — the rest keep the derived glyph, so
// the repro exercises both branches of `topicFace`/`topicFaces` at once
// (brr/the-rune-on-the-rail). Chosen off any Elder Futhark stave so a
// broken override (still deriving) is visibly wrong, not coincidentally
// right.
const TOPIC_RUNES = { mint: '⚒', tos: '☉' };

function topicFile(id) {
	const rune = TOPIC_RUNES[id];
	const frontmatter = rune ? `---\nrune: ${rune}\n---\n` : '';
	return { path: `surface/topics/${id}.md`, markdown: `${frontmatter}# ${id}\n\nids: ${id}\n` };
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

// The bench (design-the-loom.md §6/§18): folds at a place, listed by
// `GET /v1/dashboard/bench`. `path` matches `repo/place/commit`, the same
// address `benchFileHref` builds and the detail route (`bench.commit`
// below) is fetched at — see `repro/drive-bench-view.mjs`.
export const bench = {
	files: [
		{
			repo: 'hugimuni-labs__brnrd',
			place: 'design-the-loom.md',
			commit: 'a1b2c3d',
			question: 'what does the bench store look like',
			made_at: now,
			marks: [],
			path: 'hugimuni-labs__brnrd/design-the-loom.md/a1b2c3d'
		}
	]
};

export const benchFile = {
	...bench.files[0],
	body: '# The bench\n\nOne markdown file per fold, keyed to a commit.\n'
};

// The console's list of asks (`GET /v1/dashboard/warp/asks.json`, the home is
// the list — design-the-ask.md §Build cut 3). LRU order, stale rows last,
// done apart; `touched_at` is relative to `now` so shots never rot.
const ago = (hours) => new Date(Date.now() - hours * 3600 * 1000).toISOString();
export const asks = {
	stale_after_days: 60,
	asks: [
		{
			id: 'w-201',
			title: 'The home is the list — one row per ask, done rows below a rule',
			type: 'action',
			return: 'in git',
			stage: 'making',
			touched_at: ago(0.4),
			// One say with a resolved excerpt + a route (exercises the 17:51Z
			// steer's `<relative time> · <excerpt> · ↗`); one with neither
			// (falls back to the bare event id, `sayText`'s floor, asks.ts).
			says: [
				{
					event: 'evt-1790033328745535000-kmse',
					at: ago(0.5),
					excerpt: 'slick ui to inspect the done things',
					url: 'https://t.me/c/loom/42'
				},
				{ event: 'evt-1790033000000000000-aa01', at: ago(3), excerpt: null, url: null }
			],
			attempts: ['run-fallback-receipt'],
			receipt: 'https://github.com/hugimuni-labs/brnrd/pull/2080',
			topics: ['the-loom'],
			live: true,
			stale: false,
			done: false,
			after: null
		},
		{
			id: 'w-198',
			title: 'A live LRU of my asks, tracked across the runs',
			type: 'decision',
			return: 'in chat',
			stage: 'understood',
			touched_at: ago(9),
			says: [{ event: 'evt-1789990000000000000-dmnu', at: ago(9), excerpt: null }],
			attempts: [],
			receipt: null,
			topics: ['the-loom'],
			live: false,
			stale: false,
			done: false,
			after: null
		},
		{
			id: 'w-190',
			title: 'Book the ferry for the 14th, authenticated browser',
			type: 'action',
			return: 'in the world',
			stage: 'shaped',
			touched_at: ago(60),
			says: [],
			attempts: ['run-260919-1802-6zeq', 'run-260920-0910-abcd'],
			receipt: null,
			topics: ['errands'],
			live: false,
			stale: false,
			done: false,
			after: 'w-188'
		},
		// His 18:27Z follow-up: four stale, unaddressed rows so the nested
		// "top three + toggle for the rest" sub-block actually has something
		// to hide (mirrors why `done` grew to five, above).
		{
			id: 'w-80',
			title: 'Ping the printer vendor about the January renewal',
			type: 'action',
			return: 'in the world',
			stage: 'heard',
			touched_at: ago(24 * 80),
			says: [],
			attempts: [],
			receipt: null,
			topics: ['errands'],
			live: false,
			stale: true,
			done: false,
			after: null
		},
		{
			id: 'w-72',
			title: 'A second opinion on the export questionnaire wording',
			type: 'decision',
			return: 'in chat',
			stage: 'understood',
			touched_at: ago(24 * 95),
			says: [],
			attempts: [],
			receipt: null,
			topics: ['ops'],
			live: false,
			stale: true,
			done: false,
			after: null
		},
		{
			id: 'w-68',
			title: 'Sketch the quarterly numbers before the board call',
			type: 'preparation',
			return: 'in git',
			stage: 'shaped',
			touched_at: ago(24 * 110),
			says: [],
			attempts: [],
			receipt: null,
			topics: ['ops'],
			live: false,
			stale: true,
			done: false,
			after: null
		},
		{
			id: 'w-61',
			title: 'Look into the payment processor rate change notice',
			type: 'decision',
			return: 'in chat',
			stage: 'heard',
			touched_at: ago(24 * 130),
			says: [],
			attempts: [],
			receipt: null,
			topics: ['ops'],
			live: false,
			stale: true,
			done: false,
			after: null
		},
		{
			id: 'w-96',
			// His callsign steer (the-panel-third-pass: "not w-45 but
			// w-[45|anltcs]") — `sign` is the one row in this fixture that
			// carries it, so the id cell and the accept/reroute chips both
			// have something real to render for the shots. The-row-you-can-
			// judge §2 fixture: two receipts, one carrying a title (the
			// derived delivery line reads it) and one that doesn't yet (the
			// sibling PR's field landing gradually) — so the receipts chip
			// row and the "still resolving" case both have something real
			// to render.
			sign: 'anltcs',
			title: 'Tell me when the licence renewal window opens',
			type: 'preparation',
			return: 'in time',
			stage: 'delivered',
			touched_at: ago(24 * 75),
			says: [{ event: 'evt-1780000000000000000-old1', at: ago(24 * 75), excerpt: null }],
			attempts: [],
			receipt: null,
			receipts: [
				{
					ref: '#2082',
					title: 'ledger step 6a — a hold never stamps ambiguous',
					url: 'https://github.com/hugimuni-labs/brnrd/pull/2082'
				},
				{ ref: '#2081', title: null, url: null }
			],
			topics: ['errands'],
			live: false,
			stale: true,
			done: false,
			after: null
		},
		{
			// The-row-you-can-judge §4 fixture (his 19:46Z: "an in-hand seat
			// row shows what the seat is doing"): a second in-hand ask with
			// no live attempt of its own (`attempts: []`, no drone) — the
			// bare "seat" case, now joined against the seat's own live run
			// (added below, alongside the strand fixture on w-201) instead
			// of the plain static word.
			id: 'w-205',
			title: 'Reconcile the November invoice against the PO',
			type: 'action',
			return: null,
			stage: 'making',
			touched_at: ago(0.2),
			says: [],
			attempts: [],
			receipt: null,
			topics: ['ops'],
			live: false,
			stale: false,
			done: false,
			after: null
		}
	],
	// Five rows (the-panel-third-pass: done moves to the top, its newest
	// three visible by default, a `▸ 2 done` toggle for the rest) —
	// newest-touch first, matching `asks.py::build_asks`'s own sort.
	done: [
		{
			id: 'w-150',
			title: 'A design page for the ask',
			type: 'action',
			return: 'in git',
			stage: 'accepted',
			touched_at: ago(30),
			says: [{ event: 'evt-1789900000000000000-dmnu', at: ago(31), excerpt: null }],
			attempts: ['run-260921-1447-srsg'],
			receipt: 'kb/design-the-ask.md',
			topics: ['the-loom'],
			live: false,
			stale: false,
			done: true,
			after: null
		},
		{
			id: 'w-140',
			title: 'Weekly weave-up of merged PRs',
			type: 'preparation',
			return: 'in chat',
			stage: 'accepted',
			touched_at: ago(50),
			says: [],
			attempts: [],
			receipt: null,
			topics: ['ops'],
			live: false,
			stale: false,
			done: true,
			after: null
		},
		{
			id: 'w-131',
			title: 'The warp panel, per ask — four buckets in his order',
			type: 'action',
			return: 'in git',
			stage: 'accepted',
			touched_at: ago(70),
			says: [],
			attempts: ['run-260922-1145-nw37'],
			receipt: 'https://github.com/hugimuni-labs/brnrd/pull/2088',
			topics: ['the-loom'],
			live: false,
			stale: false,
			done: true,
			after: null
		},
		{
			id: 'w-120',
			title: 'The ask panel, second pass — four distinct blocks, lit',
			type: 'action',
			return: 'in git',
			stage: 'accepted',
			touched_at: ago(90),
			says: [],
			attempts: ['run-260922-1702-w4qm'],
			receipt: 'https://github.com/hugimuni-labs/brnrd/pull/2089',
			topics: ['the-loom'],
			live: false,
			stale: false,
			done: true,
			after: null
		},
		{
			id: 'w-110',
			title: 'Rung 2 — the asks lane, `--item` binds a reply to a warp item',
			type: 'action',
			return: 'in git',
			stage: 'accepted',
			touched_at: ago(120),
			says: [],
			attempts: [],
			receipt: 'kb/design-the-water-line.md',
			topics: ['the-loom'],
			live: false,
			stale: false,
			done: true,
			after: null
		}
	],
	goals: [{ id: 'g-3', title: 'Talk to one entity; it sorts the workstreams', touched_at: ago(12) }]
};

export const ROUTES = {
	'/v1/dashboard/quota': quota,
	'/v1/dashboard/runners': runners,
	'/v1/dashboard/config': {
		config: [
			{ key: 'runner.default', value: 'codex', source: 'daemon.config' },
			{ key: 'runner_policy', value: 'cost-aware', source: 'daemon.config' },
			{ key: 'seat.park_on_turn_end', value: true, source: 'daemon.config' }
		]
	},
	'/v1/dashboard/repos': repos,
	'/v1/dashboard/paired-chats': pairedChats,
	'/v1/dashboard/live-runs': liveRuns,
	'/v1/dashboard/activity': scheduledWakes,
	'/v1/dashboard/config-requests': configRequests,
	'/v1/dashboard/run-ledger': runLedger,
	'/v1/dashboard/surface': surface,
	'/v1/dashboard/bench': bench,
	'/v1/dashboard/warp/asks.json': asks
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

// --- The loom snapshot (repro/drive-home-board.mjs) -----------------------
//
// `GET /v1/dashboard/loom`, v1 (threads/inbox/loom-snapshot.md in the self
// repo): what `/` renders as the home board. Every stamp is relative to the
// moment `loomSnapshots()` is called, so the ages and resets on a shot read
// the same on any day. Three scenarios: a busy board (every turn, every
// strand state, receipts and a notice in the log), a quiet one, and one
// where fuel is the news.
export function loomSnapshots(now = Date.now()) {
	const at = (minutes) => new Date(now - minutes * 60_000).toISOString();
	const resets = (minutes) => new Date(now + minutes * 60_000).toISOString();
	const PR = 'https://github.com/hugimuni-labs/brnrd/pull';
	const bodies = {
		default: 'claude/opus',
		choices: ['claude/opus', 'claude/sonnet', 'codex/gpt-6.1-sol', 'grok/grok-4.7']
	};
	const healthyFuel = [
		{
			shell: 'claude',
			state: 'ok',
			gauges: [
				{ window: 'session', left_pct: 82, resets_at: resets(134) },
				{ window: 'week', left_pct: 97, resets_at: resets(4 * 1440) }
			],
			cores: ['opus', 'sonnet']
		},
		{
			shell: 'codex',
			state: 'ok',
			gauges: [
				{ window: 'session', left_pct: 64, resets_at: resets(71) },
				{ window: 'week', left_pct: 88, resets_at: resets(2 * 1440) }
			],
			cores: ['gpt-6.1-sol', 'gpt-6.1-mini']
		}
	];

	const busy = {
		v: 1,
		at: at(0.5),
		stale_s: 20,
		presence: {
			strand: 's-ae66-6vf7ze',
			thread: 'inbox',
			state: 'working',
			now: 'Fold the night-shift handoff into the brnrd.dev row and answer Arseni on landing copy',
			owed: 2,
			body: 'claude/opus'
		},
		fuel: healthyFuel,
		bodies,
		threads: [
			{
				id: 'landing-loom',
				title: 'The logged-out brnrd.dev landing, brought up to the loom',
				status: 'open',
				turn: 'mine',
				opened_by: 'thread:inbox',
				plan: { done: 2, total: 6, next: 'Hero: the three nouns as the first screen' },
				strands: [{ id: 's-ae66-k2p9qa', body: 'claude/sonnet', state: 'working', since: at(18) }],
				done: [],
				owed: 0,
				last_at: at(3)
			},
			{
				id: 'answer-asks-build',
				title: 'Asks answered from the board: one tap, a receipt back',
				status: 'open',
				turn: 'yours',
				opened_by: 'person:arseni',
				plan: { done: 6, total: 7, next: 'Arseni: merge #2347 or say what is wrong with it' },
				strands: [{ id: 's-ae66-riiuvn', body: 'claude/opus', state: 'holding', since: at(42) }],
				done: [{ kind: 'pr', ref: `${PR}/2347`, line: 'asks, graph, receipts on the board' }],
				owed: 1,
				last_at: at(9)
			},
			{
				id: 'loom-on-the-web',
				title: 'GET /v1/dashboard/loom and the letters POST, on the hosted side',
				status: 'open',
				turn: 'mine',
				opened_by: 'thread:inbox',
				plan: { done: 4, total: 5, next: 'Deploy and read the snapshot back from prod' },
				strands: [
					{ id: 's-ae66-nsyh0r', body: 'codex/gpt-6.1-sol', state: 'working', since: at(55) },
					{ id: 's-ae66-8vajw7', body: 'claude/sonnet', state: 'idle', since: at(120) }
				],
				done: [{ kind: 'pr', ref: `${PR}/2300`, line: 'loom snapshot endpoint' }],
				owed: 0,
				last_at: at(14)
			},
			{
				id: 'pricing-page',
				title: 'Pricing that says what a shell-hour costs',
				status: 'open',
				turn: 'yours',
				opened_by: 'thread:inbox',
				plan: { done: 1, total: 4, next: 'Pick one of the three price ladders' },
				strands: [{ id: 's-ae66-p7ricx', body: 'claude/opus', state: 'parked', since: at(300) }],
				done: [],
				owed: 1,
				last_at: at(95)
			},
			{
				id: 'grok-shell',
				title: 'Grok as a third shell, with its own fuel gauge',
				status: 'open',
				turn: 'blocked',
				blocked_on: 'an xAI API key',
				opened_by: 'loom',
				plan: { done: 3, total: 5, next: 'Read the quota headers once a key exists' },
				strands: [
					{ id: 's-ae66-gr0k4a', body: 'codex/gpt-6.1-sol', state: 'failed', since: at(400) }
				],
				done: [],
				owed: 0,
				last_at: at(6 * 60)
			},
			{
				id: 'scar-merge-union',
				title: 'A send-self edit to another thread’s file can merge as a union',
				status: 'open',
				turn: 'none',
				opened_by: 'thread:inbox',
				plan: { done: 1, total: 3, next: 'Write the failing test against send-self' },
				strands: [],
				done: [],
				owed: 0,
				last_at: at(26 * 60)
			},
			{
				id: 'park-wakes',
				title: 'Park, refill, wake: a shell that runs dry sleeps and comes back',
				status: 'settled',
				turn: 'none',
				opened_by: 'person:arseni',
				plan: { done: 5, total: 5 },
				strands: [
					{ id: 's-ae66-pk3w1z', body: 'claude/opus', state: 'settled', since: at(30 * 60) }
				],
				done: [{ kind: 'pr', ref: `${PR}/2348`, line: 'park-wakes merged' }],
				owed: 0,
				last_at: at(31 * 60)
			}
		],
		log: [
			{
				id: 'letter:mine:155783668:4520',
				at: at(1),
				from: 's-ae66-6vf7ze',
				to: 'person:arseni',
				thread: 'inbox',
				kind: 'letter',
				head: 'Landing copy: I kept your line for the hero and moved the install step under it. Two variants on the thread if you want to compare.'
			},
			{
				id: 's-ae66-k2p9qa/a81',
				at: at(3),
				from: 's-ae66-k2p9qa',
				thread: 'landing-loom',
				kind: 'letter',
				head: 'Hero draft pushed to the room; shots next.'
			},
			{
				id: 's-ae66-riiuvn/q2',
				at: at(9),
				from: 's-ae66-riiuvn',
				thread: 'answer-asks-build',
				kind: 'letter',
				head: 'Ready for you: #2347 is green, ui-shots posted. Merge, or tell me what is off.'
			},
			{
				id: 's-ae66-riiuvn/r1',
				at: at(11),
				from: 's-ae66-riiuvn',
				thread: 'answer-asks-build',
				kind: 'receipt',
				head: 'PR #2347 opened',
				ref: `${PR}/2347`
			},
			{
				id: 'letter:mine:155783668:4511',
				at: at(13),
				from: 'person:arseni',
				to: 'thread:inbox',
				thread: 'inbox',
				kind: 'letter',
				head: 'Body choice belongs on the fuel strip, not in a settings page. Tap a shell, pick a core, done.'
			},
			{
				id: 's-ae66-nsyh0r/r2',
				at: at(14),
				from: 's-ae66-nsyh0r',
				thread: 'loom-on-the-web',
				kind: 'receipt',
				head: 'PR #2300 opened: loom snapshot endpoint',
				ref: `${PR}/2300`
			},
			{
				id: 's-ae66-nsyh0r/l7',
				at: at(22),
				from: 's-ae66-nsyh0r',
				thread: 'loom-on-the-web',
				kind: 'letter',
				head: 'No snapshot is a 404; signed out is a 401. The 200 carries stale_s.'
			},
			{
				id: 'loom/n-31',
				at: at(40),
				from: 'loom',
				thread: 'grok-shell',
				kind: 'notice',
				head: 'strand s-ae66-gr0k4a failed twice on grok-shell; the loom will not wake it again until the key exists.'
			},
			{
				id: 's-ae66-p7ricx/q1',
				at: at(95),
				from: 's-ae66-p7ricx',
				thread: 'pricing-page',
				kind: 'letter',
				head: 'Three ladders on the thread. I would ship the middle one; your call.'
			},
			{
				id: 'letter:mine:155783668:4498',
				at: at(130),
				from: 'person:arseni',
				to: 'thread:inbox',
				thread: 'inbox',
				kind: 'letter',
				head: 'Open a thread for pricing. Keep it short.'
			},
			{
				id: 's-ae66-6vf7ze/o1',
				at: at(131),
				from: 's-ae66-6vf7ze',
				thread: 'inbox',
				kind: 'letter',
				head: 'Opened thread:pricing-page and put a strand on it.'
			},
			{
				id: 's-ae66-gr0k4a/l2',
				at: at(6 * 60),
				from: 's-ae66-gr0k4a',
				thread: 'grok-shell',
				kind: 'letter',
				head: 'Blocked: no xAI key on this machine.'
			},
			{
				id: 's-ae66-pk3w1z/r3',
				at: at(31 * 60),
				from: 's-ae66-pk3w1z',
				thread: 'park-wakes',
				kind: 'receipt',
				head: 'park-wakes merged',
				ref: `${PR}/2348`
			},
			{
				id: 's-ae66-6vf7ze/s1',
				at: at(31 * 60 + 5),
				from: 's-ae66-6vf7ze',
				thread: 'inbox',
				kind: 'letter',
				head: 'Settled park-wakes: merged, loaded, a dry shell slept and woke on its own.'
			},
			{
				id: 'letter:mine:155783668:4420',
				at: at(33 * 60),
				from: 'person:arseni',
				to: 'thread:inbox',
				thread: 'inbox',
				kind: 'letter',
				head: 'Good night. Keep the scar thread for tomorrow.'
			}
		]
	};

	const quiet = {
		v: 1,
		at: at(1),
		stale_s: 15,
		presence: {
			strand: 's-ae66-6vf7ze',
			thread: 'inbox',
			state: 'idle',
			now: 'Nothing owed. Write a letter to start something.',
			owed: 0,
			body: 'claude/opus'
		},
		fuel: healthyFuel,
		bodies,
		threads: [
			{
				id: 'scar-merge-union',
				title: 'A send-self edit to another thread’s file can merge as a union',
				status: 'open',
				turn: 'none',
				opened_by: 'thread:inbox',
				plan: { done: 1, total: 3, next: 'Write the failing test against send-self' },
				strands: [{ id: 's-ae66-sc4r9m', body: 'claude/sonnet', state: 'idle', since: at(200) }],
				done: [],
				owed: 0,
				last_at: at(200)
			},
			{
				id: 'park-wakes',
				title: 'Park, refill, wake: a shell that runs dry sleeps and comes back',
				status: 'open',
				turn: 'none',
				opened_by: 'person:arseni',
				plan: { done: 5, total: 5 },
				strands: [],
				done: [{ kind: 'pr', ref: `${PR}/2348`, line: 'park-wakes merged' }],
				owed: 0,
				last_at: at(9 * 60)
			}
		],
		log: busy.log
			.filter((e) => ['inbox', 'park-wakes', 'scar-merge-union'].includes(e.thread))
			.slice(-3)
	};

	const lowFuel = {
		...busy,
		at: at(2),
		stale_s: 340,
		presence: {
			...busy.presence,
			state: 'holding',
			now: 'Waiting for codex to refill before waking loom-on-the-web'
		},
		fuel: [
			{
				shell: 'claude',
				state: 'ok',
				gauges: [
					{ window: 'session', left_pct: 9, resets_at: resets(47) },
					{ window: 'week', left_pct: 61, resets_at: resets(3 * 1440) }
				],
				cores: ['opus', 'sonnet']
			},
			{
				shell: 'codex',
				state: 'dry',
				gauges: [
					{ window: 'session', left_pct: 0, resets_at: resets(118) },
					{ window: 'week', left_pct: 40, resets_at: resets(2 * 1440) }
				],
				cores: ['gpt-6.1-sol', 'gpt-6.1-mini']
			}
		]
	};

	return { busy, quiet, lowFuel };
}
