<script lang="ts">
	// The resident's mood, worn quietly beside a run's status word (#566).
	// One component for both renderings of a run — the LiveRuns grid card and
	// the selected-node panel — so the two surfaces cannot disagree about what
	// a mood looks like, and the house rule lives in exactly one place:
	//
	//   an unknown or absent mood renders as NOTHING or the bare handle name,
	//   never a guessed or default face (`brr.emotes`' own docstring).
	//
	// `moodFace` in `liveRuns.ts` is what enforces it; this file only draws
	// what it returns. Deliberately smaller and dimmer than the status word:
	// the mood is colour on a fact, not the fact.
	import type { MoodFace } from './liveRuns';

	interface Props {
		face?: MoodFace | null;
		class?: string;
	}

	let { face = null, class: klass = '' }: Props = $props();
</script>

{#if face}
	{@const label = face.glyph ? `${face.glyph} ${face.name}` : face.name}
	<span
		class="min-w-0 shrink truncate font-mono text-[9px] text-ink-mute {klass}"
		title="mood: {face.name}"
	>
		{label}
	</span>
{/if}
