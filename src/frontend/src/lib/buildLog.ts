// The build log: dated findings with receipts, published at /log.
//
// Same authoring shape as `$lib/searchTopics.ts` (#1714) — a typed array of
// structured entries, not a Markdown pipeline. /learn does not actually
// author from Markdown files despite reading like a content system that
// could; there is no markdown parser (mdsvex, remark, marked) anywhere in
// this frontend. Mirroring /learn's *real* pattern means this shape, not
// introducing a new one — see kb note in the /log build's report for the
// full reasoning.
//
// Newest first is enforced at read time (`buildLogEntriesSorted`), not by
// authoring discipline: a future entry appended out of date order still
// renders correctly.

export type BuildLogLink = {
	label: string;
	url: string;
};

export type BuildLogEntry = {
	/** Stable, URL-safe permalink segment. Never reuse after publishing. */
	slug: string;
	title: string;
	/** ISO date (YYYY-MM-DD) the finding was published. */
	date: string;
	/** One or two sentences: the finding, for the index card and <meta description>. */
	summary: string;
	/** The thing that was measured — a short, checkable clause. */
	measured: string;
	/** Full write-up, one paragraph per entry. */
	body: string[];
	/** Receipts: code, PR, issue, or commit links a reader can verify against. */
	links: BuildLogLink[];
};

export const BUILD_LOG_ENTRIES: BuildLogEntry[] = [
	{
		slug: 'retired-codex-models-still-selectable',
		title: 'Two retired Codex models were still selectable cores',
		date: '2026-09-02',
		summary:
			"brnrd's Codex model probe reads slug and visibility from Codex's own models cache and drops everything else — including the field that says a model is retired.",
		measured:
			'gpt-5.4 and gpt-5.4-mini, both past their retirement_at timestamp, still offered as selectable runner cores.',
		body: [
			"Codex CLI keeps a models_cache.json on disk, refreshed on its own network calls. Each entry carries a visibility flag and, when a model is on its way out, an upgrade block: the replacement model, migration text, and a retirement_at timestamp. brnrd's runner core probe (_models_from_disk, src/brr/runner_cores.py) reads that same file to fill out the live Codex catalog — but it only ever reads slug and visibility. The upgrade block, retirement_at included, is parsed into memory and thrown away.",
			'gpt-5.4 and gpt-5.4-mini both retired at 2026-08-31T19:00:00Z, with visibility still set to "list" — Codex\'s cache does not stop advertising a model just because it named its own successor. Because the probe never looks at retirement_at, both slugs kept surfacing as ordinary selectable cores (codex-gpt-5.4, codex-gpt-5.4-mini) in the runner catalog on 2026-09-02, a day after the timestamp in the same file said they were gone.',
			'The lesson is the shape of the bug, not the two model names: a field that gets parsed and then dropped is a worse failure than a field that was never fetched at all. A source that was never read is a known gap — nobody expects an answer from it. A source that was read, and whose relevant field was silently discarded, renders identically to one that has nothing to say. The reader has no way to tell "this file doesn\'t carry retirement data" from "this file carries retirement data nobody wired up." Both look like a clean, current catalog.',
			'Not fixed here — this entry is the finding, not the patch. The probe would need to read upgrade.retirement_at, compare it against wall-clock time, and either drop the entry or carry the retirement forward as a stale/retired marker the catalog already has a slot for (the "stale" flag freshness_date-based entries get today).'
		],
		links: [
			{
				label: 'src/brr/runner_cores.py — _models_from_disk',
				url: 'https://github.com/hugimuni-labs/brnrd/blob/brr/the-log-that-is-ours/src/brr/runner_cores.py#L597-L624'
			}
		]
	}
];

/** Entries newest-first by date, independent of authoring order. */
export function buildLogEntriesSorted(): BuildLogEntry[] {
	return [...BUILD_LOG_ENTRIES].sort((a, b) => b.date.localeCompare(a.date));
}

export function buildLogEntryBySlug(slug: string): BuildLogEntry | undefined {
	return BUILD_LOG_ENTRIES.find((entry) => entry.slug === slug);
}
