<script lang="ts">
	import { onMount } from 'svelte';
	import { resolve } from '$app/paths';

	// Draft for legal review — not yet counsel-approved. Factual claims carry
	// the file:line they were driven from; legal judgement calls carry a
	// `LAWYER:` comment. Svelte strips template comments from the production
	// build, so both kinds are for the reviewer reading source, not the user.
	//
	// This page is where the hosted-execution acceptance widget lives, moved
	// here from /terms. The widget POSTs /v1/terms/accept, which writes
	// `hosted_terms_accepted_at` / `hosted_terms_version`
	// (src/brnrd/routers/web_auth.py:180-183) — the record for THIS document
	// and nothing else. #569: do not repurpose it as acceptance of the general
	// Terms of Service. #664 (closed, merged a83043a7) settled that this
	// consent is feature-scoped, not login-scoped: the OAuth callback no
	// longer gates on it, and `_terms_status().needs_accept`
	// (src/brnrd/routers/_session.py:389-397) is the seam a hosted-execution
	// surface reads when one exists.
	//
	// OPEN: since #664 removed the gate, `_HOSTED_TERMS_VERSION`
	// (src/brnrd/routers/_session.py:40) has no enforcement behind it —
	// bumping it flips `needs_accept` to true for every account and nothing
	// prompts anyone. Whoever builds the first hosted-execution surface must
	// not assume a bump re-prompts.
	//
	// DELIBERATE NON-BUMP, for the maintainer to overrule if he disagrees:
	// #569 says to bump `_HOSTED_TERMS_VERSION` to the release date when the
	// expanded hosted terms ship, because the change is material. This diff
	// does NOT bump it, for two reasons. (1) This text is a draft awaiting
	// counsel; it will change again after review, and a version bump spent
	// now is a re-acceptance prompt spent on a text nobody should be asked to
	// accept yet. (2) Post-#664 a bump prompts nobody anyway, so it would
	// record a version change with no consent behind it — worse than not
	// bumping. Bump it when counsel signs off AND a surface exists that reads
	// `needs_accept`. Both, not either.

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
						typeof body.notice === 'string' ? body.notice : `terms acceptance failed: ${res.status}`
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

<svelte:head><title>brnrd hosted-execution beta terms</title></svelte:head>

