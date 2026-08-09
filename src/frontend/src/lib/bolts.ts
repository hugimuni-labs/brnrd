import type { RelicRecord, RunLedgerRow } from './runLedger';

// The bolt's cloth-side ack store (design-the-bolt.md §The cloth side, fork
// 2 signed: a compact summons strip at the page head → tap jumps to the
// cloth-head lane → the lane glows on arrival — moving sections,
// force-scroll, and a modal are all explicitly rejected).
//
// Per-viewer is the design requirement itself (the teams curveball: an
// away-ack must never be account-global), and localStorage satisfies that
// for the single-user pre-release. Server-side per-viewer state is the
// teams-era successor — noted, not built here.
//
// The storage shape follows the one existing pattern (`publishScope.ts`'s
// `connectPublishScopeStorageKey` + validate-on-read): namespace the key by
// account, and let anything unreadable degrade to "nothing taken" rather
// than throwing — a viewer's local ack state must never break the page it
// lives on.

const BOLT_STORAGE_PREFIX = 'brnrd.bolts.taken';

/** FIFO cap on the stored ack list — bounded so a long-lived account's
 *  local storage entry cannot grow forever; the oldest acks age out first. */
export const BOLT_TAKEN_CAP = 200;

export function boltsTakenStorageKey(accountId: string): string {
	return `${BOLT_STORAGE_PREFIX}.${accountId}`;
}

/** Parse the stored ack list. Anything that isn't a JSON array of non-empty
 *  strings — absent, corrupt, wrong shape, a stray number — reads as
 *  "nothing taken" instead of throwing. */
export function readTakenBolts(raw: string | null | undefined): string[] {
	if (!raw) return [];
	let parsed: unknown;
	try {
		parsed = JSON.parse(raw);
	} catch {
		return [];
	}
	if (!Array.isArray(parsed)) return [];
	return parsed.filter((item): item is string => typeof item === 'string' && item.length > 0);
}

/** Serialize the ack list for storage, applying the FIFO cap on write so
 *  the stored value never grows past it regardless of how it was built. */
export function serializeTakenBolts(taken: string[]): string {
	return JSON.stringify(taken.slice(-BOLT_TAKEN_CAP));
}

/** Ack one run id. Idempotent in content — acking an id already in the
 *  store keeps one entry — and a re-take moves it to the front of the FIFO
 *  so it doesn't age out early just for being taken twice. */
export function takeBolt(taken: string[], runId: string): string[] {
	if (!runId) return taken;
	const next = taken.filter((id) => id !== runId);
	next.push(runId);
	return next.slice(-BOLT_TAKEN_CAP);
}

/** Ack every id in one write. */
export function takeAll(taken: string[], runIds: string[]): string[] {
	let next = taken;
	for (const runId of runIds) next = takeBolt(next, runId);
	return next;
}

/** The frame's `bolt:` value (data contract: `state.md` frontmatter gains
 *  `bolt: accepted <iso>` | `bolt: annotated <iso>`). Tolerant of anything
 *  a reader doesn't recognise — absent or unknown is "no bolt", never an
 *  error; the writer ships separately and old rows carry no field at all. */
export type BoltState = 'accepted' | 'annotated';

export function parseBoltState(value: string | null | undefined): BoltState | null {
	return value === 'accepted' || value === 'annotated' ? value : null;
}

/** One run carrying an unacked bolt, curated for the strip and the lane. */
export interface BoltRow {
	runId: string;
	/** The run's own name when it has one, else its id — never invented,
	 *  the same rule the cloth's own curated line follows. */
	name: string;
	named: boolean;
	bolt: BoltState;
	repoLabel: string | null;
	/** Epoch ms the run ended — what "newest first" sorts on. */
	endedAt: number;
	relics: RelicRecord[];
	/** The ledger's *measured* spend for this run — carried through for the
	 *  completion card's spend section (design-the-bolt.md §The completion
	 *  card). Nulls when the ledger row never carried them (a re-report
	 *  before the measurement lane populated, or a row that predates it) —
	 *  the card renders that as an honest absence, never a zero. */
	wallClockSeconds: number | null;
	tokensInput: number | null;
	tokensOutput: number | null;
	usdSubscriptionAttributed: number | null;
	usdCreditsEquivalent: number | null;
	/** The validated `cut:` declaration this run's bolt carried, if the wire
	 *  has one (#1236 threads `bolt_declaration` through the ledger; see the
	 *  completion-card section below for the shape). `null` for a row that
	 *  predates the wire or was never cut — the same absence discipline the
	 *  rest of this interface follows. */
	declaration: BoltDeclarationValue;
}

