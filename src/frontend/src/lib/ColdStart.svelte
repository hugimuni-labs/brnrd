<script lang="ts">
	import { resolve } from '$app/paths';
	import MessengerDoors from './MessengerDoors.svelte';
	import { DOCS_URL } from './publicStats';
	import { splitPairingCommand } from './repos';
	import type { ConnectedRepo, GitHubInstallation, MachinesSummary, MessengerDoor } from './repos';
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
	// Fourth trace, 2026-08-16 (design-machines-and-guests.md R1, #1365):
	// closes the gap the third trace named but didn't fix. `machines` below
	// is the account-level presence `GET /v1/dashboard/repos` now carries
	// (`_session._machine_views`, additive) — a paired-but-repo-less
	// machine no longer reads as the classic "nothing is paired yet": it
	// gets its own honest middle state, `pairedNoRepo` below, instead of
	// either vanishing (the bug) or claiming a repo is enabled (a lie).
	//
	// Fifth trace, 2026-08-17 (origin-aware onboarding, the dispatched
	// task's own framing): a visitor arriving on a phone cannot run either
	// step below — no terminal exists there. The spec's own prior was "lead
	// the mobile CTA with the messenger door" (Telegram/WhatsApp), argued
	// down here against what the server actually offered *that day*:
	// `TgPairCode.repo_id` was required (`models.py`), both
	// `telegram_pair_core` and `_pair_repo_telegram_core`
	// (`routers/pairing.py`, `routers/repo_actions.py`) 404'd without an
	// already-connected `Repo`, and `settings.telegram_bot_username` rode
	// no wire payload — so this component could not construct even a bare
	// `t.me/<bot>` link on its own, let alone a working `?start=` deep
	// link, for an account with no repo yet. So the mobile CTA stated the
	// honest intermediate instead of a link with nothing behind it.
	//
	// Same day, later (#1457, "the link becomes constructible"): both gaps
	// closed. `telegram_pair_core` now mints an *account-level* code
	// (`repo_id=None` — the chat binds to the account, which project
	// answers is resolved per message) via `POST /v1/dashboard/telegram-pair`,
	// and `telegram_bot_username` rides `GET /v1/dashboard/repos` (`""` =
	// unset or shape-invalid, same "absent means unknown" contract
	// `machines` already set). The prior's premise is gone, so the mobile
	// CTA flips to the real door.
	//
	// #1465 ("every door declares itself"): generalized from the Telegram
	// special case above. The prop is now `messengerDoors`, the registry-
	// derived connector set `GET /v1/dashboard/repos` carries — every
	// declared platform with its own `deep_link_available` flag, WhatsApp
	// included once its Cloud API phone-number lookup resolves. `#1457`'s
	// framing named the actual gap this closes: WhatsApp was promised in
	// copy ("brnrd talks back in Telegram or WhatsApp…") with no `wa.me`
	// door behind it anywhere in the tree. A door with
	// `deep_link_available: true` ⇒ a tappable button, one per available
	// door, mints on tap (never pre-minted — codes expire in ~600s) via
	// `POST /v1/dashboard/pair` and navigates to the returned `deep_link`;
	// a failed mint or a `null` deep_link falls back to the returned
	// `pair_code` + `instructions` rendered inline, since the code alone
	// still binds the chat even when the link can't be built. No door
	// available at all ⇒ the honest-intermediate copy, naming no specific
	// platform (the set the registry actually vouches for might be empty)
	// — the install ladder survives underneath as a demoted, informational
	// "on your computer" note either way (the pairing command is not
	// copy-actionable there — nothing on a phone can run it). A new
	// connector joining the registry needs no edit here: it just appears
	// in the loop the moment its `deep_link_available` flips true.
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
		// Account-level daemon presence (`_session._machine_views`, #1365).
		// `undefined`/`null` = an older backend that predates this field —
		// falls back to the pre-fix, repo-scoped-only gate below, same
		// "absent means unknown, not false" contract `installations` uses.
		machines?: MachinesSummary | null;
		// Test/SSR-injectable override for the client-derived mobile signal
		// below. `svelte/server` (the SSR harness every test in this file
		// renders through) never runs `$effect` and has no `window` — so the
		// real detector can't fire there. `undefined`/`null` means "detect
		// for real" (the runtime default); a test passes `true`/`false` to
		// pin one branch without a browser. Not a second notion of mobile —
		// the one detector, with one seam for the one environment that
		// can't run it.
		mobileOverride?: boolean | null;
		// #1465 — the registry-derived connector set, from the same
		// `/v1/dashboard/repos` fetch (`messenger_doors.py`, backend).
		// `null`/`undefined` = an older backend that predates this field —
		// render the honest-intermediate fallback, same "absent means
		// unknown" contract `machines` above already set. A non-empty
		// `deep_link_available` door is what unlocks its own tappable
		// mobile CTA below.
		messengerDoors?: MessengerDoor[] | null;
	}

	let {
		repos,
		installations = null,
		pairCommand = null,
		machines = null,
		mobileOverride = null,
		messengerDoors = null
	}: Props = $props();

	// The doors actually worth a tappable CTA — everything else in the
	// registry (Slack, Signal, an unconfigured Telegram/WhatsApp) reads
	// through the honest-intermediate fallback instead.
	let availableDoors = $derived((messengerDoors ?? []).filter((d) => d.deep_link_available));
	let pairedDoors = $derived((messengerDoors ?? []).filter((d) => d.paired));

	// Coarse pointer / UA-CH, client-side only — no new server state, no
	// User-Agent sniffing on the backend. `pointer: coarse` is the primary
	// signal (a touchscreen with no precise pointer alongside it); UA-CH's
	// `navigator.userAgentData.mobile` is read too since Chromium ships it
	// and it costs nothing extra to check. Neither is asked for on a host
	// that lacks it (`matchMedia`/`userAgentData` both optional-chained) —
	// this never throws on an older browser, it just reads as desktop.
	let detectedMobile = $state(false);
	$effect(() => {
		if (typeof window === 'undefined') return;
		const coarse = window.matchMedia?.('(pointer: coarse)').matches ?? false;
		const uaMobile = (navigator as { userAgentData?: { mobile?: boolean } }).userAgentData?.mobile;
		detectedMobile = coarse || uaMobile === true;
	});
	let isMobile = $derived(mobileOverride ?? detectedMobile);

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

	// #1365: account-level pairing, from the same fetch. `machines` absent
	// (older backend) reads as "unknown" — never treated as paired, so an
	// old backend keeps today's repo-scoped-only behavior exactly, not a
	// regression toward the bug this exists to fix.
	let machinePaired = $derived(machines?.paired === true);

	// The block survives until the last *observable* step is done. That is
	// daemon-pairing, not repo-enablement — an enabled repo with no daemon
	// is precisely the state the old `repos.length === 0` check hid. (Chat
	// gate / Telegram pairing is deliberately not part of this gate: it is
	// optional infrastructure — self-hosted gates and local execution are
	// the standing free path — not a requirement for a working daemon.)
	//
	// #1365: `daemonEverPaired` alone is repo-scoped — true only once a
	// *connected repo* carries a daemon. `machinePaired` is the account-
	// level fact underneath it: a daemon can pair before any repo is
	// enabled (the iMac trace's own fixture), and that machine must not
	// read as "nothing is paired yet" just because no repo row exists yet
	// to carry the signal. `cold` now requires *both* to be false; the
	// paired-but-repo-less gap in between is `pairedNoRepo`.
	let cold = $derived(repos !== null && !daemonEverPaired && !machinePaired);
	let chatOnly = $derived(cold && pairedDoors.length > 0);
	let pairedNoRepo = $derived(repos !== null && !daemonEverPaired && machinePaired);

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
			{chatOnly ? 'chat connected; daemon still to go' : 'nothing is paired yet'}
		</h2>
		<p class="mt-2 text-sm text-stone-400">
			{chatOnly
				? 'Your resident can already meet you in chat. No daemon is running on your machine yet — finish that step below.'
				: 'This board reads a daemon running on your own machine. There is none yet — two steps, in order.'}
		</p>

		{#if isMobile}
			<!-- Fifth trace (2026-08-17, origin-aware onboarding), flipped
			     same day by #1457, generalized #1465: neither step below
			     runs from a phone — no terminal exists there — so whichever
			     door(s) the registry vouches for lead first. A non-empty
			     `availableDoors` ⇒ one tappable button per door; empty ⇒ the
			     honest-intermediate copy this trace originally shipped, naming
			     no specific platform (the #1465 fix: this used to promise
			     "Telegram or WhatsApp" unconditionally with no `wa.me` door
			     behind it anywhere in the tree). The ladder survives underneath
			     as reference either way, demoted — not copy-actionable;
			     nothing here runs from a phone regardless. -->
			<div class="mt-4 border border-amber-900/30 bg-amber-950/10 p-3" data-testid="messenger-door">
				<p class="font-mono text-[11px] tracking-wide text-amber-200/80 uppercase">
					the messenger door
				</p>
				{#if availableDoors.length > 0}
					<MessengerDoors doors={availableDoors} embedded heading="the messenger door" />
				{:else}
					<p class="mt-1.5 text-sm text-stone-300">
						brnrd talks back once a repo is enabled — no laptop needed after that. It opens on a
						computer, not from here: install the CLI, then run <code>brnrd</code> in a checkout.
					</p>
				{/if}
			</div>

			<p class="mt-4 font-mono text-[11px] tracking-wide text-ink-quiet uppercase">
				on your computer
			</p>
			<p class="mt-1.5 text-sm text-stone-400">For reference — nothing below runs from a phone:</p>
			<pre
				class="mt-1.5 border border-stone-800 bg-stone-950/50 p-2 font-mono text-[11px] wrap-anywhere whitespace-pre-wrap text-ink-mute"><code
					>{INSTALL_COMMAND}</code
				></pre>
			{#if pairParts?.runnable ?? pairCommand}
				<pre
					class="mt-1.5 border border-stone-800 bg-stone-950/50 p-2 font-mono text-[11px] wrap-anywhere whitespace-pre-wrap text-ink-mute"><code
						>{pairParts?.runnable ?? pairCommand}</code
					></pre>
			{/if}
		{:else}
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
						In the checkout, after 01. One word, narrated: it pairs this machine (printing a link
						back here to approve), names your doors, and queues the first run — the one that writes
						your repo's <code>AGENTS.md</code>. Re-run it any time; it resumes from whatever step is
						standing. Execution never leaves your machine.
					</p>
				</li>
			</ol>
		{/if}

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
{:else if pairedNoRepo}
	<!-- #1365: a machine has paired at the account level (`machines.paired`)
	     but no repo carries a daemon yet — the honest middle state between
	     `cold` and gone. Same panel/eyebrow/heading typography as `cold`
	     above; only the copy and the single step differ, so this reads as a
	     sibling of the cold-start block rather than a new visual language. -->
	<section
		class="panel ignite mt-4 p-4"
		style="--ignite-delay: 60ms"
		aria-labelledby="paired-no-repo-heading"
	>
		<p class="eyebrow">the cold start</p>
		<h2 id="paired-no-repo-heading" class="font-mono text-sm font-semibold text-amber-100">
			machine paired, no repo enabled yet
		</h2>
		<p class="mt-2 text-sm text-stone-400">
			A daemon has already paired on this account. This board has nothing to show until it runs in a
			repo checkout.
		</p>

		{#if isMobile}
			<!-- Same gate as the `cold` branch above, same fix: #1457 mints
			     account-level, so a paired-but-repo-less account unlocks the
			     door exactly like a fully-cold one does — `availableDoors` (#1465)
			     is the only thing this branch still asks, not repo state. -->
			<div class="mt-4 border border-amber-900/30 bg-amber-950/10 p-3" data-testid="messenger-door">
				<p class="font-mono text-[11px] tracking-wide text-amber-200/80 uppercase">
					the messenger door
				</p>
				{#if availableDoors.length > 0}
					<MessengerDoors doors={availableDoors} embedded heading="the messenger door" />
				{:else}
					<p class="mt-1.5 text-sm text-stone-300">
						A machine has paired, but the door still waits on a repo — enabling one is what's left,
						and it happens on a computer, not from here.
					</p>
				{/if}
			</div>

			<p class="mt-4 font-mono text-[11px] tracking-wide text-ink-quiet uppercase">
				on your computer
			</p>
			{#if pairParts?.runnable ?? pairCommand}
				<pre
					class="mt-1.5 border border-stone-800 bg-stone-950/50 p-2 font-mono text-[11px] wrap-anywhere whitespace-pre-wrap text-ink-mute"><code
						>{pairParts?.runnable ?? pairCommand}</code
					></pre>
			{/if}
			<p class="mt-1.5 text-sm text-stone-400">
				Same command as pairing — running it in a checkout also enables that repo. For reference;
				nothing here runs from a phone.
			</p>
		{:else}
			<div class="mt-4">
				<p class="font-mono text-[11px] tracking-wide text-ink-quiet uppercase">enable a repo</p>
				{#if pairCommand}
					{#if pairParts?.setupLine}
						<p class="mt-1.5 font-mono text-[11px] text-ink-mute">from the repo checkout:</p>
					{/if}
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
					Same command as pairing — running it in a checkout also enables that repo.
				</p>
			</div>
		{/if}

		<p class="mt-4 border-t border-stone-800 pt-3 font-mono text-[11px] text-ink-mute">
			Self-hosting, gates, and the agent CLIs brnrd drives —
			<a class="text-sky-400 underline hover:text-sky-300" href={DOCS_URL} rel="external">docs</a>.
		</p>
	</section>
{/if}
