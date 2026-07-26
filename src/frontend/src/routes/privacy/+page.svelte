<script lang="ts">
	import { resolve } from '$app/paths';

	// ── The published retention numbers ──────────────────────────────────
	//
	// Every duration this page states is declared once, here, and rendered
	// from the constant — the prose below interpolates, it never restates.
	// `tests/test_privacy_notice.py` reads these six declarations and then
	// *drives* the code that enforces each one: age an event past
	// TTL_QUEUED_BODY_DAYS and its body must actually be gone, ask the app
	// for a session cookie and its max-age must actually be
	// TTL_SESSION_DAYS, and so on.
	//
	// So if `_EVENT_BODY_TTL`, `_EVENT_ROW_TTL`, `SESSION_TTL`,
	// `pack_relay_ttl_s`, `ACTIVITY_STALE_TTL` or `_RUNS_WINDOW_DAYS_DEFAULT`
	// moves, this page goes red rather than quietly becoming a false
	// statement about someone's personal data. That is the point: a privacy
	// notice is the one document where drift is a misrepresentation.
	const TTL_QUEUED_BODY_DAYS = 14; // brnrd/inbox.py :: _EVENT_BODY_TTL
	const TTL_EVENT_ROW_DAYS = 90; // brnrd/inbox.py :: _EVENT_ROW_TTL
	const TTL_SESSION_DAYS = 30; // brnrd/routers/accounts.py :: SESSION_TTL
	const TTL_REVIEW_PACK_SECONDS = 3600; // brnrd/config.py :: pack_relay_ttl_s
	const TTL_ACTIVITY_MINUTES = 10; // brnrd/activity_records.py :: ACTIVITY_STALE_TTL
	const TTL_RUN_MIRROR_DAYS = 14; // brr/gates/cloud.py :: _RUNS_WINDOW_DAYS_DEFAULT

	const CONTACT = 'security@hugimuni.fr';
	const LAST_UPDATED = '2026-07-24';
</script>

<svelte:head><title>brnrd privacy notice</title></svelte:head>

