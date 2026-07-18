<script lang="ts">
	import MarkdownContent from './MarkdownContent.svelte';
	import { frontmatterDocument, runNodeFromSurface } from './runNode';
	import type { SurfaceResponse } from './surface';

	interface Props {
		data: SurfaceResponse;
		repoSlug: string;
		runId: string;
	}

	let { data, repoSlug, runId }: Props = $props();
	let node = $derived(runNodeFromSurface(data, repoSlug, runId));
	let frame = $derived(node.state ? frontmatterDocument(node.state.markdown) : null);
	let knownPaths = $derived(new Set(data.files.map((file) => file.path)));
	let repoLabel = $derived(frame?.metadata.repo_label ?? repoSlug.replace('__', '/'));

	function messageTime(metadata: Record<string, string>): string {
		const raw = metadata.delivered_at ?? metadata.created_at ?? metadata.failed_at;
		if (!raw) return '';
		const timestamp = Date.parse(raw);
		return Number.isNaN(timestamp) ? raw : new Date(timestamp).toLocaleString();
	}
</script>

<div class="mx-auto max-w-3xl p-6">
	<header class="ignite">
		<div class="flex items-center justify-between gap-4">
			<a
				href="/"
				class="font-mono text-[11px] tracking-wide text-stone-500 uppercase hover:text-stone-300"
				>← loom</a
			>
			<span class="font-mono text-[10px] text-stone-600">{repoLabel}</span>
		</div>
		<p class="eyebrow mt-5">wyrd · run node</p>
		<h1 class="mt-1 break-all font-mono text-lg font-semibold tracking-tight text-amber-100">
			{runId}
		</h1>
		{#if frame}
			<div class="mt-2 flex flex-wrap gap-2 font-mono text-[10px] text-stone-500">
				{#if frame.metadata.status}<span>{frame.metadata.status}</span>{/if}
				{#if frame.metadata.stage}<span>· {frame.metadata.stage}</span>{/if}
				{#if frame.metadata.source}<span>· via {frame.metadata.source}</span>{/if}
				{#if frame.metadata.runner_name}<span>· {frame.metadata.runner_name}</span>{/if}
			</div>
		{/if}
	</header>

	{#if !node.state && !node.body && node.messages.length === 0}
		<section class="panel mt-6 p-4">
			<h2 class="font-mono text-sm text-amber-100">node not mirrored</h2>
			<p class="mt-2 text-sm text-stone-400">
				This run has a ledger receipt, but its state, body, and messages are not present in the
				current corpus snapshot.
			</p>
		</section>
	{:else}
		<section class="panel mt-6 p-4" aria-labelledby="frame-heading">
			<div class="flex items-baseline justify-between gap-3 border-b border-stone-800 pb-2">
				<h2 id="frame-heading" class="font-mono text-xs tracking-wide text-amber-200 uppercase">
					attested frame
				</h2>
				<span class="font-mono text-[10px] text-stone-600">daemon-owned</span>
			</div>
			{#if node.state && frame}
				{#if node.state.truncated}<p class="mt-3 text-xs text-amber-400">frame mirror truncated</p>{/if}
				<div class="text-sm text-stone-300">
					<MarkdownContent markdown={frame.body} sourcePath={node.state.path} {knownPaths} />
				</div>
			{:else}
				<p class="mt-3 text-sm text-stone-500">No attested frame was captured for this run.</p>
			{/if}
		</section>

		<section class="panel mt-4 p-4" aria-labelledby="body-heading">
			<div class="flex items-baseline justify-between gap-3 border-b border-stone-800 pb-2">
				<h2 id="body-heading" class="font-mono text-xs tracking-wide text-amber-200 uppercase">
					woven body
				</h2>
				<span class="font-mono text-[10px] text-stone-600">resident-owned</span>
			</div>
			{#if node.body}
				{#if node.body.truncated}<p class="mt-3 text-xs text-amber-400">body mirror truncated</p>{/if}
				<div class="text-sm text-stone-300">
					<MarkdownContent markdown={node.body.markdown} sourcePath={node.body.path} {knownPaths} />
				</div>
			{:else}
				<p class="mt-3 text-sm text-stone-500">This run predates the runfile weld or wrote no body.</p>
			{/if}
		</section>

		<section class="mt-6" aria-labelledby="traffic-heading">
			<div class="flex items-baseline justify-between gap-3">
				<div>
					<p class="eyebrow">edge traffic</p>
					<h2 id="traffic-heading" class="font-mono text-sm font-semibold text-amber-100">
						{node.messages.length} message{node.messages.length === 1 ? '' : 's'}
					</h2>
				</div>
				<span class="font-mono text-[10px] text-stone-600">chronological</span>
			</div>
			{#if node.messages.length === 0}
				<div class="panel mt-2 p-4 text-sm text-stone-500">No receipted messages for this run.</div>
			{:else}
				<div class="mt-2 space-y-2">
					{#each node.messages as message (message.file.path)}
						<article class="panel p-4">
							<div class="flex flex-wrap items-baseline justify-between gap-2 border-b border-stone-800 pb-2 font-mono text-[10px]">
								<span class="text-amber-200">
									{message.metadata.direction ?? 'out'} · {message.metadata.kind ?? 'message'}
								</span>
								<span class={message.metadata.status === 'failed' || message.metadata.status === 'undeliverable' ? 'text-red-400' : 'text-stone-500'}>
									{message.metadata.status ?? 'recorded'}{messageTime(message.metadata) ? ` · ${messageTime(message.metadata)}` : ''}
								</span>
							</div>
							{#if message.file.truncated}<p class="mt-3 text-xs text-amber-400">message mirror truncated</p>{/if}
							<div class="text-sm text-stone-300">
								<MarkdownContent markdown={message.body} sourcePath={message.file.path} {knownPaths} />
							</div>
						</article>
					{/each}
				</div>
			{/if}
		</section>
	{/if}
</div>
