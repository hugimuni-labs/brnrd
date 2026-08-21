// brr/every-door-on-the-page — shared presentation logic for the
// messenger-door registry (`MessengerDoor`, `brnrd.messenger_doors`
// backend). No user-facing copy lives in the registry itself (the
// module's own house rule); this file is the one place that copy lives
// so `ColdStart.svelte`'s mobile CTA and the persistent `MessengerDoors.svelte`
// panel on `/repos` render the same words for the same platform instead of
// two call sites drifting apart the next time either changes.

import type { MessengerDoor } from './repos';

// A platform this map doesn't recognize yet still renders — its own slug,
// title-cased — rather than vanishing, same fail-safe posture
// `supportMatrix.ts`'s `doorRows` takes for an unrecognized slug.
const DOOR_LABELS: Record<string, string> = {
	telegram: 'Telegram',
	whatsapp: 'WhatsApp',
	slack: 'Slack',
	signal: 'Signal'
};

export function doorLabel(platform: string): string {
	return DOOR_LABELS[platform] ?? platform.charAt(0).toUpperCase() + platform.slice(1);
}

// The two "why is this door dark" reasons `messenger_doors.py` ships
// (`MessengerDoor.reason` — `None` for a lit door). A reason this map
// doesn't recognize (an older backend spelling, or a future third reason)
// still renders — the generic line below — rather than a blank row.
const OFF_COPY: Record<string, string> = {
	not_built: 'not built yet — no connector exists for this platform.',
	not_configured: 'built, but not configured on this deployment.',
	identity_unavailable:
		'configured, but its identity could not be verified when this service started.'
};

export function doorOffCopy(reason: string | null | undefined): string {
	if (reason && reason in OFF_COPY) return OFF_COPY[reason];
	return 'not available on this deployment.';
}

// `not_configured` is an operator lever (a bot token, a Cloud API
// credential) — worth pointing at the self-hosting docs. `not_built` and
// `identity_unavailable` have no configuration lever: the latter means the
// credentials exist but startup could not fetch the provider identity.
export function doorOffHasEnablePath(reason: string | null | undefined): boolean {
	return reason === 'not_configured';
}

/** Turn a one-shot pairing deep link into the stable conversation door. */
export function conversationLink(deepLink: string | null): string | null {
	if (!deepLink) return null;
	try {
		const url = new URL(deepLink);
		if (url.protocol !== 'https:') return null;
		url.search = '';
		url.hash = '';
		return url.toString();
	} catch {
		return null;
	}
}

// Registry order, with unknown platforms (a future connector this
// component's roster hasn't been told about) appended rather than dropped
// — same "the set stays complete" posture the backend registry itself
// documents (#1465).
export function orderedDoors(doors: MessengerDoor[]): MessengerDoor[] {
	const known = Object.keys(DOOR_LABELS);
	return [...doors].sort((a, b) => {
		const ia = known.indexOf(a.platform);
		const ib = known.indexOf(b.platform);
		return (ia === -1 ? known.length : ia) - (ib === -1 ? known.length : ib);
	});
}

// --- the countdown -----------------------------------------------------
//
// Three tiers, not a raw number: `'ample'` (plenty of time — the same
// "everything's fine" word `daemonColor`'s status dot already uses on this
// page), `'low'` (worth noticing), `'critical'` (dead or about to be —
// the moment the remint control stops being optional and starts being the
// only thing to press). The maintainer's ask named three concrete states
// (3:00 / 0:20 / 0:00) — these are those three, as a total function over
// the whole span rather than three hand-picked instants.

export type CountdownTier = 'ample' | 'low' | 'critical';

// Below this fraction of the original TTL remaining, the countdown moves
// from "ample" to "low" — chosen so a 3-minute link spends its first two
// minutes reading as calm and its last minute reading as urgent, which is
// the shape a bearer link dying actually has: nothing to say for most of
// the wait, then a real reason to look back at the tab.
const LOW_FRACTION = 1 / 3;

export interface Countdown {
	secondsLeft: number;
	tier: CountdownTier;
	label: string; // "2:14", "0:20", "0:00"
}

/** `totalSeconds` is the TTL the code was minted with (so the ample/low
 * boundary scales with a deployment's own `messenger_pair_ttl_s`, never a
 * hardcoded 3-minute assumption baked into the frontend). */
export function countdown(expiresAtIso: string, nowMs: number, totalSeconds: number): Countdown {
	const expiresMs = new Date(expiresAtIso).getTime();
	const secondsLeft = Math.max(0, Math.round((expiresMs - nowMs) / 1000));
	const fraction = totalSeconds > 0 ? secondsLeft / totalSeconds : 0;
	const tier: CountdownTier =
		secondsLeft <= 0 ? 'critical' : fraction <= LOW_FRACTION ? 'low' : 'ample';
	const mm = Math.floor(secondsLeft / 60);
	const ss = secondsLeft % 60;
	return { secondsLeft, tier, label: `${mm}:${String(ss).padStart(2, '0')}` };
}
