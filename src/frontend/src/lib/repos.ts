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

// design-machines-and-guests.md R1 / #1365 — account-level machine
// presence, compact enough to ride `ReposResponse` instead of costing
// ColdStart a second round-trip to `GET /v1/machines`. `paired` is *any*
// daemon ever registered on this account, repo or not; `any_enabled_repo`
// distinguishes "paired, nothing enabled yet" from "paired and working" —
// the two states the old repo-scoped-only gate collapsed into one.
export interface MachinesSummary {
	paired: boolean;
	any_enabled_repo: boolean;
}

// #1465 — the messenger-door registry (`brnrd.messenger_doors`, backend),
// mirrored here typed: one row per connector, `deep_link_available` the
// one flag ColdStart needs to decide whether a tappable door exists for
// it. No label/icon on the wire on purpose (same "no user-facing copy in
// the registry" rule `Capability` follows above) — the renderer owns
// those, same small roster idiom `supportMatrix.ts`'s `DOORS` already
// uses. A connector this roster doesn't recognize yet renders nothing
// rather than crashing — same fail-safe posture as `doorRows` there.
export interface MessengerDoor {
	platform: string;
	deep_link_available: boolean;
	// brr/every-door-on-the-page — why a dark door is dark: `"not_built"`
	// (no connector exists, Slack/Signal today) vs `"not_configured"` (the
	// connector exists but this deployment hasn't wired its identity,
	// Telegram/WhatsApp with no bot token / Cloud API creds). `null` for a
	// lit door. Absent on an older backend — treat as `null`, same "no
	// reason known" reading `messengerDoors.ts`'s `doorOffCopy` falls back
	// to.
	reason?: string | null;
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
	// Additive, optional, same "absent on an older backend" contract as
	// `capabilities` above — `ColdStart.svelte` falls back to its pre-#1365
	// repo-scoped-only gate when this is missing.
	machines?: MachinesSummary;
	// #1465 — the registry-derived connector set: every declared messenger
	// door with its own `deep_link_available` flag. `ColdStart.svelte`
	// renders whichever doors are available, generically — a new connector
	// joins this array server-side with no frontend edit. Absent on an
	// older backend, same "absent means unknown, render nothing tappable"
	// contract as `machines`/`capabilities` above.
	messenger_doors?: MessengerDoor[];
	// Deprecated (#1465): superseded by
	// `messenger_doors.find(d => d.platform === 'telegram')?.deep_link_available`.
	// Kept one release so a client caching this response across the deploy
	// doesn't regress; `ColdStart.svelte` no longer reads this field.
	// `""` means unset *or* shape-invalid (`dashboard.py`) — the two are
	// indistinguishable from here and both mean "no deep link is
	// constructible". Absent key = an older backend that predates #1457.
	telegram_bot_username?: string;
}

// #1277a: `pairing_command`/`setup_command` is two lines — a scene-setting
// `cd <checkout>` (a literal, unrunnable placeholder before any repo is
// known — `PAIR_REPO_PLACEHOLDER` in `_session.py` — or a real directory
// name once one is) followed by the one line that is unconditionally
// runnable. A COPY button that hands over the whole two-line string can
// hand over a placeholder no shell can run. Split here, once, so every
// caller renders the first line as prose and copies only the second —
// never a second parser of the same backend spelling.
export interface PairingCommandParts {
	// `null` when the backend ever ships a single-line command — nothing to
	// split out, the whole string is already runnable as copied.
	setupLine: string | null;
	runnable: string;
}

