<script lang="ts">
	import { onMount } from 'svelte';
	import { resolve } from '$app/paths';

	let { refused = false, compact = false }: { refused?: boolean; compact?: boolean } = $props();

	interface Trial {
		status: 'not_started' | 'active' | 'expired';
		ends_at: string | null;
		enforcement_enabled: boolean;
		member: boolean;
		subscribed: boolean;
	}
	let trial = $state<Trial | null>(null);
	let days = $derived(
		trial?.ends_at ? Math.max(0, Math.ceil((Date.parse(trial.ends_at) - Date.now()) / 86400000)) : 0
	);
	let expired = $derived(
		refused ||
			(trial?.enforcement_enabled &&
				trial.status === 'expired' &&
				!trial.member &&
				!trial.subscribed)
	);
	function accessLabel() {
		if (expired) return 'Home trial ended';
		if (trial?.member) return 'Member access';
		if (trial?.subscribed) return 'Subscribed';
		if (trial?.status === 'active')
			return `Home trial · ${days} ${days === 1 ? 'day' : 'days'} left`;
		if (trial?.status === 'not_started') return '14-day home trial starts when you connect';
		if (trial?.status === 'expired') return 'Home trial ended · access still open';
		return '';
	}
	let label = $derived(accessLabel());
	const services = 'machine connections, the hosted board, relay, and managed GitHub publishing';
	let explanation = $derived(
		expired
			? `Subscribe to restore ${services}. Your assistant keeps working on your computer.`
			: `A subscription keeps ${services} available after the trial. Your assistant keeps working on your computer.`
	);

	onMount(() => {
		const controller = new AbortController();
		async function read() {
			try {
				const response = await fetch('/v1/accounts/home-trial', {
					cache: 'no-store',
					signal: controller.signal
				});
				if (response.ok) trial = await response.json();
			} catch {
				// The board and account can still load if this read fails.
			}
		}
		void read();
		const timer = setInterval(read, 30_000);
		return () => {
			controller.abort();
			clearInterval(timer);
		};
	});
</script>

{#if label}
	{#if compact}
		<span class="dim" title={trial?.member || trial?.subscribed ? undefined : explanation}
			>{label}</span
		>
	{:else}
		<section aria-label="Home access" data-home-access class="mb-4 space-y-2">
			<h2>{label}</h2>
			{#if refused || (trial && !trial.member && !trial.subscribed)}
				<p>{explanation}</p>
			{/if}
			{#if expired}
				<a class="underline" href={resolve('/account#subscription')}>Subscribe</a>
			{/if}
		</section>
	{/if}
{/if}