<div class="mx-auto max-w-2xl p-6">
	<div class="flex items-start justify-between gap-4">
		<p class="eyebrow">brnrd · privacy</p>
		<a
			href={resolve('/')}
			class="font-mono text-[11px] tracking-wide text-ink-quiet uppercase hover:text-stone-300"
			>dashboard</a
		>
	</div>
	<h1 class="mt-1 font-mono text-2xl font-semibold tracking-tight text-amber-100">
		brnrd privacy notice
	</h1>

	<section class="panel mt-6 p-5">
		<p class="text-sm text-stone-400">
			Last updated {LAST_UPDATED}. This notice explains what personal data HugiMuni SAS processes
			when you use the hosted brnrd service at brnrd.dev, why, and for how long. It is information,
			not a contract — there is nothing here for you to accept.
		</p>

		<div class="mt-6 space-y-5 text-sm leading-6 text-stone-300">
			<section>
				<h2 class="font-mono text-sm font-semibold tracking-wide text-amber-100">
					1. Who is responsible, and for what
				</h2>
				<p class="mt-2">
					brnrd is operated by <strong class="text-stone-200">HugiMuni SAS</strong>, a company
					registered in France. Write to
					<a class="text-sky-400 underline" href={`mailto:${CONTACT}`}>{CONTACT}</a> about anything on
					this page, including a request to exercise your rights or a report of a security or data incident.
				</p>
				<p class="mt-2">
					Our role is not the same for every kind of data, and the difference matters, so we state
					it plainly rather than blurring it:
				</p>
				<ul class="mt-2 list-disc space-y-2 pl-5">
					<li>
						<strong class="text-stone-200">We are the controller</strong> — we decide the purposes and
						means — for your account identity, your terms-acceptance record, billing records, and the
						operational telemetry your daemon reports about itself.
					</li>
					<li>
						<strong class="text-stone-200">We are a processor</strong> — we act on your instructions —
						for the content that passes through the service: the message bodies we relay between your
						chat or issue threads and your daemon, the pages your daemon mirrors to the dashboard, and
						the run content inside them. You are the controller of that material. If it contains other
						people's personal data — a commit author, someone who commented on an issue, another member
						of a group chat — you decide what happens to it, and you are responsible for having a basis
						to send it to us.
					</li>
				</ul>
				<p class="mt-2">
					A data processing agreement covering the processor half is being prepared. Ask us for the
					current draft if you need one now.
				</p>
			</section>

			<section>
				<h2 class="font-mono text-sm font-semibold tracking-wide text-amber-100">
					2. Where your data actually is
				</h2>
				<p class="mt-2">
					The architecture is unusual enough that it changes what this notice can honestly say.
					<strong class="text-stone-200">brnrd runs agents on your machine, not on ours.</strong> Your
					checkout, your git history, your credentials, your model-provider subscription and the agent
					process itself all stay on your hardware. brnrd.dev is a relay and a render cache: it moves
					messages between your gates and your daemon, and it stores a replaceable copy of what your daemon
					chooses to publish so the dashboard has something to draw.
				</p>
				<p class="mt-2">
					Two consequences worth being exact about, because the loose version of each is a claim we
					are not entitled to make:
				</p>
				<ul class="mt-2 list-disc space-y-2 pl-5">
					<li>
						<strong class="text-stone-200">No part of brnrd reads your working tree.</strong> We do
						not ship your checkout and we do not take a diff of it. That is a statement about the
						mechanism, and it is narrower than it sounds: read it as a claim about what brnrd
						<em>reads</em>, never as a guarantee about what
						<em>leaves</em>. The dashboard mirror carries the pages your agent writes, verbatim, and
						those pages routinely quote the code the agent was working on. If that distinction is
						not the one you care about, turn the mirror off:
						<code class="font-mono text-xs text-amber-200">publish.layers=none</code> in your
						<code class="font-mono text-xs text-amber-200">.brr/config</code> stops collection, not merely
						transmission.
					</li>
					<li>
						<strong class="text-stone-200">The brnrd backend makes no AI model calls at all.</strong
						>
						It holds no model-provider credentials and contacts no model provider. Your agent talks to
						your provider directly, from your machine, under your own subscription and their privacy terms;
						we are not a party to that exchange and never see it. Nothing you send us is used to train
						any model, by us or by anyone on our behalf.
					</li>
				</ul>
			</section>

			<section>
				<h2 class="font-mono text-sm font-semibold tracking-wide text-amber-100">
					3. What we hold, why, and on what legal basis
				</h2>
				<p class="mt-2">
					Almost everything below is processed because it is
					<strong class="text-stone-200">necessary to perform our contract with you</strong> (Article
					6(1)(b) GDPR): without it the service does not function. We do not ask you to consent to things
					we do on that basis, because dressing a necessity up as a choice would be the false kind of
					reassurance. The exceptions are named individually.
				</p>
				<div class="mt-3 space-y-3">
					<div>
						<p class="font-mono text-xs tracking-wide text-ink-quiet uppercase">Account identity</p>
						<p>
							Your GitHub numeric id, login, and — if your GitHub account exposes one — your email
							address, received when you sign in through GitHub. We request the
							<code class="font-mono text-xs text-amber-200">user:email</code> scope and nothing wider.
							Also the date and version of the beta terms you accepted, which we keep to show that acceptance
							happened (legal obligation and our legitimate interest in being able to evidence it, Article
							6(1)(c) and 6(1)(f)).
						</p>
					</div>
					<div>
						<p class="font-mono text-xs tracking-wide text-ink-quiet uppercase">Relayed messages</p>
						<p>
							When you drive brnrd from Telegram, GitHub or another connected gate, the queue holds
							the message text until your daemon has answered it, plus the routing details needed to
							deliver the reply to the right place: for Telegram, the chat id, topic id, message id,
							sender id and username; for GitHub, the repository, issue or PR number, comment id,
							comment URL, and the author's login.
						</p>
					</div>
					<div>
						<p class="font-mono text-xs tracking-wide text-ink-quiet uppercase">
							Repository routing
						</p>
						<p>
							The full names of the repositories you connect, their default branch, and — for a
							paired Telegram chat — the channel id and the id of the user who paired it, which is
							the sole thing authorising that chat to start a run.
						</p>
					</div>
					<div>
						<p class="font-mono text-xs tracking-wide text-ink-quiet uppercase">Dashboard mirror</p>
						<p>
							If you run <code class="font-mono text-xs text-amber-200">brnrd account connect</code
							>, your daemon publishes snapshots so the dashboard can render: your work-surface and
							knowledge pages and per-run bodies as whole Markdown documents; a ledger of finished
							runs with commit subjects, branch names, file paths on your machine and the agent's
							own prose summary; the live progress note of each running thought; your runner
							catalogue; and your quota windows, which include real billing figures from your model
							provider and reset labels carrying your timezone. What may be mirrored at all is yours
							to bound with
							<code class="font-mono text-xs text-amber-200">publish.layers</code>; the full
							lane-by-lane inventory is in our public
							<a
								class="text-sky-400 underline"
								href="https://github.com/hugimuni-labs/brnrd/blob/main/SECURITY.md"
								rel="external">trust &amp; execution model</a
							>.
						</p>
					</div>
					<div>
						<p class="font-mono text-xs tracking-wide text-ink-quiet uppercase">Billing</p>
						<p>
							Your Stripe customer and subscription identifiers, your plan status, and a ledger of
							credit movements. <strong class="text-stone-200"
								>No card data ever reaches brnrd</strong
							>
							— payment details are entered on Stripe's own checkout and stay with Stripe.
						</p>
					</div>
					<div>
						<p class="font-mono text-xs tracking-wide text-ink-quiet uppercase">
							Sign-in and API tokens
						</p>
						<p>
							Stored as SHA-256 hashes only. We cannot recover a token from what we hold, which also
							means we cannot show you one again after it is issued.
						</p>
					</div>
				</div>
			</section>

			<section>
				<h2 class="font-mono text-sm font-semibold tracking-wide text-amber-100">
					4. How long we keep it
				</h2>
				<p class="mt-2">
					These are the durations the code enforces, not aspirations. A recurring sweep applies
					them; a record is deleted on the first sweep after it passes the age below.
				</p>
				<div class="subpanel mt-3 overflow-x-auto p-4">
					<table class="w-full text-left text-sm">
						<thead>
							<tr class="border-b border-stone-800">
								<th class="pb-2 font-mono text-xs tracking-wide text-ink-quiet uppercase">What</th>
								<th class="pb-2 font-mono text-xs tracking-wide text-ink-quiet uppercase"
									>How long</th
								>
							</tr>
						</thead>
						<tbody class="align-top">
							<tr class="border-b border-stone-900">
								<td class="py-2 pr-4">The text of a message you sent through a gate</td>
								<td class="py-2"
									>Erased the moment your daemon's reply is delivered. If nothing ever answers it,
									erased after {TTL_QUEUED_BODY_DAYS} days.</td
								>
							</tr>
							<tr class="border-b border-stone-900">
								<td class="py-2 pr-4">
									The routing record for that message — chat, sender and message ids, username, or
									the GitHub author and comment URL
								</td>
								<td class="py-2"
									>{TTL_EVENT_ROW_DAYS} days from arrival, then the whole row is deleted. Note that this
									outlives the message text: erasing the body does not erase who sent it.</td
								>
							</tr>
							<tr class="border-b border-stone-900">
								<td class="py-2 pr-4">Image attachments sent with a message</td>
								<td class="py-2"
									>We store pointers, never the bytes; the pointers are cleared with the message
									text on the same schedule.</td
								>
							</tr>
							<tr class="border-b border-stone-900">
								<td class="py-2 pr-4">Dashboard mirror — your pages and run bodies</td>
								<td class="py-2"
									>No timer. It is a cache, replaced wholesale each time your daemon publishes, and
									deleted in full when your last repository is disconnected. Your daemon stops
									mirroring run pages older than {TTL_RUN_MIRROR_DAYS} days by default.</td
								>
							</tr>
							<tr class="border-b border-stone-900">
								<td class="py-2 pr-4">Pending and running task rows</td>
								<td class="py-2"
									>Replaced on every publish; a row nothing has reported for {TTL_ACTIVITY_MINUTES} minutes
									is dropped. All of them are deleted when the repository is disconnected.</td
								>
							</tr>
							<tr class="border-b border-stone-900">
								<td class="py-2 pr-4"
									>Daemon telemetry — runners, quota, live runs, progress notes</td
								>
								<td class="py-2">Overwritten by the next publish. No history is kept.</td>
							</tr>
							<tr class="border-b border-stone-900">
								<td class="py-2 pr-4">Your sign-in session</td>
								<td class="py-2">Expires {TTL_SESSION_DAYS} days after you sign in.</td>
							</tr>
							<tr class="border-b border-stone-900">
								<td class="py-2 pr-4">A relayed code-review pack</td>
								<td class="py-2"
									>Held in memory for {TTL_REVIEW_PACK_SECONDS / 60} minutes behind an unguessable link,
									then dropped. Never written to disk or to the database.</td
								>
							</tr>
							<tr>
								<td class="py-2 pr-4">Your account record and billing ledger</td>
								<td class="py-2"
									>Kept while your account exists. Disconnecting a repository does not remove them —
									see section 8.</td
								>
							</tr>
						</tbody>
					</table>
				</div>
				<p class="mt-3">
					Disconnecting a repository is the strongest self-service control here: it deletes that
					repository's queued messages, task rows, chat pairings and tokens immediately, and when it
					is your last connected repository it empties the mirror too.
				</p>
			</section>

			<section>
				<h2 class="font-mono text-sm font-semibold tracking-wide text-amber-100">
					5. Who else receives it
				</h2>
				<p class="mt-2">
					The complete list. We do not sell personal data, we run no advertising, and there are no
					recipients beyond these.
				</p>
				<ul class="mt-2 list-disc space-y-2 pl-5">
					<li>
						<strong class="text-stone-200">Upsun (Platform.sh)</strong> — hosting and the PostgreSQL database.
						This is where everything described above is stored, and it is the only place we store it.
					</li>
					<li>
						<strong class="text-stone-200">Stripe</strong> — payments, as merchant of record. Receives
						your email address and your brnrd account id, and nothing else from us. United States; transfers
						rely on the EU-US Data Privacy Framework and standard contractual clauses.
					</li>
					<li>
						<strong class="text-stone-200">GitHub</strong> — sign-in and repository access. United States;
						same transfer basis.
					</li>
					<li>
						<strong class="text-stone-200">Telegram</strong> — only if you connect a Telegram chat, and
						then only for the messages in it. This one deserves its own sentence rather than a line in
						a list: Telegram is based outside the EU and outside the countries the European Commission
						has found adequate, so a message you send us through Telegram has already passed through Telegram's
						own infrastructure under Telegram's own privacy policy before it reaches us. That transport
						is inherent to choosing a Telegram gate. If that is not acceptable for the content you work
						with, use the GitHub gate or a self-hosted daemon instead.
					</li>
				</ul>
				<p class="mt-2">
					Notably absent, and deliberately so: no analytics provider, no email delivery service, no
					advertising network, no customer-data platform, and no AI model provider. The only
					external hosts the backend contacts at all are GitHub's, Stripe's and Telegram's.
				</p>
			</section>

			<section>
				<h2 class="font-mono text-sm font-semibold tracking-wide text-amber-100">
					6. Cookies — and why there is no banner
				</h2>
				<p class="mt-2">
					brnrd sets exactly one cookie: a session cookie holding your sign-in token, marked
					HttpOnly and SameSite=Lax, expiring after {TTL_SESSION_DAYS} days. It exists so that the page
					you loaded knows you are signed in. There is no analytics cookie, no advertising cookie and
					no third-party tag, because we run no analytics at all.
				</p>
				<p class="mt-2">
					That is why you are not being asked to click anything. A cookie strictly necessary to
					deliver a service the user asked for is exempt from the consent requirement under the
					ePrivacy Directive as transposed in France, and ours is the only cookie we have. We would
					rather keep it that way than trade it for a dashboard of visitor counts — if that ever
					changes, a banner appears and this paragraph is rewritten before it does.
				</p>
			</section>

			<section>
				<h2 class="font-mono text-sm font-semibold tracking-wide text-amber-100">7. Security</h2>
				<p class="mt-2">
					Sign-in and API tokens are stored as SHA-256 hashes, so a copy of our database yields no
					usable token. Sessions expire after {TTL_SESSION_DAYS} days. Incoming webhooks are signature-verified.
					Pairing uses a device-flow code rather than a shared secret. Encryption at rest is what our
					hosting platform provides; brnrd adds no second, application-level layer of its own, and we
					would rather say that than let the word "encrypted" imply more than it covers.
				</p>
				<p class="mt-2">
					We publish our threat model rather than summarising it flatteringly: what each execution
					environment does and does not isolate, where data crosses the network, and which gaps are
					still open, are all in
					<a
						class="text-sky-400 underline"
						href="https://github.com/hugimuni-labs/brnrd/blob/main/SECURITY.md"
						rel="external">SECURITY.md</a
					>. If you find a hole, tell us privately at
					<a class="text-sky-400 underline" href={`mailto:${CONTACT}`}>{CONTACT}</a> rather than in a
					public issue.
				</p>
			</section>

			<section>
				<h2 class="font-mono text-sm font-semibold tracking-wide text-amber-100">
					8. Your rights, including the one we cannot yet self-serve
				</h2>
				<p class="mt-2">
					Under the GDPR you may ask us for access to your personal data, correction of it, erasure,
					restriction of processing, portability, and you may object to processing we carry out on
					the basis of our legitimate interests (Articles 15 to 22). Write to
					<a class="text-sky-400 underline" href={`mailto:${CONTACT}`}>{CONTACT}</a>. We answer
					within one month, and will tell you if we need the extension the GDPR allows.
				</p>
				<p class="mt-2">
					<strong class="text-stone-200">Being straight about deletion.</strong> Disconnecting your
					repositories does most of the work automatically — it removes the queued messages, task
					rows, chat pairings, tokens and, on the last one, the mirror. What it does not remove is
					your account record itself: your GitHub id, login, email and Stripe customer id survive
					it. There is currently
					<strong class="text-stone-200">no self-service button that erases an account</strong>, and
					we would rather write that sentence than show you one that does not exist. Until we ship
					it, email us and we will do it by hand within the statutory month. Building the endpoint
					is tracked work, not an aspiration.
				</p>
				<p class="mt-2">
					You can also complain to the French supervisory authority, the
					<a class="text-sky-400 underline" href="https://www.cnil.fr" rel="external">CNIL</a>, or
					to the authority where you live.
				</p>
			</section>

			<section>
				<h2 class="font-mono text-sm font-semibold tracking-wide text-amber-100">
					9. Children, marketing, and changes
				</h2>
				<p class="mt-2">
					brnrd is a developer tool for professional use and is not directed at children. We do not
					knowingly create accounts for anyone under 16; tell us if one exists and we will delete
					it.
				</p>
				<p class="mt-2">
					We send no marketing email. The service sends operational messages only — replies to your
					own requests, and notices about your account or a security matter.
				</p>
				<p class="mt-2">
					When this notice changes we update the date at the top. If a change materially affects how
					we handle your data we will tell you through the service before it takes effect. This
					notice is information about our processing; it is not a contract, and nothing here asks
					for or records your acceptance. The agreement you accept is in the
					<a class="text-sky-400 underline" href={resolve('/terms')}>terms</a>.
				</p>
			</section>
		</div>
	</section>

	<footer class="mt-8 border-t border-stone-800 pt-4">
		<p class="font-mono text-[10px] text-ink-mute">
			HugiMuni SAS, France ·
			<a class="hover:text-stone-300" href={resolve('/terms')}>terms</a>
			·
			<a
				class="hover:text-stone-300"
				href="https://github.com/hugimuni-labs/brnrd/blob/main/SECURITY.md"
				rel="external">security</a
			>
			·
			<a class="hover:text-stone-300" href={`mailto:${CONTACT}`}>{CONTACT}</a>
		</p>
	</footer>
</div>
