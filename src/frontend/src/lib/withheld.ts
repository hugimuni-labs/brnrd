export interface WithheldLane {
	lane: string;
	unrecorded: string[];
	opted_out: string[];
}

/** One-line explanation for an empty lane that consent certainly withheld. */
export function withheldCopy(withheld: WithheldLane): string {
	const parts: string[] = [];
	if (withheld.unrecorded.length > 0) {
		parts.push(
			`paused — these repos were connected before the publish consent existed and have never been asked: ${withheld.unrecorded.join(', ')}`
		);
	}
	if (withheld.opted_out.length > 0) {
		const subject =
			withheld.opted_out.length === 1
				? "you set this repo's publish scope"
				: "you set these repos' publish scope";
		parts.push(`off — ${subject} to nothing: ${withheld.opted_out.join(', ')}`);
	}
	if (parts.length === 0) {
		return 'off — no connected repo includes this lane in its publish scope';
	}
	return parts.join(' · ');
}
