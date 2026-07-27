export interface WithheldLane {
	lane: string;
	// Repos whose owner was never asked / never recorded a publish scope for
	// this lane. Omitted entirely when empty — never `[]`, never `null`.
	unrecorded?: string[];
	// Repos whose owner explicitly set this lane's scope to none. Same
	// omission rule. A repo can appear in neither list (it permits this lane)
	// even while the lane as a whole is withheld by other repos.
	opted_out?: string[];
}
