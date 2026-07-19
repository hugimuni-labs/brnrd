/**
 * The boot curtain, as a signal other motion can wait on.
 *
 * `+layout.svelte` covers the whole viewport with the `bRnЯd` boot glitch for
 * roughly 1.2s after mount. Every other entrance animation on this dashboard
 * is scheduled against a different clock: `.ignite` starts at mount, and the
 * text reveal starts whenever the corpus fetch lands. Those clocks agree with
 * the curtain's only by accident.
 *
 * Measured 2026-07-19 on the run node with a warm cache: the corpus arrived at
 * +1.1s and the reveal ran to +2.3s — the first, most visible half of it played
 * out behind an opaque overlay. On a cold fetch (~11s) the same reveal is fully
 * visible. So whether the reader ever saw the motion depended on their cache,
 * which is the kind of bug that gets reported as "sometimes it doesn't glitch"
 * and never reproduces for the person fixing it.
 *
 * A reveal is a statement that *this text just arrived*. Made to an audience
 * behind a curtain, it is not a statement at all — so the reveal waits, and the
 * curtain is what starts it.
 */

let booted = false;
const waiting: (() => void)[] = [];

/**
 * Deadline after which boot is assumed to have happened whether the layout said
 * so or not.
 *
 * The curtain is the only thing that calls `markBooted`, which makes it a
 * single point of failure for every reveal on the page: a layout that throws
 * during mount, a route rendered without it, or a future edit that drops the
 * call would leave text waiting on a signal that never comes. Waiting text is
 * *invisible* text, so the failure would be a blank page rather than a missing
 * flourish. The animation is a nicety; the words are not, and they are not
 * allowed to depend on it.
 *
 * Comfortably longer than the ~1.2s curtain, short enough that a reader never
 * notices it was the fallback that fired.
 */
export const BOOT_FALLBACK_MS = 2500;

if (typeof window !== 'undefined') {
	setTimeout(() => markBooted(), BOOT_FALLBACK_MS);
}

/** Called by the layout when the curtain lifts, or immediately if none runs. */
export function markBooted(): void {
	if (booted) return;
	booted = true;
	for (const run of waiting.splice(0)) run();
}

export function isBooted(): boolean {
	return booted;
}

/**
 * Run `task` once the curtain is up — synchronously if it already is.
 * Returns a canceller, because the element that queued the work can be
 * destroyed (a collapsed panel, a navigation) before the boot ever completes.
 */
export function whenBooted(task: () => void): () => void {
	if (booted) {
		task();
		return () => {};
	}
	waiting.push(task);
	return () => {
		const at = waiting.indexOf(task);
		if (at !== -1) waiting.splice(at, 1);
	};
}

/** Test seam: forget that boot happened. */
export function resetBootForTest(): void {
	booted = false;
	waiting.length = 0;
}
