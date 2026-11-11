<script lang="ts">
	// The topics column: one row per topics/<slug>.md, newest-entry first. Press unfolds the newest quote;
	// a selected letter that cites the page marks the row.
	import { hue, short } from '../text.ts';
	import { board } from './board.svelte.ts';
	const b = board();
	const rows = $derived(b.model?.topics || []);
</script>

<section class="column topics" data-column="topics" data-b="quiet" style:--h={hue('topics')}>
	<div class="heading">
		<h2>topics</h2>
		<div class="state">{rows.length} pages · newest entry first</div>
	</div>
	<div class="messages">
		{#each rows as row (row.id)}
			{@const open = b.open.has('topic:' + row.id)}
			<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
			<div
				class="topic-row"
				class:open
				class:linked={(row.cites || []).includes(b.sel || '')}
				data-topic={row.id}
				onclick={() => b.toggle(b.open, 'topic:' + row.id)}
			>
				<b>{row.id}</b>
				{#if row.at}· {row.at}{/if}
				· {row.line || row.title}
				{#if open}
					{#if row.quote}<div class="from-body">{row.quote}</div>{/if}
					{#if (row.cites || []).length}
						<div class="topic-cites">
							{#each row.cites || [] as id (id)}<button
									type="button"
									class="ref"
									onclick={(e) => {
										e.stopPropagation();
										b.openBead(id);
									}}>{short(id)}</button
								>{/each}
						</div>
					{/if}
				{/if}
			</div>
		{:else}<div class="empty">no topics</div>{/each}
	</div>
</section>
