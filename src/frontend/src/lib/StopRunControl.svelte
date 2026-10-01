<script lang="ts">
	// The user's exit from a live run — one component, so it can be rendered
	// wherever a live run is shown rather than living on exactly one surface.
	//
	// Extracted 2026-09-19 from `RunNodeInline.svelte`, where it was the only
	// copy. The control was never missing: it rendered solely in the
	// dashboard's selected-run panel, behind the `▸ more` expand, because only
	// `Dashboard.svelte` passes `liveLevel`. The run page — the URL a run's own
	// link points at — renders `RunNode.svelte` and had no stop code at all,
	// and the cloth renders `RunNodeInline` without `liveLevel`.
	//
	// Measured cost of that, `run-260919-1802-6zeq`: a seat whose own `halt:`
	// could not reach its finalize seam, a maintainer on the run page reporting
	// "the dashboard has no button anymore", and three hours spent routing
	// around a working endpoint. A surface that narrows renders as if it hadn't.
	import { LiveRunsAuthError, requestRunStop } from '$lib/liveRuns';

	interface Props {
		runId: string;
		/** Test/injection seam — the live call by default. */
		stopRun?: (runId: string) => Promise<unknown>;
		/** Extra classes for the row, so each host can seat it in its own band. */
		class?: string;
	}

	let { runId, stopRun = requestRunStop, class: klass = '' }: Props = $props();

	// Two visible buttons rather than a self-disarming glyph: a visible
	// `cancel` beside a visible `confirm stop` says what a glyph could only
	// imply, and this is a destructive control.
	let confirmingStop = $state(false);
	let stopPending = $state(false);
	let stopped = $state(false);
	let stopNote = $state<string | null>(null);

	async function commitStop() {
		confirmingStop = false;
		stopPending = true;
		try {
			await stopRun(runId);
			stopped = true;
			// Deliberately not "stopped": the daemon has not consumed it yet.
			stopNote = 'stopping — ends on the next daemon sync, partial work kept';
		} catch (e) {
			// A swallowed stop must be loud (the 2026-07-11 lesson): the reader
			// just tried to kill a burning run and nothing visible happened.
			stopNote =
				e instanceof LiveRunsAuthError
					? 'session expired — sign in again, then retry'
					: e instanceof Error
						? e.message
						: 'stop request failed';
		} finally {
			stopPending = false;
		}
	}
</script>

<div class="flex flex-wrap items-center gap-2 font-mono text-[10px] {klass}">
	{#if stopped}
		<span class="tracking-wide text-amber-500 uppercase">stopping</span>
	{:else if confirmingStop}
		<button
			type="button"
			class="cursor-pointer border border-red-900/60 bg-stone-950/70 px-2 py-1 tracking-wide text-red-300 uppercase hover:bg-red-950/40 disabled:cursor-wait disabled:opacity-50"
			disabled={stopPending}
			onclick={commitStop}>{stopPending ? 'stopping' : 'confirm stop'}</button
		>
		<button
			type="button"
			class="cursor-pointer border border-stone-800 px-2 py-1 tracking-wide text-ink-quiet uppercase hover:text-stone-300"
			disabled={stopPending}
			onclick={() => (confirmingStop = false)}>cancel</button
		>
		<span class="text-ink-mute">partial work is kept; the thought does not resume</span>
	{:else}
		<button
			type="button"
			class="cursor-pointer border border-stone-800 px-2 py-1 tracking-wide text-ink-quiet uppercase hover:text-red-300"
			onclick={() => (confirmingStop = true)}>stop run</button
		>
	{/if}
	{#if stopNote}
		<!-- Receipt line: a tap that gets swallowed must never be silent
		     (found live 2026-07-11 on the spool rack's own taps). -->
		<span class="text-amber-400/90">{stopNote}</span>
	{/if}
</div>
