// The console's list of asks (design-the-ask.md §The list, §Build cut 3):
// the home *is* the list — one row per ask, LRU, done rows below a rule.
// Rows come from `GET /v1/dashboard/warp/asks.json` (`src/brr/asks.py`);
// everything here is plain TS so node's test runner reaches it.

import type { LiveRun } from './liveRuns.ts';

export interface AskSay {
	event: string;
	at: string | null;
	excerpt: string | null;
	/** A link to the thread/message this say came from, when a route
	 *  exists (the-panel-third-pass's 17:51Z steer) — the server sends
	 *  `null` today (no dashboard page resolves a specific message yet;
	 *  `dashboard.py::_enrich_ask_says` writes the key regardless, wired
	 *  for the day one lands) and older payloads may omit the field
	 *  entirely, so this reads optional. */
	url?: string | null;
}

/** One delivered receipt (design-the-row-you-can-judge, his 19:46Z steer
 *  evt-…-c7c3): a sibling strand (`brr/the-bases-the-dashboard-needs`) adds
 *  this array to the hosted payload, sourced from whatever forge/kb record
 *  the daemon can name for the ask. `title` may simply not be there yet —
 *  read when present, never resolved client-side (his steer, verbatim: "do
 *  not build a resolver yourself"). */
export interface AskReceipt {
	ref: string;
	url?: string | null;
	title?: string | null;
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
	/** Plural, structured receipts — absent on a payload predating the
	 *  sibling PR, in which case every reader here falls back to `receipt` /
	 *  `return`. Never assume non-empty even when present. */
	receipts?: AskReceipt[] | null;
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
 *  never re-sorts. Reused for the unaddressed bucket's own preview (same
 *  "newest N, rest behind a toggle" shape). */
export function doneWindow(rows: AskRow[], limit = 3): { visible: AskRow[]; restCount: number } {
	return { visible: rows.slice(0, limit), restCount: Math.max(0, rows.length - limit) };
}

/** A collapsed bucket bar's preview text (the-row-you-can-judge, his roast:
 *  "when collapsed it should show three last items but occupy one bar"):
 *  the newest `limit` titles, `·`-joined — the bar's own truncation clips
 *  the rest, this never does. `''` when the bucket is empty (caller skips
 *  the em dash rather than print a trailing one). */
export function previewNames(rows: AskRow[], limit = 3): string {
	return doneWindow(rows, limit)
		.visible.map((row) => row.title)
		.join(' · ');
}

/** The judge row's one derived line (the-row-you-can-judge §2, his "how much
 *  can you really judge from here?"): the newest receipt's title when the
 *  row carries one, else the plain `return:` text. Never resolves a title
 *  itself — his 19:46Z steer is explicit that a sibling strand owns filling
 *  `receipts[].title`, this only reads it. `null` when neither exists. */
export function deliveryLine(row: Pick<AskRow, 'receipts' | 'return'>): string | null {
	const title = row.receipts?.[0]?.title;
	return title || row.return || null;
}

/** One receipt chip, normalized for rendering: a label and an optional link. */
export interface ReceiptChip {
	label: string;
	url: string | null;
}

const HTTP_URL_RE = /^https?:\/\//;

/** The judge row's receipts, once (his roast: "the receipt data kinda
 *  repeats three times" — this is the single place it now renders):
 *  `receipts[]` when the row carries it, else a synthetic one-chip list from
 *  the legacy singular `receipt` field so an older payload still shows
 *  something, else `[]` (no receipts row at all). */
export function receiptChips(row: Pick<AskRow, 'receipts' | 'receipt'>): ReceiptChip[] {
	// `receipts` present (even `[]`) is the server's own word: an explicit
	// empty list means "none", never "ask the legacy field instead" — only
	// `null`/`undefined` (a payload predating the sibling PR) falls through.
	if (row.receipts != null) {
		return row.receipts.map((r) => ({
			label: r.title ? `${r.ref} · ${r.title}` : r.ref,
			url: r.url ?? null
		}));
	}
	if (row.receipt) {
		return [{ label: row.receipt, url: HTTP_URL_RE.test(row.receipt) ? row.receipt : null }];
	}
	return [];
}

/** The seat's own current live run (the-row-you-can-judge §4, his 19:46Z:
 *  "an in-hand `seat` row shows what the seat is doing") — the first
 *  non-strand row in the same live-runs payload the MACHINE block already
 *  reads (`Dashboard.svelte`'s `liveRuns`, joined here by `is_subspawn`
 *  rather than fetched again). `null` when no live run is known (no daemon
 *  awake, or the payload lags a beat behind the ask list's own poll). */
export function seatRun(liveRuns: readonly LiveRun[] | null | undefined): LiveRun | null {
	return (liveRuns ?? []).find((run) => !run.is_subspawn) ?? null;
}

/** The seat run's own `## Now`, first line only — `card_text` is already
 *  that section's projection, daemon-side (`liveRuns.ts`'s own note on the
 *  field), so this only takes the first line, never re-projects. `null` on
 *  an empty/whitespace-only card (a seat that hasn't written `## Now` yet). */
export function seatNowLine(run: Pick<LiveRun, 'card_text'> | null | undefined): string | null {
	const text = run?.card_text?.trim();
	if (!text) return null;
	return text.split('\n')[0]?.trim() || null;
}

/** A say line's own text — the excerpt when the server resolved one, else
 *  the event id as a last resort. The 17:51Z steer's complaint ("the
 *  evt-say lines say nothing") was about every row degrading to the bare
 *  id; this keeps that as the floor, not the default. */
export function sayText(say: AskSay): string {
	return say.excerpt || say.event;
}

/** The glyph beside a run link in `attempts` — the ask's own first topic
 *  stands in for "the run's topic" (a run id carries no topic of its own
 *  in this payload; the-panel-third-pass's report names this choice).
 *  `glyphFor` resolves a topic id/alias to its rendered glyph; `null`
 *  when unresolved or the row is topicless. */
export function attemptGlyph(
	row: AskRow,
	glyphFor: (topic: string) => string | null
): string | null {
	return row.topics.length ? glyphFor(row.topics[0]) : null;
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