/**
 * A run "carries a bolt" = ended + a `bolt` field present. "Unacked" = it
 * carries a bolt and its run id is not in the viewer's ack store
 * (design-the-bolt.md §Data contract).
 *
 * One run can arrive as several ledger rows (re-reports); merged by run id
 * the same way the cloth itself merges (`cloth.ts`'s `mergeRuns`) — relics
 * accumulate, the latest close and bolt state win, the name fills in
 * first-known. Rows with no `run_id` are skipped: taking a bolt needs a
 * stable id to ack against, and a row without one can never leave the
 * strip.
 */
export function unackedBolts(
	rows: RunLedgerRow[],
	takenIds: ReadonlySet<string> | string[]
): BoltRow[] {
	const taken = takenIds instanceof Set ? takenIds : new Set(takenIds);
	const byId = new Map<string, BoltRow>();
	for (const row of rows) {
		const runId = row.run_id;
		if (!runId || taken.has(runId)) continue;
		const bolt = parseBoltState(row.bolt);
		if (!bolt) continue;
		const declaration = parseBoltDeclaration(row.bolt_declaration);
		const endedAt = row.ended_at ? Date.parse(row.ended_at) : Number.NaN;
		if (!Number.isFinite(endedAt)) continue;
		const name = row.name?.trim() || null;
		const existing = byId.get(runId);
		if (existing) {
			existing.endedAt = Math.max(existing.endedAt, endedAt);
			existing.bolt = bolt;
			// Same re-report as the bolt state itself — the declaration is
			// stamped alongside `bolt`/`accepted_at` in one write
			// (`daemon.py::_drain_outbox`), so it latest-wins on the same
			// row, never merged field-by-field the way spend is below.
			existing.declaration = declaration;
			existing.relics.push(...(row.external_refs ?? []));
			existing.repoLabel ??= row.repo_label;
			// Spend, like bolt state, is this re-report's own measurement — a
			// later row that actually carries a value wins, same as `bolt`
			// itself; a re-report that leaves a field null must not blank
			// out a figure an earlier row already measured.
			if (row.wall_clock_seconds !== null && row.wall_clock_seconds !== undefined) {
				existing.wallClockSeconds = row.wall_clock_seconds;
			}
			if (row.tokens_input !== null && row.tokens_input !== undefined) {
				existing.tokensInput = row.tokens_input;
			}
			if (row.tokens_output !== null && row.tokens_output !== undefined) {
				existing.tokensOutput = row.tokens_output;
			}
			if (
				row.usd_subscription_attributed !== null &&
				row.usd_subscription_attributed !== undefined
			) {
				existing.usdSubscriptionAttributed = row.usd_subscription_attributed;
			}
			if (row.usd_credits_equivalent !== null && row.usd_credits_equivalent !== undefined) {
				existing.usdCreditsEquivalent = row.usd_credits_equivalent;
			}
			if (!existing.named && name) {
				existing.name = name;
				existing.named = true;
			}
		} else {
			byId.set(runId, {
				runId,
				name: name ?? runId,
				named: name !== null,
				bolt,
				declaration,
				repoLabel: row.repo_label,
				endedAt,
				relics: [...(row.external_refs ?? [])],
				wallClockSeconds: row.wall_clock_seconds ?? null,
				tokensInput: row.tokens_input ?? null,
				tokensOutput: row.tokens_output ?? null,
				usdSubscriptionAttributed: row.usd_subscription_attributed ?? null,
				usdCreditsEquivalent: row.usd_credits_equivalent ?? null
			});
		}
	}
	return Array.from(byId.values()).sort((a, b) => b.endedAt - a.endedAt);
}

