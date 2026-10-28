// The loom's board parts as the SPA reads them (brr.loom.runtime.board.compute): an index, one part per
// thread, and beads. Shapes follow board.py; fields the board does not read are left out.

export type Stamp = { stamp: string; at: string; ref?: string; line?: string; by?: string };
export type Verdict = { as: string; at: string; evidence?: string; ref?: string };
export type Result = {
	ref?: string;
	kind?: string;
	line?: string;
	at?: string;
	delivered?: boolean;
};
export type Say = { id: string; quote: string; cites: string };

export type Wish = {
	wish: string;
	line: string;
	state: string;
	origin?: string;
	thread?: string | null;
	at?: string;
	stamps: Stamp[];
	says?: Say[];
	letters?: string[];
	results?: Result[];
	verdict?: Verdict | null;
};

export type Letter = {
	id: string;
	at: string;
	from: string;
	to?: string;
	body: string;
	re?: string;
	person?: boolean;
	notice?: boolean;
	threads?: string[];
	typed_by?: string;
	answers?: string[];
	wishes?: { wish: string; thread?: string; line: string; state: string; stamps: Stamp[] }[];
	chatter?: string[];
	// each strand that picked the letter up, first sight; a channel send's state
	shown?: { strand: string; at: string }[];
	sent?: { state: string; at: string };
	open?: boolean;
	// a letter sent from this page that the ledger has not shown yet
	pendingNote?: boolean;
	wish?: string;
	verdict?: string;
};

export type Call = {
	kind: 'ask' | 'verdict';
	wish: string;
	line: string;
	at: string;
	waits?: string;
	ref?: string;
	result?: string;
	closed?: { verdict: string; letter: string; at: string };
};

export type Strand = { id: string; state?: string; body?: string };
export type Step = { text: string; wishes: string[]; receipt: string | null };

export type BoardState = 'seated' | 'your-call' | 'mine' | 'waiting' | 'quiet' | 'settled';

export type Thread = {
	id: string;
	title?: string;
	status?: string;
	url?: string;
	opened_at?: string;
	last_at?: string;
	opened_for?: { wish: string }[];
	strands?: Strand[];
	steps?: Step[];
	plan?: { done: number; total: number; bytes?: number; cap?: number };
	board?: {
		state?: BoardState;
		since?: string;
		stall?: boolean;
		red?: boolean;
		strand?: string;
		why?: string;
		waits?: boolean;
		open?: boolean;
		ready?: boolean;
		unanswered?: string[];
		calls?: Call[];
	};
};

export type ToolCall = {
	tool: string;
	at: string;
	end?: string;
	arg?: string;
	cmd?: string;
	say?: string;
	by?: string;
	failed?: boolean;
};

export type Live = {
	open: ToolCall[];
	say?: string;
	last_end?: string;
	paused?: boolean;
	paused_at?: string;
};

export type Gauge = { window: string; left_pct: number };
export type Fuel = { shell: string; gauges: Gauge[] };

export type Index = {
	v: number;
	threads: Thread[];
	fuel?: Fuel[];
	// the rail's face frames by 'running' / 'idle' (brr.emotes.for_telemetry)
	faces?: Record<string, string[]>;
	wishes: Record<string, Record<string, number>>;
	unthreaded: Wish[];
	person: string | null;
	// the channel a letter typed on the board goes out through
	via?: string | null;
	parts: Record<string, string>;
};

export type ThreadPart = {
	id: string;
	count: number;
	letters: Letter[];
	wishes: Wish[];
	tools: Record<string, ToolCall[]>;
	live: Record<string, Live>;
	more?: boolean;
};

// What the board reads at once, assembled from the parts (parts.ts).
export type Model = {
	threads: Thread[];
	letters: Letter[];
	byId: Map<string, Letter>;
	wishes: Wish[];
	tools: Record<string, ToolCall[]>;
	live: Record<string, Live>;
	fuel: Fuel[];
	faces?: Record<string, string[]>;
	person: string | null;
	via?: string | null;
};
