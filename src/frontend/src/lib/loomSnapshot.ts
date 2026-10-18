import { quotaLevel } from './quota.ts';

// The loom snapshot, v1: one JSON document the loom pushes (`PUT
// /v1/daemons/loom`) and the home board reads (`GET /v1/dashboard/loom`).
// The contract lives in the self repo (threads/inbox/loom-snapshot.md); the
// types below mirror it field for field. Whatever the loom has no value for
// it omits, so every optional field here renders as absent, never as 0.

export type StrandState =
	'working' | 'holding' | 'idle' | 'parked' | 'failed' | 'molted' | 'settled' | 'stopped';

export type Turn = 'yours' | 'mine' | 'blocked' | 'none';

export type LogKind = 'letter' | 'receipt' | 'notice';

export interface LoomPresence {
	strand?: string;
	thread?: string;
	state?: StrandState | string;
	now?: string;
	owed?: number;
	body?: string;
}

export interface LoomGauge {
	window: string;
	left_pct?: number;
	resets_at?: string;
}

export interface LoomShell {
	shell: string;
	state?: string;
	gauges?: LoomGauge[];
	cores?: string[];
}

export interface LoomBodies {
	default?: string;
	choices?: string[];
}

export interface LoomStrand {
	id: string;
	body?: string;
	state?: StrandState | string;
	since?: string;
}

export interface LoomDone {
	kind?: string;
	ref?: string;
	line?: string;
}

export interface LoomThread {
	id: string;
	title?: string;
	status?: string;
	turn?: Turn | string;
	blocked_on?: string;
	opened_by?: string;
	plan?: { done?: number; total?: number; next?: string };
	strands?: LoomStrand[];
	done?: LoomDone[];
	owed?: number;
	last_at?: string;
}

export interface LoomLogEntry {
	id: string;
	at?: string;
	from?: string;
	to?: string;
	thread?: string;
	kind?: LogKind | string;
	head?: string;
	ref?: string;
}

export interface LoomSnapshot {
	v: 1;
	at?: string;
	/** Seconds since the server stored this snapshot (the endpoint adds it). */
	stale_s?: number;
	presence?: LoomPresence;
	fuel?: LoomShell[];
	bodies?: LoomBodies;
	threads?: LoomThread[];
	log?: LoomLogEntry[];
}

/** 'anon' = signed out (401); 'none' = signed in, no snapshot stored yet. */
export type LoomRead = 'anon' | 'none' | LoomSnapshot;

export class LoomAuthError extends Error {}

/** Same bound as the quota gate: `/` decides what to render on this fetch. */
export const LOOM_GATE_TIMEOUT_MS = 8_000;

/** 401 is signed out; 404, 204 and a 200 without `v: 1` all read as no
 * snapshot, so a board never renders from a shape it does not know. Any other
 * failure throws, and the caller falls back to the dashboard. */
export async function fetchLoom(
	fetchImpl: typeof fetch = fetch,
	signal: AbortSignal = AbortSignal.timeout(LOOM_GATE_TIMEOUT_MS)
): Promise<LoomRead> {
	const res = await fetchImpl('/v1/dashboard/loom', { credentials: 'include', signal });
	if (res.status === 401) return 'anon';
	if (res.status === 404 || res.status === 204) return 'none';
	if (!res.ok) throw new Error(`loom fetch failed: ${res.status}`);
	const body = (await res.json().catch(() => null)) as Partial<LoomSnapshot> | null;
	if (!body || body.v !== 1) return 'none';
	return body as LoomSnapshot;
}

export interface LetterQueued {
	ok: boolean;
	event_id?: string;
}

/** A 202 means queued for the relay, not delivered: the loom has not read it
 * yet, and the composer says 'queued'. */
export async function postLetter(
	letter: { thread: string; text: string; body?: string },
	fetchImpl: typeof fetch = fetch
): Promise<LetterQueued> {
	const payload: Record<string, string> = { thread: letter.thread, text: letter.text };
	if (letter.body) payload.body = letter.body;
	const res = await fetchImpl('/v1/dashboard/loom/letters', {
		method: 'POST',
		credentials: 'include',
		headers: { 'content-type': 'application/json' },
		body: JSON.stringify(payload)
	});
	if (res.status === 401) throw new LoomAuthError('not signed in');
	if (!res.ok) throw new Error(`letter not queued: ${res.status}`);
	return ((await res.json().catch(() => null)) as LetterQueued | null) ?? { ok: true };
}

// --- turn -----------------------------------------------------------------

const TURN_RANK: Record<string, number> = { yours: 0, mine: 1, blocked: 2, none: 3 };

export function turnRank(turn: string | undefined): number {
	return TURN_RANK[turn ?? 'none'] ?? 3;
}

const stamp = (iso: string | undefined): number => {
	const t = iso ? Date.parse(iso) : NaN;
	return Number.isFinite(t) ? t : 0;
};

/** Open threads first, then whose turn (yours pulls to the top), then the
 * most recently active. Settled threads sink to the end. */
export function sortThreads(threads: LoomThread[]): LoomThread[] {
	const settled = (t: LoomThread) => (t.status === 'settled' ? 1 : 0);
	return [...threads].sort(
		(a, b) =>
			settled(a) - settled(b) ||
			turnRank(a.turn) - turnRank(b.turn) ||
			stamp(b.last_at) - stamp(a.last_at)
	);
}

export const LIVE_STATES = new Set(['working', 'holding']);

export function isWorking(thread: LoomThread): boolean {
	return (thread.strands ?? []).some((s) => s.state === 'working');
}

export function isDelivered(thread: LoomThread): boolean {
	return (thread.done ?? []).length > 0;
}

export type ThreadFilter = 'all' | 'yours' | 'working' | 'delivered';

