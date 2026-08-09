<script lang="ts">
	import { resolve } from '$app/paths';
	import { SvelteMap, SvelteSet } from 'svelte/reactivity';
	import { DOCS_URL } from './publicStats';
	import { ageSince } from './runLedger';
	import { statusDotStyle, STATUS_GOOD, STATUS_COOLING } from './statusPalette';
	import type { Capability, ConnectedRepo } from './repos';

	// The capability panel (design-capability-panel.md). One registry, one
	// renderer: the wire ships evaluated `lit`/`dark`/`waiting`/`unobservable`
	// rows with no copy attached (`Capability.to_wire`, `capabilities.py` —
	// "the catch site owns classification; this component owns copy," the
	// house precedent MarkerNotice.svelte:3-4 already sets) — every sentence
	// below is this file's to own, and a fallback row exists for any id this
	// file doesn't yet have a sentence for (§Implications: "visible, not
	// swallowed").
	//
	// Scope for this build (task spec, 2026-08-09): the panel at rest and the
	// frontier it points at — not the whole doctrine. Not built here, named
	// so the gap is a decision and not an omission:
	//   - no live "receiver" for a `command` act (Build order step 4) — a
	//     `command` row shows the step, never watches for it landing;
	//   - no `declined` state (doc itself: "unspecified" — design-capability-
	//     panel.md §What this page could not verify);
	//   - no generic POST executor for `act.kind === 'post'`. `act.target`
	//     for those rows is an API path (`/v1/repos/connect`, a billing
	//     checkout route, …), not a page — inventing a blind POST per
	//     capability id here would be exactly the "fake the act locally"
	//     the design forbids (§The act ladder). Each `post` row instead
	//     deep-links to the existing surface that already performs it
	//     (`/repos`, this page's own billing section) — real navigation,
	//     never a fabricated success.
	interface Props {
		// `null` = the `/v1/dashboard/repos` fetch hasn't landed yet (same
		// "unknown, don't render" convention ColdStart's `repos` prop uses).
		// `undefined`-from-the-wire is normalised to `null` by the caller —
		// this field is additive/optional server-side (repos.ts:155-158).
		capabilities: Capability[] | null;
		// Repo-scope capability `subject` is a repo id with no name of its
		// own on the wire (capabilities.py: `subject=repo.id`) — resolved
		// against the same `connectedRepos` fetch that feeds ColdStart.
		connectedRepos: ConnectedRepo[] | null;
		// Backend-owned pairing line (see `+page.svelte`'s own comment on
		// `pairingCommand`) — the one `command` act this panel can render as
		// an actual copy box, because the string already exists on this page.
		// Every other `command` row has no known string client-side (no
		// receiver, no generic command catalog) and says so rather than
		// guessing one.
		pairingCommand: string | null;
		now: number;
	}

	let { capabilities, connectedRepos = null, pairingCommand = null, now }: Props = $props();

	// Renderer-owned copy (spec §"No copy"). Every catalog id as of
	// capabilities.py:159-175; an id this map doesn't carry still renders,
	// per §Implications — see `labelFor`/`hintFor`.
	const COPY: Record<string, { label: string; hint: string }> = {
		'signed-in': { label: 'signed in', hint: 'the account record exists' },
		terms: { label: 'terms accepted', hint: 'the legal floor for using brnrd' },
		'github-app': {
			label: 'GitHub App installed',
			hint: 'short-lived repo-scoped tokens instead of a PAT on a laptop'
		},
		subscription: { label: 'subscription', hint: 'the hosted mailbox keeps existing' },
		'cli-installed': { label: 'CLI installed', hint: 'execution is local' },
		'machine-paired': {
			label: 'machine paired',
			hint: 'the sensor — every lamp below becomes observable'
		},
		'daemon-live': { label: 'daemon live', hint: 'a mailbox that is actually being read' },
		'runner-available': { label: 'a runner is available', hint: 'a Core that can actually think' },
		'runner-quota': { label: 'runner quota', hint: 'it can think today' },
		'repo-enabled': { label: 'repo enabled', hint: 'a mailbox for this repo' },
		'publish-scope': { label: 'publish scope set', hint: 'what leaves your machine' },
		'repo-initialised': {
			label: 'repo initialised',
			hint: 'AGENTS.md, kb, .brr/config — a contract to read'
		},
		'bot-collaborator': {
			label: 'bot collaborator',
			hint: 'assignment · review requests · @ autocomplete'
		},
		'channel-bound': { label: 'channel bound', hint: 'a human can reach the resident' },
		'gate-health': { label: 'gate health', hint: 'the channel is actually polling' }
	};

	function labelFor(id: string): string {
		return COPY[id]?.label ?? id;
	}
	function hintFor(id: string): string | null {
		return COPY[id]?.hint ?? null;
	}

	interface Row {
		cap: Capability;
		label: string;
		hint: string | null;
		known: boolean;
	}
	interface Group {
		key: string;
		title: string;
		rows: Row[];
	}

	function toRow(cap: Capability): Row {
		return { cap, label: labelFor(cap.id), hint: hintFor(cap.id), known: cap.id in COPY };
	}

	function repoLabel(subject: string | null): string {
		const repo = (connectedRepos ?? []).find((r) => r.id === subject);
		return repo?.repo_full_name ?? `repo ${(subject ?? '?').slice(0, 8)}`;
	}

	// Machine-scope `subject` is a daemon id with no name resolvable from any
	// fetch this page already makes (`ConnectedRepo` carries `latest_daemon_name`
	// per *repo*, not per daemon id, and a paired machine with zero enabled
	// repos has no row to borrow a name from at all) — reported as a gap
	// rather than papered over with a guessed join.
	function machineLabel(subject: string | null, index: number): string {
		// No name to show — read honestly as an id fragment (`#a1b2c3`), never
		// as a truncated word (`dmn-lapt` reads like a typo, not an id).
		return subject ? `machine #${subject.slice(-6)}` : `machine ${index + 1}`;
	}

	// #1268 maintainer follow-up: "multiple hex-id machines render with no
	// tell for which is real vs ghost." A machine group's own `daemon-live`
	// row already carries the answer (lit = inside the 2-minute heartbeat
	// window, `evidence.as_of` = the last touch) — it was just buried among
	// the group's other rows instead of riding the group's own header,
	// which is the one line a human scanning several machines actually
	// reads first. `null` when the group has no `daemon-live` row at all
	// (shouldn't happen for a real daemon — `cli-installed` always seeds
	// one — but a catalog row can't assume its neighbour is present).
	function machineLiveTell(g: Group): string | null {
		const live = g.rows.find((r) => r.cap.id === 'daemon-live');
		if (!live) return null;
		if (live.cap.state === 'lit') return 'live';
		// `ageSince` already appends its own "ago" (`"3h 12m ago"`).
		const age = live.cap.evidence.as_of ? ageSince(live.cap.evidence.as_of, now) : null;
		return age ? `last seen ${age}` : 'not live';
	}

	// #1268: expanded-group state for "collapse the lit" — a key from
	// `Group.key` that the resident has pressed open this session. Session-
	// scoped only, same shape as `seenLit`/`regressed` below: nothing here
	// persists past a reload, and a freshly-collapsed group starts collapsed
	// again on the next fetch that still reads all-lit.
	let expanded = new SvelteSet<string>();
	function toggleExpanded(key: string) {
		if (expanded.has(key)) expanded.delete(key);
		else expanded.add(key);
	}

	let groups = $derived.by((): { account: Group; machine: Group[]; repo: Group[] } => {
		const list = capabilities ?? [];
		const account: Group = {
			key: 'account',
			title: 'account',
			rows: list.filter((c) => c.scope === 'account').map(toRow)
		};

		const bySubject = (scope: string) => {
			const map = new SvelteMap<string, Capability[]>();
			for (const c of list) {
				if (c.scope !== scope) continue;
				const key = c.subject ?? '';
				if (!map.has(key)) map.set(key, []);
				map.get(key)!.push(c);
			}
			return map;
		};

		const machineMap = bySubject('machine');
		const machine = Array.from(machineMap.entries()).map(([subject, caps], i) => ({
			key: subject || `m${i}`,
			title: machineLabel(subject || null, i),
			rows: caps.map(toRow)
		}));

		const repoMap = bySubject('repo');
		const repo = Array.from(repoMap.entries()).map(([subject, caps]) => ({
			key: subject,
			title: repoLabel(subject || null),
			rows: caps.map(toRow)
		}));

		return { account, machine, repo };
	});

	// Mechanism 5 (§Never nagging): "the count is a sentence, not an alarm."
	// `waitingOnYou` deliberately excludes optional-heat frontier rows — those
	// are counted in `optionalUnlit` instead, so an optional upgrade never
	// inflates the number a required gap owns.
	let summary = $derived.by(() => {
		const list = capabilities ?? [];
		if (!list.length) return null;
		const lit = list.filter((c) => c.state === 'lit').length;
		const optionalUnlit = list.filter((c) => c.heat === 'optional' && c.state !== 'lit').length;
		const waitingOnYou = list.filter((c) => c.frontier && c.heat !== 'optional').length;
		const parts = [`${lit} lit`];
		if (optionalUnlit) parts.push(`${optionalUnlit} optional, unlit`);
		parts.push(waitingOnYou ? `${waitingOnYou} waiting on you` : 'nothing waiting on you');
		return parts.join(' · ');
	});

	// Regression watch (mechanism 3: "the board never initiates except on
	// regression"). Session-scoped only — the wire carries no persisted
	// "previous state" for a capability, so this remembers what it has seen
	// *this page load* and nothing before it. A required lamp that goes
	// lit → dark while the tab is open is news; the same lamp already dark
	// on the first fetch of the session is not a regression, it's the
	// starting board.
	let seenLit = new SvelteSet<string>();
	let regressed = new SvelteSet<string>();
	$effect(() => {
		const list = capabilities ?? [];
		for (const c of list) {
			const key = `${c.scope}:${c.id}:${c.subject ?? ''}`;
			if (c.state === 'lit') {
				seenLit.add(key);
				regressed.delete(key);
			} else if (c.heat === 'required' && seenLit.has(key)) {
				regressed.add(key);
			}
		}
	});

	function isRegressed(cap: Capability): boolean {
		return regressed.has(`${cap.scope}:${cap.id}:${cap.subject ?? ''}`);
	}

	function evidenceTitle(cap: Capability): string {
		const age = cap.evidence.as_of ? ageSince(cap.evidence.as_of, now) : null;
		return age
			? `via ${cap.evidence.source}, ${age} ago`
			: `via ${cap.evidence.source}${cap.evidence.as_of ? '' : ', no timestamp'}`;
	}

	function requiresLabel(cap: Capability): string | null {
		if (!cap.requires.length) return null;
		return `needs ${cap.requires.map(labelFor).join(', ')}`;
	}

	interface Affordance {
		text: string;
		href: string | null;
		copyValue: string | null;
	}

	// The act ladder, rendered honestly (see the file-header note on what
	// this build deliberately does not wire). `post` targets are API paths,
	// not pages — routed to the existing surface instead of POSTed blind.
	function affordanceFor(cap: Capability): Affordance {
		const { kind, target } = cap.act;
		if (kind === 'deep-link' && target) return { text: 'open', href: target, copyValue: null };
		if (kind === 'post') {
			if (cap.id === 'subscription')
				return { text: 'manage billing', href: '#billing-heading', copyValue: null };
			if (cap.id === 'terms') return { text: 'reload', href: null, copyValue: null };
			return { text: 'open repos', href: resolve('/repos'), copyValue: null };
		}
		if (kind === 'command') {
			if (cap.id === 'machine-paired' && pairingCommand) {
				return { text: 'pair', href: null, copyValue: pairingCommand };
			}
			return { text: 'terminal step — docs', href: DOCS_URL, copyValue: null };
		}
		return { text: '', href: null, copyValue: null };
	}

	let copied = $state<string | null>(null);
	let copyTimer: ReturnType<typeof setTimeout> | undefined;
	async function copy(key: string, text: string) {
		try {
			await navigator.clipboard.writeText(text);
			copied = key;
			clearTimeout(copyTimer);
			copyTimer = setTimeout(() => (copied = null), 1500);
		} catch {
			// No clipboard access — the value is still visible to select by hand.
		}
	}
