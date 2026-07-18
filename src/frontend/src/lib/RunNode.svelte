<script lang="ts">
	// One Wyrd run node, composed from parts with three different owners:
	//
	//   frame     daemon-attested `state.md` frontmatter + the ledger receipt
	//   body      the resident's own `.card`, captured at closeout
	//   traffic   every receipted outbound message, in write order
	//
	// Each part is rendered as *missing* rather than faked when it is absent —
	// a run that predates the runfile weld, or died before writing a body, is a
	// normal shape and the page has to say so plainly.
	import MarkdownContent from './MarkdownContent.svelte';
	import RunLedgerReceipt from './RunLedgerReceipt.svelte';
	import type { RunLedgerRow } from './runLedger';
	import {
		dispatchEdges,
		frameFields,
		frontmatterDocument,
		messageInstant,
		messageTarget,
		messageTone,
		runNodeFromSurface
	} from './runNode';
	import type { SurfaceResponse } from './surface';

	interface Props {
		data: SurfaceResponse;
		repoSlug: string;
		runId: string;
		/** Ledger rows for this run, or null while the receipt is still loading. */
		ledgerRows?: RunLedgerRow[] | null;
		ledgerStale?: boolean;
		ledgerError?: string | null;
	}

	let {
		data,
		repoSlug,
		runId,
		ledgerRows = null,
		ledgerStale = false,
		ledgerError = null
	}: Props = $props();

	let node = $derived(runNodeFromSurface(data, repoSlug, runId));
	let frame = $derived(node.state ? frontmatterDocument(node.state.markdown) : null);
	let fields = $derived(frame ? frameFields(frame.metadata) : []);
	let knownPaths = $derived(new Set(data.files.map((file) => file.path)));
	// The daemon writes the true label into the frame; the slug is a lossy
	// reverse (a repo whose name already held '__' cannot round-trip), so use
	// it only as the fallback.
	let repoLabel = $derived(frame?.metadata.repo_label || repoSlug.replaceAll('__', '/'));
	let edges = $derived(dispatchEdges(frame?.metadata ?? {}, repoSlug, knownPaths));
	let running = $derived((frame?.metadata.status ?? '').toLowerCase() === 'running');

	const TONE_CLASS: Record<string, string> = {
		delivered: 'text-emerald-400/80',
		pending: 'text-amber-400',
		undeliverable: 'text-red-400',
		unknown: 'text-stone-500'
	};

	function instantLabel(raw: string): string {
		if (!raw) return '';
		const timestamp = Date.parse(raw);
		return Number.isNaN(timestamp) ? raw : new Date(timestamp).toLocaleString();
	}
</script>

