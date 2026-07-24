<script lang="ts">
	import { resolve } from '$app/paths';

	// Draft for legal review — not yet counsel-approved. Every factual claim
	// below carries an HTML comment with the file:line it was driven from.
	// Every legal judgement call carries a `LAWYER:` comment. Neither kind of
	// comment reaches the browser: Svelte strips template comments from the
	// production build, so they live in source for the reviewer only.
	//
	// This page is a *document*. It deliberately carries no acceptance
	// widget: the checkbox that used to live here writes
	// `hosted_terms_accepted_at` (src/brnrd/routers/web_auth.py:180-183),
	// which is the hosted-execution addendum's record, not acceptance of
	// these terms. #569: "Do not silently repurpose hosted_terms_accepted_at
	// / hosted_terms_version as acceptance of the general ToS." The widget
	// now lives on /beta-hosted-execution, where the document it records
	// acceptance of also lives.
	//
	// OPEN (#569 acceptance criterion, not this diff): there is no ToS
	// acceptance record in the schema at all. `Account`
	// (src/brnrd/models.py:15-35) carries only the hosted_terms_* pair. A
	// separate `terms_accepted_at` / `terms_version` pair and the surface
	// that writes it are still owed.
	const TERMS_VERSION = '2026-07-24';
</script>

<svelte:head><title>brnrd.dev — Terms of Service</title></svelte:head>

