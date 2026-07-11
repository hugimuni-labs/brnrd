<script lang="ts">
	import { onMount } from 'svelte';

	interface TermsStatus {
		authenticated: boolean;
		needs_accept: boolean;
		terms_version: string;
		accepted_at: string | null;
	}

	let status = $state<TermsStatus | null>(null);
	let statusError = $state<string | null>(null);
	let checked = $state(false);
	let posting = $state(false);
	let result = $state<{ level: 'success' | 'error'; message: string } | null>(null);
	let nextUrl = $state('/');

	function safeNext(value: string | null): string {
		if (!value || !value.startsWith('/') || value.startsWith('//')) return '/';
		return value;
	}

	async function refreshStatus() {
		try {
			const res = await fetch('/v1/dashboard/terms-status', { credentials: 'include' });
			if (!res.ok) throw new Error(`terms-status fetch failed: ${res.status}`);
			status = (await res.json()) as TermsStatus;
			statusError = null;
		} catch (e) {
			statusError = e instanceof Error ? e.message : 'terms-status fetch failed';
		}
	}

	async function acceptTerms() {
		if (!checked) {
			result = {
				level: 'error',
				message: 'You need to accept the beta hosted-execution terms before continuing.'
			};
			return;
		}
		posting = true;
		result = null;
		try {
			const res = await fetch('/v1/terms/accept', {
				method: 'POST',
				credentials: 'include',
				headers: { 'content-type': 'application/json' },
				body: JSON.stringify({ accept_terms: 'yes' })
			});
			const body = await res.json().catch(() => ({}));
			if (res.status === 401) {
				result = { level: 'error', message: 'Sign in before accepting the beta terms.' };
				return;
			}
			if (!res.ok || body.ok !== true) {
				result = {
					level: 'error',
					message:
						typeof body.notice === 'string'
							? body.notice
							: `terms acceptance failed: ${res.status}`
				};
				return;
			}
			result = { level: 'success', message: 'Accepted.' };
			window.location.assign(nextUrl);
		} finally {
			posting = false;
		}
	}

	onMount(() => {
		nextUrl = safeNext(new URLSearchParams(window.location.search).get('next'));
		refreshStatus();
	});
</script>

<svelte:head><title>brnrd beta terms</title></svelte:head>