<div class="mx-auto max-w-3xl px-4 py-6 sm:px-6">
	<header class="ignite">
		<div class="flex items-baseline justify-between gap-3">
			<a
				href="/"
				class="shrink-0 font-mono text-[11px] tracking-wide text-stone-500 uppercase hover:text-stone-300"
				>← loom</a
			>
			<span class="truncate font-mono text-[10px] text-stone-600">{repoLabel}</span>
		</div>
		<p class="eyebrow mt-5">wyrd · run node</p>
		<h1
			class="mt-1 font-mono text-base font-semibold tracking-tight break-all text-amber-100 sm:text-lg"
		>
			{runId}
		</h1>
		{#if data.reported_at}
			<p class="mt-1 font-mono text-[10px] text-stone-600">
				corpus mirrored {instantLabel(data.reported_at)}
			</p>
		{/if}
	</header>

	<!-- The ledger receipt carries what the mirror does not: spend, tokens, and
	     the relic manifest. It is the one part that survives an unmirrored node,
	     so it sits above the corpus split. The API window is bounded (7 days),
	     so its absence is reported as a window miss, never as "produced
	     nothing". -->
	<section class="mt-6" aria-labelledby="receipt-heading">
		<h2 id="receipt-heading" class="sr-only">ledger receipt</h2>
		{#if ledgerRows === null}
			<p class="panel p-4 font-mono text-xs text-stone-500">reading the ledger…</p>
		{:else if ledgerError}
			<p class="panel p-4 text-sm text-stone-500">
				Ledger receipt unavailable — {ledgerError}. The mirrored run node remains readable below.
			</p>
		{:else if ledgerRows.length > 0}
			<RunLedgerReceipt rows={ledgerRows} stale={ledgerStale} />
		{:else}
			<p class="panel p-4 text-sm text-stone-500">
				No ledger receipt for this run in the reported window — the ledger API reaches back seven
				days.
			</p>
		{/if}
	</section>

	{#if !node.mirrored}
		<!-- A run can be real and absent from the mirror at once: the corpus is
		     republished on change and a run node only exists once the daemon has
		     written it. Say which, rather than implying the run never happened. -->
		<section class="panel mt-6 p-4">
			<h2 class="font-mono text-sm text-amber-100">node not mirrored</h2>
			<p class="mt-2 text-sm text-stone-400">
				No <code class="font-mono text-xs text-stone-300">runs/{repoSlug}/{runId}/</code> files are present
				in the current corpus snapshot. Either the run has not been published yet, or it closed before
				the durable run node existed.
			</p>
		</section>
	{:else}
		<!-- ── Frame: what the daemon attests ─────────────────────────────── -->
		<section class="panel mt-6 p-4" aria-labelledby="frame-heading">
			<div class="flex items-baseline justify-between gap-3 border-b border-stone-800 pb-2">
				<h2 id="frame-heading" class="font-mono text-xs tracking-wide text-amber-200 uppercase">
					attested frame
				</h2>
				<span class="shrink-0 font-mono text-[10px] text-stone-600">daemon-owned</span>
			</div>
			{#if frame && fields.length > 0}
				{#if node.state?.truncated}
					<p class="mt-3 font-mono text-[10px] text-amber-400">frame mirror truncated</p>
				{/if}
				<dl class="mt-3 grid grid-cols-1 gap-x-6 gap-y-1.5 sm:grid-cols-2">
					{#each fields as field (field.label)}
						<div
							class="flex min-w-0 items-baseline justify-between gap-3 border-b border-stone-900 pb-1"
						>
							<dt class="shrink-0 font-mono text-[10px] tracking-wide text-stone-500 uppercase">
								{field.label}
							</dt>
							<dd class="min-w-0 truncate font-mono text-[11px] text-stone-300" title={field.value}>
								{field.value}
							</dd>
						</div>
					{/each}
				</dl>
				{#if frame.body}
					<div class="mt-2 text-sm text-stone-300">
						<MarkdownContent
							markdown={frame.body}
							sourcePath={node.state?.path ?? ''}
							{knownPaths}
						/>
					</div>
				{/if}
			{:else}
				<p class="mt-3 text-sm text-stone-500">No attested frame was captured for this run.</p>
			{/if}
		</section>

		<!-- ── Body: what the resident wove ───────────────────────────────── -->
		<section class="panel mt-4 p-4" aria-labelledby="body-heading">
			<div class="flex items-baseline justify-between gap-3 border-b border-stone-800 pb-2">
				<h2 id="body-heading" class="font-mono text-xs tracking-wide text-amber-200 uppercase">
					woven body
				</h2>
				<span class="shrink-0 font-mono text-[10px] text-stone-600">resident-owned</span>
			</div>
			{#if node.body}
				{#if node.body.truncated}
					<p class="mt-3 font-mono text-[10px] text-amber-400">body mirror truncated</p>
				{/if}
				<div class="text-sm text-stone-300">
					<MarkdownContent markdown={node.body.markdown} sourcePath={node.body.path} {knownPaths} />
				</div>
			{:else if running}
				<!-- A live node is not an empty one. The daemon mirrors the card as
				     it changes, so an absent body here means the run has not
				     written one *yet* — a different statement from a closed run
				     that never wrote one at all. -->
				<p class="mt-3 text-sm text-stone-500">
					This run is still going and has not written its card yet.
				</p>
			{:else}
				<p class="mt-3 text-sm text-stone-500">
					No body was captured — the run wrote no card, or it predates the runfile weld.
				</p>
			{/if}
		</section>

		<!-- ── Traffic: what actually left the run ────────────────────────── -->
		<section class="mt-6" aria-labelledby="traffic-heading">
			<div class="flex items-baseline justify-between gap-3">
				<div class="min-w-0">
					<p class="eyebrow">edge traffic</p>
					<h2 id="traffic-heading" class="font-mono text-sm font-semibold text-amber-100">
						{node.messages.length} message{node.messages.length === 1 ? '' : 's'}
					</h2>
				</div>
				<span class="shrink-0 font-mono text-[10px] text-stone-600">write order</span>
			</div>
			{#if node.messages.length === 0}
				<div class="panel mt-2 p-4 text-sm text-stone-500">
					{#if running}
						Nothing has left this run yet.
					{:else}
						No receipted messages are present. This run may predate the message store or have
						produced no deliverable traffic.
					{/if}
				</div>
			{:else}
				<div class="mt-2 space-y-2">
					{#each node.messages as message (message.file.path)}
						{@const tone = messageTone(message.metadata.status)}
						{@const target = messageTarget(message.metadata)}
						{@const instant = instantLabel(messageInstant(message.metadata))}
						<article class="panel p-4">
							<div
								class="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1 border-b border-stone-800 pb-2 font-mono text-[10px]"
							>
								<span class="min-w-0 truncate text-amber-200">
									{message.metadata.kind || 'message'}{target ? ` → ${target}` : ''}
								</span>
								<span class="shrink-0 {TONE_CLASS[tone]}">
									{message.metadata.status || 'recorded'}{instant ? ` · ${instant}` : ''}
								</span>
							</div>
							{#if tone === 'undeliverable' && message.metadata.reason}
								<p class="mt-2 font-mono text-[10px] text-red-400/80">{message.metadata.reason}</p>
							{/if}
							{#if message.file.truncated}
								<p class="mt-2 font-mono text-[10px] text-amber-400">message mirror truncated</p>
							{/if}
							<div class="text-sm text-stone-300">
								<MarkdownContent
									markdown={message.body}
									sourcePath={message.file.path}
									{knownPaths}
								/>
							</div>
						</article>
					{/each}
				</div>
			{/if}
		</section>

		<!-- ── Edges: where this node hangs off the tree ──────────────────── -->
		<footer class="panel mt-6 p-4" aria-labelledby="edges-heading">
			<div class="flex items-baseline justify-between gap-3 border-b border-stone-800 pb-2">
				<h2 id="edges-heading" class="font-mono text-xs tracking-wide text-amber-200 uppercase">
					dispatch edges
				</h2>
				<span class="shrink-0 font-mono text-[10px] text-stone-600">wyrd</span>
			</div>
			<dl class="mt-3 space-y-2 font-mono text-[11px]">
				<div class="flex flex-wrap items-baseline gap-x-3 gap-y-1">
					<dt class="shrink-0 text-[10px] tracking-wide text-stone-500 uppercase">dispatched by</dt>
					<dd class="min-w-0 break-all text-stone-300">
						{#if edges.parent}
							{#if edges.parent.href}
								<a class="text-amber-200 hover:text-amber-100" href={edges.parent.href}
									>{edges.parent.runId}</a
								>
							{:else}
								{edges.parent.runId} <span class="text-stone-600">· not mirrored</span>
							{/if}
						{:else if edges.origin}
							{edges.origin}
						{:else}
							<span class="text-stone-600">unrecorded</span>
						{/if}
					</dd>
				</div>
				{#if edges.children.length > 0}
					<div class="flex flex-wrap items-baseline gap-x-3 gap-y-1">
						<dt class="shrink-0 text-[10px] tracking-wide text-stone-500 uppercase">dispatched</dt>
						<dd class="flex min-w-0 flex-wrap gap-x-3 gap-y-1 text-stone-300">
							{#each edges.children as child (child.runId)}
								{#if child.href}
									<a class="break-all text-amber-200 hover:text-amber-100" href={child.href}
										>{child.runId}</a
									>
								{:else}
									<span class="break-all"
										>{child.runId} <span class="text-stone-600">· not mirrored</span></span
									>
								{/if}
							{/each}
						</dd>
					</div>
				{/if}
			</dl>
		</footer>
	{/if}
</div>
