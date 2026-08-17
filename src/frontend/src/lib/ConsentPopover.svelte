<script lang="ts">
	import { resolve } from '$app/paths';
	import type { WithheldLane } from './withheld';
	import { consentGapRepos, laneForWithheld, laneShareClause } from './consentGap';
	import { setPublishLayers, type RepoActionResponse } from './repos';

	interface Props {
		withheld: WithheldLane;
	}

	let { withheld }: Props = $props();

	let dialogEl: HTMLDialogElement | undefined = $state();
	let pending = $state<string | null>(null);
	let result = $state<RepoActionResponse | null>(null);
	// Repos the reader just enabled, this dialog instance only — the server
	// payload that produced `withheld` won't refresh mid-dialog, so this is
	// what keeps a just-fixed row from still reading "never recorded" until
	// the next 2s poll replaces the prop.
	let justEnabled = $state<Set<string>>(new Set());

	let lane = $derived(laneForWithheld(withheld));
	let laneName = $derived(lane ? lane.label.split(/\s+—\s+/)[0] : withheld.lane);
	let shareClause = $derived(lane ? laneShareClause(lane) : "this lane's data");
	let gaps = $derived(consentGapRepos(withheld).filter((repo) => !justEnabled.has(repo.id)));

	export function open() {
		result = null;
		dialogEl?.showModal();
	}

	// Native <dialog> closes on Escape and on a submitting <button
	// type="submit"> for free; it does not close on a backdrop click by
	// default, so that's the one bit wired by hand — checked by identity
	// (click landed on the <dialog> itself, past its own padding, never on
	// a descendant) rather than by coordinates.
	function onBackdropClick(event: MouseEvent) {
		if (event.target === dialogEl) dialogEl?.close();
	}

	async function enable(repoId: string) {
		pending = repoId;
		result = null;
		try {
			const response = await setPublishLayers(repoId, withheld.lane);
			result = response;
			if (response.ok) justEnabled = new Set([...justEnabled, repoId]);
		} catch (err) {
			result = {
				ok: false,
				notice: err instanceof Error ? err.message : 'could not update publish scope'
			};
		} finally {
			pending = null;
		}
	}
</script>

<dialog
	bind:this={dialogEl}
	class="consent-dialog panel max-w-md border-amber-900/60 text-sm text-stone-300 backdrop:bg-stone-950/80 backdrop:backdrop-blur-sm"
	aria-labelledby="consent-dialog-heading-{withheld.lane}"
	onclick={onBackdropClick}
	onclose={() => (result = null)}
>
	<form method="dialog">
		<div class="flex items-start justify-between gap-3">
			<h2
				id="consent-dialog-heading-{withheld.lane}"
				class="font-mono text-sm font-semibold text-amber-100"
			>
				{laneName}
			</h2>
			<button
				type="submit"
				class="shrink-0 cursor-pointer font-mono text-xs text-ink-quiet hover:text-stone-300"
				aria-label="close">close</button
			>
		</div>
		<p class="mt-2 text-stone-400">
			Enabling this here shares <strong class="text-stone-200">{shareClause}</strong> with your brnrd.dev
			dashboard — it leaves this machine and renders there, unredacted, for this account.
		</p>
		{#if gaps.length === 0}
			<p class="mt-3 text-stone-400">
				Nothing left to name here —
				<a class="underline hover:text-amber-100" href={resolve('/repos')}>the repos page</a> covers anything
				else this lane is waiting on.
			</p>
		{:else}
			<ul class="mt-3 space-y-2">
				{#each gaps as repo (repo.id)}
					<li class="subpanel flex items-center justify-between gap-3 px-2 py-1.5">
						<div class="min-w-0">
							<p class="truncate font-mono text-[12px] text-stone-200">{repo.name}</p>
							<p class="font-mono text-[10px] text-ink-mute">
								{repo.reason === 'unrecorded'
									? 'never recorded a publish scope'
									: 'chose to publish nothing'}
							</p>
						</div>
						<button
							type="button"
							class="shrink-0 cursor-pointer border border-amber-700 bg-amber-950/40 px-2 py-1 font-mono text-[11px] tracking-wide text-amber-100 uppercase hover:border-amber-500 disabled:cursor-wait disabled:opacity-50"
							disabled={pending !== null}
							onclick={() => enable(repo.id)}
							>{pending === repo.id ? 'enabling' : 'enable here'}</button
						>
					</li>
				{/each}
			</ul>
		{/if}
		{#if result}
			<p
				class="mt-3 font-mono text-[11px] {result.ok ? 'text-amber-200' : 'text-red-400'}"
				role="status"
			>
				{result.notice}
			</p>
		{/if}
		<p class="mt-3 font-mono text-[10px] text-ink-mute">
			<a class="underline hover:text-amber-100" href={resolve('/repos')}>the repos page</a> is the full
			picture — every lane, every repo, in one place.
		</p>
	</form>
</dialog>

<style>
	/* `.panel` (layout.css) sets `position: relative` for its bracket-corner
	   chrome, which — found live in this component's own repro screenshot —
	   outranks the UA stylesheet's `dialog:modal { position: fixed; inset:
	   0; margin: auto }` centering rule and pins the dialog to the top-left
	   of the page instead of the viewport center. Restated explicitly here
	   rather than dropping `.panel` (its bracket chrome is exactly what the
	   rest of this surface wears). Border/padding reset for the same
	   already-covered-by-.panel reason. */
	dialog.consent-dialog {
		position: fixed;
		inset: 0;
		margin: auto;
		border: none;
		padding: 1rem;
	}
</style>
