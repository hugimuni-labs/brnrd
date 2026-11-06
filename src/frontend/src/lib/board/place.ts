// Where he was on the board (w-kkghfg2vo6do): the bead sheet and its history, the selected letter, the grown columns and
// opened rows, the board's horizontal scroll and each column's. Saved to localStorage as the page hides and read back
// by the next page, so a restart lands where the last one stood. Pure: the Board reads and writes the storage.
export type Place = {
	bead: string | null;
	hist: string[];
	sel: string | null;
	selCol: string | null;
	grown: string[];
	open: string[];
	x: number;
	// a column's scroll: a number from the top, or 'end' when the reader was at the bottom (the pane sticks there)
	y: Record<string, number | 'end'>;
};

export const PLACE = 'board.place';
export const EMPTY: Place = {
	bead: null,
	hist: [],
	sel: null,
	selCol: null,
	grown: [],
	open: [],
	x: 0,
	y: {}
};

export function readPlace(json: string | null): Place {
	if (!json) return EMPTY;
	try {
		const p = JSON.parse(json);
		if (!p || typeof p !== 'object') return EMPTY;
		return {
			bead: typeof p.bead === 'string' ? p.bead : null,
			hist: Array.isArray(p.hist) ? p.hist.filter((x: unknown) => typeof x === 'string') : [],
			sel: typeof p.sel === 'string' ? p.sel : null,
			selCol: typeof p.selCol === 'string' ? p.selCol : null,
			grown: Array.isArray(p.grown) ? p.grown.filter((x: unknown) => typeof x === 'string') : [],
			open: Array.isArray(p.open) ? p.open.filter((x: unknown) => typeof x === 'string') : [],
			x: typeof p.x === 'number' && isFinite(p.x) ? p.x : 0,
			y: p.y && typeof p.y === 'object' ? p.y : {}
		};
	} catch {
		return EMPTY;
	}
}

// A pane's scroll as the place keeps it: within 40 px of the bottom is 'end', as Column's stick-to-bottom reads it.
export function paneScroll(top: number, height: number, client: number): number | 'end' {
	return height - client - top < 40 ? 'end' : Math.round(top);
}

// Whether a saved column scroll is a place to land: a number is, 'end' and nothing both mean the bottom.
export function landAt(y: number | 'end' | undefined): number | null {
	return typeof y === 'number' ? y : null;
}
