import { ok, equal } from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

// Source-level, deliberately — same call as ColdStart.test.ts's
// SIGNED_IN_ROUTES check and reposPage.test.ts: `routes/+page.svelte` reads
// `page`, `onMount`, and `localStorage`, none of which the SSR-compile-and-
// render harness the other component tests use can stub cheaply, and the
// claims here are about which components render, not derived state.
const here = dirname(fileURLToPath(import.meta.url));
const dashboardPagePath = join(here, '..', 'routes', '+page.svelte');

function source(): string {
	return readFileSync(dashboardPagePath, 'utf8');
}

// #1281 — the maintainer's own report, live from an onboarding test: one
// consent fact ("… chose to publish nothing") rendered 3-4x stacked down a
// single load, once per lane, with the "off —"/"paused —" wording
// disagreeing with itself. Fix direction: one banner at the page head (the
// `off —` variant, `PublishConsentNotice`, which carries the `set a scope`
// action) — the per-lane `paused —` repeats carry no new information and
// are dropped, not restated.
test('PublishConsentNotice renders once, directly under the cold-start block', () => {
	const src = source();
	const coldStartAt = src.indexOf('<ColdStart');
	const noticeAt = src.indexOf('<PublishConsentNotice');
	ok(coldStartAt >= 0, 'the cold-start block renders');
	ok(noticeAt > coldStartAt, 'the account-level notice sits right after it, above the fold');
	equal(
		src.indexOf('<PublishConsentNotice', noticeAt + 1),
		-1,
		'PublishConsentNotice renders exactly once — it is the one page-head banner, not a per-lane one'
	);
});

// The rail (next pick), the machine lane, and the warp section each used to
// carry their own standalone `<WithheldNotice>` restating the same
// account-level fact `PublishConsentNotice` already states once, above —
// the literal "stacked 3-4x" the report described. One genuinely different
// case survives untouched: the cloth section's `<WithheldNotice>` replaces
// its content area entirely (mutually exclusive with `<Cloth>`) rather than
// stacking an extra line above content that renders regardless — that one
// is substitutive, not a repeat, and this pins there being exactly one.
test('no standalone per-lane WithheldNotice repeats the page-head banner', () => {
	const src = source();
	const standalone = src.match(/<WithheldNotice\b/g) ?? [];
	equal(
		standalone.length,
		1,
		"exactly one standalone <WithheldNotice> remains — the cloth section's substitute-for-content case"
	);
	// The state these removed banners drove is dead too — no orphaned
	// write-only `$state` left behind by dropping their only reader. (A
	// one-line explanatory comment naming them by way of explanation is
	// fine and expected; only the declaration itself must be gone.)
	ok(
		!/let runnersWithheld = \$state/.test(src),
		"the rail's runners-lane withheld state is gone, not just unread"
	);
	ok(
		!/let quotaWithheld = \$state/.test(src),
		"the rail's quota-lane withheld state is gone, not just unread"
	);
	ok(
		!/let activityWithheld = \$state/.test(src),
		"the machine lane's withheld state is gone, not just unread"
	);
});

// The derived needs-you strip already receives `withheld` and folds it
// into `BackchannelQueue`'s empty state — a standalone banner inside the
// warp section would be a second repeat of the same fact for that lane
// alone (#1281's rule, carried through the graph rewrite).
test('the warp section feeds the queue its withheld lane instead of also rendering a standalone notice', () => {
	const src = source();
	const warpSection = src.match(
		/aria-labelledby="warp-heading"[\s\S]*?aria-labelledby="cloth-heading"/
	);
	ok(warpSection, 'the warp section exists');
	const body = warpSection![0];
	ok(/withheld=\{prReviewQueueWithheld\}/.test(body), 'the queue still receives the withheld lane');
	ok(!/<WithheldNotice/.test(body), 'no standalone WithheldNotice remains inside the warp section');
});
