<script lang="ts">
	import { resolve } from '$app/paths';

	const LAST_UPDATED = '2026-07-24';
</script>

<svelte:head><title>brnrd sub-processors</title></svelte:head>

<div class="mx-auto max-w-2xl p-6">
	<div class="flex items-start justify-between gap-4">
		<p class="eyebrow">brnrd · sub-processors</p>
		<a
			href={resolve('/')}
			class="font-mono text-[11px] tracking-wide text-ink-quiet uppercase hover:text-stone-300"
			>dashboard</a
		>
	</div>
	<h1 class="mt-1 font-mono text-2xl font-semibold tracking-tight text-amber-100">
		Sub-processors for brnrd.dev
	</h1>

	<section class="panel mt-6 p-5">
		<p class="text-sm text-stone-400">
			Last updated {LAST_UPDATED}. This page lists the organisations that process personal data on
			behalf of brnrd.dev (HugiMuni SAS). It is comprehensive and current as of the date shown.
		</p>

		<div class="mt-6 space-y-5 text-sm leading-6 text-stone-300">
			<section>
				<h2 class="font-mono text-sm font-semibold tracking-wide text-amber-100">
					Data recipients and sub-processors
				</h2>
				<p class="mt-2">
					The following organisations process personal data passed through brnrd.dev or held in its
					systems:
				</p>

				<table class="mt-4 w-full border-collapse text-xs leading-5">
					<thead>
						<tr class="border-b border-stone-700">
							<th
								class="border-r border-stone-700 px-3 py-2 text-left font-semibold text-amber-100"
							>
								Name & jurisdiction
							</th>
							<th
								class="border-r border-stone-700 px-3 py-2 text-left font-semibold text-amber-100"
							>
								Function
							</th>
							<th
								class="border-r border-stone-700 px-3 py-2 text-left font-semibold text-amber-100"
							>
								Data categories
							</th>
							<th class="px-3 py-2 text-left font-semibold text-amber-100"> Transfer basis </th>
						</tr>
					</thead>
					<tbody class="divide-y divide-stone-700">
						<tr>
							<td class="border-r border-stone-700 px-3 py-3">
								<strong class="text-stone-200">Upsun / Platform.sh</strong>
								<br />
								<span class="text-xs text-stone-500">France (EU)</span>
							</td>
							<td class="border-r border-stone-700 px-3 py-3">
								Cloud hosting infrastructure; PostgreSQL database
							</td>
							<td class="border-r border-stone-700 px-3 py-3">
								Account identity (GitHub user ID, login), email, billing records, conversation and
								event data, corpus mirror, run metadata
							</td>
							<td class="px-3 py-3">Data localised within EU; no transfer required.</td>
						</tr>

						<tr>
							<td class="border-r border-stone-700 px-3 py-3">
								<strong class="text-stone-200">Stripe</strong>
								<br />
								<span class="text-xs text-stone-500">United States</span>
							</td>
							<td class="border-r border-stone-700 px-3 py-3">
								Payment processing and billing; Stripe Managed Payments (merchant of record)
							</td>
							<td class="border-r border-stone-700 px-3 py-3">
								Email address, account identifiers (Stripe customer ID)
								<br />
								<span class="text-xs text-stone-500"
									>Note: card data does not pass through brnrd.dev</span
								>
							</td>
							<td class="px-3 py-3">
								Data Protection Framework (DPF) and Standard Contractual Clauses (SCCs)
							</td>
						</tr>

						<tr>
							<td class="border-r border-stone-700 px-3 py-3">
								<strong class="text-stone-200">GitHub</strong>
								<br />
								<span class="text-xs text-stone-500">United States</span>
							</td>
							<td class="border-r border-stone-700 px-3 py-3">
								Identity provider (OAuth 2.0) and repository API integration
							</td>
							<td class="border-r border-stone-700 px-3 py-3">
								GitHub user ID, login, email, repository and issue/pull-request metadata
							</td>
							<td class="px-3 py-3">
								Data Protection Framework (DPF) and Standard Contractual Clauses (SCCs)
							</td>
						</tr>

						<tr>
							<td class="border-r border-stone-700 px-3 py-3">
								<strong class="text-stone-200">Telegram</strong>
								<br />
								<span class="text-xs text-stone-500">United Arab Emirates</span>
							</td>
							<td class="border-r border-stone-700 px-3 py-3">
								Message transport channel (optional, user-elected)
							</td>
							<td class="border-r border-stone-700 px-3 py-3">
								Telegram chat ID, user ID, username, message content (when user elects Telegram as
								output channel)
							</td>
							<td class="px-3 py-3">
								Telegram is used only when explicitly selected by the user for message relay. Users
								should review
								<a
									href="https://telegram.org/privacy"
									class="text-sky-400 underline"
									target="_blank"
									rel="noopener noreferrer">Telegram's privacy terms</a
								>
								before selecting this channel.
							</td>
						</tr>
					</tbody>
				</table>
			</section>

			<section>
				<h2 class="font-mono text-sm font-semibold tracking-wide text-amber-100">
					What we do not process
				</h2>
				<p class="mt-2">
					The following services are <strong class="text-stone-200">not</strong> used as sub-processors
					by brnrd.dev:
				</p>
				<ul class="mt-2 list-disc space-y-1 pl-5">
					<li>Analytics platforms (no user tracking, no Google Analytics, no similar services)</li>
					<li>
						Email service providers (no marketing email, no transactional email sent by brnrd)
					</li>
					<li>
						Large language model providers (agents run on your hardware with your subscriptions;
						brnrd makes no API calls to model providers)
					</li>
					<li>Session storage outside database (sessions are token-based and server-managed)</li>
				</ul>
			</section>

			<section>
				<h2 class="font-mono text-sm font-semibold tracking-wide text-amber-100">
					Changes to this list
				</h2>
				<p class="mt-2">
					Material changes to sub-processors will be announced before they take effect. Where a new
					recipient involves data transfers outside the EU, we will notify you in advance of any new
					legal basis required (such as an updated Data Protection Framework certification or
					Standard Contractual Clause amendment).
				</p>
				<p class="mt-2">
					Questions about data transfers or sub-processors? Write to
					<a href="mailto:security@hugimuni.fr" class="text-sky-400 underline"
						>security@hugimuni.fr</a
					>
					.
				</p>
			</section>
		</div>
	</section>
</div>

<style>
	:global(body) {
		background-color: rgb(6, 5, 4);
	}
</style>
