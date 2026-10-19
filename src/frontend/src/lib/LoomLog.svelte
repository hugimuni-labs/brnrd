<script lang="ts">
	// The log: every letter, newest first. Receipts are their own kind (a
	// link and a short line, sky like every other link here); loom notices
	// are warm, since one is the loom saying it could not settle something.
	// An unread dot marks what arrived since this browser last looked.
	import { resolve } from '$app/paths';
	import { ago, isUnread, senderLabel, sortedLog, type LoomLogEntry } from './loomSnapshot.ts';

	let {
		log,
		seenAt,
		now,
		thread,
		limit = 24
	}: {
		log: LoomLogEntry[];
		seenAt: string | null;
		now: number;
		thread?: string;
		limit?: number;
	} = $props();

	let all = $state(false);
	let entries = $derived(sortedLog(log, thread));
	let shown = $derived(all ? entries : entries.slice(0, limit));
	let unread = $derived(entries.filter((e) => isUnread(e, seenAt)).length);
</script>

<div data-testid="loom-log">
	{#if unread > 0}
		<p class="mb-2 font-mono text-[10px] tracking-wide text-amber-300/90">{unread} unread</p>
	{/if}
	<ol class="divide-y divide-stone-800/60">
		{#each shown as entry (entry.id)}
			{@const fresh = isUnread(entry, seenAt)}
			<li class="flex gap-2.5 py-2" data-kind={entry.kind} data-unread={fresh ? 'true' : 'false'}>
				<span class="mt-1.5 flex size-2 shrink-0 items-center justify-center" aria-hidden="true">
					{#if fresh}
						<span class="size-1.5 rounded-full bg-amber-400 shadow-[0_0_6px_rgba(251,191,36,0.7)]"
						></span>
					{/if}
				</span>
				<div class="min-w-0 flex-1">
					<p class="flex flex-wrap items-baseline gap-x-2 font-mono text-[10px] text-ink-quiet">
						{#if entry.kind === 'receipt'}
							<span class="text-sky-300/90 uppercase">receipt</span>
						{:else if entry.kind === 'notice'}
							<span class="text-orange-300 uppercase">notice</span>
						{/if}
						<span class="text-stone-300">{senderLabel(entry.from)}</span>
						{#if entry.thread && !thread}
							<span class="text-ink-mute">on</span>
							<a
								href={resolve('/thread/[id]', { id: entry.thread })}
								class="text-ink-quiet hover:text-amber-200">{entry.thread}</a
							>
						{/if}
						<span class="ml-auto text-ink-mute">{ago(entry.at, now) ?? ''}</span>
					</p>
					{#if entry.kind === 'receipt' && entry.ref}
						<!-- eslint-disable svelte/no-navigation-without-resolve -- an external receipt -->
						<a
							href={entry.ref}
							target="_blank"
							rel="noopener noreferrer"
							class="mt-0.5 block truncate text-sm text-sky-200 hover:text-sky-100"
						>
							↗ {entry.head || entry.ref}
						</a>
						<!-- eslint-enable svelte/no-navigation-without-resolve -->
					{:else}
						<p
							class={`mt-0.5 line-clamp-3 text-sm break-words ${
								entry.kind === 'notice'
									? 'text-orange-100/90'
									: fresh
										? 'text-stone-100'
										: 'text-stone-300'
							}`}
						>
							{entry.head ?? ''}
						</p>
					{/if}
				</div>
			</li>
		{:else}
			<li class="py-3 font-mono text-[11px] text-ink-mute">no letters yet</li>
		{/each}
	</ol>
	{#if entries.length > shown.length}
		<button
			type="button"
			class="mt-2 font-mono text-[11px] text-ink-quiet hover:text-amber-200"
			onclick={() => (all = true)}
		>
			show all {entries.length}
		</button>
	{/if}
</div>
