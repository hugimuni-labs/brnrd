<script lang="ts">
	// The wish a pull request linked to, and the chain up to the letter that asked.
	// Other wishes on the thread stay collapsed. A query that names none of them is one line.
	import { showOrder, type ShownWish } from './wishLink.ts';

	let {
		wishes,
		selected = null,
		missing = null
	}: { wishes: ShownWish[]; selected?: string | null; missing?: string | null } = $props();

	let selectedEl = $state<HTMLElement | null>(null);
	let ordered = $derived(showOrder(wishes, selected));

	$effect(() => {
		const node = selectedEl;
		if (node && typeof node.scrollIntoView === 'function') node.scrollIntoView({ block: 'start' });
	});
</script>

{#if missing}
	<p class="mt-3 font-mono text-[11px] text-ink-quiet" data-testid="wish-missing">
		no wish {missing} on this thread
	</p>
{/if}

{#if ordered.length}
	<section class="mt-4" aria-labelledby="wishes-heading" data-testid="thread-wishes">
		<h2 id="wishes-heading" class="eyebrow mb-2">wishes</h2>
		<ul class="space-y-3">
			{#each ordered as wish (wish.id)}
				<li>
					{#if wish.id === selected}
						<article
							bind:this={selectedEl}
							class="subpanel border-l-2 border-l-amber-400 p-3"
							data-testid="wish-selected"
							data-wish={wish.id}
						>
							{#if wish.chain.length}
								<ol class="mb-3 space-y-2" data-testid="wish-chain">
									{#each wish.chain as step (step.id)}
										<li data-request={step.person ? 'true' : undefined}>
											<p class="text-sm leading-snug break-words text-stone-100">{step.line}</p>
											<p class="font-mono text-[10px] text-ink-mute">
												{step.person ? 'the request' : step.kind}
												{#if step.from}· {step.from}{/if}
											</p>
										</li>
									{/each}
								</ol>
							{/if}
							<p class="text-[15px] leading-snug text-amber-50" data-testid="wish-line">
								{wish.line}
							</p>
							{#if wish.state}
								<p class="mt-1 font-mono text-[10px] text-sky-300/80">{wish.state}</p>
							{/if}
						</article>
					{:else}
						<article class="subpanel p-3" data-wish={wish.id}>
							<p class="text-sm text-stone-300">{wish.line}</p>
						</article>
					{/if}
				</li>
			{/each}
		</ul>
	</section>
{/if}
