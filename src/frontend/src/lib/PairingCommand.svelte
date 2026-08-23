<script lang="ts">
	import { splitPairingCommand } from './repos';

	// The pairing-command copy box — one component, one clipboard helper.
	// Previously copied verbatim across CapabilityPanel.svelte (deleted),
	// ColdStart.svelte, and routes/repos/+page.svelte. The pattern is always
	// the same: split the backend string (splitPairingCommand), show optional
	// scene-setting prose, render the runnable line in a copy box.
	interface Props {
		// The full backend pairing command string (e.g. `pairing_command` from
		// the repos fetch). `null` / missing ⇒ renders nothing, same "don't
		// invent a placeholder" contract ColdStart's own gating already uses.
		command: string | null;
	}

	let { command }: Props = $props();

	let parts = $derived(command ? splitPairingCommand(command) : null);

	let copied = $state(false);
	let copyTimer: ReturnType<typeof setTimeout> | undefined;

	async function copy() {
		const text = parts?.runnable ?? command;
		if (!text) return;
		try {
			await navigator.clipboard.writeText(text);
			copied = true;
			clearTimeout(copyTimer);
			copyTimer = setTimeout(() => (copied = false), 1500);
		} catch {
			// Clipboard unavailable or denied — no crash, the command is still
			// there to select by hand.
		}
	}
</script>

{#if command}
	{#if parts?.setupLine}
		<!-- #1277a: scene-setting, not copyable — the box below hands over only
		     the line that is unconditionally runnable. -->
		<p class="mt-1.5 font-mono text-[11px] text-ink-mute">from your repo checkout:</p>
	{/if}
	<!-- Wrapped, not scrolled (driven on a 390px phone, 2026-08-03 — same
	     rationale as ColdStart.svelte's step-02 comment): `overflow-x-auto`
	     clips the middle of a long URL with no visible tell. Soft wrap keeps
	     every character on screen; the copy button hands over the real string. -->
	<div class="mt-1.5 flex items-start gap-2">
		<pre
			class="min-w-0 grow border border-stone-800 bg-stone-950/50 p-2 font-mono text-[11px] wrap-anywhere whitespace-pre-wrap text-stone-300"><code
				>{parts?.runnable ?? command}</code
			></pre>
		<button
			type="button"
			class="shrink-0 cursor-pointer border border-stone-800 px-2 py-2 font-mono text-[10px] tracking-wide text-ink-quiet uppercase hover:text-stone-300"
			onclick={copy}>{copied ? 'copied' : 'copy'}</button
		>
	</div>
{/if}
