export interface DaemonConfigEntry {
	key: string;
	value: string | number | boolean;
	source: string;
}

export async function fetchDaemonConfig(
	fetchImpl: typeof fetch = fetch
): Promise<DaemonConfigEntry[]> {
	const res = await fetchImpl('/v1/dashboard/config', { credentials: 'include' });
	if (!res.ok) throw new Error(`daemon config fetch failed: ${res.status}`);
	return ((await res.json()) as { config: DaemonConfigEntry[] }).config;
}