/** The strip's copy: `1 bolt awaits taking` singular, `N bolts await taking`
 *  otherwise — the only two shapes the strip ever renders. */
export function boltSummonsLabel(count: number): string {
	return count === 1 ? '1 bolt awaits taking' : `${count} bolts await taking`;
}

// ── The completion card (design-the-bolt.md §The completion card) ─────────
//
// Data honesty is the constraint the design doc names as this account's
// dominant defect class: render only what actually arrives on the wire, and
// say plainly when a section's data does not.
//
// The wire audit this note used to document (report at the declared path,
// #1255): `cut_verb.py` parses a full declaration — `asks`, `decisions`,
// `owed`, a declared `spend` estimate, `next` — but `daemon.py`'s
// `_drain_outbox` kept only `{accepted_at, annotated, spend_declared}` in
// `task.meta["bolt"]`, and both `state.md` and `run_ledger.py`'s
// `_bolt_value` narrowed that further to a bare `"accepted" | "annotated"`
// string. Nothing past that flag ever reached this frontend.
//
// #1236 closed that gap upstream: `cut_verb.durable_declaration` produces a
// bounded, capped copy of the validated declaration plus the daemon's own
// dissent, `_drain_outbox` keeps it whole on `task.meta["bolt"]`, and
// `run_ledger.py`'s `bolt_declaration_value` carries it into the ledger row
// (`RunLedgerRow.bolt_declaration` below) and the run node's own
// `## Bolt Declaration` JSON block. This module's job past that point is
// the same tolerant-read discipline every other export here follows
// (`parseBoltState`, `readTakenBolts`): a row that predates the wire, or
// whose declaration blew the persistence caps and was replaced by an
// explicit `{omitted: true, reason}` marker, must read as its own honest
// state — never silently as "nothing declared" and never as a crash.

/** One `asks:` row, curated for the card — an event this run carried and
 *  how it was closed (`answered` / `deferred:<where>` / `noted:<why>`,
 *  `cut_verb.py`'s `_valid_disposition`). */
export interface BoltAsk {
	event: string;
	disposition: string;
	label?: string;
}

/** One carried `owed:` row — a promise named rather than shipped. */
export interface BoltOwedRow {
	label: string;
	ref: string;
	why: string;
	where?: string;
}

/** The validated declaration, curated for the card (wire field names
 *  translated to this module's usual camelCase, same as `BoltRow` translates
 *  `wall_clock_seconds` → `wallClockSeconds`). Every array is present, even
 *  when empty — an empty array is the honest "declared, nothing here" state;
 *  `BoltDeclarationValue`'s `null` is the one that means "not carried". */
export interface BoltDeclaration {
	asks: BoltAsk[];
	owed: BoltOwedRow[];
	decisions: string[];
	spendDeclared: string | null;
	next: string | null;
	/** The daemon's own mismatch lines, present only when the bolt is
	 *  `annotated` — `cut_verb.durable_declaration`'s `dissent` tuple. */
	dissent: string[];
}

/** The declaration existed but exceeded `cut_verb.py`'s persistence caps
 *  (64 rows per section, 1024 chars per field, 64KB whole). Distinct from
 *  `null`: the resident *did* declare something, it just could not be
 *  stored whole — never rendered as a plain empty declaration. */
export interface BoltDeclarationOmitted {
	omitted: true;
	reason: string;
}

/** `null` = not carried on this row (predates #1236, or the run was never
 *  cut). Tolerant of anything else a reader doesn't recognise, same
 *  contract `parseBoltState` holds for the bare flag. */
export type BoltDeclarationValue = BoltDeclaration | BoltDeclarationOmitted | null;

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function stringArray(value: unknown): string[] {
	return Array.isArray(value)
		? value.filter((item): item is string => typeof item === 'string')
		: [];
}

function parseAsk(raw: unknown): BoltAsk | null {
	if (!isRecord(raw)) return null;
	const event = typeof raw.event === 'string' ? raw.event : '';
	const disposition = typeof raw.disposition === 'string' ? raw.disposition : '';
	if (!event || !disposition) return null;
	const label = typeof raw.label === 'string' && raw.label ? raw.label : undefined;
	return label ? { event, disposition, label } : { event, disposition };
}

