<script lang="ts">
	import { resolve } from '$app/paths';
	import { DOCS_URL } from './publicStats';
	import { splitPairingCommand } from './repos';
	import type { ConnectedRepo, GitHubInstallation } from './repos';

	// The cold start (2026-08-03). Reported from a real signup on the
	// deployed dashboard: "two screens - no clarity on the installation, or
	// what is missing, the actual repo enablement is the repos screen
	// (another page)… and no docs link or like install it like this line or
	// anything clear you know?"
	//
	// He was right, and the page proved it: an account with nothing
	// connected rendered six sections, every one of them an empty state —
	// "nothing armed", "the warp is bare", "No corpus mirrored yet" — and
	// not one of them naming the CLI that has to exist first or the page
	// where a repo gets enabled. Six correct answers to questions a new
	// reader has not earned yet.
	//
	// So: the three things that actually have to happen, in order, directly
	// under the h1, because for this reader everything below it is empty and
	// therefore below the horizon. It is not a tour, and step 01 does not
	// track progress — nothing here can observe whether the CLI is
	// installed, and a checkmark that guesses would be a worse lie than no
	// checkmark. Steps 02 and 03 are different: App-installed, repo-enabled,
	// and daemon-paired are all facts already on the wire (#1084's second
	// trace — "the repos are not detected/synced… on a new account"), and a
	// step whose completion is a fact gets marked done. The regression that
	// forced this: the block used to leave the instant `repos.length` went
	// non-zero, taking the pairing step with it at the exact moment pairing
	// the daemon was the only thing left — the maintainer's own "connected
	// but not connected", reborn inside the component built to answer it.
	//
	// Rung order (2026-08-05, `brr/one-sequence-two-surfaces`): this used to
	// read install → enable a repository → pair the daemon, while `/repos`
	// (the page step 02 sent the reader to) opens with run the pairing
	// command, *then* install the GitHub App — the opposite dependency
	// graph on the very next screen. design-onboarding-ladder.md's Direction
	// A settles which one is wrong: the checkout already knows which
	// repository you mean, the web does not, so the terminal drives — the
	// account-connect command below is both sign-in and pairing — and the
	// GitHub App install is the single web consent that follows — never a
	// prerequisite to it. `pairing_command` (`_session.py`) already builds
	// with a `<repo>` placeholder and needs no enabled repo to run, so
	// nothing here waits on the App.
	//
	// Second trace, 2026-08-08 (design-onboarding-second-trace.md, #1243):
	// step 03 still rendered the pre-Direction-A "install the App, then
	// enable the repository" copy — the repo had already bound at pairing,
	// so that instruction described a rung that no longer exists, and the
	// one rung that does exist here (`brnrd init` — `daemon.start` hard-
	// exits without it, #1238) appeared on no web surface at all. Step 03
	// is now that command; the App becomes what it actually is, an optional
	// identity upgrade named once in the footer, never a gate. ColdStart
	// now matches Direction A: install → pair → init.
	//
	// Third trace, 2026-08-14 (the iMac onboarding): `brnrd init` was
	// retired into the bare-`brnrd` front door (decision-retire-init.md,
	// 08-10) and this board kept teaching the dead verb for four days —
	// the maintainer's own fresh-machine run read the wall, typed the old
	// spell, and met the new flow only by accident. Two steps now:
	// install, then `brnrd` in the checkout — the guided door pairs,
	// names your doors, and queues the first run, resuming from whatever
	// rung stands. The command box still renders the *served* string
	// (`pairing_command`, `_session.py`) so a CLI rename reaches here
	// without a frontend deploy — the exact drift this trace measured,
	// closed at its cause.
	//
	// Named, not yet closed (same trace): this block gates on repo-scoped
	// `daemon_status` only, so an account-level paired machine with no
	// enabled repo reads "nothing is paired yet" while the capabilities
	// board below lists the very machine. Needs machines on this
	// component's wire — filed with the trace, not hacked around here.
	interface Props {
		// `null` = the repos fetch hasn't landed. Render nothing rather than
		// flashing a cold start at an account that has fifteen repos: the
		// same source the rest of the page gates on, never a second notion
		// of "empty".
		repos: ConnectedRepo[] | null;
		// GitHub App installations for this account, from the same fetch.
		// `null`/`undefined` reads as "unknown, don't claim installed" —
		// distinct from `[]` ("checked, none installed").
		installations?: GitHubInstallation[] | null;
		// Backend-owned, from `GET /v1/dashboard/repos` — the same spelling
		// every connected repo carries as `setup_command`, with `<repo>` in
		// place of a checkout name (`_session.pairing_command`). Not retyped
		// here: two copies of one command line drift apart the first time the
		// CLI renames a verb.
		pairCommand?: string | null;
	}

	let { repos, installations = null, pairCommand = null }: Props = $props();

	const INSTALL_COMMAND = 'npm install -g brnrd';

	let appInstalled = $derived((installations?.length ?? 0) > 0);
	// "Paired" here means the one-time setup act completed, not "currently
	// online" — `daemon_status` is `missing` only until step 02's own
	// pairing command first registers this repo's daemon; after that it
	// reads `online`, `offline`, or `never_started` depending on whether
	// it is heartbeating *right now* (#1243: `never_started` is a daemon
	// that registered and never completed a publish cycle — still very
	// much paired, just crash-looping — so it belongs in this set exactly
	// like `offline` does; leaving it out would resurrect the vanishing-
	// ladder regression this gate exists to prevent, for the one account
	// shape the 08-08 trace actually hit). This is a setup checklist, not a
	// live health monitor (that job belongs to the daemon-status dot
	// elsewhere on the page) — a laptop that's asleep must not resurrect
	// "nothing is paired yet" for an account that has already done this
	// step once.
	let daemonEverPaired = $derived(
		(repos ?? []).some(
			(r) =>
				r.daemon_status === 'online' ||
				r.daemon_status === 'offline' ||
				r.daemon_status === 'never_started'
		)
	);

	// The block survives until the last *observable* step is done. That is
	// daemon-pairing, not repo-enablement — an enabled repo with no daemon
	// is precisely the state the old `repos.length === 0` check hid. (Chat
	// gate / Telegram pairing is deliberately not part of this gate: it is
	// optional infrastructure — self-hosted gates and local execution are
	// the standing free path — not a requirement for a working daemon.)
	let cold = $derived(repos !== null && !daemonEverPaired);

	// #1277a: the pairing command's first line is `cd <repo>` before any
	// checkout is known — a literal placeholder no shell can run — handed
	// over verbatim by the COPY button along with the runnable line beneath
	// it. Split so the box only ever holds, and only ever copies, the line
	// that is unconditionally runnable; the `cd` step becomes prose above it.
	let pairParts = $derived(pairCommand ? splitPairingCommand(pairCommand) : null);

	let copied = $state<string | null>(null);
	let copyTimer: ReturnType<typeof setTimeout> | undefined;

	async function copy(key: string, text: string) {
		try {
			await navigator.clipboard.writeText(text);
			copied = key;
			clearTimeout(copyTimer);
			copyTimer = setTimeout(() => (copied = null), 1500);
		} catch {
			// Clipboard unavailable or denied — no crash, just no flash.
			// The command is still there to select by hand.
		}
	}
