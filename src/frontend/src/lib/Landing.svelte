<script lang="ts">
	import { resolve } from '$app/paths';
	import { DOCS_URL, GITHUB_REPO } from '$lib/publicStats';
	import { isComplete as legalNoticeIsComplete } from '$lib/legalNotice';
	import { typeReveal } from '$lib/transitions';
	import WinkWordmark from '$lib/WinkWordmark.svelte';
	import ShelfIcon from '$lib/ShelfIcon.svelte';
	import {
		DOORS,
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

	// `/` draws this page while it is still probing for a board (routes/+page.svelte), so its fetches wait for
	// `live`: a reader whose board answers never touches the app's endpoints.
	let { live = true }: { live?: boolean } = $props();
	let fetched = false;
	$effect(() => {
		if (!live || fetched) return;
		fetched = true;
		Promise.all([fetchDoorStatus(), fetchLoginContext(null).catch(() => null)]).then(
			([doors, login]) => {
				doorStatuses = doors;
				loginContext = login;
			}
		);
	});

	let signinHref = $derived(resolveSigninHref(loginContext, resolve('/login')));

	// Arseni's call (letter 5257): the returned pull request is the hook.
	const HERO_HEADLINE = 'Message it the work. Get back a pull request.';
	// Served by the app from docs/public/install.sh (spa.py, mount_installer).
	const INSTALL_LINE = 'curl -fsSL https://brnrd.dev/install | sh';

	interface DeckStep {
		index: string;
		verb: string;
		body: string;
		readout: string;
	}
	const DECK_STEPS: DeckStep[] = [
		{
			index: '01',
			verb: 'assign',
			body: 'Write a letter from Telegram, WhatsApp, or a GitHub issue. It lands on a thread: one topic, with its own Plan.',
			readout: 'letter → thread'
		},
		{
			index: '02',
			verb: 'work',
			body: 'A strand takes the thread: your agent CLI, in its own room with the repo checked out. It works the Plan and checks its work.',
			readout: 'strand · plan → change → test'
		},
		{
			index: '03',
			verb: 'receipt',
			body: 'The receipt comes back to the thread. Inspect the diff, answer on the thread, or write the next letter; the next strand starts from what this one wrote down.',
			readout: 'out: pull request · commit · reply'
		}
	];
</script>

<div class="mx-auto max-w-4xl p-5 sm:p-6">
	<header
		class="ignite flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between sm:gap-6"
		style="--ignite-delay: 0ms"
	>
		<div class="shrink-0">
			<p class="font-mono text-3xl font-semibold tracking-tight text-amber-100">
				<WinkWordmark />
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

	<section
		class="ignite mt-14 sm:mt-16"
		style="--ignite-delay: 140ms"
		aria-label="what brnrd delivers"
	>
		<div class="max-w-3xl">
			<p class="eyebrow">the resident engine</p>
			<h1
				class="mt-3 font-mono text-4xl leading-[1.08] font-semibold tracking-tight text-amber-100 sm:text-5xl lg:text-6xl"
				use:typeReveal={{ text: HERO_HEADLINE, delay: 180 }}
			>
				{HERO_HEADLINE}
			</h1>
			<p class="mt-5 max-w-2xl text-base leading-relaxed text-stone-300 sm:text-lg">
				Write to brnrd from Telegram, WhatsApp, Slack, Signal, or GitHub. A resident on your machine
				keeps a thread per topic, works each one with the agent CLI you already pay for, and lands
				every change as a commit or a pull request you can check.
			</p>

			<div class="mt-6 flex flex-col gap-3 sm:flex-row sm:items-center">
				<pre
					class="panel overflow-x-auto px-4 py-2.5 font-mono text-sm text-stone-200"
					aria-label="install brnrd"><code
						><span class="text-ink-mute select-none">$ </span>{INSTALL_LINE}</code
					></pre>
				<a
					href={signinHref}
					rel="external"
					class="border border-amber-700 bg-amber-950/40 px-3 py-2 text-center font-mono text-[12px] tracking-wide text-amber-200 uppercase hover:bg-amber-950/70"
					>sign in</a
				>
			</div>
		</div>

		<div class="mt-10">
			<h2 class="font-mono text-xl font-semibold tracking-tight text-amber-100">
				brnrd's board. Your window.
			</h2>
			<p class="mt-1 text-sm text-stone-400">You don't maintain the tasks. brnrd does.</p>
		</div>
		<figure class="mt-4">
			<img
				src="/board.jpg"
				width="1440"
				height="745"
				alt="brnrd's own board: one column per thread, each showing what its strand is doing now"
				class="panel h-auto w-full"
			/>
			<figcaption class="mt-2 font-mono text-[10px] tracking-wide text-ink-mute uppercase">
				brnrd's own board, building brnrd
			</figcaption>
		</figure>
	</section>

	<section
		class="ignite mt-16 sm:mt-20"
		style="--ignite-delay: 200ms"
		aria-label="how work moves through brnrd"
	>
		<p class="eyebrow">how work moves</p>
		<div class="mt-5 grid grid-cols-1 gap-4 sm:grid-cols-3">
			{#each DECK_STEPS as step (step.index)}
				<div class="panel flex flex-col gap-3 p-4">
					<div class="flex items-baseline justify-between">
						<span class="font-mono text-[11px] tracking-[0.2em] text-amber-200/70"
							>{step.index}</span
						>
						<span class="font-mono text-sm font-semibold tracking-wide text-amber-100 uppercase"
							>{step.verb}</span
						>
					</div>
					<p class="text-xs leading-relaxed text-stone-400">{step.body}</p>
					<p
						class="subpanel mt-auto border-l-2 border-l-amber-700/60 px-2.5 py-1.5 font-mono text-[11px] break-words text-stone-300"
					>
						{step.readout}
					</p>
				</div>
			{/each}
		</div>
	</section>

	<section
		class="ignite mt-16 sm:mt-20"
		style="--ignite-delay: 260ms"
		aria-label="one mind for all your work"
	>
		<div class="grid grid-cols-1 gap-8 md:grid-cols-[1.2fr_1fr]">
			<div>
				<h2 class="font-mono text-xl font-semibold tracking-tight text-amber-100">
					One persistent mind for all your work.
				</h2>
				<p class="mt-3 text-sm leading-relaxed text-stone-300">
					Message brnrd from anywhere. It keeps its own threads moving, uses the AI tools you
					already pay for, and gives you a window into everything it's doing, waiting on, and
					finished.
				</p>
				<p class="mt-3 text-sm leading-relaxed text-ink-quiet">
					The loom runs on your machine, with your repos and your credentials. Its memory is a git
					repository you can read.
				</p>
			</div>
			<table class="w-full text-sm" aria-label="what brnrd works with">
				<tbody>
					<tr class="border-b border-stone-800">
						<th
							class="py-2 pr-4 text-left align-top font-mono text-[10px] font-normal tracking-wide whitespace-nowrap text-ink-mute uppercase"
							>write from</th
						>
						<td class="py-2 text-stone-300">{DOORS.map((d) => d.label).join(' · ')}</td>
					</tr>
					<tr class="border-b border-stone-800">
						<th
							class="py-2 pr-4 text-left align-top font-mono text-[10px] font-normal tracking-wide whitespace-nowrap text-ink-mute uppercase"
							>works with</th
						>
						<td class="py-2">
							<span class="flex flex-wrap gap-x-4 gap-y-2">
								{#each SHELLS as shell (shell.slug)}
									<span class="flex items-center gap-2 text-stone-300">
										<ShelfIcon icon={shell.icon} />{shell.label}
									</span>
								{/each}
							</span>
						</td>
					</tr>
					<tr>
						<th
							class="py-2 pr-4 text-left align-top font-mono text-[10px] font-normal tracking-wide whitespace-nowrap text-ink-mute uppercase"
							>runs on</th
						>
						<td class="py-2 text-stone-300">macOS · Linux</td>
					</tr>
				</tbody>
			</table>
		</div>
	</section>

	<section class="ignite mt-16 sm:mt-20" style="--ignite-delay: 340ms" aria-label="why a resident">
		<p class="eyebrow">why a resident</p>
		<div class="mt-5 max-w-2xl">
			<h2 class="font-mono text-xl font-semibold tracking-tight text-amber-100">
				it sleeps without forgetting
			</h2>
			<p class="mt-3 text-sm leading-relaxed text-stone-400">
				Persistence lives in durable state, not in keeping a model process warm. When a letter
				arrives, a strand wakes on its thread, reads what the last one wrote down, and ends when the
				work is done. The thread stays.
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
			Telegram, WhatsApp, and GitHub bring letters to the loom today; the dashboard is control. The
			same resident sits behind each of them.
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
					Claude Code, Codex, Grok, or Mistral Vibe subscription, and wire local gates with
					credentials you control. No brnrd account, payment, phone-home, or feature gate is
					required for that path.
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
					Connect that same loom to add hosted Telegram and WhatsApp identities, GitHub App ingress,
					and the dashboard from anywhere. The control plane relays letters; it does not become the
					machine doing the coding.
				</p>
				<p class="mt-3 text-xs leading-relaxed text-ink-quiet">
					Execution stays local. The loom exchanges letters and replies with brnrd.dev, never your
					source tree.
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
					One line puts the brnrd CLI on your PATH; the guided setup walks you through the rest. Run
					it from a repository and setup picks up that project.
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
						><span class="text-ink-mute">$</span> {INSTALL_LINE}
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