function parseOwedRow(raw: unknown): BoltOwedRow | null {
	if (!isRecord(raw)) return null;
	const ref = typeof raw.ref === 'string' ? raw.ref : '';
	const why = typeof raw.why === 'string' ? raw.why : '';
	if (!ref || !why) return null;
	const label = typeof raw.label === 'string' ? raw.label : '';
	const where = typeof raw.where === 'string' && raw.where ? raw.where : undefined;
	return where ? { label, ref, why, where } : { label, ref, why };
}

/** Tolerant read of the ledger's `bolt_declaration` column. Absent,
 *  malformed, or shaped unlike anything `run_ledger.bolt_declaration_value`
 *  produces reads as `null` — "not carried here" — rather than throwing;
 *  every other reader in this module degrades the same way. */
export function parseBoltDeclaration(raw: unknown): BoltDeclarationValue {
	if (!isRecord(raw)) return null;
	if (raw.omitted === true) {
		const reason =
			typeof raw.reason === 'string' && raw.reason ? raw.reason : 'persistence limits exceeded';
		return { omitted: true, reason };
	}
	const asks = Array.isArray(raw.asks)
		? raw.asks.map(parseAsk).filter((a): a is BoltAsk => a !== null)
		: [];
	const owed = Array.isArray(raw.owed)
		? raw.owed.map(parseOwedRow).filter((o): o is BoltOwedRow => o !== null)
		: [];
	const spendDeclared =
		typeof raw.spend_declared === 'string' && raw.spend_declared ? raw.spend_declared : null;
	const next = typeof raw.next === 'string' && raw.next ? raw.next : null;
	return {
		asks,
		owed,
		decisions: stringArray(raw.decisions),
		spendDeclared,
		next,
		dissent: stringArray(raw.dissent)
	};
}

/** The verdict head's label — `bolt.ts`'s two-value contract, worded for
 *  the card's first line. */
export function boltVerdictLabel(bolt: BoltState): string {
	return bolt === 'annotated' ? 'accepted — with dissent' : 'accepted';
}

/** A declaration-backed section's own render state, one notch richer than
 *  the plain `'present' | 'empty'` `produce`/`spend` use: `'omitted'` is the
 *  capped-declaration case, distinct from both "declared, nothing here"
 *  (`'empty'`) and "never carried on this row" (`'absent'`). */
export type DeclarationSectionState = 'present' | 'empty' | 'omitted' | 'absent';

function declarationSectionState(
	declaration: BoltDeclarationValue,
	hasContent: (d: BoltDeclaration) => boolean
): DeclarationSectionState {
	if (!declaration) return 'absent';
	if ('omitted' in declaration) return 'omitted';
	return hasContent(declaration) ? 'present' : 'empty';
}

/** Sections the completion card is prepared to render honestly, each tagged
 *  with whether real data reached the wire for it. `present` sections get
 *  the design's rendering; `absent` ones render as a labeled absence
 *  (design doc: "render a labeled absence... do not render empty-as-clean")
 *  rather than being skipped — a skipped section reads as "nothing to
 *  report" when the true state is "never carried here at all", which is
 *  exactly the distinction the constraint exists to keep visible.
 *
 *  `produce` and `spend` (the *measured* stamp) are conditioned on whether
 *  *this row* actually carries anything — an empty produce list or a null
 *  spend figure is a real, honest state, not a missing wire. `asks`,
 *  `decisions`, `owed`, and `spendDeclared` (the resident's estimate) read
 *  off the declaration itself (#1236): `'absent'` when no declaration
 *  argument is given at all, matching every call site that predates it. */
export interface BoltCardSections {
	asks: DeclarationSectionState;
	decisions: DeclarationSectionState;
	produce: 'present' | 'empty';
	owed: DeclarationSectionState;
	spend: 'present' | 'empty';
	spendDeclared: DeclarationSectionState;
}

/** The completion card's own data shape — the subset of `BoltRow` the card
 *  actually renders, decoupled from the strip/lane's ack-filtering. */