</script>

{#if cold}
	<section
		class="panel ignite mt-4 p-4"
		style="--ignite-delay: 60ms"
		aria-labelledby="cold-heading"
	>
		<p class="eyebrow">the cold start</p>
		<h2 id="cold-heading" class="font-mono text-sm font-semibold text-amber-100">
			nothing is paired yet
		</h2>
		<p class="mt-2 text-sm text-stone-400">
			This board reads a daemon running on your own machine. There is none yet — two steps, in
			order.
		</p>

		<ol class="mt-4 flex flex-col gap-4">
			<li>
				<p class="font-mono text-[11px] tracking-wide text-ink-quiet uppercase">
					<span class="text-amber-200/80">01</span> install the cli
				</p>
				<div class="mt-1.5 flex items-start gap-2">
					<pre
						class="min-w-0 grow border border-stone-800 bg-stone-950/50 p-2 font-mono text-[11px] wrap-anywhere whitespace-pre-wrap text-stone-300"><code
							>{INSTALL_COMMAND}</code
						></pre>
					<button
						type="button"
						class="shrink-0 cursor-pointer border border-stone-800 px-2 py-2 font-mono text-[10px] tracking-wide text-ink-quiet uppercase hover:text-stone-300"
						onclick={() => copy('install', INSTALL_COMMAND)}
						>{copied === 'install' ? 'copied' : 'copy'}</button
					>
				</div>
				<p class="mt-1 font-mono text-[11px] text-ink-mute">
					or <code class="text-stone-400">uv tool install brnrd</code> ·
					<code class="text-stone-400">pipx install brnrd</code>
				</p>
			</li>

			<li>
				<p class="font-mono text-[11px] tracking-wide text-ink-quiet uppercase">
					<span class="text-amber-200/80">02</span> run <code>brnrd</code> — the guided setup
				</p>
				{#if pairCommand}
					{#if pairParts?.setupLine}
						<!-- #1277a: scene-setting, not copyable — the box below hands
						     over only the line that is unconditionally runnable. -->
						<p class="mt-1.5 font-mono text-[11px] text-ink-mute">from your repo checkout:</p>
					{/if}
					<!-- Wrapped, not scrolled (driven on a 390px phone, 2026-08-03):
					     `overflow-x-auto` clipped the middle line to "brnrd account
					     connect https://brnrd.de" with no visible tell, which is a
					     plausible-looking wrong domain on the one command that has
					     to be right. A soft wrap keeps every character on screen and
					     the copy button hands over the real string regardless. -->
					<div class="mt-1.5 flex items-start gap-2">
						<pre
							class="min-w-0 grow border border-stone-800 bg-stone-950/50 p-2 font-mono text-[11px] wrap-anywhere whitespace-pre-wrap text-stone-300"><code
								>{pairParts?.runnable ?? pairCommand}</code
							></pre>
						<button
							type="button"
							class="shrink-0 cursor-pointer border border-stone-800 px-2 py-2 font-mono text-[10px] tracking-wide text-ink-quiet uppercase hover:text-stone-300"
							onclick={() => copy('pair', pairParts?.runnable ?? pairCommand ?? '')}
							>{copied === 'pair' ? 'copied' : 'copy'}</button
						>
					</div>
				{/if}
				<p class="mt-1.5 text-sm text-stone-400">
					In the checkout, after 01. One word, narrated: it pairs this machine (printing a link back
					here to approve), names your doors, and queues the first run — the one that writes your
					repo's <code>AGENTS.md</code>. Re-run it any time; it resumes from whatever step is
					standing. Execution never leaves your machine.
				</p>
			</li>
		</ol>

		<p class="mt-4 border-t border-stone-800 pt-3 font-mono text-[11px] text-ink-mute">
			{#if appInstalled}
				GitHub App installed — commits and comments already post as <code>brnrd-dev[bot]</code>, not
				your own identity. Manage it on the
				<a class="text-sky-400 underline hover:text-sky-300" href={resolve('/repos')}
					>repos screen</a
				>.
			{:else}
				Optional: install the GitHub App so commits and comments post as
				<code>brnrd-dev[bot]</code> instead of your own identity — one click to revoke, any time, on
				the
				<a class="text-sky-400 underline hover:text-sky-300" href={resolve('/repos')}
					>repos screen</a
				>. Nothing here waits on it.
			{/if}
		</p>

		<p class="mt-2 font-mono text-[11px] text-ink-mute">
			Self-hosting, gates, and the agent CLIs brnrd drives —
			<a class="text-sky-400 underline hover:text-sky-300" href={DOCS_URL} rel="external">docs</a>.
		</p>
	</section>
{/if}