<div class="mx-auto max-w-2xl p-6">
	<div class="flex items-start justify-between gap-4">
		<p class="eyebrow">brnrd.dev · hosted-execution beta</p>
		<a
			href={resolve('/')}
			class="font-mono text-[11px] tracking-wide text-ink-quiet uppercase hover:text-stone-300"
			>dashboard</a
		>
	</div>
	<h1 class="mt-1 font-mono text-2xl font-semibold tracking-tight text-amber-100">
		Hosted-execution beta terms
	</h1>

	<section class="panel mt-6 p-5">
		<p class="text-sm text-stone-400">
			Version {status?.terms_version ?? '2026-07-08'}. These terms supplement the
			<a class="text-sky-400 underline" href={resolve('/terms')}>Terms of Service</a> — they do not replace
			them. They apply to what happens when you drive a brnrd thought through brnrd.dev, and to hosted
			compute if and when HugiMuni SAS operates any for your account. You are asked to accept them at
			the point you use the feature, not when you sign in.
		</p>

		<div class="mt-6 space-y-5 text-sm leading-6 text-stone-300">
			<section>
				<h2 class="font-mono text-sm font-semibold tracking-wide text-amber-100">
					1. Two different things get called "hosted"
				</h2>
				<!-- Driven: src/frontend/src/lib/Landing.svelte:120 — "Execution stays
				     on your machine — brnrd.dev is the control plane, not a compute
				     farm." SECURITY.md:260-261 — "all code executes locally on your
				     daemon — the backend relays, it does not run your agent." There
				     is no hosted-execution route in src/frontend/src/routes, and #664
				     records that there is no point-of-use surface to gate. -->
				<p class="mt-2">
					<strong class="text-amber-100">What happens today.</strong> You send a task from
					brnrd.dev, or from a chat or forge gate connected through it. brnrd.dev queues it and
					hands it to
					<strong class="text-amber-100">a daemon you run, on your own machine</strong>. Your
					machine runs the agent, calls the model provider under your own subscription, and sends
					the reply back through brnrd.dev to wherever you asked. That is what this page mostly
					describes, because it is what exists.
				</p>
				<p class="mt-2">
					<strong class="text-amber-100">What does not exist yet.</strong> HugiMuni SAS running the agent
					on its own compute. Section 8 covers that case, so that the terms are in place before the feature
					is — but as of this version, brnrd.dev is a control plane, not a compute farm, and there is
					no hosted-compute surface to use.
				</p>
			</section>

			<section>
				<h2 class="font-mono text-sm font-semibold tracking-wide text-amber-100">
					2. What actually happens when you drive a thought through brnrd.dev
				</h2>
				<p class="mt-2">
					A message arrives at brnrd.dev from the gate you connected. Its text is stored so it can
					be delivered. Your daemon polls, takes the task, and runs the agent locally — file reads
					and writes, shell commands, dependency installation, network and API calls, Git
					operations, tests and builds, whatever the work needs. The reply comes back to brnrd.dev
					and is delivered to the thread you wrote from.
				</p>
				<!-- Driven: SECURITY.md:12-23 — runners are launched with approval
				     prompts deliberately bypassed
				     (`claude --dangerously-skip-permissions`,
				     `codex exec --dangerously-bypass-…`); "the base assumption is that
				     whoever can reach a configured gate has been authorized to
				     instruct the agent". -->
				<p class="mt-2">
					The agent does not ask you to approve each step. It is launched with its approval prompts
					deliberately bypassed, because unattended work is what the product is for. It acts with
					your user's authority, using your credentials and your network. Anyone you authorise to
					trigger a run can, in effect, instruct your agent — so treat that permission the way you
					would treat handing out shell access.
				</p>
			</section>

			<section>
				<h2 class="font-mono text-sm font-semibold tracking-wide text-amber-100">
					3. What leaves your machine
				</h2>
				<!-- The wording discipline here is copied from SECURITY.md:113-120,
				     not just its facts. NEVER phrase this as "your code never leaves":
				     "No publisher reads your working tree" is a claim about the
				     MECHANISM, and SECURITY.md immediately narrows it — the corpus
				     lane mirrors agent-written pages verbatim and, measured against a
				     real account, those pages contained fenced python/diff/bash/toml/
				     yaml blocks including unified-diff fragments of repository test
				     files. "Treat 'we don't ship your source' as a claim about what
				     brnrd reads, never a guarantee about what leaves." -->
				<p class="mt-2">
					Two things leave, and they are different. <strong class="text-amber-100">The relay</strong
					>
					carries your task text and your agent's reply, because that is the job you connected it for.
					<strong class="text-amber-100">Dashboard publishing</strong>, which only exists if you ran
					<code class="font-mono text-amber-200">brnrd account connect</code>, additionally mirrors
					seven lanes of repo-derived content to brnrd.dev every few seconds so the dashboard can
					render them.
				</p>
				<p class="mt-2">
					No brnrd publisher reads your working tree, so we do not ship your checkout or a diff of
					it. <strong class="text-amber-100"
						>That is narrower than it sounds, and we will not let it be read as "your code never
						leaves".</strong
					> The largest mirrored lane carries whole Markdown pages your agent wrote — your work surface,
					every knowledge-base page, and per-run bodies and message transcripts — verbatim. Those pages
					routinely quote the code the agent was working on. Measured against a real account, mirrored
					pages contained fenced code blocks including unified-diff fragments of repository files. If
					your agent wrote about your code, your code is in the mirror.
				</p>
				<p class="mt-2">The seven lanes also carry, in free text:</p>
				<ul class="mt-2 list-disc space-y-1 pl-5">
					<li>
						run receipts — commit SHAs and subjects, branch names, file paths on your machine, and
						the summary the agent wrote about the run;
					</li>
					<li>
						the live progress card the agent is writing right now, for every active run, whichever
						gate it came from;
					</li>
					<li>
						your quota and billing figures for your agent CLIs, reset labels carrying your timezone,
						and the raw error string from the last failed gate poll;
					</li>
					<li>open pull-request titles across the repositories in your account;</li>
					<li>
						your locally-discovered runner catalog — which agent CLIs are installed and
						authenticated, and whether Docker is present. A fingerprint of your machine's tooling.
					</li>
				</ul>
				<p class="mt-2">
					The full lane-by-lane table, produced by driving each publisher and capturing the actual
					payload, is in
					<a
						class="text-sky-400 underline"
						href="https://github.com/hugimuni-labs/brnrd/blob/main/SECURITY.md"
						rel="external">SECURITY.md</a
					>. It is the document to read before deciding what to publish — it is more specific than
					this page can be.
				</p>
			</section>

			<section>
				<h2 class="font-mono text-sm font-semibold tracking-wide text-amber-100">
					4. What brnrd.dev stores, and for how long
				</h2>
				<p class="mt-2">
					These are measurements of what the code does, not intentions. Where a figure is
					configurable, the default is given.
				</p>
				<div class="subpanel mt-3 space-y-3 p-4">
					<!-- Driven: src/brnrd/models.py:158-190 (Event, `body` nullable at
					     :167), src/brnrd/inbox.py:311 (`event.body = None` once the
					     reply is delivered), inbox.py:327-328
					     (`_EVENT_BODY_TTL = timedelta(days=14)`,
					     `_EVENT_ROW_TTL = timedelta(days=90)`), inbox.py:360
					     (`.values(body=None, attachments_json="[]")`). -->
					<p>
						<strong class="text-amber-100">Message bodies.</strong> The text of a message you send through
						a gate is nulled out of the database as soon as the reply is delivered. A message that never
						gets answered has its body nulled after 14 days, and the row itself is deleted after 90. The
						queue is a relay, not an archive.
					</p>
					<!-- Driven: SECURITY.md:249-254 — the interaction is stated because
					     the two halves are true separately and misleading together. -->
					<p>
						<strong class="text-amber-100">But read that with the next line.</strong> If dashboard
						publishing is on, the same message text is also part of the mirrored run page, and stays
						there for the run-mirror window — 14 days by default, configurable, and
						<code class="font-mono text-amber-200">0</code> drops run pages from the mirror entirely.
						On a connected daemon, that window, not the null-after-reply, is the real retention for message
						text.
					</p>
					<!-- Driven: SECURITY.md:147-149 (snapshot is a render cache,
					     replaced wholesale each publish; disconnecting the last repo
					     deletes the mirror server-side) and
					     src/brnrd/routers/_session.py:356-359
					     (`account.surface_json = "[]"`). -->
					<p>
						<strong class="text-amber-100">The mirror.</strong> Each publish replaces the previous
						snapshot wholesale — it is a render cache, and your repository, knowledge base, and
						dominion remain the durable copies. Disconnecting your last repository empties it on our
						side. The two non-run mirror layers, your work surface and your knowledge base, have
						<strong class="text-amber-100">no age bound</strong>: every page ships however old, up
						to a 256 KB per-file cap.
					</p>
					<!-- Driven: src/brnrd/models.py:53-68 — the Token row stores
					     `token_hash` and no plaintext column;
					     src/brnrd/security.py:12-14 — sha256 hexdigest, "Stored; never
					     reversed". -->
					<p>
						<strong class="text-amber-100">Tokens.</strong> Session and daemon tokens are stored as SHA-256
						hashes only. There is no column holding the token itself.
					</p>
					<!-- Driven: src/brnrd/pack_relay.py:1-17 (in-memory only, "never
					     written to the database or to disk") and :39
					     (`default_ttl_s: int = 3600`). -->
					<p>
						<strong class="text-amber-100">Review packs.</strong> A diffense review pack relayed through
						brnrd.dev lives in process memory only, behind an unguessable token, for at most one hour,
						and is dropped on expiry or restart. It is never written to the database or to disk. A pack
						carries file paths, line numbers, and the agent's prose about your code — not a patch.
					</p>
					<!-- Driven: src/brnrd/models.py:15-35 — Account holds github id,
					     login, nullable email, stripe customer id, the hosted-terms
					     acceptance pair, and `surface_json`. -->
					<p>
						<strong class="text-amber-100">Your account.</strong> Your GitHub id and login, your email
						address if GitHub gave us one, your Stripe customer id if you have one, and the record of
						which version of these terms you accepted and when. This has no automatic expiry; ask us and
						we will delete it.
					</p>
				</div>
			</section>

			<section>
				<h2 class="font-mono text-sm font-semibold tracking-wide text-amber-100">
					5. What brnrd.dev does not do
				</h2>
				<!-- Driven by absence, and each absence is checkable in the source:
				     · No model-provider client in src/brnrd/. Its outbound HTTP
				       clients are GitHub (oauth.py, platforms/github.py,
				       platforms/github_app.py), Stripe (stripe_api.py), and Telegram
				       (platforms/telegram.py) — the complete list of `import httpx`
				       sites in the package.
				     · No analytics or tag-manager script in src/frontend/src.
				     · No SMTP client and no transactional-email provider anywhere in
				       src/brnrd/.
				     A claim of absence is only as good as the sweep behind it; this
				     one is a grep over the backend package and the frontend source,
				     re-run 2026-07-24. It should be re-run before publication. -->
				<ul class="mt-2 list-disc space-y-1 pl-5">
					<li>
						<strong class="text-amber-100">It calls no model provider.</strong> There is no model-provider
						client in the backend. Every model call your agent makes is made by your machine, under your
						own subscription, under that provider's terms with you.
					</li>
					<li>
						<strong class="text-amber-100">It runs no analytics.</strong> No analytics script, tag
						manager, or third-party tracker is loaded by this site. The session cookie is strictly
						necessary, which is why you are not being asked to dismiss a cookie banner.
						<!-- LAWYER: the second half of that sentence is a legal
						     conclusion, not a measurement. The measurement is that no
						     analytics or tag-manager script exists in
						     src/frontend/src. The conclusion — that the session cookie
						     is strictly necessary and so exempt from the ePrivacy /
						     Art 82 loi Informatique et Libertés consent requirement,
						     meaning no banner is owed — is a reading. Confirm it. If it
						     is wrong, this half-sentence goes; the claim before it
						     stands either way. Worth defending: the 2026-07-24 review
						     records the no-banner position as a UX asset any future
						     analytics adoption would forfeit. -->
					</li>
					<li>
						<strong class="text-amber-100">It sends no email.</strong> There is no mail-sending path in
						the service at all.
					</li>
					<li>
						<strong class="text-amber-100">It does not sell your data</strong>, and it does not use
						your content to train or fine-tune models.
					</li>
					<li>
						<strong class="text-amber-100">It does not read your working tree</strong> — with the caveat
						in section 3, which matters more than this line does.
					</li>
				</ul>
				<p class="mt-2">
					The providers that do receive something are our host, Stripe for payments, GitHub for
					sign-in and forge access, and the chat gate you chose to connect.
				</p>
			</section>

			<section>
				<h2 class="font-mono text-sm font-semibold tracking-wide text-amber-100">
					6. What you control
				</h2>
				<!-- Driven: SECURITY.md:183-227 — `publish.layers` in `.brr/config`
				     (`none` stops collection, not merely transmission; a value
				     matching no lane mirrors nothing and warns — a typo fails closed),
				     `publish.runs_window_days` default 14, `0` drops the runs layer.
				     The last caveat is SECURITY.md:223-227 verbatim in substance. -->
				<ul class="mt-2 list-disc space-y-1 pl-5">
					<li>
						<code class="font-mono text-amber-200">publish.layers</code> in your
						<code class="font-mono text-amber-200">.brr/config</code> names what may be mirrored at
						all. Unset mirrors everything. <code class="font-mono text-amber-200">none</code> stops all
						seven lanes — and stops collection, not just transmission: no snapshot is built. Naming individual
						lanes turns everything you did not name off.
					</li>
					<li>
						<code class="font-mono text-amber-200">publish.runs_window_days</code> (default 14)
						bounds how far back run pages are mirrored;
						<code class="font-mono text-amber-200">0</code> drops them.
					</li>
					<li>Disconnecting your last repository deletes the mirror from our servers.</li>
					<li>
						Disconnecting the account entirely leaves nothing running: without
						<code class="font-mono text-amber-200">brnrd account connect</code>, none of these lanes
						exist.
					</li>
				</ul>
				<p class="mt-2">
					One honest caveat about that switch: <code class="font-mono text-amber-200"
						>.brr/config</code
					> lives in your checkout and is writable by anything with local write access — including the
					agent itself. It bounds what brnrd publishes. It is not a control an untrusted run cannot reach.
				</p>
				<p class="mt-2">
					Turning publishing off does not disconnect you. A connected daemon still talks to
					brnrd.dev for the relay job you connected it for — inbox polling, reply delivery, progress
					cards, review-pack relay, pairing. If you want no traffic at all, disconnect.
				</p>
			</section>

			<section>
				<h2 class="font-mono text-sm font-semibold tracking-wide text-amber-100">
					7. Review before you rely on it
				</h2>
				<p class="mt-2">
					An agent can produce output that is wrong, insecure, or subtly incorrect while reading as
					correct. Review its work before you merge it, deploy it, publish it, run it in production,
					or otherwise rely on it. Where a run touched credentials, dependencies, or infrastructure,
					review that too. This is your responsibility and it does not move to us because the change
					was made by an agent rather than by hand.
				</p>
			</section>

			<section>
				<h2 class="font-mono text-sm font-semibold tracking-wide text-amber-100">
					8. If HugiMuni SAS ever operates the compute
				</h2>
				<p class="mt-2">
					If and when we offer hosted compute for your account, the following applies to it, in
					addition to everything above.
				</p>
				<p class="mt-2">
					<strong class="text-amber-100">Your authorisation.</strong> You authorise us to run your selected
					agent on compute we operate, for the account, repositories, branches, and tasks you give it,
					and to perform the actions that work needs: file reads and writes, shell commands, dependency
					and package installation, network, API and browser calls, Git operations, tests and builds,
					and other tool use. Some runs use unattended or auto-approval modes; you are not asked to confirm
					each command.
				</p>
				<p class="mt-2">
					<strong class="text-amber-100">Secrets and credentials.</strong> You decide which repositories,
					credentials, tokens, environment variables, branches, data, prompts, and dependencies are safe
					to give a hosted run. A run can read and use anything you make available to it, and anything
					it can reach with what you gave it. Do not give a run a credential you would not give a person.
				</p>
				<p class="mt-2">
					<strong class="text-amber-100">Costs incurred by a run.</strong> A run can consume quota and
					incur charges with third parties — your model provider, your cloud accounts, any metered API
					it calls. Those charges are between you and that provider, and they are yours.
				</p>
				<!-- Driven: SECURITY.md:68-92 — the honest isolation matrix.
				     `worktree` is explicitly "not a security boundary"; `docker` "is
				     not a credential or containment boundary"; `solitary` cannot close
				     content shown to the model provider. This is the paragraph the
				     product's honesty is worth money on: it defeats the "you promised
				     a sandbox" claim shape. Do not soften it. -->
				<p class="mt-2">
					<strong class="text-amber-100">No sandbox guarantee.</strong> We apply the execution
					defaults and controls stated in the product documentation, and they reduce risk. They are
					not a complete sandbox and not a containment boundary. We do not promise that hosted
					execution resists prompt injection, malicious code, supply-chain compromise, data loss, or
					misbehaviour by a tool the run can reach. Our published
					<a
						class="text-sky-400 underline"
						href="https://github.com/hugimuni-labs/brnrd/blob/main/SECURITY.md"
						rel="external">trust and execution model</a
					> names, for each environment, exactly what it does and does not isolate, and which gaps are
					still open. That document is the description of what you are getting.
				</p>
			</section>

			<section>
				<h2 class="font-mono text-sm font-semibold tracking-wide text-amber-100">
					9. Beta status, availability, and changes
				</h2>
				<p class="mt-2">
					This is a beta feature and you use it at your own risk. We may change, pause, reject,
					throttle, or stop hosted execution at any time to protect the service, our infrastructure,
					other users, or connected third-party services. brnrd.dev is currently free to use; if
					paid plans apply to this feature later, the rules for them will be given at checkout and
					in the billing terms, and the
					<a class="text-sky-400 underline" href={resolve('/terms')}>Terms of Service</a> govern the rest.
				</p>
				<p class="mt-2">
					When we materially change this document we bump its version. The version shown at the top
					of this page is the one your acceptance is recorded against.
				</p>
			</section>

			<section>
				<h2 class="font-mono text-sm font-semibold tracking-wide text-amber-100">
					10. Warranty and liability
				</h2>
				<!-- LAWYER: this section deliberately does NOT restate the cap. It
				     incorporates sections 12 and 13 of the ToS by reference, so there
				     is one liability clause to review and no risk of two texts drifting
				     apart or of a court reading the narrower one as a carve-out from
				     the wider. Confirm that incorporation by reference is adequate
				     here given this document has its own acceptance record, or say if
				     the cap must be restated in full on this page. -->
				<p class="mt-2">
					Hosted execution is provided as is and as available, with no warranty that it will be
					uninterrupted, error-free, secure, or fit for your purpose, and no warranty that agent
					output is correct.
				</p>
				<p class="mt-2">
					The warranty disclaimer and the limitation of liability in sections 12 and 13 of the
					<a class="text-sky-400 underline" href={resolve('/terms')}>Terms of Service</a> apply to this
					feature, including their carve-outs. Nothing here limits rights or remedies that cannot be waived
					under French law or European Union consumer protection law; if you are a consumer, your mandatory
					rights apply in full.
				</p>
			</section>

			<section>
				<h2 class="font-mono text-sm font-semibold tracking-wide text-amber-100">
					11. Company and contact
				</h2>
				<p class="mt-2">
					brnrd is operated by HugiMuni SAS, France, and its legal successors. These terms
					supplement the <a class="text-sky-400 underline" href={resolve('/terms')}
						>Terms of Service</a
					>
					and any billing terms, data-processing agreement, or separately agreed contract; they replace
					none of them. Contact:
					<a class="text-sky-400 underline" href="mailto:security@hugimuni.fr"
						>security@hugimuni.fr</a
					>.
				</p>
			</section>
		</div>

		{#if statusError}
			<p class="mt-6 text-sm text-red-400">{statusError}</p>
		{:else if status === null}
			<p class="mt-6 text-sm text-ink-quiet">Loading…</p>
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
						I have read and accept the
						<a class="text-sky-400 underline" href={resolve('/beta-hosted-execution')}
							>brnrd beta hosted-execution terms</a
						>, including the unattended-execution risk and the no-sandbox disclaimer, and I have
						read the <a class="text-sky-400 underline" href={resolve('/terms')}>Terms of Service</a> they
						supplement.
					</span>
				</label>
				<div class="mt-4 flex flex-wrap items-center gap-3">
					<button
						type="button"
						class="cursor-pointer border border-amber-700 bg-amber-950/40 px-3 py-1.5 font-mono text-[11px] tracking-wide text-amber-100 uppercase hover:border-amber-500 disabled:cursor-not-allowed disabled:border-stone-800 disabled:text-ink-mute"
						disabled={posting}
						onclick={acceptTerms}>{posting ? 'accepting…' : 'accept and continue'}</button
					>
					<span class="font-mono text-[11px] text-ink-mute">next {nextUrl}</span>
				</div>
				{#if result}
					<p class={`mt-3 text-sm ${result.level === 'error' ? 'text-red-400' : 'text-amber-200'}`}>
						{result.message}
					</p>
				{/if}
			</div>
		{:else if status.authenticated}
			<p class="mt-6 text-sm text-ink-quiet">
				{#if status.accepted_at}
					Accepted {new Date(status.accepted_at).toLocaleString()}.
				{:else}
					Your account does not need a hosted-execution terms update.
				{/if}
			</p>
		{:else}
			<p class="mt-6 text-sm text-stone-400">
				This page is readable signed out. To record acceptance, <a
					class="text-sky-400 underline"
					href="/login?next=/beta-hosted-execution"
					rel="external">log in</a
				>.
			</p>
		{/if}

		<p class="mt-6 text-xs text-ink-quiet">
			See also: <a class="text-sky-400 underline" href={resolve('/terms')}>terms of service</a>
			·
			<a
				class="text-sky-400 underline"
				href="https://github.com/hugimuni-labs/brnrd/blob/main/SECURITY.md"
				rel="external">trust and execution model</a
			>
		</p>
	</section>
</div>
