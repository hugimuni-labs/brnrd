/** One request at a time, paused in hidden tabs, aborted on teardown.
 * The callback checks its signal before committing state: a fetch adapter
 * may ignore abort, but an unmounted page must still ignore its result. */
export function startVisiblePoll(
	refresh: (signal: AbortSignal) => Promise<void>,
	visibility: Pick<Document, 'hidden' | 'addEventListener' | 'removeEventListener'>,
	intervalMs = 15_000
): () => void {
	let stopped = false;
	let inFlight = false;
	let controller: AbortController | null = null;
	let timer: ReturnType<typeof setTimeout> | undefined;
	async function tick() {
		if (stopped || inFlight || visibility.hidden) return;
		clearTimeout(timer);
		inFlight = true;
		controller = new AbortController();
		try {
			await refresh(controller.signal);
		} finally {
			inFlight = false;
			if (!stopped && !visibility.hidden) timer = setTimeout(tick, intervalMs);
		}
	}
	function changed() {
		clearTimeout(timer);
		if (!visibility.hidden) void tick();
	}
	visibility.addEventListener('visibilitychange', changed);
	void tick();
	return () => {
		stopped = true;
		clearTimeout(timer);
		controller?.abort();
		visibility.removeEventListener('visibilitychange', changed);
	};
}
