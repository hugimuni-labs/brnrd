// The viewer's look anchor (formerly THE DIGEST's, #1256). The digest
// block itself is gone (2026-08-11, the maintainer's ask: it was a
// redirect onto a run and repeated after the cloth) — what survives is
// the per-viewer "since you looked" instant, which now lights the cloth
// instead: rows newer than the anchor wear the brighter ground, and one
// "caught up" press in the cloth's header advances it. Same discipline as
// before: localStorage keyed by account id, advanced only on an explicit
// press, never on a mere render (an anchor that creeps forward because
// the page loaded is the "optimistic direction" lie #1256 named).

const DIGEST_STORAGE_PREFIX = 'brnrd.digest.lastLookedAt';

export function digestLastLookedStorageKey(accountId: string): string {
	return `${DIGEST_STORAGE_PREFIX}.${accountId}`;
}

/** Parse the stored anchor. Anything that isn't a finite, positive,
 *  not-in-the-future epoch-ms instant reads as "never looked" — corrupt or
 *  absent storage must never fabricate a look that didn't happen. */
export function readLastLookedAt(raw: string | null | undefined, nowMs: number): number | null {
	if (!raw) return null;
	const parsed = Number(raw);
	return Number.isFinite(parsed) && parsed > 0 && parsed <= nowMs ? parsed : null;
}

export function serializeLastLookedAt(ms: number): string {
	return String(Math.trunc(ms));
}
