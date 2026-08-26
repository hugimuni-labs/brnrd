<script lang="ts">
	import { onMount } from 'svelte';
	import { resolve } from '$app/paths';
	import { DOCS_URL, GITHUB_REPO } from '$lib/publicStats';
	import { isComplete as legalNoticeIsComplete } from '$lib/legalNotice';
	import { typeReveal } from '$lib/transitions';
	import CyberpalMark from '$lib/CyberpalMark.svelte';
	import HeroExchange from '$lib/HeroExchange.svelte';
	import ShelfIcon from '$lib/ShelfIcon.svelte';
	import {
		REACH_GROUPS,
		SHELLS,
		fetchDoorStatus,
		reachBadge,
		type DoorStatus
	} from '$lib/supportMatrix';
	import { fetchLoginContext, resolveSigninHref, type LoginContext } from '$lib/login';

	// The resident is the persistent coordinating identity above project
	// contexts and agent CLIs. brnrd.dev is an optional control plane around
	// the local engine, never a remote compute farm. Keep those layers distinct
	// in the page shape rather than asking paragraphs of copy to repair them.
	let doorStatuses = $state<Map<string, DoorStatus> | null>(null);
	let loginContext = $state<LoginContext | null>(null);
	const legalNoticeReady = legalNoticeIsComplete();

	onMount(async () => {
		const [doors, login] = await Promise.all([
			fetchDoorStatus(),
			fetchLoginContext(null).catch(() => null)
		]);
		doorStatuses = doors;
		loginContext = login;
	});

	let signinHref = $derived(resolveSigninHref(loginContext, resolve('/login')));
</script>

