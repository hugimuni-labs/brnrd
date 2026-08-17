// Pure logic for the in-place consent popover (WithheldNotice's affordance,
// ConsentPopover.svelte). Split out from the .svelte file per this repo's
// convention (LiveRuns.svelte + liveRuns.ts) — this is the half a plain
// node:test can exercise without compiling Svelte to render markup. Named
// `consentGap`, not `consentPopover`, deliberately: this filesystem is
// case-insensitive, so `consentPopover.ts` would collide with
// `ConsentPopover.svelte` on every reader that doesn't preserve case
// (`BackchannelQueue.svelte`'s own logic sidesteps the same trap by living
// in `backchannel.ts`, not `backchannelQueue.ts`).

import { PUBLISH_LANES, type PublishLane } from './publishScope.ts';
import type { WithheldLane } from './withheld.ts';

export interface ConsentGapRepo {
	id: string;
	name: string;
	// Same distinction `repos_without_publish_consent` draws server-side:
	// never asked vs. asked-and-declined. The popover's per-repo row states
	// which, same as WithheldNotice's own sentence does today.
	reason: 'unrecorded' | 'opted_out';
}

// Zips `withheld.unrecorded`/`opted_out` against their id-bearing twins
// (same order, same length — both minted from one server-side pass, see
// `_withheld_lane` in dashboard.py). A name with no id at its index is
// dropped rather than guessed: an older backend or an in-flight deploy could
// still ship names without ids, and a row with no real id has no in-place
// act to offer — the /repos fallback link covers it instead.
export function consentGapRepos(withheld: WithheldLane): ConsentGapRepo[] {
	const rows: ConsentGapRepo[] = [];
	const unrecordedNames = withheld.unrecorded ?? [];
	const unrecordedIds = withheld.unrecorded_ids ?? [];
	unrecordedNames.forEach((name, index) => {
		const id = unrecordedIds[index];
		if (id) rows.push({ id, name, reason: 'unrecorded' });
	});
	const optedOutNames = withheld.opted_out ?? [];
	const optedOutIds = withheld.opted_out_ids ?? [];
	optedOutNames.forEach((name, index) => {
		const id = optedOutIds[index];
		if (id) rows.push({ id, name, reason: 'opted_out' });
	});
	return rows;
}

export function laneForWithheld(withheld: WithheldLane): PublishLane | null {
	return PUBLISH_LANES.find((lane) => lane.value === withheld.lane) ?? null;
}

// `PUBLISH_LANES` labels read "Corpus & knowledge — authored pages, kb, run
// bodies" — the em-dash splits a short name from the honest what-it-carries
// clause. The popover wants only the second half (the name already sits in
// the dialog heading); this is the one place that split happens; do not
// re-derive it a second way elsewhere.
export function laneShareClause(lane: PublishLane): string {
	const [, clause] = lane.label.split(/\s+—\s+/);
	return clause ?? lane.label;
}
