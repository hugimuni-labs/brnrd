<script lang="ts">
	// #1464 — the revocation half of the transparency + revocation floor.
	// `ChannelRoute`s were invisible on the dashboard before this: nothing
	// listed which chats/principals could reach the account's resident, and
	// un-pairing existed only as `/repo`-level chat verbs. Self-contained
	// (own fetch, own state) so it can sit on any settings-adjacent surface
	// without the host page threading paired-chat state through its own
	// `data` — the same reasoning `MarkerNotice` already applies one level
	// down, at the per-repo row.
	import { onMount } from 'svelte';
	import { ReposAuthError, fetchPairedChats, revokePairedChat } from './repos';
	import type { PairedChat } from './repos';

	let chats = $state<PairedChat[] | null>(null);
	let loadError = $state<string | null>(null);
	let confirming = $state<string | null>(null);
	let busy = $state<string | null>(null);
	let revokeError = $state<string | null>(null);

	async function load() {
		try {
			const res = await fetchPairedChats();
			chats = res.paired_chats;
		} catch (e) {
			// Unauthenticated is not an error state worth a message here —
			// the host page's own auth gate already covers it, and this
			// component would otherwise render a second, redundant one.
			loadError = e instanceof ReposAuthError ? null : 'could not load paired chats';
		}
	}

	onMount(load);

	async function revoke(chat: PairedChat) {
		busy = chat.id;
		revokeError = null;
		try {
			await revokePairedChat(chat.id);
			chats = (chats ?? []).filter((c) => c.id !== chat.id);
			confirming = null;
		} catch {
			revokeError = "Couldn't revoke — try again.";
		} finally {
			busy = null;
		}
	}

	function platformLabel(platform: string): string {
		if (platform === 'telegram') return 'Telegram';
		if (platform === 'whatsapp') return 'WhatsApp';
		return platform;
	}
</script>

{#if chats !== null && chats.length > 0}
	<section class="panel mt-6 p-4" aria-labelledby="paired-chats-heading">
		<p class="eyebrow">messenger doors</p>
		<h2
			id="paired-chats-heading"
			class="font-mono text-lg font-semibold tracking-tight text-amber-100"
		>
			paired chats
		</h2>
		<p class="mt-1 max-w-2xl text-sm text-ink-quiet">
			Every chat that can reach your resident right now. Revoking one kills that chat's pairing
			outright — it stops authorizing, it does not just un-pin a project (that's <code
				class="font-mono text-amber-200">/repo auto</code
			> from inside the chat).
		</p>
		<div class="mt-4 grid grid-cols-1 gap-2 lg:grid-cols-2">
			{#each chats as chat (chat.id)}
				<div class="subpanel p-3" data-testid="paired-chat-row">
					<div class="flex items-start justify-between gap-2">
						<div class="min-w-0">
							<p class="truncate font-mono text-sm font-semibold text-amber-100">
								{chat.principal_display ?? '(no name reported)'}
							</p>
							<p class="mt-1 truncate font-mono text-[11px] text-ink-quiet">
								{platformLabel(chat.platform)}{chat.chat_title ? ` · ${chat.chat_title}` : ''} · paired
								{chat.paired_at_label}
							</p>
							<p class="mt-1 truncate font-mono text-[11px] text-ink-quiet">
								{chat.repo_full_name ? `pinned to ${chat.repo_full_name}` : 'auto-routed'}
							</p>
						</div>
						<div class="flex shrink-0 items-center gap-2">
							{#if confirming === chat.id}
								<button
									type="button"
									class="cursor-pointer border border-red-900/60 bg-red-950/30 px-2 py-1 font-mono text-[11px] tracking-wide text-red-200 uppercase hover:bg-red-950/50 disabled:cursor-wait disabled:opacity-60"
									disabled={busy === chat.id}
									onclick={() => revoke(chat)}
									>{busy === chat.id ? 'revoking' : 'confirm revoke'}</button
								>
								<button
									type="button"
									class="cursor-pointer border border-stone-800 px-2 py-1 font-mono text-[11px] tracking-wide text-ink-quiet uppercase hover:text-stone-300"
									disabled={busy === chat.id}
									onclick={() => (confirming = null)}>cancel</button
								>
							{:else}
								<button
									type="button"
									class="cursor-pointer border border-stone-800 px-2 py-1 font-mono text-[11px] tracking-wide text-ink-quiet uppercase hover:text-stone-300"
									onclick={() => (confirming = chat.id)}
									data-testid="revoke-open">revoke</button
								>
							{/if}
						</div>
					</div>
				</div>
			{/each}
		</div>
		{#if revokeError}
			<p class="mt-3 text-sm text-red-300" data-testid="revoke-error">{revokeError}</p>
		{/if}
	</section>
{:else if loadError}
	<p class="mt-6 text-sm text-red-300" data-testid="paired-chats-error">{loadError}</p>
{/if}
