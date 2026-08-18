export interface WithheldLane {
	lane: string;
	// Repos whose owner was never asked / never recorded a publish scope for
	// this lane. Omitted entirely when empty — never `[]`, never `null`.
	unrecorded?: string[];
	// Repos whose owner explicitly set this lane's scope to none. Same
	// omission rule. A repo can appear in neither list (it permits this lane)
	// even while the lane as a whole is withheld by other repos.
	opted_out?: string[];
	// Repo ids parallel to `unrecorded` (same order, same length) — minted
	// from the same server-side pass, so index `i` here is always the same
	// repo as index `i` of `unrecorded`. Lets an in-place consent act target
	// `POST /v1/repos/{id}/publish-layers` directly instead of sending the
	// reader to /repos to resolve a name back to an id by hand.
	unrecorded_ids?: string[];
	// Same pairing, for `opted_out`.
	opted_out_ids?: string[];
}