<div class="mx-auto max-w-2xl p-6">
	<div class="flex items-start justify-between gap-4">
		<p class="eyebrow">brnrd.dev · terms of service</p>
		<a
			href={resolve('/')}
			class="font-mono text-[11px] tracking-wide text-ink-quiet uppercase hover:text-stone-300"
			>dashboard</a
		>
	</div>
	<h1 class="mt-1 font-mono text-2xl font-semibold tracking-tight text-amber-100">
		Terms of Service for brnrd.dev
	</h1>

	<section class="panel mt-6 p-5">
		<p class="text-sm text-stone-400">
			Version {TERMS_VERSION}. These terms are an agreement between you and HugiMuni SAS about the
			hosted service at brnrd.dev. They are a draft pending review by French counsel; where a
			statement below is a reading rather than a settled position, it says so.
		</p>

		<div class="mt-6 space-y-5 text-sm leading-6 text-stone-300">
			<section>
				<h2 class="font-mono text-sm font-semibold tracking-wide text-amber-100">
					1. What these terms govern — and what they do not
				</h2>
				<p class="mt-2">
					These terms govern <strong class="text-amber-100">brnrd.dev</strong>: the website, the
					account, the dashboard, and the relay that carries messages between a chat or forge gate
					and a daemon you run. That is the whole of what HugiMuni SAS operates and the whole of
					what you are agreeing to here.
				</p>
				<!-- Driven: the licence table, LICENSE-OVERVIEW.md:9-14 — repo root
				     LICENSE + src/brr/ are MIT; src/brnrd/ and src/frontend/ are
				     AGPLv3. "What you run on your own machine is MIT",
				     LICENSE-OVERVIEW.md:25-27. -->
				<p class="mt-2">
					They do <strong class="text-amber-100">not</strong> govern the brnrd engine — the program you
					install and run on your own machine. That is open-source software licensed to you under its
					own licence (MIT for the daemon core, AGPLv3 for the backend and dashboard components), it costs
					nothing, and it needs no account here. Your rights to it come from that licence, not from this
					page, and nothing on this page adds a condition to it.
				</p>
				<!-- Driven: SECURITY.md:136-137 — "This section applies only if you
				     ran `brnrd account connect`. Without it, no lane below exists."
				     Independently surveyed in the 2026-07-24 legal review: no
				     telemetry, no phone-home, no unconditional brnrd.dev calls in
				     src/brr/. -->
				<p class="mt-2">
					An installation that never runs <code class="font-mono text-amber-200"
						>brnrd account connect</code
					> sends us nothing. If that describes you, you are not a party to these terms, and you do not
					need to read further.
				</p>
			</section>

			<section>
				<h2 class="font-mono text-sm font-semibold tracking-wide text-amber-100">2. Who we are</h2>
				<!-- LAWYER: "and its legal successors" is deliberate. A cross-border
				     conversion SAS → SL under Directive (EU) 2019/2121 (Spain: Ley
				     5/2023) preserves legal personality, so contracts do not need
				     novating — but naming successors now costs nothing and removes
				     any argument later. Maintainer intent is a move to Spain around
				     end 2026. Confirm the wording is adequate under French law. -->
				<p class="mt-2">
					The service is operated by <strong class="text-amber-100">HugiMuni SAS</strong>, a société
					par actions simplifiée incorporated in France, and its legal successors. Its full company
					and publication details — registered office, share capital, SIREN/RCS registration, VAT
					number, publication director, and hosting provider — belong in a separate legal notice
					(mentions légales) which is not yet published.
				</p>
				<!-- OPEN: mentions légales is #569 document 4, mandatory under LCEN
				     Art 6-III for a French-operated site, and does not exist in this
				     repository. Not drafted here because every value on it must come
				     from the Kbis, not from a guess. It is a launch blocker for a
				     French site and should ship before or with these terms. -->
			</section>

			<section>
				<h2 class="font-mono text-sm font-semibold tracking-wide text-amber-100">
					3. Your account and eligibility
				</h2>
				<!-- Driven: src/brnrd/routers/web_auth.py:194 (`/auth/github/start`)
				     is the only sign-in path; the OAuth scope requested is
				     `user:email` (src/brnrd/config.py:82). -->
				<p class="mt-2">
					You sign in with a GitHub account. You are responsible for that account's security and for
					anything done through your brnrd.dev session. You must be old enough to enter a contract
					where you live, and you must not use the service if we have previously terminated your
					access.
				</p>
				<p class="mt-2">
					If you accept these terms on behalf of a company or other organisation, you confirm you
					are authorised to bind it, and "you" in these terms means that organisation.
				</p>
			</section>

			<section>
				<h2 class="font-mono text-sm font-semibold tracking-wide text-amber-100">
					4. Beta status: the service may change or end
				</h2>
				<p class="mt-2">
					brnrd.dev is in beta. Features may be added, changed, throttled, or withdrawn; the service
					may be interrupted for maintenance or to protect it, its infrastructure, other users, or
					connected third-party services. We may end the beta, or the service, at any time. We will
					give reasonable notice of a change that materially reduces what a paying subscriber has
					already paid for, and section 10 governs what happens to money in that case.
				</p>
				<p class="mt-2">
					We do not promise any level of availability, response time, or data durability. The
					durable copies of your work are the ones on your own machine and in your own repositories.
				</p>
			</section>

			<section>
				<h2 class="font-mono text-sm font-semibold tracking-wide text-amber-100">
					5. Where the agent actually runs
				</h2>
				<!-- Driven, and the single most load-bearing factual paragraph on
				     this page — it is both the product description and the factual
				     basis for sections 12 and 13:
				     · SECURITY.md:12-23 — agents "execute commands with your
				       authority against your real repository, using your real
				       credentials and network", launched with approval prompts
				       deliberately bypassed (`claude --dangerously-skip-permissions`
				       / `codex exec --dangerously-bypass-…`).
				     · SECURITY.md:260-261 — "all code executes locally on your
				       daemon — the backend relays, it does not run your agent."
				     · src/frontend/src/lib/Landing.svelte:120 — "brnrd.dev is the
				       control plane, not a compute farm."
				     · No model-provider client exists in src/brnrd/. Its outbound
				       HTTP clients are GitHub (oauth.py:16, platforms/github.py,
				       platforms/github_app.py), Stripe (stripe_api.py:18), and
				       Telegram (platforms/telegram.py). -->
				<p class="mt-2">
					This is the most important thing to understand about the service, and it is a description
					of the product before it is a disclaimer.
				</p>
				<p class="mt-2">
					The agent runs on <strong class="text-amber-100">your machine</strong>, as
					<strong class="text-amber-100">your user</strong>, with
					<strong class="text-amber-100">your credentials</strong>, against your real repository, on
					your network — and it runs with its approval prompts deliberately bypassed, because
					unattended work is the point of the product. It calls the model provider under
					<strong class="text-amber-100">your own subscription</strong>, from your machine.
					brnrd.dev calls no model provider: there is no model-provider client in the backend at
					all. What brnrd.dev does is relay a message to your daemon and relay the reply back, and —
					if you turned it on — mirror pages your agent wrote so the dashboard can render them.
				</p>
				<p class="mt-2">
					Two consequences follow, and we would rather state them than have you discover them.
					First, anything the agent does, it does with your authority: it can write files, run shell
					commands, install dependencies, make network calls, and push to your forge, and a mistake
					it makes is a mistake made on your system. Second, any text the service ingests — an issue
					body, a review comment, a chat message — reaches the agent's prompt as potential
					instruction. Whoever you authorise to trigger a run can, in practice, instruct your agent.
					Authorise people as carefully as you would grant shell access.
				</p>
				<p class="mt-2">
					We publish the full execution and trust model, including what each execution environment
					does and does not isolate and which gaps are still open, in
					<a
						class="text-sky-400 underline"
						href="https://github.com/hugimuni-labs/brnrd/blob/main/SECURITY.md"
						rel="external">SECURITY.md</a
					>. It is part of how you should evaluate this service. It names, in particular, that
					<code class="font-mono text-amber-200">worktree</code>
					and <code class="font-mono text-amber-200">docker</code> are not security boundaries.
				</p>
			</section>

			<section>
				<h2 class="font-mono text-sm font-semibold tracking-wide text-amber-100">
					6. Acceptable use, and what you instruct the agent to do
				</h2>
				<p class="mt-2">You must not use brnrd.dev to:</p>
				<ul class="mt-2 list-disc space-y-1 pl-5">
					<li>break the law, or infringe anyone's rights;</li>
					<li>access systems, accounts, repositories, or data you are not authorised to access;</li>
					<li>
						develop or distribute malware, credential harvesters, or tooling whose purpose is
						unauthorised access;
					</li>
					<li>
						attack, overload, probe, or circumvent the service's controls, quotas, authorisation
						checks, or billing;
					</li>
					<li>
						resell or provide the hosted service to third parties as if it were your own service;
					</li>
					<li>
						process personal data through the service in a way that breaks the law applying to you
						as its controller.
					</li>
				</ul>
				<p class="mt-2">
					<strong class="text-amber-100"
						>You are responsible for what you instruct the agent to do</strong
					>, for what the people you authorise instruct it to do, and for what it does as a result.
					That responsibility does not shift to us because an agent, rather than your hands, carried
					out the instruction.
				</p>
				<p class="mt-2">
					An agent produces output that can be wrong, insecure, or subtly incorrect while looking
					correct. You must review its output before you merge it, deploy it, publish it, run it in
					production, or otherwise rely on it. We may suspend an account that is breaking this
					section, immediately where the breach is causing harm.
				</p>
			</section>

			<section>
				<h2 class="font-mono text-sm font-semibold tracking-wide text-amber-100">
					7. Your content stays yours
				</h2>
				<p class="mt-2">
					Your code, prompts, messages, repositories, and everything your agent writes remain yours.
					We claim no ownership of any of it, and nothing here transfers any intellectual property
					to us.
				</p>
				<!-- Driven, and the paragraph a careless draft over-grants:
				     · SECURITY.md:147-149 — each published snapshot is "a render
				       cache, replaced wholesale on every publish: the repo,
				       dominion, and knowledge repos remain the durable copies, and
				       disconnecting your last repo deletes the mirror server-side."
				     · src/brnrd/routers/_session.py:356-359 — on last-repo
				       disconnect, `account.surface_json = "[]"`.
				     · src/brnrd/inbox.py:311 — `event.body = None` once the reply is
				       delivered.
				     The licence is therefore scoped to hosting/copying/transmitting/
				     displaying for as long as the copy exists, and no further. It is
				     NOT the "operate, secure, support, and improve the service"
				     licence #569 sketched: "improve" would license us to keep and
				     study content that the code actually deletes, and would be a
				     larger grant than the implementation takes. -->
				<p class="mt-2">
					To run the service we have to hold copies of some of it for a while: a message has to be
					stored to be relayed, and a page has to be copied to brnrd.dev to be rendered in your
					dashboard. So you grant us a non-exclusive, worldwide, royalty-free licence to host, copy,
					transmit, and display your content
					<strong class="text-amber-100"
						>solely to operate the features you have enabled, and only for as long as we hold that
						copy</strong
					>. That is the whole grant. It ends when the copy does.
				</p>
				<p class="mt-2">
					For the avoidance of doubt, it is not a licence to use your content to train or fine-tune
					any model, to build datasets, to publish or market it, or to sublicense it to anyone other
					than the providers we need to run the service (hosting, payments, GitHub, and the chat
					gate you chose to connect). brnrd.dev sends no part of your content to a model provider,
					because the service makes no model calls at all — the model calls happen on your machine,
					under your own account with that provider, under their terms.
				</p>
				<p class="mt-2">
					What the mirror contains, and for how long, is set out on the
					<a class="text-sky-400 underline" href={resolve('/beta-hosted-execution')}
						>hosted-execution page</a
					>. Read it before you enable publishing: it is measured, and it is more than people
					assume.
				</p>
			</section>

			<section>
				<h2 class="font-mono text-sm font-semibold tracking-wide text-amber-100">
					8. Our intellectual property
				</h2>
				<p class="mt-2">
					The brnrd source code is licensed to you under the open-source licences that ship with it,
					and those licences govern what you may do with the code. The name "brnrd", the name
					"HugiMuni", and the site's branding are not covered by those licences and remain ours; use
					them to refer to the project, not to suggest we endorse or operate something we do not.
				</p>
			</section>

			<section>
				<h2 class="font-mono text-sm font-semibold tracking-wide text-amber-100">
					9. Personal data, and third parties
				</h2>
				<p class="mt-2">
					For your account, billing, and the security of the service, HugiMuni SAS decides why and
					how personal data is processed. For the content you route through the service — message
					bodies, mirrored pages, repository-derived text, which may contain other people's personal
					data — you decide, and we process it on your behalf and on your instructions.
				</p>
				<!-- OPEN, and stated rather than papered over: /privacy (#569
				     document 3) and an Art 28 DPA do not exist in this repository.
				     Both are owed; the DPA is non-negotiable before any organisation
				     customer. Not linked here because linking a page that 404s is
				     worse than naming the gap. The 2026-07-24 legal review also
				     records: no account-deletion path exists — repo disconnect
				     clears repo-scoped data (_session.py:347-359) but the Account
				     row survives, so Art 17 erasure is manual today. -->
				<p class="mt-2">
					A privacy notice and a data-processing agreement are in preparation and are not yet
					published. Until they are, the measured description of what the service holds and for how
					long is on the
					<a class="text-sky-400 underline" href={resolve('/beta-hosted-execution')}
						>hosted-execution page</a
					>
					and in
					<a
						class="text-sky-400 underline"
						href="https://github.com/hugimuni-labs/brnrd/blob/main/SECURITY.md"
						rel="external">SECURITY.md</a
					>, and you can ask us anything about it at the address in section 16. To have your account
					data deleted, write to that address; there is no self-service deletion button yet.
				</p>
				<p class="mt-2">
					The service relies on providers: our host, our payment processor, GitHub for sign-in and
					forge access, and whichever chat gate you choose to connect. Your use of a connected
					third-party service is governed by that service's own terms, and we are not responsible
					for it.
				</p>
			</section>

			<section>
				<h2 class="font-mono text-sm font-semibold tracking-wide text-amber-100">
					10. Paid plans, Stripe, and refunds
				</h2>
				<!-- Driven: src/brnrd/stripe_api.py:98-113. Stripe Managed Payments
				     rejects an explicit `automatic_tax` parameter, and rejects
				     `tax_id_collection` too (probed live 2026-07-23): "under
				     merchant-of-record, Stripe owns VAT calculation and remittance
				     outright, collects no buyer tax IDs, and offers no B2B reverse
				     charge." Also src/frontend/src/routes/pricing/+page.svelte:139 —
				     "prices at checkout are set by Stripe and shown before you pay". -->
				<p class="mt-2">
					Where a paid plan is offered, checkout runs through <strong class="text-amber-100"
						>Stripe</strong
					>
					under Stripe's Managed Payments, which means
					<strong class="text-amber-100">Stripe is the merchant of record</strong> — the seller for that
					transaction. Stripe calculates and remits the applicable tax, issues the invoice, and shows
					you the final price including tax before you pay. The terms Stripe presents at checkout govern
					that sale. We do not describe here what Stripe undertakes to you; read what Stripe shows you
					at checkout.
				</p>
				<!-- LAWYER, flagged as the prompt requires and as the 2026-07-24
				     review's question 3: WHO presents the 14-day withdrawal right
				     (Code conso L221-18) and its digital-services waiver (L221-28 —
				     express consent to immediate performance plus acknowledgment
				     that the right is lost) at checkout under the Stripe MoR
				     contract? If Stripe's checkout does not render it, the
				     pricing → checkout flow must, and this paragraph must change.
				     We deliberately assert nothing on Stripe's behalf here. Second
				     open item from the same review: French B2C traders must
				     designate a consumer-mediation body (Code conso L612-1) and name
				     it in the terms; none is designated, so no clause is drafted. -->
				<p class="mt-2">
					Subscriptions renew for successive periods until cancelled. You can cancel at any time and
					the cancellation takes effect at the end of the period you have already paid for; we do
					not pro-rate a part-used period.
				</p>
				<p class="mt-2">
					<strong class="text-amber-100">Refunds.</strong> Outside your mandatory legal rights, fees already
					paid are non-refundable. We will, however, refund the unused remainder of a period you have
					paid for if we terminate your account without your being in breach, or if we end the service
					or withdraw a feature you were paying for. If you think you were charged in error, write to
					us and we will look at it. Consumers in the European Union have statutory withdrawal and conformity
					rights that this paragraph does not reduce; where those rights apply, they win.
				</p>
			</section>

			<section>
				<h2 class="font-mono text-sm font-semibold tracking-wide text-amber-100">
					11. Hosted execution
				</h2>
				<!-- #664 (closed, merged a83043a7): hosted-execution consent is
				     feature-scoped, not login-scoped. The gate at web_auth.py:251 was
				     removed; `_terms_status().needs_accept`
				     (_session.py:377-386, 389-397) is the seam a hosted-execution
				     surface reads. Do not re-litigate. -->
				<p class="mt-2">
					Running an agent on compute that HugiMuni SAS operates, rather than on your own machine,
					is a separate feature with its own additional terms:
					<a class="text-sky-400 underline" href={resolve('/beta-hosted-execution')}
						>the hosted-execution beta terms</a
					>. Those terms supplement these; they do not replace them, and where they say more about
					hosted runs specifically, they apply. You are asked to accept them at the point you use
					that feature — not when you sign in — and accepting them is not acceptance of anything
					else.
				</p>
			</section>

			<section>
				<h2 class="font-mono text-sm font-semibold tracking-wide text-amber-100">
					12. No warranty
				</h2>
				<p class="mt-2">
					To the fullest extent the law allows, brnrd.dev is provided
					<strong class="text-amber-100">as is</strong> and
					<strong class="text-amber-100">as available</strong>, and we disclaim all implied
					warranties, including any implied warranty of merchantability, satisfactory quality,
					fitness for a particular purpose, or non-infringement.
				</p>
				<p class="mt-2">
					In particular, we do not warrant that the service will be uninterrupted or error-free,
					that agent output will be correct, safe, secure, or fit for your purpose, or that the
					execution environments constitute a sandbox, a containment boundary, or a defence against
					prompt injection, malicious code, supply-chain compromise, data loss, or a third-party
					tool your run can reach. The
					<a
						class="text-sky-400 underline"
						href="https://github.com/hugimuni-labs/brnrd/blob/main/SECURITY.md"
						rel="external">trust and execution model</a
					> states what each control does and does not do; that document, not an assumption, is the description
					of what you are getting.
				</p>
				<!-- LAWYER: this carve-out is placed inside section 12 and repeated
				     in 13 on purpose, so that a strike-through of the disclaimer
				     against a consumer does not leave the page silent about the
				     rights that survive. Under French consumer law the legal
				     guarantee of conformity for digital content and services
				     (Code conso L224-25-12 et seq., transposing Directive (EU)
				     2019/770) cannot be excluded against a consumer; a blanket
				     "no warranty" is unenforceable against them and risks being read
				     as a clause abusive. Confirm the wording, and confirm whether it
				     should name the legal guarantee explicitly rather than by
				     reference. -->
				<p class="mt-2">
					Nothing in this section excludes a warranty or guarantee that the law does not let us
					exclude. If you are a consumer, the legal guarantees you have under French and European
					Union law — including the guarantee of conformity for digital content and services — apply
					in full, and nothing above reduces them.
				</p>
			</section>

			<section>
				<h2 class="font-mono text-sm font-semibold tracking-wide text-amber-100">
					13. Limitation of liability
				</h2>
				<!-- LAWYER — the clause is written in four separately-severable
				     limbs, deliberately ordered so that striking one leaves the rest
				     standing rather than voiding the section. Reasoning, for you to
				     correct:
				     (a) The carve-out is stated FIRST, not last. Under French law
				         liability for dol and faute lourde cannot be excluded
				         (Code civil art. 1231-3; Cass. com. Chronopost line), and a
				         clause that empties the debtor's essential obligation of its
				         substance is deemed unwritten (art. 1170). Given that
				         approval-bypassed execution is the documented product design
				         (SECURITY.md:12-23), a court could read an unqualified
				         exclusion as exactly that. Leading with the carve-out is the
				         cheapest defence against the whole section being struck.
				     (b) B2C vs B2B is split explicitly. A developer paying a few
				         euros a month personally IS a consumer; the presumption of
				         abusiveness under Code conso R212-1/R212-2 applies to caps
				         against consumers, and an aggregate cap enforceable against
				         a professional may well not be enforceable against them. Two
				         sentences, so a strike against consumers does not take the
				         B2B cap with it.
				     (c) The cap has a EUR 100 floor rather than "fees paid". During
				         a free beta "fees paid" is zero, and a cap of zero is an
				         exclusion wearing a cap's clothes — much likelier to be read
				         as abusive and struck whole. A small positive floor is a real
				         cap, which is the point. The number is a placeholder for you
				         to set; the structure is the argument.
				     (d) The severability sentence is limb-level, not
				         section-level. Please confirm this is the right formulation
				         for a French court, and set the actual figures. -->
				<p class="mt-2">
					<strong class="text-amber-100">First, what is never limited.</strong> Nothing in these
					terms excludes or limits our liability for death or personal injury caused by our
					negligence, for fraud or fraudulent misrepresentation, for
					<em>dol</em>
					or <em>faute lourde</em>, or for any other liability that the law does not permit us to
					exclude or limit. If you are a consumer, your mandatory rights under French and European
					Union consumer law apply in full and this section does not reduce them.
				</p>
				<p class="mt-2">
					<strong class="text-amber-100">Second, indirect loss.</strong> Subject to the paragraph above,
					we are not liable for indirect or consequential loss, loss of profit, loss of revenue, loss
					of business or goodwill, or loss of or damage to data, however caused.
				</p>
				<p class="mt-2">
					<strong class="text-amber-100">Third, the cap.</strong> Subject to the first paragraph, our
					total liability to you for all claims arising in any twelve-month period is limited to the greater
					of the fees you paid us for the service in that period and one hundred euros (EUR 100). If you
					are a consumer and this cap is unenforceable against you, it does not apply to you, and our
					liability to you is instead whatever the law provides.
				</p>
				<p class="mt-2">
					<strong class="text-amber-100">Fourth, severability.</strong> Each of the paragraphs above,
					and each limitation within them, is separate. If one is held unenforceable — in general, or
					against a particular user — it is severed to the minimum extent needed and the remaining paragraphs
					continue to apply.
				</p>
				<p class="mt-2">
					You keep the responsibilities described in sections 5 and 6: the agent runs with your
					authority on your machine, and reviewing its output before you rely on it is your job, not
					ours.
				</p>
			</section>

			<section>
				<h2 class="font-mono text-sm font-semibold tracking-wide text-amber-100">
					14. Suspension and termination
				</h2>
				<!-- Driven: src/brnrd/routers/_session.py:347-359 — disconnect deletes
				     every repo-scoped row, and when no repo remains
				     `account.surface_json = "[]"` empties the mirror. The Account row
				     itself survives, which is why deletion is by request. -->
				<p class="mt-2">
					You can stop using brnrd.dev at any time: disconnect your repositories and stop signing
					in. Disconnecting your last repository deletes the mirrored copy of your pages from our
					servers. To have the account itself deleted, write to the address in section 16.
				</p>
				<p class="mt-2">
					We may suspend or terminate your access if you breach these terms, if your use is harming
					the service or another user, if we are required to by law, or if we discontinue the
					service. Where the circumstances allow it, we will tell you first. Sections 6, 7, 8, 12,
					13, 15, and 16 survive termination.
				</p>
			</section>

			<section>
				<h2 class="font-mono text-sm font-semibold tracking-wide text-amber-100">
					15. Changes to these terms
				</h2>
				<!-- LAWYER: continued-use-as-acceptance is the intended mechanism
				     for non-material changes, and the 30-day notice with a
				     cancellation right is the intended handling for material ones.
				     Two things to confirm: (a) whether continued use is sufficient
				     consent for a consumer under French law for anything beyond a
				     trivial change; (b) whether this drafting is adequate to carry a
				     change of governing law and forum on a future entity move to
				     Spain — that is the change it was written for. Note also that
				     `_HOSTED_TERMS_VERSION` (src/brnrd/routers/_session.py:40) has no
				     enforcement behind it since #664, so a version bump currently
				     re-prompts nobody; a re-acceptance mechanism is still owed. -->
				<!-- OPERATOR / OPEN — deliberately NOT drafted as "we will notify you
				     by email". The backend has no email-sending path at all: there is
				     no SMTP client and no transactional-email provider anywhere in
				     src/brnrd/, and the 2026-07-24 legal review's recipient list
				     records "notably absent: analytics, email service, model
				     providers". Promising email notice would be a term the
				     implementation cannot honour on day one. Notice is therefore
				     drafted as a dashboard/site notice, which the code can actually
				     deliver. If counsel requires individual notice for a material
				     change to a paid subscription, an email path has to be built
				     first — that is an engineering prerequisite, not a wording
				     choice. -->
				<p class="mt-2">
					We may change these terms. The version and date at the top of this page always identify
					the current text. For a change that materially affects your rights or obligations, we will
					publish the new version here and show a notice in the dashboard at least thirty days
					before it takes effect, and if you do not accept it you may cancel and stop using the
					service; for a paid subscription, section 10's refund sentence applies. For other changes,
					continuing to use the service after the new version is published means you accept it.
				</p>
			</section>

			<section>
				<h2 class="font-mono text-sm font-semibold tracking-wide text-amber-100">
					16. Governing law, forum, and contact
				</h2>
				<!-- LAWYER: (a) French law + French courts is the default for a
				     French SAS, with the Brussels I bis art. 18 consumer carve-out
				     stated so the clause is not abusive; confirm the wording.
				     (b) Written knowing the governing law is expected to change once,
				     on a cross-border conversion to a Spanish SL — see section 15's
				     note. (c) No consumer-mediation body is designated
				     (Code conso L612-1) and none is named here; designating one is a
				     prerequisite for consumer sales in France and is outstanding. -->
				<p class="mt-2">
					These terms are governed by French law. Disputes go before the courts of France. If you
					are a consumer resident in the European Union, this does not deprive you of the protection
					of the mandatory law of your country of residence, and you may also bring proceedings in
					the courts there.
				</p>
				<!-- The only published contact address in the repository is
				     security@hugimuni.fr (SECURITY.md:291). Used here rather than
				     inventing a legal@ or support@ alias. OPERATOR: a dedicated
				     contact address for legal and privacy correspondence should
				     replace it before this page is published, and the mentions
				     légales will need one anyway. -->
				<p class="mt-2">
					Contact: <a class="text-sky-400 underline" href="mailto:security@hugimuni.fr"
						>security@hugimuni.fr</a
					>. For a security issue, use that address and please report it privately rather than in a
					public issue.
				</p>
			</section>
		</div>

		<p class="mt-6 text-xs text-ink-quiet">
			See also: <a class="text-sky-400 underline" href={resolve('/beta-hosted-execution')}
				>hosted-execution beta terms</a
			>
			·
			<a
				class="text-sky-400 underline"
				href="https://github.com/hugimuni-labs/brnrd/blob/main/SECURITY.md"
				rel="external">trust and execution model</a
			>
		</p>
	</section>
</div>
