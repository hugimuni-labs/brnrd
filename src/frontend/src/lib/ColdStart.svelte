<script lang="ts">
	import { resolve } from '$app/paths';
	import { DOCS_URL } from './publicStats';
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
	// non-zero, taking step 03 with it at the exact moment pairing the
	// daemon was the only thing left — the maintainer's own "connected but
	// not connected", reborn inside the component built to answer it.
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
	let repoEnabled = $derived((repos?.length ?? 0) > 0);
	// "Paired" here means the one-time setup act completed, not "currently
	// online" — `daemon_status` is `missing` only until step 03's own
	// pairing command first registers this repo's daemon; after that it
	// reads `online` or `offline` depending on whether it is heartbeating
	// *right now*. This is a setup checklist, not a live health monitor (that job
	// belongs to the daemon-status dot elsewhere on the page) — a laptop
	// that's asleep must not resurrect "nothing is paired yet" for an
	// account that has already done this step once.
	let daemonEverPaired = $derived((repos ?? []).some((r) => r.daemon_status !== 'missing'));

	// The block survives until the last *observable* step is done. That is
	// daemon-pairing, not repo-enablement — an enabled repo with no daemon
	// is precisely the state the old `repos.length === 0` check hid. (Chat
	// gate / Telegram pairing is deliberately not part of this gate: it is
	// optional infrastructure — self-hosted gates and local execution are
	// the standing free path — not a requirement for a working daemon.)
	let cold = $derived(repos !== null && !daemonEverPaired);

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
			This board reads a daemon running on your own machine. There is none yet — three steps, in
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
					<span class="text-amber-200/80">02</span> enable a repository
					{#if repoEnabled}
						<span class="text-emerald-400">— done</span>
					{/if}
				</p>
				{#if repoEnabled}
					<!-- Marked done, not hidden: the block is still up because 03
					     isn't, and a reader re-reading the ladder should see 02 as
					     settled rather than wonder if it still applies. -->
					<p class="mt-1.5 text-sm text-stone-400">
						At least one repository is enabled. Enable another, or change what's enabled, on the
						<a href={resolve('/repos')} class="text-sky-400 underline hover:text-sky-300"
							>repos screen</a
						>.
					</p>
				{:else if appInstalled}
					<!-- The other half of the reported bug: an account that has
					     already installed the App must not be told to install it
					     again — that was step 05 of the trace ("connected but not
					     connected"), one level up. -->
					<p class="mt-1.5 text-sm text-stone-400">
						The brnrd GitHub App is installed. Enable the repository on a separate screen. Nothing
						on this board fills in until you do.
					</p>
					<a
						href={resolve('/repos')}
						class="mt-2 inline-flex items-center border border-amber-700 bg-amber-950/40 px-3 py-1.5 font-mono text-[11px] tracking-wide text-amber-100 uppercase hover:border-amber-500"
						>enable a repository</a
					>
				{:else}
					<!-- Named as the reported confusion, in the first clause: "the
					     actual repo enablement is the repos screen (another page)".
					     Say that it is another page rather than let the reader
					     discover it. -->
					<p class="mt-1.5 text-sm text-stone-400">
						A separate screen. Install the brnrd GitHub App where the repository lives, then enable
						the repository there. Nothing on this board fills in until you do.
					</p>
					<a
						href={resolve('/repos')}
						class="mt-2 inline-flex items-center border border-amber-700 bg-amber-950/40 px-3 py-1.5 font-mono text-[11px] tracking-wide text-amber-100 uppercase hover:border-amber-500"
						>enable a repository</a
					>
				{/if}
			</li>

			<li>
				<p class="font-mono text-[11px] tracking-wide text-ink-quiet uppercase">
					<span class="text-amber-200/80">03</span> pair the daemon
				</p>
				{#if pairCommand}
					<!-- Wrapped, not scrolled (driven on a 390px phone, 2026-08-03):
					     `overflow-x-auto` clipped the middle line to "brnrd account
					     connect https://brnrd.de" with no visible tell, which is a
					     plausible-looking wrong domain on the one command that has
					     to be right. A soft wrap keeps every character on screen and
					     the copy button hands over the real string regardless. -->
					<div class="mt-1.5 flex items-start gap-2">
						<pre
							class="min-w-0 grow border border-stone-800 bg-stone-950/50 p-2 font-mono text-[11px] wrap-anywhere whitespace-pre-wrap text-stone-300"><code
								>{pairCommand}</code
							></pre>
						<button
							type="button"
							class="shrink-0 cursor-pointer border border-stone-800 px-2 py-2 font-mono text-[10px] tracking-wide text-ink-quiet uppercase hover:text-stone-300"
							onclick={() => copy('pair', pairCommand ?? '')}
							>{copied === 'pair' ? 'copied' : 'copy'}</button
						>
					</div>
				{/if}
				<p class="mt-1.5 text-sm text-stone-400">
					In the checkout, after 02. It prints a link back here to approve, and this board starts
					reading the daemon. Execution never leaves your machine.
				</p>
			</li>
		</ol>

		<p class="mt-4 border-t border-stone-800 pt-3 font-mono text-[11px] text-ink-mute">
			Self-hosting, gates, and the agent CLIs brnrd drives —
			<a class="text-sky-400 underline hover:text-sky-300" href={DOCS_URL} rel="external">docs</a>.
		</p>
	</section>
{/if}