export interface BoltCardData {
	bolt: BoltState;
	relics: RelicRecord[];
	wallClockSeconds: number | null;
	tokensInput: number | null;
	tokensOutput: number | null;
	usdSubscriptionAttributed: number | null;
	usdCreditsEquivalent: number | null;
	declaration: BoltDeclarationValue;
}

/** Merge every ledger row already known to belong to *one* run
 *  (`runNode.ts`'s `runLedgerRowsForNode`) into the card's data shape. Unlike
 *  `unackedBolts` (many runs, ack-filtered, keyed by id) this needs no key
 *  and no dedup — every row in the list is this run's own re-report history,
 *  latest-value-wins per field, same rule `unackedBolts` merges by. Null
 *  when no row carries a recognised bolt value: mirrors `parseBoltState`'s
 *  own contract that an absent/unrecognised value is "no bolt", never an
 *  error — the run node's "no `## Bolt` section" case, not a wire failure. */
export function boltCardDataFromLedgerRows(rows: RunLedgerRow[]): BoltCardData | null {
	let bolt: BoltState | null = null;
	let declaration: BoltDeclarationValue = null;
	const relics: RelicRecord[] = [];
	let wallClockSeconds: number | null = null;
	let tokensInput: number | null = null;
	let tokensOutput: number | null = null;
	let usdSubscriptionAttributed: number | null = null;
	let usdCreditsEquivalent: number | null = null;
	for (const row of rows) {
		const rowBolt = parseBoltState(row.bolt);
		if (rowBolt) {
			bolt = rowBolt;
			// Same write as `bolt` itself (`daemon.py::_drain_outbox` stamps
			// both in one `task.meta["bolt"]` update) — latest-wins together,
			// not merged field-by-field the way spend is below.
			declaration = parseBoltDeclaration(row.bolt_declaration);
		}
		relics.push(...(row.external_refs ?? []));
		if (row.wall_clock_seconds !== null && row.wall_clock_seconds !== undefined) {
			wallClockSeconds = row.wall_clock_seconds;
		}
		if (row.tokens_input !== null && row.tokens_input !== undefined) {
			tokensInput = row.tokens_input;
		}
		if (row.tokens_output !== null && row.tokens_output !== undefined) {
			tokensOutput = row.tokens_output;
		}
		if (row.usd_subscription_attributed !== null && row.usd_subscription_attributed !== undefined) {
			usdSubscriptionAttributed = row.usd_subscription_attributed;
		}
		if (row.usd_credits_equivalent !== null && row.usd_credits_equivalent !== undefined) {
			usdCreditsEquivalent = row.usd_credits_equivalent;
		}
	}
	if (!bolt) return null;
	return {
		bolt,
		relics,
		wallClockSeconds,
		tokensInput,
		tokensOutput,
		usdSubscriptionAttributed,
		usdCreditsEquivalent,
		declaration
	};
}

/** `declaration` defaults to `null` (never carried) so every call site that
 *  predates #1236 — and the pre-existing test pinning "asks/decisions/owed
 *  are unconditionally absent" — keeps reading exactly as before; passing
 *  a real `BoltDeclarationValue` is what turns the three sections
 *  data-driven. */
export function boltCardSections(
	row: {
		relics: RelicRecord[];
		wallClockSeconds: number | null;
		tokensInput: number | null;
		tokensOutput: number | null;
		usdSubscriptionAttributed: number | null;
		usdCreditsEquivalent: number | null;
	},
	declaration: BoltDeclarationValue = null
): BoltCardSections {
	const hasSpend =
		row.wallClockSeconds !== null ||
		row.tokensInput !== null ||
		row.tokensOutput !== null ||
		row.usdSubscriptionAttributed !== null ||
		row.usdCreditsEquivalent !== null;
	return {
		asks: declarationSectionState(declaration, (d) => d.asks.length > 0),
		decisions: declarationSectionState(declaration, (d) => d.decisions.length > 0),
		produce: row.relics.length > 0 ? 'present' : 'empty',
		owed: declarationSectionState(declaration, (d) => d.owed.length > 0),
		spend: hasSpend ? 'present' : 'empty',
		spendDeclared: declarationSectionState(declaration, (d) => d.spendDeclared !== null)
	};
}