export function splitPairingCommand(raw: string): PairingCommandParts {
	const newline = raw.indexOf('\n');
	if (newline === -1) return { setupLine: null, runnable: raw };
	return { setupLine: raw.slice(0, newline), runnable: raw.slice(newline + 1) };
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

// #1457 — `schemas.TelegramPairStarted` on the wire, verbatim (`pairing.py`).
// `deep_link` is `null` when the configured bot handle is unset or fails
// the same shape check `telegram_bot_username` on `ReposResponse` already
// passed — a deep link built on a bad handle is worse than none, so the
// caller falls back to `pair_code` + `instructions` in that case.
export interface TelegramPairStarted {
	pair_code: string;
	instructions: string;
	deep_link: string | null;
	// brr/every-door-on-the-page — when this code goes dead, so a caller can
	// render a live countdown instead of a link that just stops working
	// with no explanation. `settings.messenger_pair_ttl_s` out from mint
	// (or from the still-active row a repeat mint reused).
	expires_at: string;
}

// #1457 — account-level mint: `POST /v1/dashboard/telegram-pair`, no body,
// session-cookie auth. Distinct from `pairRepoTelegram` above (repo-scoped,
// 404s without a connected repo) — this is the one the mobile cold-start
// CTA calls, since it works with zero repos connected. Codes expire in
// ~600s server-side (`settings.pair_ttl_s`); call this on tap, never ahead
// of it. Superseded as ColdStart's own call site by
// `mintAccountMessengerPair('telegram')` below (#1465) — kept for any
// other caller and its own test coverage.
export async function mintAccountTelegramPair(
	fetchImpl: typeof fetch = fetch
): Promise<TelegramPairStarted> {
	const res = await fetchImpl('/v1/dashboard/telegram-pair', {
		method: 'POST',
		credentials: 'include',
		headers: { 'content-type': 'application/json' }
	});
	if (res.status === 401) {
		throw new ReposAuthError('not signed in');
	}
	if (!res.ok) {
		throw new Error(`telegram pair mint failed: ${res.status}`);
	}
	return (await res.json()) as TelegramPairStarted;
}

// #1465 — `schemas.MessengerPairStarted` on the wire, verbatim
// (`dashboard.py`'s `dashboard_pair_api`). The registry-generalized twin
// of `TelegramPairStarted` above: same shape plus which `platform` it
// minted for, since the endpoint itself is no longer platform-specific.
export interface MessengerPairStarted {
	pair_code: string;
	instructions: string;
	deep_link: string | null;
	platform: string;
	// brr/every-door-on-the-page — same field, same reason, as
	// `TelegramPairStarted.expires_at` above.
	expires_at: string;
}

// #1465 — account-level mint: `POST /v1/dashboard/pair`, `{platform}`
// body, session-cookie auth. The one call ColdStart's messenger door uses
// for every available connector — a 409 means the requested platform has
// no deep-link door configured on this deployment (`messenger_doors`'s own
// `deep_link_available` flag said so already; the caller should not be
// offering the button in the first place, but the endpoint still refuses
// rather than minting a code nobody can act on).
export async function mintAccountMessengerPair(
	platform: string,
	fetchImpl: typeof fetch = fetch
): Promise<MessengerPairStarted> {
	const res = await fetchImpl('/v1/dashboard/pair', {
		method: 'POST',
		credentials: 'include',
		headers: { 'content-type': 'application/json' },
		body: JSON.stringify({ platform })
	});
	if (res.status === 401) {
		throw new ReposAuthError('not signed in');
	}
	if (!res.ok) {
		throw new Error(`${platform} pair mint failed: ${res.status}`);
	}
	return (await res.json()) as MessengerPairStarted;
}

export function disconnectRepo(
	repoId: string,
	fetchImpl: typeof fetch = fetch
): Promise<RepoActionResponse> {
	return postRepoAction(`/v1/repos/${encodeURIComponent(repoId)}/disconnect`, {}, fetchImpl);
}

// #1464 — the minting session's outcome readback: `GET
// /v1/dashboard/pair/{code}` (`dashboard.py`), scoped to the
// account that minted the code. `display` is `null` until redeemed (or
// forever, for a code that expires unused); `consumed` alone is enough to
// stop polling.
export interface TelegramPairStatus {
	consumed: boolean;
	display: string | null;
}

export async function fetchPairStatus(
	code: string,
	fetchImpl: typeof fetch = fetch
): Promise<TelegramPairStatus> {
	const res = await fetchImpl(`/v1/dashboard/pair/${encodeURIComponent(code)}`, {
		credentials: 'include'
	});
	if (res.status === 401) {
		throw new ReposAuthError('not signed in');
	}
	if (!res.ok) {
		throw new Error(`pair status fetch failed: ${res.status}`);
	}
	return (await res.json()) as TelegramPairStatus;
}

// #1464 — one row per `ChannelRoute` this account carries, the transparency
// half of the floor. `chat_title` is `null` for a private Telegram chat or
// any WhatsApp route (no title concept there — see `models.ChannelRoute`),
// distinct from an empty string; `repo_full_name` is `null` for an
// account-level route (repo resolved per message) and set for a pinned or
// legacy repo-scoped one.
export interface PairedChat {
	id: string;
	platform: string;
	chat_title: string | null;
	principal_display: string | null;
	paired_at: string | null;
	paired_at_label: string;
	repo_full_name: string | null;
}

export interface PairedChatsResponse {
	paired_chats: PairedChat[];
}

export async function fetchPairedChats(
	fetchImpl: typeof fetch = fetch
): Promise<PairedChatsResponse> {
	const res = await fetchImpl('/v1/dashboard/paired-chats', { credentials: 'include' });
	if (res.status === 401) {
		throw new ReposAuthError('not signed in');
	}
	if (!res.ok) {
		throw new Error(`paired chats fetch failed: ${res.status}`);
	}
	return (await res.json()) as PairedChatsResponse;
}

// #1464 — the revoke half: deletes the `ChannelRoute` row outright (kills
// the principal — not #1459's repo-unpin). `DELETE
// /v1/dashboard/paired-chats/{id}`, session-auth, scoped to the owning
// account server-side.
export async function revokePairedChat(
	routeId: string,
	fetchImpl: typeof fetch = fetch
): Promise<void> {
	const res = await fetchImpl(`/v1/dashboard/paired-chats/${encodeURIComponent(routeId)}`, {
		method: 'DELETE',
		credentials: 'include'
	});
	if (res.status === 401) {
		throw new ReposAuthError('not signed in');
	}
	if (!res.ok) {
		throw new Error(`revoke failed: ${res.status}`);
	}
}