export const FILTERS: ThreadFilter[] = ['all', 'yours', 'working', 'delivered'];

export function matchesFilter(thread: LoomThread, filter: ThreadFilter): boolean {
	if (filter === 'yours') return thread.turn === 'yours';
	if (filter === 'working') return isWorking(thread);
	if (filter === 'delivered') return isDelivered(thread);
	return true;
}

export function filterCounts(threads: LoomThread[]): Record<ThreadFilter, number> {
	const counts = { all: 0, yours: 0, working: 0, delivered: 0 };
	for (const t of threads) for (const f of FILTERS) if (matchesFilter(t, f)) counts[f] += 1;
	return counts;
}

/** The origin chip: who opened the thread, in the loom's own words. */
export function originLabel(openedBy: string | undefined): string | null {
	if (!openedBy) return null;
	if (openedBy.startsWith('person:')) return 'by you';
	if (openedBy === 'loom') return 'by loom';
	if (openedBy.startsWith('thread:')) return `by ${openedBy.slice('thread:'.length)}`;
	return `by ${openedBy}`;
}

export function planFraction(thread: LoomThread): number | null {
	const total = thread.plan?.total;
	const done = thread.plan?.done;
	if (!total || done === undefined) return null;
	return Math.max(0, Math.min(1, done / total));
}

export function latestReceipt(thread: LoomThread): LoomDone | null {
	const done = (thread.done ?? []).filter((d) => d.ref);
	return done.length ? done[done.length - 1] : null;
}

// --- fuel -----------------------------------------------------------------

/** Low is the quota track's own 'spent' tier (≤15% left, `quotaLevel`), and a
 * parked shell is low whatever its gauges say: nothing runs on it. */
export function shellLow(shell: LoomShell): boolean {
	if (shell.state === 'parked') return true;
	return (shell.gauges ?? []).some((g) => quotaLevel(g.left_pct) === 'spent');
}

export function fuelPromoted(fuel: LoomShell[] | undefined): boolean {
	return (fuel ?? []).some(shellLow);
}

/** Every body a picker can offer: the declared choices, else shell/core. */
export function bodyChoices(snapshot: LoomSnapshot): string[] {
	const declared = snapshot.bodies?.choices ?? [];
	if (declared.length) return declared;
	return (snapshot.fuel ?? []).flatMap((s) => (s.cores ?? []).map((c) => `${s.shell}/${c}`));
}

export function defaultBody(snapshot: LoomSnapshot): string | null {
	return snapshot.bodies?.default ?? snapshot.presence?.body ?? null;
}

// --- time -----------------------------------------------------------------

function span(seconds: number): string {
	const s = Math.floor(seconds);
	if (s < 60) return `${Math.max(0, s)}s`;
	const m = Math.floor(s / 60);
	if (m < 60) return `${m}m`;
	const h = Math.floor(m / 60);
	if (h < 48) return m % 60 && h < 10 ? `${h}h ${m % 60}m` : `${h}h`;
	return `${Math.floor(h / 24)}d`;
}

/** "4m ago" — the age of an activity stamp, read against `now` (ms). */
export function ago(iso: string | undefined, now: number): string | null {
	if (!iso) return null;
	const t = Date.parse(iso);
	if (!Number.isFinite(t)) return null;
	const seconds = (now - t) / 1000;
	return seconds < 30 ? 'now' : `${span(seconds)} ago`;
}

/** "in 2h 14m" — a reset ahead of `now`; a reset already passed reads 'now'. */
export function resetsIn(iso: string | undefined, now: number): string | null {
	if (!iso) return null;
	const t = Date.parse(iso);
	if (!Number.isFinite(t)) return null;
	const seconds = (t - now) / 1000;
	return seconds <= 0 ? 'now' : `in ${span(seconds)}`;
}

/** The snapshot's age as the server measured it, or null when it is fresh
 * enough not to mention (under two minutes). */
export function snapshotAge(staleS: number | undefined): string | null {
	if (staleS === undefined || staleS < 120) return null;
	return span(staleS);
}

// --- log ------------------------------------------------------------------

export function sortedLog(log: LoomLogEntry[] | undefined, thread?: string): LoomLogEntry[] {
	return (log ?? [])
		.filter((e) => !thread || e.thread === thread)
		.sort((a, b) => stamp(b.at) - stamp(a.at));
}

/** Unread = newer than the last snapshot this browser showed. A browser that
 * has never been here has nothing unread: dots on everything say nothing. */
export function isUnread(entry: LoomLogEntry, seenAt: string | null): boolean {
	if (!seenAt || !entry.at) return false;
	return stamp(entry.at) > stamp(seenAt);
}

export function senderLabel(from: string | undefined): string {
	if (!from) return '';
	if (from.startsWith('person:')) return 'you';
	return from;
}

export const SEEN_KEY = 'brnrd.loom.seen';
export const BODY_KEY = 'brnrd.loom.body';

/** The newest moment a snapshot shows: what 'seen' records once it rendered. */
export function newestAt(snapshot: LoomSnapshot): string | null {
	const stamps = [snapshot.at, ...(snapshot.log ?? []).map((e) => e.at)].filter(
		(s): s is string => !!s && Number.isFinite(Date.parse(s))
	);
	if (!stamps.length) return null;
	return stamps.reduce((a, b) => (Date.parse(a) >= Date.parse(b) ? a : b));
}

/** localStorage, or nothing: a private window or a blocked store is not an error. */
export function stored(key: string): string | null {
	try {
		return localStorage.getItem(key);
	} catch {
		return null;
	}
}

export function store(key: string, value: string | null): void {
	try {
		if (value === null) localStorage.removeItem(key);
		else localStorage.setItem(key, value);
	} catch {
		// nothing to keep it in
	}
}
