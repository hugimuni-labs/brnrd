// The console's list of asks (design-the-ask.md §The list, §Build cut 3):
// the home *is* the list — one row per ask, LRU, done rows below a rule.
// Rows come from `GET /v1/dashboard/warp/asks.json` (`src/brr/asks.py`);
// everything here is plain TS so node's test runner reaches it.

export interface AskSay {
	event: string;
	at: string | null;
	excerpt: string | null;
}

export interface AskRow {
	id: string;
	/** A short memorable handle a sibling PR may attach to the row
	 *  (design-the-ask.md, his "not w-45 but w-[45|anltcs]") — read when
	 *  present, never synthesized here; the field may simply not exist yet
	 *  on a given payload. */
	sign?: string | null;
	title: string;
	type: string | null;
	return: string | null;
	stage: string | null;
	touched_at: string | null;
	says: AskSay[];
	attempts: string[];
	receipt: string | null;
	topics: string[];
	stale: boolean;
	done: boolean;
	after: string | null;
}

export interface AskGoal {
	id: string;
	title: string;
	touched_at: string | null;
}

export interface AsksResponse {
	asks: AskRow[];
	done: AskRow[];
	goals: AskGoal[];
	stale_after_days: number;
}

export class AsksAuthError extends Error {}

/** Account-scoped list of asks. Throws `AsksAuthError` on a 401, same shape
 * as the other dashboard fetchers. */
export async function fetchAsks(fetchImpl: typeof fetch = fetch): Promise<AsksResponse> {
	const res = await fetchImpl('/v1/dashboard/warp/asks.json', { credentials: 'include' });
	if (res.status === 401) throw new AsksAuthError('not signed in');
	if (!res.ok) throw new Error(`asks fetch failed: ${res.status}`);
	return (await res.json()) as AsksResponse;
}

/** Compact relative time: `now`, `5m`, `3h`, `4d`, `9w`. Empty when unknown. */
export function touchedLabel(at: string | null, now: number = Date.now()): string {
	if (!at) return '';
	const then = Date.parse(at);
	if (Number.isNaN(then)) return '';
	const minutes = Math.max(0, Math.floor((now - then) / 60000));
	if (minutes < 1) return 'now';
	if (minutes < 60) return `${minutes}m`;
	const hours = Math.floor(minutes / 60);
	if (hours < 48) return `${hours}h`;
	const days = Math.floor(hours / 24);
	return days < 70 ? `${days}d` : `${Math.floor(days / 7)}w`;
}

/** The alive rows the list shows: a stale row stays listed, below the rule. */
export function splitAlive(rows: AskRow[]): { alive: AskRow[]; stale: AskRow[] } {
	return { alive: rows.filter((row) => !row.stale), stale: rows.filter((row) => row.stale) };
}

/** The three open buckets the console renders alongside the done bucket
 *  (design-the-ask.md §Done, reopened, linked, "the console = the warp
 *  panel, per ask"): **in hand** (a strand or the seat is working it right
 *  now) · **yours to judge** (delivered, not yet accepted) · **unaddressed**
 *  (not yet even shaped) · done/accepted rides apart as `data.done` (see
 *  `AskList.svelte`, which renders it first, per his third-pass steer: "the
 *  done block should be collapsible but on top"). `rows` is expected
 *  pre-sorted LRU (the server already does this — `asks.py::build_asks`),
 *  and filtering it into buckets preserves that order within each one. */
export interface AskBuckets {
	inHand: AskRow[];
	toJudge: AskRow[];
	unaddressed: AskRow[];
}

const UNADDRESSED_STAGES = new Set(['heard', 'understood', 'shaped']);

export function bucketAsks(rows: AskRow[]): AskBuckets {
	const inHand: AskRow[] = [];
	const toJudge: AskRow[] = [];
	const unaddressed: AskRow[] = [];
	for (const row of rows) {
		if (row.stage === 'making') inHand.push(row);
		else if (row.stage === 'delivered') toJudge.push(row);
		else if (!row.stage || UNADDRESSED_STAGES.has(row.stage)) unaddressed.push(row);
		// any other stage (accepted/reshaped/sprouted on a still-open row,
		// which `done` should already have claimed) renders in none of the
		// three — never silently duplicated into one by a fallback guess.
	}
	return { inHand, toJudge, unaddressed };
}

/** The id cell's display text — `w-45` normally, `w-45 · anltcs` once a row
 *  carries a `sign` (design-the-ask.md, his "not w-45 but w-[45|anltcs]"):
 *  read when present, never invented when it isn't. */
export function askLabel(row: AskRow): string {
	return row.sign ? `${row.id} · ${row.sign}` : row.id;
}

/** The token the accept/reroute chips address — the sign when the row
 *  carries one (shorter, easier to say back), else the bare id. */
export function askHandle(row: AskRow): string {
	return row.sign || row.id;
}

/** The done bucket's default window (his "on top, showing a few last
 *  items"): the newest `limit` rows visible already; the rest counted for
 *  the toggle. Rows arrive newest-first from the server
 *  (`asks.py::build_asks` sorts `done` descending) — this only slices, it
 *  never re-sorts. */
export function doneWindow(rows: AskRow[], limit = 3): { visible: AskRow[]; restCount: number } {
	return { visible: rows.slice(0, limit), restCount: Math.max(0, rows.length - limit) };
}

/** A row is lit by the heddle filter when it wears a lit topic; a row with no
 *  topics is always shown (a filter never hides what it cannot place).
 *  `resolve` maps a topic slug/alias to its canonical id (`null` = unknown). */
export function askInTopics(
	row: AskRow,
	selected: ReadonlySet<string> | null,
	resolve: (slug: string) => string | null
): boolean {
	if (selected === null || row.topics.length === 0) return true;
	return row.topics.some((slug) => {
		const id = resolve(slug);
		return id !== null && selected.has(id);
	});
}

/** The drone mark: a run on this ask's `attempts` is live right now. */
export function liveAttempt(row: AskRow, liveRunIds: ReadonlySet<string>): string | null {
	return row.attempts.find((run) => liveRunIds.has(run)) ?? null;
}

/** `j`/`k` step over `count` rows; from no focus `j` lands on the first row
 *  and `k` on the last. Clamped, never wrapping. */
export function moveFocus(current: number | null, key: 'j' | 'k', count: number): number | null {
	if (count === 0) return null;
	if (current === null) return key === 'j' ? 0 : count - 1;
	return Math.min(count - 1, Math.max(0, current + (key === 'j' ? 1 : -1)));
}

/** Keys the list must leave alone (typing in a field, chords). */
export function keymapIgnores(target: EventTarget | null, event: KeyboardEvent): boolean {
	if (event.metaKey || event.ctrlKey || event.altKey) return true;
	const el = target as HTMLElement | null;
	const tag = el?.tagName?.toLowerCase();
	return (
		tag === 'input' || tag === 'textarea' || tag === 'select' || Boolean(el?.isContentEditable)
	);
}
