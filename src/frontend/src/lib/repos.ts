// Repo-management dashboard (#327 Jinja-removal, /repos slice). Types mirror
// `GET /v1/dashboard/repos`; mutations mirror the retired Jinja forms as JSON.

export interface RepoAccount {
	id: string;
	github_login: string;
}

export interface ConnectedRepo {
	id: string;
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
	daemon_status: 'online' | 'offline' | 'missing' | string;
	daemon_label: string;
	daemon_last_seen: string;
	daemon_last_seen_at: string | null;
	latest_daemon_name: string;
	gates: GateHealth[];
	setup_command: string;
	telegram_pair_enabled: boolean;
	bot_invite_enabled: boolean;
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
	github_bot_user_login: string;
	notice: string | null;
	setup_installation_id: string;
}

export interface ConnectRepoPayload {
	repo_full_name: string;
	forge_repo_id?: string | null;
	default_branch?: string | null;
}

export interface RepoActionResponse {
	ok: boolean;
	notice: string;
	pairing_code?: string;
	instructions?: string;
	action_url?: string | null;
}

export class ReposAuthError extends Error {}

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

export async function fetchRepos(fetchImpl: typeof fetch = fetch): Promise<ReposResponse> {
	const res = await fetchImpl('/v1/dashboard/repos', { credentials: 'include' });
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
			default_branch: payload.default_branch ?? ''
		},
		fetchImpl
	);
}

export function inviteRepoBot(
	repoId: string,
	fetchImpl: typeof fetch = fetch
): Promise<RepoActionResponse> {
	return postRepoAction(`/v1/repos/${encodeURIComponent(repoId)}/invite-bot`, {}, fetchImpl);
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
