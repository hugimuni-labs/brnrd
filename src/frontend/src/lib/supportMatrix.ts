// Product support data used by brnrd.dev's landing.
//
// There are two different facts here, and the landing must not collapse them:
//
// 1. the backend's six platform-level deployment statuses (`DOORS`), fetched
//    from `/v1/stats/support`; and
// 2. the way a human can actually reach a resident (`REACH_GROUPS`).
//
// Those are not the same taxonomy. Telegram deliberately appears twice in
// the reach model: once as brnrd.dev's shared hosted identity and once as a
// bot token the operator owns and wires directly to the local daemon. Signal
// is local/BYO only. GitHub and Slack are app-shaped integrations. The web
// dashboard is a control surface, not a chat connector. Keeping those shapes
// separate is the point of this file: a provider logo is not an architecture.

export type DoorStatus = 'live' | 'soon' | 'ready';

export interface DoorMeta {
	slug: string;
	label: string;
	tag?: string;
	/** 'mono' = a plain text glyph tile (no clean vendored mark exists);
	 * anything else names the inline SVG this shelf renders for it. */
	icon:
		| 'claude'
		| 'codex-mono'
		| 'telegram'
		| 'slack-mono'
		| 'github'
		| 'dashboard'
		| 'whatsapp'
		| 'signal';
}

export const SHELLS: DoorMeta[] = [
	{ slug: 'claude', label: 'Claude Code', icon: 'claude' },
	{ slug: 'codex', label: 'Codex', icon: 'codex-mono' }
];

// Slugs must match `brr.support_matrix.DOORS`. Status still comes from the
// running backend rather than being hard-coded into the frontend bundle.
export const DOORS: DoorMeta[] = [
	{ slug: 'telegram', label: 'Telegram', icon: 'telegram' },
	{ slug: 'slack', label: 'Slack', icon: 'slack-mono' },
	{ slug: 'github', label: 'GitHub', tag: 'notifications', icon: 'github' },
	{ slug: 'dashboard', label: 'Web dashboard', icon: 'dashboard' },
	{ slug: 'whatsapp', label: 'WhatsApp', icon: 'whatsapp' },
	{ slug: 'signal', label: 'Signal', icon: 'signal' }
];

export type ReachBadge = 'live' | 'coming' | 'byo' | 'checking';
export type ReachStatusMode = 'hosted' | 'byo';

export interface ReachSurface {
	/** Unique because one platform can legitimately appear more than once. */
	id: string;
	label: string;
	detail: string;
	icon: DoorMeta['icon'];
	/** Platform status to consult when brnrd.dev owns/mediates the identity. */
	doorSlug?: string;
	statusMode: ReachStatusMode;
}

export interface ReachGroup {
	slug: 'hosted' | 'apps' | 'byo' | 'control';
	label: string;
	description: string;
	surfaces: ReachSurface[];
}

/**
 * The landing's user-facing reach model.
 *
 * This intentionally does not mirror DOORS one-for-one. DOORS is a backend
 * deployment roster; this is the topology a visitor needs in order to choose
 * how to reach their resident.
 */
export const REACH_GROUPS: ReachGroup[] = [
	{
		slug: 'hosted',
		label: 'hosted identities',
		description: 'brnrd-operated identities — no bot token or phone number to provision.',
		surfaces: [
			{
				id: 'telegram-hosted',
				label: 'Telegram',
				detail:
					'Message the shared brnrd bot; the control plane routes the thread to your paired daemon.',
				icon: 'telegram',
				doorSlug: 'telegram',
				statusMode: 'hosted'
			},
			{
				id: 'whatsapp-hosted',
				label: 'WhatsApp',
				detail: 'Message the brnrd number; hosted routing hands the work to your paired daemon.',
				icon: 'whatsapp',
				doorSlug: 'whatsapp',
				statusMode: 'hosted'
			}
		]
	},
	{
		slug: 'apps',
		label: 'installable apps',
		description: 'brnrd gets an identity inside a workspace or repository you own.',
		surfaces: [
			{
				id: 'github-app',
				label: 'GitHub App',
				detail:
					'Issues, review requests, replies, and a managed identity for the resident’s pushes.',
				icon: 'github',
				doorSlug: 'github',
				statusMode: 'hosted'
			},
			{
				id: 'slack-app',
				label: 'Slack App',
				detail: 'Workspace-installed ingress, rather than a bot identity you provision yourself.',
				icon: 'slack-mono',
				doorSlug: 'slack',
				statusMode: 'hosted'
			}
		]
	},
	{
		slug: 'byo',
		label: 'bring your own',
		description: 'your credentials, your identity, wired straight to the local daemon.',
		surfaces: [
			{
				id: 'telegram-byo',
				label: 'Telegram bot',
				detail: 'Run your own bot token directly against the daemon.',
				icon: 'telegram',
				statusMode: 'byo'
			},
			{
				id: 'signal-byo',
				label: 'Signal',
				detail: 'Link your own Signal number/device to the daemon.',
				icon: 'signal',
				statusMode: 'byo'
			}
		]
	},
	{
		slug: 'control',
		label: 'control',
		description: 'a surface for seeing and steering the resident, not another messaging identity.',
		surfaces: [
			{
				id: 'web-dashboard',
				label: 'Web dashboard',
				detail: 'Inspect and steer paired residents from brnrd.dev.',
				icon: 'dashboard',
				doorSlug: 'dashboard',
				statusMode: 'hosted'
			}
		]
	}
];

interface SupportMatrixResponse {
	doors: Array<{ slug: string; status: string }>;
}

/** Fetches `/v1/stats/support` and returns a slug → status map, or `null`
 * on any failure. A garbage status from a future backend rollout is dropped
 * rather than passed through. */
export async function fetchDoorStatus(
	fetcher: typeof fetch = fetch
): Promise<Map<string, DoorStatus> | null> {
	try {
		const resp = await fetcher('/v1/stats/support');
		if (!resp.ok) return null;
		const data = (await resp.json()) as SupportMatrixResponse;
		if (!Array.isArray(data?.doors)) return null;
		const statuses = new Map<string, DoorStatus>();
		for (const entry of data.doors) {
			if (typeof entry?.slug !== 'string') continue;
			if (entry.status !== 'live' && entry.status !== 'soon' && entry.status !== 'ready') continue;
			statuses.set(entry.slug, entry.status);
		}
		return statuses;
	} catch {
		return null;
	}
}

export interface DoorRow extends DoorMeta {
	/** `null` = no confirmed status yet (still loading, or the fetch failed). */
	status: DoorStatus | null;
}

/** Joins the static backend roster with a fetched status map. */
export function doorRows(statuses: Map<string, DoorStatus> | null): DoorRow[] {
	return DOORS.map((door) => ({
		...door,
		status: statuses?.get(door.slug) ?? null
	}));
}

/**
 * Translate implementation/deployment state into the vocabulary a visitor
 * actually needs on the landing.
 *
 * `ready` is intentionally not exposed. Internally it means "gate code is
 * shipped but brnrd.dev has no confirmed identity for it"; to a visitor that
 * still means "you cannot use the hosted route yet", i.e. coming. BYO routes
 * do not consult hosted status at all because brnrd.dev is not in that path.
 */
export function reachBadge(
	surface: ReachSurface,
	statuses: Map<string, DoorStatus> | null
): ReachBadge {
	if (surface.statusMode === 'byo') return 'byo';
	if (!surface.doorSlug) return 'coming';
	const status = statuses?.get(surface.doorSlug) ?? null;
	if (status === null) return 'checking';
	return status === 'live' ? 'live' : 'coming';
}
