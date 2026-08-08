// Repo-management dashboard (#327 Jinja-removal, /repos slice). Types mirror
// `GET /v1/dashboard/repos`; mutations mirror the retired Jinja forms as JSON.

export interface RepoAccount {
	id: string;
	github_login: string;
}

export interface ConnectedRepo {
	id: string;
	dispatch_default: boolean;
	repo_full_name: string;
	forge: string;
	forge_repo_id: string | null;
	repo_owner: string;
	repo_name: string;
	default_branch: string | null;
	created_at: string | null;
	updated_at: string | null;
	created_label: string;
	updated_label: string;
	daemon_count: number;
	// 'never_started' (#1243): registered but has never completed a publish
	// cycle — distinct from 'offline' (was alive, went quiet) so a crash-
	// looping daemon doesn't get called a heartbeat it never sent.
	daemon_status: 'online' | 'offline' | 'never_started' | 'missing' | string;
	daemon_label: string;
	daemon_last_seen: string;
	daemon_last_seen_at: string | null;
	latest_daemon_name: string;
	gates: GateHealth[];
	setup_command: string;
	// #885: whether a real `ChannelRoute` row pairs this repo to a Telegram
	// chat — false for both "never paired" and "route exists but its
	// principal is NULL" (authorizes nobody; see the backend's `models.py`
	// `ChannelRoute.paired_user_id` doc).
	telegram_paired: boolean;
	environment_default: string | null;
	environments: EnvironmentOption[];
	// Explicit publish-scope consent (legal pack item 2, #417 follow-on).
	// `null` = no consent recorded — this repo connected before the setting
	// existed, and nothing server-side is enforced for it.
	publish_layers: string | null;
	// #874 — brnrd-bot's own marker-collaborator state. `null` = unknown (no
	// bot token configured, never checked yet, or the last check was
	// ambiguous) — render that distinctly from a checked-and-false "not a
	// collaborator"; never show it as though it were determined.
	github_bot_collaborator: boolean | null;
	github_bot_checked_at: string | null;
	// Pre-rendered age of the check above ("never" when it hasn't run) — the
	// lit rendering's timestamp; same convention as `updated_label` etc.
	github_bot_checked_label: string;
	// Machine-readable state rendered by MarkerNotice. `null` means either a
	// successful collaborator check or that no check has run yet.
	github_bot_status:
		| 'permission-missing'
		| 'not-a-collaborator'
		| 'check-unavailable'
		| 'not-configured'
		| 'unknown'
		| null;
	// Pre-rendered one-sentence absence line (server-owned wording), present
	// only when `github_bot_collaborator === false`.
	github_bot_marker_notice: string | null;
	// Safe compatibility copy for clients predating github_bot_status.
	github_bot_notice: string | null;
}

export interface EnvironmentOption {
	name: string;
	available: boolean;
	reason?: string | null;
}

export interface GateHealth {
	gate: string;
	last_poll_ok: string | null;
	age_seconds: number | null;
	last_error: string | null;
	status: 'ok' | 'degraded' | 'never';
}

export interface GitHubInstallation {
	id: string;
	installation_id: string;
	target_login: string;
	target_type: string;
	created_at: string | null;
	last_synced_at: string | null;
	last_synced_label: string;
}

export interface InstalledRepo {
	id: string;
	github_installation_id: string;
	repo_full_name: string;
	forge_repo_id: string | null;
	is_private: boolean;
	default_branch: string | null;
	github_pushed_at: string | null;
	github_updated_at: string | null;
	last_seen_at: string | null;
	pushed_label: string;
	updated_label: string;
	last_seen_label: string;
	connected: boolean;
	// The retired "enable" button's replacement (2026-08-06): running this
	// from the checkout is what connects it now — same idiom as
	// `ConnectedRepo.setup_command`, best-guess local dir name from the
	// repo's own short name.
	setup_command: string;
}

// The capability registry (design-capability-panel.md; backend build step 1,
// `src/brnrd/capabilities.py`). Mirrored here typed, unconsumed — no
// component reads this field yet; the Panel component that renders it is a
// later strand. `scope`/`heat`/`state`/`act.kind` are written as open string
// unions rather than the backend's closed literal sets on purpose: a wire
// contract that adds a fifth state or a new scope should not need a
// frontend type edit merely to stop erroring on an unrecognised value the
// renderer already has to have a fallback row for (design doc §Implications:
// "a fallback row for an id it has no copy for, visible, not swallowed").
export interface Capability {
	id: string;
	scope: 'account' | 'machine' | 'repo' | string;
	subject: string | null;
	state: 'lit' | 'dark' | 'waiting' | 'unobservable' | string;
	evidence: { source: string; as_of: string | null };
	requires: string[];
	heat: 'required' | 'recommended' | 'optional' | string;
	act: { kind: 'post' | 'deep-link' | 'command' | 'none' | string; target: string | null };
	frontier: boolean;
}

