// Publish-scope consent vocabulary (legal pack item 2, #417 follow-on).
//
// Mirrors the grammar `brnrd.publish_scope` validates server-side, which
// itself reuses `brr.gates.cloud`'s daemon-side parser — one vocabulary,
// not three copies that can drift. Corpus sub-slices (`authored`,
// `knowledge`, `runs`) exist server-side but are not exposed as separate
// checkboxes here: naming the whole `corpus` lane is the granularity this
// consent step asks a human to reason about.

export interface PublishLane {
	value: string;
	label: string;
}

export const PUBLISH_LANES: PublishLane[] = [
	{ value: 'activity', label: 'Activity — pending/running tasks and summaries' },
	{ value: 'corpus', label: 'Corpus & knowledge — authored pages, kb, run bodies' },
	{ value: 'live_runs', label: 'Live run cards — card_text, unredacted, while a run is live' },
	{ value: 'quota', label: 'Quota & billing — spend figures, reset times, gate errors' },
	{ value: 'runners', label: 'Runner catalog — installed Shell+Core fingerprint' },
	{ value: 'pr_review_queue', label: 'Open PR queue — titles and URLs' },
	{ value: 'run_ledger', label: 'Run ledger — closed-run receipts, commit subjects, paths' }
];

export const PUBLISH_SCOPE_OFF = 'none';

// The pre-consent daemon-config default ("absent means everything") spelled
// out as an explicit choice — offered as a preset so a user who genuinely
// wants the old behaviour can pick it without hand-checking seven boxes.
export const PUBLISH_SCOPE_EVERYTHING = PUBLISH_LANES.map((lane) => lane.value).join(',');

export type PublishScopePreset = 'none' | 'everything' | 'custom';

export function presetForValue(value: string): PublishScopePreset {
	const normalized = normalizePublishLayers(value);
	if (normalized === PUBLISH_SCOPE_OFF) return 'none';
	if (parsePublishLayers(normalized).size === PUBLISH_LANES.length) return 'everything';
	return 'custom';
}

export function parsePublishLayers(value: string | null | undefined): Set<string> {
	const text = (value ?? '').trim();
	if (!text || text === PUBLISH_SCOPE_OFF) return new Set();
	return new Set(
		text
			.split(',')
			.map((part) => part.trim().toLowerCase())
			.filter(Boolean)
	);
}

export function serializePublishLayers(lanes: Set<string>): string {
	if (lanes.size === 0) return PUBLISH_SCOPE_OFF;
	return PUBLISH_LANES.filter((lane) => lanes.has(lane.value))
		.map((lane) => lane.value)
		.join(',');
}

function normalizePublishLayers(value: string): string {
	const text = value.trim();
	return text === '' ? PUBLISH_SCOPE_OFF : text;
}

// A short, honest one-liner for a repo row — never longer than the fact
// itself. `null` means no consent was ever recorded for this repo (it
// connected before this setting existed, or was minted through the account
// API before that surface asked). The server reads an unrecorded consent as
// "publish nothing", so this repo is paused rather than unenforced — say so,
// because a repo that has gone quiet without explanation is the failure mode.
export function publishScopeSummary(value: string | null | undefined): string {
	if (value == null)
		return 'no consent recorded — publishing paused. Pick a scope below to resume.';
	const lanes = parsePublishLayers(value);
	if (lanes.size === 0) return 'nothing — dashboard mirroring is off for this repo';
	if (lanes.size === PUBLISH_LANES.length) return 'everything (all seven lanes)';
	return `${lanes.size} of ${PUBLISH_LANES.length} lanes: ${Array.from(lanes).sort().join(', ')}`;
}

// ── One vocabulary for the consent gap ──────────────────────────────────────
//
// Two surfaces state this same fact — WithheldNotice (lane-local: "this panel
// is empty because of it") and PublishConsentNotice (account-level: "something
// in this account is paused") — and briefly stated it in two hand-copied
// wordings that could only drift apart. `unrecordedClause`/`optedOutClause`
// are the one place either sentence is written; each caller is free to frame
// it differently (a full paragraph vs. a `paused —`/`off —` fragment) but not
// to re-word the fact itself.

// Joins repo names into a natural-language list: "a", "a and b", "a, b, and c".
export function joinRepoNames(names: string[]): string {
	if (names.length === 0) return '';
	if (names.length === 1) return names[0];
	if (names.length === 2) return `${names[0]} and ${names[1]}`;
	return `${names.slice(0, -1).join(', ')}, and ${names[names.length - 1]}`;
}

// The fact `publish_layers === null` actually proves: no scope was ever
// recorded for these repos. Nothing more — a repo minted through the account
// API today lands `null` the same as one that predates the consent setting
// entirely, so this must not claim a history the data cannot support. Returns
// `null` (not an empty string) when there is no gap of this kind to report,
// so a caller can tell "no repos" apart from "no clause".
export function unrecordedClause(repoNames: string[]): string | null {
	if (repoNames.length === 0) return null;
	return `${joinRepoNames(repoNames)} never recorded a publish scope`;
}

// The other half of the same fact: these repos' owners were asked and
// answered "none" — a deliberate choice, not a gap in the record.
export function optedOutClause(repoNames: string[]): string | null {
	if (repoNames.length === 0) return null;
	return `${joinRepoNames(repoNames)} chose to publish nothing`;
}
