<script lang="ts">
	import { resolve } from '$app/paths';

	let code = $state('');
	let error = $state<string | null>(null);

	function normalize(raw: string): string {
		return raw.trim().toUpperCase().replace(/\s+/g, '');
	}

	function continuePairing() {
		const entered = normalize(code);
		if (!/^BR-[A-Z2-9]{8}$/.test(entered)) {
			error = 'Enter the BR- code printed in your terminal.';
			return;
		}
		// The one-time device code is also the initiator proof. Keep it in the
		// fragment on the detail page so login detours preserve it without
		// sending it in a query string or Referer.
		window.location.assign(resolve(`/connect/${entered}#${entered}`));
	}
</script>

<svelte:head><title>connect daemon · brnrd</title></svelte:head>

<div class="mx-auto max-w-xl p-6">
	<p class="eyebrow">pairing handshake</p>
	<h1 class="mt-1 font-mono text-2xl font-semibold tracking-tight text-amber-100">
		Connect your daemon
	</h1>
	<section class="panel mt-6 p-5">
		<p class="text-sm text-stone-300">
			Run <code class="font-mono text-amber-200">brnrd account connect</code> in your checkout, then enter
			the one-time code it prints.
		</p>
		<form
			class="mt-5"
			onsubmit={(event) => {
				event.preventDefault();
				continuePairing();
			}}
		>
			<label
				class="font-mono text-[11px] tracking-wide text-amber-200/80 uppercase"
				for="pair-code"
			>
				pairing code
			</label>
			<input
				id="pair-code"
				bind:value={code}
				class="mt-2 w-full border border-stone-700 bg-stone-950 px-3 py-3 font-mono text-lg tracking-wider text-stone-100 uppercase"
				placeholder="BR-XXXXXXXX"
				autocomplete="one-time-code"
				spellcheck="false"
			/>
			<button
				type="submit"
				class="mt-4 cursor-pointer border border-sky-700 bg-sky-950/40 px-4 py-2 font-mono text-sm tracking-wide text-sky-100 uppercase hover:border-sky-500"
				>continue</button
			>
		</form>
		{#if error}<p class="mt-3 text-sm text-red-400">{error}</p>{/if}
	</section>
</div>