</script>

{#snippet dot(row: Row)}
	{@const cap = row.cap}
	{#if cap.state === 'lit'}
		<span
			class="inline-block h-2 w-2 shrink-0 rounded-full"
			style={statusDotStyle('burning', STATUS_GOOD)}
			aria-hidden="true"
		></span>
	{:else if cap.state === 'unobservable'}
		<span
			class="inline-block h-2 w-2 shrink-0 rounded-full border border-dashed border-stone-600"
			aria-hidden="true"
		></span>
	{:else if cap.frontier}
		<span
			class="inline-block h-2 w-2 shrink-0 rounded-full"
			style={statusDotStyle(
				'cooling',
				STATUS_COOLING,
				cap.heat === 'optional' ? 'calm' : 'attention'
			)}
			aria-hidden="true"
		></span>
	{:else}
		<span
			class="inline-block h-2 w-2 shrink-0 rounded-full border border-stone-700 bg-stone-900"
			aria-hidden="true"
		></span>
	{/if}
{/snippet}

{#snippet row(r: Row)}
	{@const cap = r.cap}
	{@const affordance = affordanceFor(cap)}
	{@const requires = requiresLabel(cap)}
	<li class="flex items-start gap-2 py-1" class:opacity-60={cap.state !== 'lit' && !cap.frontier}>
		{@render dot(r)}
		<div class="min-w-0 grow">
			<div class="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
				<span
					class="font-mono text-[11px] {cap.heat === 'required'
						? 'font-semibold text-stone-200'
						: cap.heat === 'recommended'
							? 'text-stone-300'
							: 'text-ink-quiet'}"
					title={r.known ? (r.hint ?? undefined) : `unrecognised capability id: ${cap.id}`}
				>
					{r.label}{#if !r.known}<span class="text-ink-mute"> (no local copy)</span>{/if}
				</span>
				{#if cap.heat === 'optional'}
					<span class="font-mono text-[9px] tracking-wide text-ink-mute uppercase">optional</span>
				{/if}
				{#if isRegressed(cap)}
					<span class="font-mono text-[9px] tracking-wide text-amber-300 uppercase"
						>was lit, now off</span
					>
				{/if}
				<span class="font-mono text-[9px] text-ink-mute" title={evidenceTitle(cap)}>
					{cap.state}
				</span>
			</div>
			{#if cap.state === 'waiting' && requires}
				<p class="mt-0.5 font-mono text-[10px] text-ink-mute">{requires}</p>
			{/if}
		</div>
		{#if cap.state !== 'lit' && cap.state !== 'unobservable' && affordance.text}
			{#if affordance.copyValue}
				<button
					type="button"
					class="shrink-0 cursor-pointer border px-2 py-1 font-mono text-[10px] tracking-wide uppercase {cap.frontier
						? 'border-stone-700 text-stone-300 hover:text-stone-100'
						: 'pointer-events-none border-stone-800 text-ink-mute'}"
					disabled={!cap.frontier}
					onclick={() => affordance.copyValue && copy(cap.id, affordance.copyValue)}
				>
					{copied === cap.id ? 'copied' : affordance.text}
				</button>
			{:else if affordance.href}
				<!-- `rel="external"`, always: this component receives raw
				     destination strings from three different origins (a
				     backend-computed deep-link target, DOCS_URL, or an
				     already-`resolve()`d internal path built inside
				     `affordanceFor`) and by the time they reach this
				     template they're an untyped `string`, not a branded
				     `ResolvedPathname` — svelte's own
				     no-navigation-without-resolve rule can't see through
				     that, and marking every one external (same escape
				     Landing.svelte takes for DOCS_URL) is honest: this
				     panel never assumes client-side routing owns these
				     links. -->
				<a
					href={affordance.href}
					rel="external"
					class="shrink-0 border px-2 py-1 font-mono text-[10px] tracking-wide uppercase {cap.frontier
						? 'border-stone-700 text-sky-400 hover:text-sky-300'
						: 'pointer-events-none border-stone-800 text-ink-mute'}"
					aria-disabled={!cap.frontier}
					tabindex={cap.frontier ? 0 : -1}
				>
					{affordance.text}
				</a>
			{:else}
				<!-- No href, no copy value (e.g. `terms`'s "reload" — nothing on
				     this page performs that act, and inventing a target would be
				     the same faked act the design forbids elsewhere). Still reads
				     the frontier/heat weight rather than always looking inert, so
				     a genuinely-actionable-but-unwireable row doesn't look
				     identical to a merely-not-your-turn-yet one. -->
				<span
					class="shrink-0 border px-2 py-1 font-mono text-[10px] tracking-wide uppercase {cap.frontier
						? 'border-stone-700 text-stone-300'
						: 'border-stone-800 text-ink-mute'}"
				>
					{affordance.text}
				</span>
			{/if}
		{/if}
	</li>
{/snippet}

{#snippet groupRows(g: Group)}
	<!-- #1268 mechanisms 1+2, both scoped to one group's own row list:
	     - "collapse the lit": every row in this group is `lit` ⇒ one quiet
	       "all N lit" line, expandable on press, instead of N rows a mature
	       account never needs to glance at.
	     - "quiet the unobservable": unobservable rows never render their own
	       boilerplate line or act affordance (an act on a lamp that *cannot
	       be measured* invites a result the board can't show) — collapsed to
	       one count per group instead, alongside whatever did render. -->
	{@const visible = g.rows.filter((r) => r.cap.state !== 'unobservable')}
	{@const unobservable = g.rows.filter((r) => r.cap.state === 'unobservable')}
	{@const allLit = g.rows.length > 0 && g.rows.every((r) => r.cap.state === 'lit')}
	{#if allLit && !expanded.has(g.key)}
		<button
			type="button"
			class="mt-1 flex cursor-pointer items-center gap-2 py-1 font-mono text-[10px] text-ink-mute hover:text-ink-quiet"
			onclick={() => toggleExpanded(g.key)}
		>
			<span
				class="inline-block h-2 w-2 shrink-0 rounded-full"
				style={statusDotStyle('burning', STATUS_GOOD)}
				aria-hidden="true"
			></span>
			all {g.rows.length} lit
		</button>
	{:else}
		<ul class="mt-1 flex flex-col divide-y divide-stone-900">
			{#each visible as r (r.cap.id + r.cap.subject)}
				{@render row(r)}
			{/each}
		</ul>
		{#if unobservable.length}
			<p class="mt-1 flex items-center gap-2 py-1 font-mono text-[10px] text-ink-mute">
				<span
					class="inline-block h-2 w-2 shrink-0 rounded-full border border-dashed border-stone-600"
					aria-hidden="true"
				></span>
				{unobservable.length} lamp{unobservable.length === 1 ? '' : 's'} unobservable from here
			</p>
		{/if}
		{#if allLit}
			<button
				type="button"
				class="mt-1 cursor-pointer font-mono text-[9px] tracking-wide text-ink-mute uppercase hover:text-ink-quiet"
				onclick={() => toggleExpanded(g.key)}
			>
				collapse
			</button>
		{/if}
	{/if}
{/snippet}

{#if capabilities !== null && capabilities.length > 0}
	<section
		class="panel ignite mt-4 p-4"
		style="--ignite-delay: 120ms"
		aria-labelledby="capabilities-heading"
	>
		<div class="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
			<div>
				<p class="eyebrow">the board</p>
				<h2 id="capabilities-heading" class="font-mono text-sm font-semibold text-amber-100">
					capabilities
				</h2>
			</div>
			{#if summary}
				<!-- Own line at narrow widths (`w-full`, driven at 390px, 2026-08-09):
				     the sentence is long enough to wrap, and wrapping while pinned
				     beside the heading collided its second line into "capabilities".
				     `sm:` un-wraps it back to the top-right, matching every other
				     section's status-line placement. -->
				<p class="w-full font-mono text-[10px] text-ink-quiet sm:w-auto sm:text-right">
					{summary}
				</p>
			{/if}
		</div>

		{#if groups.account.rows.length}
			<div class="mt-3">
				<p class="font-mono text-[10px] tracking-wide text-ink-quiet uppercase">account</p>
				{@render groupRows(groups.account)}
			</div>
		{/if}

		{#if groups.machine.length}
			<div class="mt-3">
				<p class="font-mono text-[10px] tracking-wide text-ink-quiet uppercase">machine</p>
				{#each groups.machine as g (g.key)}
					{@const tell = machineLiveTell(g)}
					<p class="mt-2 font-mono text-[10px] text-stone-400">
						{g.title}{#if tell}<span class="text-ink-mute"> · {tell}</span>{/if}
					</p>
					{@render groupRows(g)}
				{/each}
			</div>
		{/if}

		{#if groups.repo.length}
			<div class="mt-3">
				<p class="font-mono text-[10px] tracking-wide text-ink-quiet uppercase">repo</p>
				{#each groups.repo as g (g.key)}
					<p class="mt-2 font-mono text-[10px] text-stone-400">{g.title}</p>
					{@render groupRows(g)}
				{/each}
			</div>
		{/if}
	</section>
{/if}
