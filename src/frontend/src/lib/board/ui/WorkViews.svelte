<script lang="ts">
	import { page } from '$app/state';
	import { replaceState } from '$app/navigation';
	import { onMount } from 'svelte';
	import { allCalls, answered } from '../calls.ts';
	import type { Call, Goal, GoalAsk } from '../types.ts';
	import { board } from './board.svelte.ts';
	import Ref from './Ref.svelte';

	let { view = $bindable(null) }: { view: 'goals' | 'needs' | 'ready' | null } = $props();
	const b = board();
	const calls = $derived(
		allCalls(b.model?.threads ?? []).filter((call) => !call.stale && !answered(call, b.pending))
	);
	const asks = $derived(calls.filter((call) => call.kind === 'ask'));
	const reviews = $derived(calls.filter((call) => call.kind === 'verdict'));
	const views = $derived([
		{ id: 'goals', label: 'Goals' },
		{ id: 'needs', label: 'Needs you', count: asks.length },
		{ id: 'ready', label: 'Ready', count: reviews.length }
	] as const);
	let selectedCall = $state<Call | null>(null);
	const target = $derived(page.url.searchParams.get('delivery'));
	const historical = $derived.by((): Call | undefined => {
		if (!target) return;
		for (const wish of b.model?.wishes ?? []) {
			const result = wish.results?.find((entry) => entry.delivery_id === target);
			if (result)
				return {
					kind: 'verdict',
					wish: wish.wish,
					delivery_id: target,
					line: wish.line,
					at: result.at || '',
					ref: result.ref,
					result: result.line
				};
		}
	});
	const selected = $derived(target ? historical || selectedCall : selectedCall);
	const current = $derived(
		selected &&
			calls.find(
				(c) =>
					c.wish === selected?.wish &&
					c.delivery_id === selected?.delivery_id &&
					c.at === selected?.at
			)
	);
	const decision = $derived.by(() => {
		if (!selected?.delivery_id) return;
		const id = selected.delivery_id;
		const wish = b.model?.wishes.find((w) => w.wish === selected?.wish);
		if (wish?.verdict?.delivery_id === id) return wish.verdict.as;
		const letter = [...b.letters]
			.reverse()
			.find((row) => row.person && row.delivery_id === id && row.verdict);
		if (letter?.pendingNote) return 'sent';
		return letter?.verdict === 'correct' ? 'correction recorded' : undefined;
	});

	onMount(() => {
		if (target) view = 'ready';
		const wish = page.url.searchParams.get('wish');
		if (wish && !target) b.openBead(wish, 'keep');
	});

	function select(call: Call | null) {
		selectedCall = call;
		const url = new URL(page.url);
		if (call?.delivery_id) url.searchParams.set('delivery', call.delivery_id);
		else url.searchParams.delete('delivery');
		// eslint-disable-next-line svelte/no-navigation-without-resolve -- change only the current page's query
		if (url.href !== page.url.href) replaceState(url, page.state);
	}

	function openAsk(ask: GoalAsk) {
		const call = asks.find((c) => c.wish === ask.wish);
		if (!call) return b.openBead(ask.wish, 'keep');
		b.closeBead();
		view = 'needs';
		select(call);
	}

	function openDelivery(goal: Goal) {
		const d = goal.delivery;
		if (!d) return;
		if (!d.delivery_id) return b.openBead(d.wish, 'keep');
		b.closeBead();
		view = 'ready';
		// The live review when it is still waiting, so Accept and Correct act on it; else the record.
		const live = reviews.find((c) => c.delivery_id === d.delivery_id);
		if (live) return select(live);
		select({
			kind: 'verdict',
			wish: d.wish,
			delivery_id: d.delivery_id,
			line: d.line,
			at: d.at,
			ref: d.ref
		});
	}

	function tally(goal: Goal): string {
		const counts = Object.entries(goal.progress ?? {});
		if (!counts.length) return 'No work linked yet';
		return counts.map(([state, n]) => `${n} ${state}`).join(' · ');
	}

	function switchView(next: typeof view) {
		b.closeBead();
		select(null);
		view = next;
	}
</script>

