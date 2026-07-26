<script lang="ts">
	// The acceptance widget, once, for every document that has one (#735).
	//
	// It was written for /beta-hosted-execution and lived inline in that page.
	// The general Terms of Service needed the same checkbox, and a second copy
	// of a consent widget is exactly the kind of duplicate that drifts into two
	// different consent behaviours — so the widget moved here rather than being
	// written again. The page supplies the document key and the sentence next
	// to the checkbox; everything about what "accepted" means is here.
	//
	// The attestation sentence is a page-supplied snippet on purpose: a
	// checkbox may only claim acceptance of the words rendered beside it
	// (#569), and those words belong to the page carrying the document.

	import type { Snippet } from 'svelte';

	import { acceptDocument, type DocumentStatus } from './terms.ts';

	interface Props {
		/** `DOC_TOS` / `DOC_HOSTED` — which document this checkbox accepts.
		 * Not named `document`: that shadows the DOM global inside a component. */
		documentKind: string;
		/** Server-owned state for that document; `null` while the fetch is in flight. */
		status: DocumentStatus | null;
		/** True once the session is known to be authenticated. */
		authenticated: boolean;
		/** Where to send the user after a successful acceptance. */
		next: string;
		/** Shown when the box is unchecked, and as the signed-out prompt's subject. */
		name: string;
		/** The sentence beside the checkbox. Owned by the page, not by this widget. */
		attestation: Snippet;
	}

	let { documentKind, status, authenticated, next, name, attestation }: Props = $props();

	let checked = $state(false);
	let posting = $state(false);
	let result = $state<{ level: 'success' | 'error'; message: string } | null>(null);

	async function accept() {
		if (!checked) {
			result = { level: 'error', message: `You need to accept the ${name} before continuing.` };
			return;
		}
		posting = true;
		result = null;
		try {
			const outcome = await acceptDocument(documentKind);
			if (!outcome.ok) {
				result = { level: 'error', message: outcome.notice };
				return;
			}
			result = { level: 'success', message: 'Accepted.' };
			// A full navigation, not the client router: `next` can be a
			// backend-owned path (e.g. /connect/BR-123).
			window.location.assign(next);
		} finally {
			posting = false;
		}
	}
</script>

{#if status === null}
	<p class="mt-6 text-sm text-ink-quiet">Loading…</p>
{:else if status.needs_accept}
	<div class="subpanel mt-6 p-4">
		<label class="flex items-start gap-3 text-sm text-stone-300">
			<input
				bind:checked
				type="checkbox"
				class="mt-1 h-4 w-4 accent-amber-500"
				aria-describedby="accept-copy"
			/>
			<span id="accept-copy">{@render attestation()}</span>
		</label>
		<div class="mt-4 flex flex-wrap items-center gap-3">
			<button
				type="button"
				class="cursor-pointer border border-amber-700 bg-amber-950/40 px-3 py-1.5 font-mono text-[11px] tracking-wide text-amber-100 uppercase hover:border-amber-500 disabled:cursor-not-allowed disabled:border-stone-800 disabled:text-ink-mute"
				disabled={posting}
				onclick={accept}>{posting ? 'accepting…' : 'accept and continue'}</button
			>
			<span class="font-mono text-[11px] text-ink-mute">next {next}</span>
		</div>
		<p class="mt-3 font-mono text-[10px] break-all text-ink-mute">
			version {status.version} · sha256 {status.sha256}
		</p>
		{#if result}
			<p class={`mt-3 text-sm ${result.level === 'error' ? 'text-red-400' : 'text-amber-200'}`}>
				{result.message}
			</p>
		{/if}
	</div>
{:else if authenticated}
	<p class="mt-6 text-sm text-ink-quiet">
		{#if status.accepted_at}
			Accepted {new Date(status.accepted_at).toLocaleString()} — version {status.version}{#if status.accepted_sha256},
				sha256
				{status.accepted_sha256.slice(0, 16)}…{/if}.
		{:else}
			Your account does not need a {name} update.
		{/if}
	</p>
{:else}
	<p class="mt-6 text-sm text-stone-400">
		This page is readable signed out. To record acceptance, <a
			class="text-sky-400 underline"
			href={`/login?next=${encodeURIComponent(status.accept_url)}`}
			rel="external">log in</a
		>.
	</p>
{/if}
