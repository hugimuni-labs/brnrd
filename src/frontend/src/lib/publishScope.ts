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
// itself. `null` is the legacy case: no consent was ever recorded for this
// repo, so nothing here is enforced and the daemon's own `.brr/config`
// `publish.layers` is the only control that applies.
export function publishScopeSummary(value: string | null | undefined): string {
	if (value == null)
		return 'not set — daemon config controls this (connected before this setting existed)';
	const lanes = parsePublishLayers(value);
	if (lanes.size === 0) return 'nothing — dashboard mirroring is off for this repo';
	if (lanes.size === PUBLISH_LANES.length) return 'everything (all seven lanes)';
	return `${lanes.size} of ${PUBLISH_LANES.length} lanes: ${Array.from(lanes).sort().join(', ')}`;
}
