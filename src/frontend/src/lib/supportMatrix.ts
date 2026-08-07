// The shells-and-doors shelf on brnrd.dev's own landing (#1070 follow-up).
//
// The docs site (docs/src/content/docs/index.md) renders the same idea —
// what brnrd supports — but answers a different question: "can I self-host
// this today" (gate code shipped on `main`). This landing answers "is
// brnrd.dev's own hosted convenience layer wired up for this today", which
// is a deployment fact this static bundle cannot know at build time — only
// the running backend can, from its own live `Settings`. So: roster
// (identity, icon, tag) lives here as presentation data, same as the docs
// page hand-writes its own; **status never does** — it always comes from
// `GET /v1/stats/support`, computed by `brr.support_matrix` (see that
// module's docstring for the full design and why the two landings can
// legitimately disagree about the same door).
//
// The property this file exists to hold: a door this landing has not heard
// a live status for is never rendered as though it were confirmed live —
// see `doorRows`'s `status: null` branch and `supportMatrix.test.ts`'s
// "status-drift" cases.
//
// Three door states, not two: `'ready'` is shipped code with no confirmed
// brnrd.dev identity yet — the maintainer's brief, named directly: code
// shipped, lane wired, and an identity that actually answers are three
// independent facts, and a two-value status collapses the last two into
// each other in both directions (an unconfigured hosted axis reading the
// same as unwritten code; a door with no hosted axis at all reading the
// same as a working one). See `hosted_status`'s docstring for the full
// reasoning.

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

// Order matches the docs shelf (#1070) so the two pages read as the same
// list. Slugs must match `brr.support_matrix.DOORS` — `doorRows` ignores a
// fetched status for any slug this roster doesn't recognize, so a slug
// typo on either side fails safe (that door reads `status: null`, never a
// fabricated `live`) rather than crashing or mismatching silently.
export const DOORS: DoorMeta[] = [
	{ slug: 'telegram', label: 'Telegram', icon: 'telegram' },
	{ slug: 'slack', label: 'Slack', icon: 'slack-mono' },
	{ slug: 'github', label: 'GitHub', tag: 'notifications', icon: 'github' },
	{ slug: 'dashboard', label: 'Web dashboard', icon: 'dashboard' },
	{ slug: 'whatsapp', label: 'WhatsApp', icon: 'whatsapp' },
	{ slug: 'signal', label: 'Signal', icon: 'signal' }
];

interface SupportMatrixResponse {
	doors: Array<{ slug: string; status: string }>;
}

/** Fetches `/v1/stats/support` and returns a slug → status map, or `null`
 * on any failure (network, non-2xx, malformed body) — the same "decoration,
 * never a gate" posture as `fetchPublicStats` / `fetchPricing`. A garbage
 * status value from a future backend rollout is dropped rather than passed
 * through, so a typo server-side degrades to "unknown", never to a
 * fabricated claim. */
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
	/** `null` = no confirmed status yet (still loading, or the fetch
	 * failed) — rendered distinctly from both `live` and `soon`, never
	 * folded into either. This is the property the maintainer's brief
	 * named: a door nobody has vouched for today must never read the same
	 * as one that has. */
	status: DoorStatus | null;
}

/** Joins the static roster with a fetched status map. Every roster door
 * always gets a row (so the shelf's shape never jumps between "loading"
 * and "loaded"); a door the status map didn't mention — because the fetch
 * hasn't resolved yet, failed, or a slug drifted out of sync with the
 * backend — reads `status: null` rather than defaulting to `live` or
 * being silently dropped. */
export function doorRows(statuses: Map<string, DoorStatus> | null): DoorRow[] {
	return DOORS.map((door) => ({
		...door,
		status: statuses?.get(door.slug) ?? null
	}));
}
