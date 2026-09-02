import { ageSince } from './runLedger.ts';
import { GITHUB_REPO, type BuildVersion } from './publicStats.ts';

// What the dashboard actually renders — a short commit (linked to the forge
// commit page) and the build's age, independently optional. `null` overall
// means there is nothing honest to show at all (both fields absent), so the
// caller renders nothing rather than an empty shell.
export interface BuildIdentityView {
	commitShort: string | null;
	commitUrl: string | null;
	builtAgo: string | null;
}

/** Pure formatting over a fetched `BuildVersion` — no field is fabricated
 * past what the endpoint sent. `commit` renders as its short (8-char) form
 * linked to `github.com/<repo>/commit/<sha>`; `built_at` renders through the
 * dashboard's one relative-age grammar (`ageSince`, #1256) so it reads the
 * same way every other "how old" fact on this page does. Deliberately not
 * drift-aware (#1734 item 2): the endpoint carries no
 * `origin_main_relationship` field — the deployed backend has no git
 * checkout to compute one from (see the task report) — so there is nothing
 * to render here even if a future field appeared unrecognised; this
 * function only knows the three fields above. */
export function buildIdentityView(
	version: BuildVersion | null,
	now: number
): BuildIdentityView | null {
	if (!version) return null;
	const commit = version.commit;
	const commitShort = commit ? commit.slice(0, 8) : null;
	const commitUrl = commit ? `https://github.com/${GITHUB_REPO}/commit/${commit}` : null;
	const builtAgo = ageSince(version.built_at, now);
	if (!commitShort && !builtAgo) return null;
	return { commitShort, commitUrl, builtAgo };
}