<div class="mx-auto max-w-2xl p-6">
	<div class="flex items-start justify-between gap-4">
		<p class="eyebrow">brnrd · beta terms</p>
		<a
			href="/"
			class="font-mono text-[11px] tracking-wide text-stone-500 uppercase hover:text-stone-300"
			>dashboard</a
		>
	</div>
	<h1 class="mt-1 font-mono text-2xl font-semibold tracking-tight text-amber-100">
		brnrd hosted-execution beta disclaimer
	</h1>

	<section class="panel mt-6 p-5">
		<p class="text-sm text-stone-400">
			Version {status?.terms_version ?? '2026-07-08'}. These beta terms apply when HugiMuni
			SAS operates brnrd-hosted compute for your account.
		</p>

		<div class="mt-6 space-y-5 text-sm leading-6 text-stone-300">
			<section>
				<h2 class="font-mono text-sm font-semibold tracking-wide text-amber-100">
					1. Hosted agent execution
				</h2>
				<p class="mt-2">
					brnrd may run your selected agent, runner, or automation on compute operated by
					HugiMuni SAS. Hosted execution can include shell commands, file writes, dependency
					installation, network requests, Git operations, and other tool use needed to work on
					the repositories or services you connect.
				</p>
				<p class="mt-2">
					Some hosted runs may use yolo-exec or equivalent unattended execution modes. You
					authorize brnrd to perform those actions for the connected account, repository,
					branch, and task you provide.
				</p>
			</section>

			<section>
				<h2 class="font-mono text-sm font-semibold tracking-wide text-amber-100">
					2. Beta status and your risk
				</h2>
				<p class="mt-2">
					Hosted execution is a beta feature. You use it at your own risk. Agentic code
					execution can make incorrect changes, expose secrets already available to the run,
					call external services, consume quota, or run code supplied by your project or its
					dependencies.
				</p>
				<p class="mt-2">
					You are responsible for deciding which repositories, credentials, branches, data,
					prompts, dependencies, and approvals are safe to give to hosted brnrd runs.
				</p>
			</section>

			<section>
				<h2 class="font-mono text-sm font-semibold tracking-wide text-amber-100">
					3. No execution-sandbox guarantee
				</h2>
				<p class="mt-2">
					brnrd will apply the execution defaults and controls stated in its product
					documentation or configuration, but HugiMuni SAS does not promise that hosted
					execution is a security sandbox, a containment boundary, or a guarantee against
					prompt injection, malicious code, supply-chain compromise, data loss, or unauthorized
					behavior by tools your run can reach.
				</p>
			</section>

			<section>
				<h2 class="font-mono text-sm font-semibold tracking-wide text-amber-100">
					4. Availability and changes
				</h2>
				<p class="mt-2">
					The beta service may change, pause, reject, throttle, or stop hosted execution at
					any time to protect the service, HugiMuni SAS-operated infrastructure, other users,
					or connected third-party services.
				</p>
			</section>

			<section>
				<h2 class="font-mono text-sm font-semibold tracking-wide text-amber-100">
					5. Liability limits
				</h2>
				<p class="mt-2">
					To the maximum extent permitted by applicable law, HugiMuni SAS excludes implied
					warranties and will not be liable for indirect, incidental, special, consequential,
					punitive, or lost-profit damages arising from hosted execution.
				</p>
				<p class="mt-2">
					Any liability that cannot be excluded is limited to the maximum extent allowed by
					law. Nothing in these beta terms limits rights or remedies that cannot be waived
					under French law or European Union consumer protection law.
				</p>
			</section>

			<section>
				<h2 class="font-mono text-sm font-semibold tracking-wide text-amber-100">6. Company</h2>
				<p class="mt-2">
					brnrd is operated by HugiMuni SAS, France. These terms are intended as a beta
					hosted-execution disclaimer and do not replace a full customer agreement where one
					is separately agreed.
				</p>
			</section>
		</div>

		{#if statusError}
			<p class="mt-6 text-sm text-red-400">{statusError}</p>
		{:else if status === null}
			<p class="mt-6 text-sm text-stone-500">Loading…</p>
		{:else if status.needs_accept}
			<div class="subpanel mt-6 p-4">
				<label class="flex items-start gap-3 text-sm text-stone-300">
					<input
						bind:checked
						type="checkbox"
						class="mt-1 h-4 w-4 accent-amber-500"
						aria-describedby="accept-copy"
					/>
					<span id="accept-copy">
						I have read and accept the brnrd beta hosted-execution terms, including the
						yolo-exec risk and no-sandbox disclaimer.
					</span>
				</label>
				<div class="mt-4 flex flex-wrap items-center gap-3">
					<button
						type="button"
						class="cursor-pointer border border-amber-700 bg-amber-950/40 px-3 py-1.5 font-mono text-[11px] tracking-wide text-amber-100 uppercase hover:border-amber-500 disabled:cursor-not-allowed disabled:border-stone-800 disabled:text-stone-600"
						disabled={posting}
						onclick={acceptTerms}>{posting ? 'accepting…' : 'accept and continue'}</button
					>
					<span class="font-mono text-[11px] text-stone-600">next {nextUrl}</span>
				</div>
				{#if result}
					<p class={`mt-3 text-sm ${result.level === 'error' ? 'text-red-400' : 'text-amber-200'}`}>
						{result.message}
					</p>
				{/if}
			</div>
		{:else if status.authenticated}
			<p class="mt-6 text-sm text-stone-500">
				{#if status.accepted_at}
					Accepted {new Date(status.accepted_at).toLocaleString()}.
				{:else}
					Your account does not need a hosted-execution terms update.
				{/if}
			</p>
		{:else}
			<p class="mt-6 text-sm text-stone-400">
				Sign in to use hosted execution — <a
					class="text-sky-400 underline"
					href="/login?next=/terms"
					rel="external">log in</a
				>.
			</p>
		{/if}
	</section>
</div>
