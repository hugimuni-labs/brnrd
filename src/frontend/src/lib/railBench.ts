import type { ConnectedRepo } from './repos';

// The bench's own pure logic (w-68, the gauge/bench split). Split out for
// the same reason `controlStrip.ts`/`spoolRack.ts` are: pinned by plain
// node:test assertions instead of only by rendering.

export interface EnvironmentDisplay {
	/** The environment's own resolved name — never carries the word
	 *  "default" concatenated onto it. */
	name: string;
	/** True when this is the repo's resolved default — the *badge* sense of
	 *  "default", rendered as a separate element, never joined to `name` by
	 *  the same `·` the environment's own name may already use internally. */
	isDefault: boolean;
}

/**
 * #1516: the environment lane's own "default"/"default" collision — the
 * rack's twin, fixed in #1515 (`SpoolRack.coreLabel`). Against an
 * environment literally named `host · default` (the ordinary case on a
 * host-environment account), the old code built one string —
 * `` `${environment_default} · default}` `` in the slim bar, `` `default —
 * ${environment_default}` `` in the picker — joining the *badge* sense of
 * "default" (this is what the next wake resolves to) to the *name* sense
 * (the environment is literally called `default`) with the same `·` the
 * name already uses internally. A reader had nothing to cut the two words
 * on.
 *
 * The fix is the shape #1515 already set: keep the badge sense as its own
 * element, never concatenate it into the name string at all. This function
 * returns the two facts separately; every caller renders `name` and
 * `isDefault` as distinct nodes (a value plus a chip), never joined by a
 * separator character the name might already contain.
 */
export function environmentDisplay(
	selectedRepo: ConnectedRepo | null | undefined,
	environmentSelection: string | null
): EnvironmentDisplay {
	if (environmentSelection) return { name: environmentSelection, isDefault: false };
	if (selectedRepo?.environment_default) {
		return { name: selectedRepo.environment_default, isDefault: true };
	}
	return { name: 'no environment configured', isDefault: false };
}