<nav aria-label="Work views">
	{#each views as item (item.id)}
		<button
			type="button"
			aria-pressed={view === item.id}
			onclick={() => switchView(view === item.id ? null : item.id)}
		>
			{item.label}{'count' in item && item.count ? ' ' + item.count : ''}
		</button>
	{/each}
</nav>

{#if view}
	<section aria-label={views.find((item) => item.id === view)?.label}>
		<button type="button" onclick={() => switchView(null)}>← Board</button>
		{#if !b.model}
			<p>{b.error || 'Loading work…'}</p>
		{:else if view === 'goals'}
			<h2>Goals</h2>
			{#each b.model.goals ?? [] as goal (goal.id)}
				<details>
					<summary>
						<strong>{goal.id.replaceAll('-', ' ')}</strong> · {goal.stands}
						{#if goal.blocked}<em> · waits on you</em>{:else if goal.stalled}<em>
								· stalled</em
							>{/if}
					</summary>
					<p>
						{tally(goal)}{#if goal.moved}
							· last move {goal.moved.slice(0, 16).replace('T', ' ')}Z{/if}
					</p>
					{#each goal.asks ?? [] as ask (ask.wish)}
						<p>
							<button type="button" onclick={() => openAsk(ask)}>Needs you · {ask.line}</button>
						</p>
					{/each}
					{#if goal.delivery}
						<p>
							<button type="button" onclick={() => openDelivery(goal)}
								>Latest delivery · {goal.delivery.line}</button
							>
						</p>
					{/if}
					<dl>
						<dt>Now</dt>
						<dd>{goal.now}</dd>
						<dt>Next</dt>
						<dd>{goal.next}</dd>
						<dt>What’s at stake</dt>
						<dd>{goal.stake}</dd>
					</dl>
				</details>
			{:else}
				<p>
					{b.model.goals ? 'No active goals.' : 'Goals are unavailable from this board connection.'}
				</p>
			{/each}
		{:else if target && !selected}
			<h2>Delivery unavailable</h2>
			<p>This board has no record of the linked delivery.</p>
		{:else if selected}
			<button
				type="button"
				onclick={() => {
					b.closeBead();
					select(null);
				}}>← {view === 'needs' ? 'Needs you' : 'Ready'}</button
			>
			<h2>{selected.line}</h2>
			{#if selected.blocks}<p><strong>Holds up</strong> · {selected.blocks}</p>{/if}
			{#if selected.next}<p><strong>Next move</strong> · {selected.next}</p>{/if}
			{#if selected.meanwhile?.length}
				<p><strong>Continues meanwhile</strong></p>
				{#each selected.meanwhile as other (other.wish)}
					<p>
						<button type="button" onclick={() => b.openBead(other.wish, 'keep')}
							>{other.line} · {other.state}</button
						>
					</p>
				{/each}
			{/if}
			{#if selected.parent}
				<button type="button" onclick={() => b.openBead(selected!.parent!, 'keep')}
					>Related work</button
				>
			{/if}
			{#if selected.result}<p>{selected.result}</p>{/if}
			{#if selected.ref}<p><Ref ref={selected.ref} /></p>{/if}
			<button type="button" onclick={() => b.openBead(selected!.wish, 'keep')}>Wish history</button>
			{#if current && (view === 'needs' || current.delivery_id)}
				<button
					type="button"
					disabled={b.sending.has(current.wish)}
					onclick={() =>
						b.answer(current!.wish, view === 'needs' ? 'done' : 'agree', current!.delivery_id)}
				>
					{view === 'needs' ? 'Done' : 'Accept'}
				</button>
				<button
					type="button"
					onclick={() => {
						b.answer(current!.wish, 'correct', current!.delivery_id);
					}}>{view === 'needs' ? 'Reply with details' : 'Correct'}</button
				>
			{:else if current}
				<p>Review actions require an updated board connection.</p>
			{:else if decision}
				<p>Decision · {decision}</p>
			{:else}
				<p>This item is no longer waiting for a decision.</p>
			{/if}
		{:else}
			<h2>{view === 'needs' ? 'Needs you' : 'Ready'}</h2>
			{#each view === 'needs' ? asks : reviews as call (call.wish)}
				<article>
					<button type="button" onclick={() => select(call)}>{call.line}</button>
					{#if call.next || call.result}<p>{call.next || call.result}</p>{/if}
				</article>
			{:else}
				<p>
					{view === 'needs' ? 'No decisions waiting for you.' : 'No deliveries awaiting review.'}
				</p>
			{/each}
		{/if}
	</section>
{/if}

<style>
	nav {
		display: flex;
		gap: 0.5rem;
		padding: 0.4rem 1rem;
		border-bottom: 1px solid #aaa;
	}
	button {
		min-height: 44px;
		padding: 0.4rem 0.75rem;
		font: inherit;
		color: inherit;
		border: 1px solid #aaa;
		background: white;
		border-radius: 0.25rem;
		cursor: pointer;
	}
	button[aria-pressed='true'] {
		color: white;
		background: #171717;
		border-color: #171717;
	}
	section {
		flex: 1;
		min-height: 0;
		overflow: auto;
		padding: 1rem;
		font-size: 14px;
	}
	h2 {
		margin-block: 1rem;
		overflow-wrap: anywhere;
	}
	article,
	details {
		max-width: 64ch;
		padding-block: 0.75rem;
		border-bottom: 1px solid #ddd;
		overflow-wrap: anywhere;
	}
	article button {
		text-align: left;
	}
	summary {
		min-height: 44px;
		cursor: pointer;
	}
	dt {
		font-weight: bold;
	}
	dd {
		margin: 0 0 0.75rem;
	}
</style>
