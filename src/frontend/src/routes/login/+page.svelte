<script lang="ts">
	import { onMount } from 'svelte';
	import { fetchLoginContext, type LoginContext } from '$lib/login';

	// #327 Jinja-removal, /login slice — the first page most new users see,
	// so it carries the brand honestly: lowercase `brnrd` wordmark (the old
	// template said `brnrd` too, but `.brand-name`'s text-transform shouted
	// it to BRNRD), no boxed sigil, and copy that says what the thing
	// actually does instead of a mocked-up preview frame. `next` validation
	// and the OAuth start URL stay backend-owned (login-context endpoint).
	let context = $state<LoginContext | null>(null);
	let error = $state<string | null>(null);

	onMount(async () => {
		const next = new URLSearchParams(window.location.search).get('next');
		try {
			context = await fetchLoginContext(next);
		} catch (e) {
			error = e instanceof Error ? e.message : 'login context fetch failed';
		}
	});

	function continueSignedIn() {
		// `next` can be backend-owned (e.g. /connect/BR-123), so a full
		// navigation, not the client router (same call the /terms port made).
		window.location.assign(context?.next ?? '/');
	}
</script>

<svelte:head><title>sign in · brnrd</title></svelte:head>

<div class="mx-auto max-w-4xl p-6">
	<div class="mt-6 grid grid-cols-1 items-start gap-8 md:mt-14 md:grid-cols-[minmax(0,1fr)_320px]">
		<section aria-label="what brnrd is">
			<p class="font-mono text-3xl font-semibold tracking-tight text-amber-100">brnrd</p>
			<p class="mt-1 font-mono text-[11px] tracking-wide text-stone-500 uppercase">
				drain local · route wisely
			</p>

			<p class="mt-6 max-w-md text-sm leading-relaxed text-stone-400">
				brnrd is the control plane for <span class="font-mono text-stone-300">brr</span> — resident
				coding agents that live with your repositories. Work arrives from GitHub and Telegram, a
				daemon on your own machine runs it, and the results come back as commits, pull requests,
				and replies.
			</p>

			<div class="mt-6 grid max-w-md grid-cols-1 gap-2">
				<div class="subpanel p-3">
					<p class="font-mono text-[10px] tracking-wide text-amber-200/80 uppercase">pair</p>
					<p class="mt-1 text-sm text-stone-400">
						Enable GitHub repositories and Telegram chats, then pair a local daemon from a
						checkout.
					</p>
				</div>
				<div class="subpanel p-3">
					<p class="font-mono text-[10px] tracking-wide text-amber-200/80 uppercase">route</p>
					<p class="mt-1 text-sm text-stone-400">
						Issues, review requests, and messages become runs — executed on your hardware, paced
						by your quotas.
					</p>
				</div>
				<div class="subpanel p-3">
					<p class="font-mono text-[10px] tracking-wide text-amber-200/80 uppercase">receipts</p>
					<p class="mt-1 text-sm text-stone-400">
						Every run leaves something auditable: a commit, a pull request, a reply on the thread
						that asked.
					</p>
				</div>
			</div>
		</section>

		<section class="panel p-4" aria-label="GitHub sign in">
			<p class="eyebrow">github identity</p>
			<h1 class="mt-1 font-mono text-lg font-semibold tracking-tight text-amber-100">sign in</h1>
			<p class="mt-2 text-sm text-stone-400">
				brnrd uses GitHub for account identity. Daemon and API access stay scoped to brnrd tokens.
			</p>

			{#if error}
				<p class="mt-4 text-sm text-red-400">{error}</p>
			{:else if context === null}
				<p class="mt-4 text-sm text-stone-500">Loading…</p>
			{:else if context.authenticated}
				<button
					type="button"
					class="mt-4 inline-flex w-full cursor-pointer items-center justify-center gap-2 border border-amber-700 bg-amber-950/40 px-3 py-2 font-mono text-[12px] tracking-wide text-amber-200 uppercase hover:bg-amber-950/70"
					onclick={continueSignedIn}>already signed in — continue</button
				>
			{:else if context.oauth_ready}
				<a
					class="mt-4 inline-flex w-full items-center justify-center gap-2 border border-amber-700 bg-amber-950/40 px-3 py-2 font-mono text-[12px] tracking-wide text-amber-200 uppercase hover:bg-amber-950/70"
					href={context.signin_url}
					rel="external"
				>
					<svg class="h-4 w-4 fill-current" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
						<path
							d="M12 .5C5.65.5.5 5.65.5 12c0 5.1 3.29 9.43 7.86 10.96.58.1.79-.25.79-.56v-2.02c-3.2.7-3.87-1.36-3.87-1.36-.53-1.33-1.28-1.68-1.28-1.68-1.04-.71.08-.7.08-.7 1.15.08 1.76 1.18 1.76 1.18 1.03 1.76 2.7 1.25 3.35.96.1-.74.4-1.25.73-1.54-2.55-.29-5.23-1.28-5.23-5.69 0-1.26.45-2.28 1.18-3.08-.12-.29-.51-1.46.11-3.04 0 0 .96-.31 3.15 1.18A10.9 10.9 0 0 1 12 6.22c.97 0 1.94.13 2.86.39 2.19-1.49 3.15-1.18 3.15-1.18.62 1.58.23 2.75.11 3.04.74.8 1.18 1.82 1.18 3.08 0 4.42-2.69 5.39-5.25 5.68.41.36.78 1.06.78 2.14v3.03c0 .31.21.67.8.56A11.5 11.5 0 0 0 23.5 12C23.5 5.65 18.35.5 12 .5Z"
						></path>
					</svg>
					<span>sign in with GitHub</span>
				</a>
				<p class="mt-3 text-xs text-stone-500">
					New accounts continue through the
					<a class="text-sky-400 underline" href="/terms">brnrd beta hosted-execution terms</a>
					before using the dashboard.
				</p>
			{:else}
				<button
					type="button"
					class="mt-4 w-full cursor-not-allowed border border-stone-800 px-3 py-2 font-mono text-[12px] tracking-wide text-stone-500 uppercase"
					disabled>GitHub login unavailable</button
				>
				<p class="mt-3 text-xs text-stone-500">
					OAuth client settings are missing on this brnrd server.
				</p>
			{/if}
		</section>
	</div>
</div>
