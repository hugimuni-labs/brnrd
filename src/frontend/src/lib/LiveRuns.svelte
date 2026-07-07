<script lang="ts">
	import { ageSince, type LiveRun } from './liveRuns';

	interface Props {
		runs: LiveRun[];
		stale: boolean;
		now: number;
	}

	let { runs, stale, now }: Props = $props();

	// Same fixed status palette as WindowTrack (dataviz skill's palette.md):
	// a status color must never double as a series identity. `$derived`
	// (not a plain `const`) so it reacts to `stale` changing on later polls,
	// not just the value at mount.
	const DOT_COLOR = $derived(stale ? '#57534e' : '#0ca30c');
</script>

<div class="rounded-md border border-slate-800 bg-slate-900/60 p-3">
	<div class="mb-2 flex items-center justify-between text-sm">
		<span class="font-medium text-slate-200">live runs</span>
		{#if stale}
			<span class="rounded bg-amber-900/40 px-1.5 py-0.5 text-xs text-amber-300">stale report</span>
		{/if}
	</div>
	{#if runs.length === 0}
		<p class="text-sm text-slate-500">Nothing awake right now.</p>
	{:else}
		<ul class="space-y-2">
			{#each runs as run (run.id)}
				<li class="flex items-center justify-between rounded bg-slate-800/60 px-2 py-1.5 text-xs">
					<span class="flex items-center gap-1.5 text-slate-300">
						<span
							class="inline-block h-2 w-2 rounded-full"
							style={`background-color: ${DOT_COLOR}`}
							aria-hidden="true"
						></span>
						<span class="font-medium">{run.repo_label || 'unknown repo'}</span>
						<span class="text-slate-500">· {run.kind || 'run'}</span>
					</span>
					<span class="text-slate-500">{ageSince(run.started_at, now) ?? ''}</span>
				</li>
			{/each}
		</ul>
	{/if}
</div>