<div class="mx-auto max-w-4xl p-5 sm:p-6">
	<header
		class="ignite flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between sm:gap-6"
		style="--ignite-delay: 0ms"
	>
		<div class="shrink-0">
			<p class="font-mono text-3xl font-semibold tracking-tight text-amber-100">
				<CyberpalMark />
			</p>
			<p class="mt-1 hidden font-mono text-[11px] tracking-wide text-ink-quiet uppercase sm:block">
				drain local · route wisely
			</p>
		</div>
		<nav
			class="flex w-full flex-wrap items-center gap-x-4 gap-y-2 sm:w-auto sm:justify-end sm:pt-2"
		>
			<a
				href={DOCS_URL}
				rel="external"
				class="font-mono text-[11px] tracking-wide text-ink-quiet uppercase hover:text-stone-300"
				>docs</a
			>
			<a
				href={`https://github.com/${GITHUB_REPO}`}
				rel="external"
				class="font-mono text-[11px] tracking-wide text-ink-quiet uppercase hover:text-stone-300"
				>github</a
			>
			<a
				href={resolve('/pricing')}
				class="font-mono text-[11px] tracking-wide text-ink-quiet uppercase hover:text-stone-300"
				>pricing</a
			>
			<a
				href={signinHref}
				rel="external"
				class="ml-auto border border-amber-700 bg-amber-950/40 px-3 py-1.5 font-mono text-[11px] tracking-wide text-amber-200 uppercase hover:bg-amber-950/70 sm:ml-0"
				>sign in</a
			>
		</nav>
	</header>

	<section class="ignite mt-14 sm:mt-16" style="--ignite-delay: 140ms" aria-label="what brnrd is">
		<div class="max-w-3xl">
			<h1
				class="font-mono text-2xl font-semibold tracking-tight text-amber-100 sm:text-3xl"
				use:typeReveal={{ text: 'a resident, not a chatbot', delay: 180 }}
			>
				a resident, not a chatbot
			</h1>
			<p class="mt-4 max-w-2xl text-base leading-relaxed text-stone-300 sm:text-lg">
				Your coding agent, persistent between runs and reachable from anywhere.
			</p>
			<p class="mt-2 max-w-2xl text-sm leading-relaxed text-ink-quiet">
				brnrd keeps continuity around the agent CLI you already use: work comes in, context
				survives, and commits, pull requests, progress, and replies go back to the thread that
				asked.
			</p>

			<div class="mt-5 flex flex-wrap items-center gap-3">
				<a
					href="#install"
					class="border border-amber-700 bg-amber-950/40 px-3 py-2 font-mono text-[12px] tracking-wide text-amber-200 uppercase hover:bg-amber-950/70"
					>install brnrd</a
				>
				<a
					href={DOCS_URL}
					rel="external"
					class="border border-stone-700 px-3 py-2 font-mono text-[12px] tracking-wide text-stone-300 uppercase hover:border-stone-500"
					>read the docs</a
				>
			</div>
			<p class="mt-3 font-mono text-[10px] tracking-wide text-ink-mute uppercase">
				open source · local execution · no brnrd account required for the local engine
			</p>
		</div>

		<div class="mt-8 max-w-3xl">
			<HeroExchange />
		</div>
	</section>

	<section class="ignite mt-16 sm:mt-20" style="--ignite-delay: 260ms" aria-label="how brnrd fits">
		<p class="eyebrow">how it fits</p>
		<div class="mt-5">
			<div class="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
				{#each ['Telegram', 'WhatsApp', 'GitHub', 'Slack', 'Signal', 'Web'] as route (route)}
					<div
						class="border-t border-stone-800 px-1 pt-2 text-center font-mono text-[10px] tracking-wide text-ink-mute uppercase"
					>
						{route}
					</div>
				{/each}
			</div>

			<p class="my-4 text-center font-mono text-[10px] tracking-wide text-ink-mute uppercase">
				messages · issues · reviews · steering<br />↓
			</p>

			<div class="mx-auto max-w-xl border-y border-amber-900/60 py-5 sm:border sm:px-6">
				<p class="text-center font-mono text-[10px] tracking-[0.18em] text-amber-200/70 uppercase">
					brnrd resident
				</p>
				<h2 class="mt-2 text-center font-mono text-xl font-semibold tracking-tight text-amber-100">
					persistent identity · project-aware context
				</h2>
				<div
					class="mt-5 grid grid-cols-2 gap-x-5 gap-y-3 text-center font-mono text-[11px] text-stone-400 sm:grid-cols-4"
				>
					<span>memory</span>
					<span>routing</span>
					<span>steering</span>
					<span>git receipts</span>
				</div>
				<p class="mt-4 text-center font-mono text-[10px] tracking-wide text-ink-mute uppercase">
					persistent state · not a permanently-running model
				</p>
			</div>

			<p class="my-4 text-center font-mono text-[10px] tracking-wide text-ink-mute uppercase">
				↓ resolves where the work belongs · dispatches bounded work when useful
			</p>

			<div class="mx-auto grid max-w-xl grid-cols-1 gap-5 sm:grid-cols-2">
				<div class="border-t border-stone-800 pt-3 text-center">
					<p class="font-mono text-[10px] tracking-wide text-amber-200/70 uppercase">
						project context
					</p>
					<p class="mt-2 text-xs leading-relaxed text-stone-400">
						repo target · project knowledge · current bearing
					</p>
				</div>
				<div class="border-t border-stone-800 pt-3 text-center">
					<p class="font-mono text-[10px] tracking-wide text-amber-200/70 uppercase">strands</p>
					<p class="mt-2 text-xs leading-relaxed text-stone-400">
						bounded workers · isolated worktrees · return to resident
					</p>
				</div>
			</div>

			<p class="my-4 text-center font-mono text-[10px] tracking-wide text-ink-mute uppercase">
				↓ runs through the agent CLI already on your machine
			</p>

			<div class="flex flex-wrap items-center justify-center gap-5">
				{#each SHELLS as shell (shell.slug)}
					<div class="flex items-center gap-2.5">
						<ShelfIcon icon={shell.icon} />
						<span class="text-sm text-stone-300">{shell.label}</span>
					</div>
				{/each}
			</div>
			<p class="mt-4 text-center font-mono text-[10px] tracking-wide text-amber-200/60 uppercase">
				repository · worktree · tests · credentials
			</p>
		</div>
		<p class="mx-auto mt-6 max-w-2xl text-center text-sm leading-relaxed text-ink-quiet">
			The daemon stays on your machine. Routes bring work to the resident; it keeps continuity,
			resolves the project context, dispatches strands when useful, and sends durable results back
			out. brnrd is the layer connecting those pieces, not another model behind them.
		</p>
	</section>

	<section class="ignite mt-16 sm:mt-20" style="--ignite-delay: 340ms" aria-label="why a resident">
		<p class="eyebrow">why a resident</p>
		<div class="mt-5 max-w-2xl">
			<h2 class="font-mono text-xl font-semibold tracking-tight text-amber-100">
				it sleeps without forgetting
			</h2>
			<p class="mt-3 text-sm leading-relaxed text-stone-400">
				Persistence lives in durable state, not in keeping a model process warm. When work arrives,
				the resident wakes, resolves the project context, can dispatch bounded strands, and goes
				quiet again when the work is done.
			</p>
		</div>
		<div class="mt-7 grid grid-cols-1 gap-7 md:grid-cols-3">
			<article class="border-t border-stone-800 pt-3">
				<h2 class="font-mono text-base font-semibold text-amber-100">it remembers</h2>
				<p class="mt-2 text-sm leading-relaxed text-stone-400">
					Decisions, pitfalls, project knowledge, and unfinished bearings survive the run that
					created them. The next task does not start from zero.
				</p>
			</article>
			<article class="border-t border-stone-800 pt-3">
				<h2 class="font-mono text-base font-semibold text-amber-100">you can reach it</h2>
				<p class="mt-2 text-sm leading-relaxed text-stone-400">
					Send work while you are away from the terminal. Progress and answers return to the same
					place that asked for them.
				</p>
			</article>
			<article class="border-t border-stone-800 pt-3">
				<h2 class="font-mono text-base font-semibold text-amber-100">work leaves receipts</h2>
				<p class="mt-2 text-sm leading-relaxed text-stone-400">
					Branches, commits, pull requests, issues, and thread replies make the work inspectable
					instead of disappearing into another chat history.
				</p>
			</article>
		</div>
	</section>

	<section
		class="ignite mt-16 sm:mt-20"
		style="--ignite-delay: 420ms"
		aria-label="ways to reach the resident"
	>
		<p class="eyebrow">reach your resident</p>
		<h2 class="mt-2 font-mono text-xl font-semibold tracking-tight text-amber-100">
			same platforms, different topology
		</h2>
		<p class="mt-3 max-w-2xl text-sm leading-relaxed text-ink-quiet">
			Use a hosted identity, install brnrd where you already work, or bring your own local gate.
			GitHub and Slack are app-shaped integrations; the dashboard is control. The same resident sits
			behind each route.
		</p>

		<div class="mt-7 grid grid-cols-1 gap-x-8 gap-y-8 md:grid-cols-2">
			{#each REACH_GROUPS as group (group.slug)}
				<article class="border-t border-stone-800 pt-3">
					<div>
						<h3 class="font-mono text-[11px] tracking-wide text-amber-200/80 uppercase">
							{group.label}
						</h3>
						<p class="mt-1 text-xs leading-relaxed text-ink-quiet">{group.description}</p>
					</div>
					<ul class="mt-4 flex flex-col gap-4">
						{#each group.surfaces as surface (surface.id)}
							{@const badge = reachBadge(surface, doorStatuses)}
							<li class="flex items-start gap-3">
								<ShelfIcon icon={surface.icon} />
								<div class="min-w-0 flex-1">
									<div class="flex flex-wrap items-center gap-2">
										<span class="text-sm text-stone-300">{surface.label}</span>
										{#if badge === 'live'}
											<span class="font-mono text-[9px] tracking-wide text-amber-300 uppercase">
												<span class="mr-1" aria-hidden="true">●</span>live
											</span>
										{:else if badge === 'coming'}
											<span class="font-mono text-[9px] tracking-wide text-slate-400 uppercase">
												<span class="mr-1" aria-hidden="true">○</span>coming
											</span>
										{:else if badge === 'byo'}
											<span class="font-mono text-[9px] tracking-wide text-ink-mute uppercase">
												<span class="mr-1" aria-hidden="true">◇</span>BYO
											</span>
										{:else}
											<span class="font-mono text-[9px] tracking-wide text-ink-mute uppercase">
												<span class="mr-1" aria-hidden="true">·</span>checking…
											</span>
										{/if}
									</div>
									<p class="mt-1 text-xs leading-relaxed text-ink-quiet">{surface.detail}</p>
								</div>
							</li>
						{/each}
					</ul>
				</article>
			{/each}
		</div>
	</section>

	<section
		class="ignite mt-16 sm:mt-20"
		style="--ignite-delay: 500ms"
		aria-label="local and managed brnrd"
	>
		<p class="eyebrow">local at the core</p>
		<div class="mt-5 grid grid-cols-1 gap-8 md:grid-cols-[1.05fr_0.95fr] md:items-start">
			<div>
				<h2 class="font-mono text-xl font-semibold tracking-tight text-amber-100">
					the engine is yours
				</h2>
				<p class="mt-3 text-sm leading-relaxed text-stone-400">
					The full resident engine is open source. Install it, point it at a checkout, use your own
					Claude Code or Codex subscription, and wire local gates with credentials you control. No
					brnrd account, payment, phone-home, or feature gate is required for that path.
				</p>
				<p class="mt-3 text-xs leading-relaxed text-ink-quiet">
					Your source trees stay on your machine. The local engine runs where your repositories,
					shells, credentials, and tests already live.
				</p>
				<a
					class="mt-5 inline-flex items-center gap-2 border border-stone-700 px-3 py-2 font-mono text-[12px] tracking-wide text-stone-300 uppercase hover:border-stone-500"
					href={`https://github.com/${GITHUB_REPO}`}
					rel="external">read the source</a
				>
			</div>

			<div class="border-l border-amber-900/60 pl-5">
				<p class="font-mono text-[10px] tracking-wide text-amber-200/70 uppercase">
					optional layer
				</p>
				<h3 class="mt-1 font-mono text-lg font-semibold tracking-tight text-amber-100">
					brnrd.dev control plane
				</h3>
				<p class="mt-3 text-sm leading-relaxed text-stone-400">
					Pair that same local daemon to add hosted Telegram and WhatsApp identities, installable
					app ingress, and the dashboard from anywhere. The control plane routes and coordinates; it
					does not become the machine doing the coding.
				</p>
				<p class="mt-3 text-xs leading-relaxed text-ink-quiet">
					Execution stays local. Connecting an account mirrors derived project notes to brnrd.dev,
					never your source tree.
				</p>
				<a
					class="mt-5 inline-flex items-center gap-2 border border-amber-700 bg-amber-950/40 px-3 py-2 font-mono text-[12px] tracking-wide text-amber-200 uppercase hover:bg-amber-950/70"
					href={signinHref}
					rel="external">sign in with GitHub</a
				>
			</div>
		</div>
	</section>

	<section
		id="install"
		class="ignite mt-16 sm:mt-20"
		style="--ignite-delay: 560ms"
		aria-label="install brnrd"
	>
		<p class="eyebrow">install</p>
		<div class="mt-5 grid grid-cols-1 gap-6 md:grid-cols-[1fr_0.9fr] md:items-start">
			<div>
				<h2 class="font-mono text-xl font-semibold tracking-tight text-amber-100">
					install your resident
				</h2>
				<p class="mt-3 text-sm leading-relaxed text-stone-400">
					The npm package installs the same brnrd CLI as the uv and pipx routes. Run it from a
					repository to start; the guided setup picks up that project context and walks you through
					the rest.
				</p>
				<a
					class="mt-4 inline-flex font-mono text-[11px] tracking-wide text-sky-400 underline underline-offset-2"
					href={`${DOCS_URL}getting-started/install/`}
					rel="external">all install routes →</a
				>
			</div>
			<div class="panel p-4" aria-label="terminal install commands">
				<p class="font-mono text-[10px] tracking-wide text-ink-mute uppercase">terminal</p>
				<pre class="mt-3 overflow-x-auto font-mono text-sm leading-7 text-stone-300"><code
						><span class="text-ink-mute">$</span> npm install -g brnrd
<span class="text-ink-mute">$</span> brnrd</code
					></pre>
			</div>
		</div>
	</section>

	<footer class="ignite mt-16 border-t border-stone-800 pt-4" style="--ignite-delay: 620ms">
		<p class="font-mono text-[10px] text-ink-mute">
			open source · runs on your hardware ·
			<a class="hover:text-stone-300" href={resolve('/terms')}>terms</a>
			·
			<a class="hover:text-stone-300" href={resolve('/privacy')}>privacy</a>
			·
			{#if legalNoticeReady}
				<a class="hover:text-stone-300" href={resolve('/legal-notice')}>mentions légales</a>
				·
			{/if}
			<a
				class="hover:text-stone-300"
				href={`https://github.com/${GITHUB_REPO}/blob/main/SECURITY.md`}
				rel="external">security</a
			>
		</p>
	</footer>
</div>
