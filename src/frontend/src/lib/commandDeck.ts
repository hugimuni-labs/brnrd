import { readyItems, type WarpGraph, type WarpItem } from './warpGraph.ts';
import type { LiveRun } from './liveRuns.ts';

export type DeckSection = 'overview' | 'objectives' | 'history' | 'library' | 'shelf';

export type DeckCommand =
	| { kind: 'section'; section: DeckSection }
	| { kind: 'open'; id: string }
	| { kind: 'crew'; index: number }
	| { kind: 'look' | 'inspect' | 'map' | 'help' | 'unknown' };

export function parseDeckCommand(text: string): DeckCommand {
	const command = text.trim().toLowerCase();
	const sections: Record<string, DeckSection> = {
		bridge: 'overview',
		objectives: 'objectives',
		log: 'history',
		knowledge: 'library',
		artifacts: 'shelf'
	};
	if (Object.hasOwn(sections, command)) return { kind: 'section', section: sections[command] };
	const open = /^open ([a-z0-9][a-z0-9-]*)$/.exec(command);
	if (open) return { kind: 'open', id: open[1] };
	const crew = /^crew ([1-9]\d*)$/.exec(command);
	if (crew) return { kind: 'crew', index: Number(crew[1]) - 1 };
	if (command === 'look' || command === 'inspect' || command === 'map' || command === 'help')
		return { kind: command };
	return { kind: 'unknown' };
}

/** Readiness comes from the graph; an old taken row is not a live assignment. */
export function commandQueue(graph: WarpGraph, runs: LiveRun[]): WarpItem[] {
	const liveIds = new Set(runs.flatMap((run) => [run.id, run.run_id]));
	return readyItems(graph)
		.filter(
			(item) =>
				(item.type === 'decision' || item.type === 'preparation') &&
				!item.taken.some((id) => liveIds.has(id))
		)
		.sort((a, b) => unlockCount(b.id, graph) - unlockCount(a.id, graph));
}

/** Direct, open dependents, not a claim that answering this clears every blocker. */
export function unlockCount(id: string, graph: WarpGraph): number {
	return graph.items.filter((item) => item.state === 'open' && item.needs.includes(id)).length;
}

export function missionRun(runs: LiveRun[], selectedId: string | null): LiveRun | null {
	return (
		runs.find((run) => run.id === selectedId) ??
		runs.find((run) => !run.is_subspawn && !run.parent_run_id) ??
		runs[0] ??
		null
	);
}