export interface ReposResponse {
	generated_at: string;
	account: RepoAccount;
	connected_repos: ConnectedRepo[];
	connected_count: number;
	installations: GitHubInstallation[];
	installed_repos: InstalledRepo[];
	github_sync_configured: boolean;
	oauth_ready: boolean;
	install_url: string;
	github_app_slug: string;
	github_bot_login: string;
	notice: string | null;
	setup_installation_id: string;
	// The three pairing lines with `<repo>` where a checkout name goes —
	// the same spelling each connected repo carries as `setup_command`,
	// served account-level because the surface that prints it (the
	// dashboard's cold-start block) renders exactly when `connected_repos`
	// is empty. One source, backend-owned: see `_session.pairing_command`.
	pairing_command: string;
	// Additive, optional: present once the backend ships it, absent on any
	// client/response that predates it. No component reads this yet — see
	// the `Capability` doc comment above.
	capabilities?: Capability[];
}

export interface ConnectRepoPayload {
	repo_full_name: string;
	forge_repo_id?: string | null;
	default_branch?: string | null;
	// Explicit publish-scope consent captured at connect (legal pack item 2).
	// Omitted or empty both mean "off" server-side — there is no separate
	// not-provided branch: absence of a choice is nothing publishing.
	publish_layers?: string;
}

export interface RepoActionResponse {
	ok: boolean;
	notice: string;
	pairing_code?: string;
	instructions?: string;
	action_url?: string | null;
}

export class ReposAuthError extends Error {}

// #885: a paired repo renders a quiet status line; the re-pair action lives
// *inside* it, behind the status disclosure, because re-pairing is an
// exception (the chat moved, the route broke) and not a routine act. Keep
// "Telegram" in the action itself: revealed on its own, the label has no
// neighbouring control to borrow context from.
export function telegramPairLabel(paired: boolean, busy: boolean): string {
	if (paired) return busy ? 're-pairing Telegram' : 're-pair Telegram';
	return busy ? 'pairing' : 'pair Telegram';
}

async function parseJson(res: Response): Promise<Record<string, unknown>> {
	try {
		const body = await res.json();
		return body && typeof body === 'object' ? (body as Record<string, unknown>) : {};
	} catch {
		return {};
	}
}

async function postRepoAction(
	url: string,
	body: Record<string, unknown> = {},
	fetchImpl: typeof fetch = fetch
): Promise<RepoActionResponse> {
	const res = await fetchImpl(url, {
		method: 'POST',
		credentials: 'include',
		headers: { 'content-type': 'application/json' },
		body: JSON.stringify(body)
	});
	const payload = await parseJson(res);
	if (res.status === 401) {
		throw new ReposAuthError('not signed in');
	}
	if (!res.ok && typeof payload.ok !== 'boolean') {
		throw new Error(`repo action failed: ${res.status}`);
	}
	return payload as unknown as RepoActionResponse;
}

export interface FetchReposParams {
	// The GitHub Setup URL's own query params (`routers/github_app.py`'s
	// `github_app_setup`), forwarded so the backend can echo the mapped
	// notice text back (`_notice_text` — the same table `/v1/repos/*`
	// action responses already read) instead of the frontend keeping a
	// second copy of that mapping.
	notice?: string | null;
	installationId?: string | null;
}

export async function fetchRepos(
	fetchImpl: typeof fetch = fetch,
	params?: FetchReposParams
): Promise<ReposResponse> {
	const qs = new URLSearchParams();
	if (params?.notice) qs.set('notice', params.notice);
	if (params?.installationId) qs.set('installation_id', params.installationId);
	const suffix = qs.toString();
	const res = await fetchImpl(`/v1/dashboard/repos${suffix ? `?${suffix}` : ''}`, {
		credentials: 'include'
	});
	if (res.status === 401) {
		throw new ReposAuthError('not signed in');
	}
	if (!res.ok) {
		throw new Error(`repos fetch failed: ${res.status}`);
	}
	return (await res.json()) as ReposResponse;
}

export function connectRepo(
	payload: ConnectRepoPayload,
	fetchImpl: typeof fetch = fetch
): Promise<RepoActionResponse> {
	return postRepoAction(
		'/v1/repos/connect',
		{
			repo_full_name: payload.repo_full_name,
			forge_repo_id: payload.forge_repo_id ?? '',
			default_branch: payload.default_branch ?? '',
			publish_layers: payload.publish_layers ?? ''
		},
		fetchImpl
	);
}

export function setPublishLayers(
	repoId: string,
	publishLayers: string,
	fetchImpl: typeof fetch = fetch
): Promise<RepoActionResponse> {
	return postRepoAction(
		`/v1/repos/${encodeURIComponent(repoId)}/publish-layers`,
		{ publish_layers: publishLayers },
		fetchImpl
	);
}

export function pairRepoTelegram(
	repoId: string,
	fetchImpl: typeof fetch = fetch
): Promise<RepoActionResponse> {
	return postRepoAction(`/v1/repos/${encodeURIComponent(repoId)}/telegram-pair`, {}, fetchImpl);
}

export function disconnectRepo(
	repoId: string,
	fetchImpl: typeof fetch = fetch
): Promise<RepoActionResponse> {
	return postRepoAction(`/v1/repos/${encodeURIComponent(repoId)}/disconnect`, {}, fetchImpl);
}
