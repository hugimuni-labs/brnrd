<script lang="ts">
	import { resolve } from '$app/paths';
	import { DOCS_URL } from './publicStats';
	import type { ConnectedRepo } from './repos';

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
	// therefore below the horizon. It is not a tour and it does not track
	// progress — nothing here can observe whether the CLI is installed, and
	// a checkmark that guesses would be a worse lie than no checkmark.
	interface Props {
		// `null` = the repos fetch hasn't landed. Render nothing rather than
		// flashing a cold start at an account that has fifteen repos: the
		// same source the rest of the page gates on, never a second notion
		// of "empty".
		repos: ConnectedRepo[] | null;
		// Backend-owned, from `GET /v1/dashboard/repos` — the same spelling
		// every connected repo carries as `setup_command`, with `<repo>` in
		// place of a checkout name (`_session.pairing_command`). Not retyped
		// here: two copies of one command line drift apart the first time the
		// CLI renames a verb.
		pairCommand?: string | null;
	}

	let { repos, pairCommand = null }: Props = $props();

	const INSTALL_COMMAND = 'npm install -g brnrd';

	let cold = $derived(repos !== null && repos.length === 0);

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
				</p>
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
