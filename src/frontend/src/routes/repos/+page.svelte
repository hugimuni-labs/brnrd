<script lang="ts">
	import { onMount } from 'svelte';
	import {
		ReposAuthError,
		connectRepo,
		disconnectRepo,
		fetchRepos,
		inviteRepoBot,
		pairRepoTelegram,
		type ConnectedRepo,
		type InstalledRepo,
		type RepoActionResponse,
		type ReposResponse
	} from '$lib/repos';
	import { STATUS_GOOD, STATUS_UNKNOWN, STATUS_WARN, statusDotStyle } from '$lib/statusPalette';

	let data = $state<ReposResponse | null>(null);
	let error = $state<string | null>(null);
	let unauthenticated = $state(false);
	let actionResult = $state<RepoActionResponse | null>(null);
	let pendingAction = $state<string | null>(null);
	let confirmingDisconnect = $state<string | null>(null);
	let manualRepo = $state('');
	let manualBranch = $state('');

	const connectedRepos = $derived(data?.connected_repos ?? []);
	const availableInstalled = $derived(
		data?.installed_repos.filter((repo) => !repo.connected) ?? []
	);
	const connectedInstalled = $derived(data?.installed_repos.filter((repo) => repo.connected) ?? []);

	async function refresh() {
		try {
			data = await fetchRepos();
			error = null;
			unauthenticated = false;
			if (!actionResult && data.notice) {
				actionResult = { ok: true, notice: data.notice };
			}
		} catch (e) {
			if (e instanceof ReposAuthError) {
				unauthenticated = true;
			} else {
				error = e instanceof Error ? e.message : 'repos fetch failed';
			}
		}
	}

	async function runAction(label: string, action: () => Promise<RepoActionResponse>) {
		pendingAction = label;
		try {
			const result = await action();
			actionResult = result;
			if (result.ok) {
				confirmingDisconnect = null;
				await refresh();
			}
		} catch (e) {
			if (e instanceof ReposAuthError) {
				unauthenticated = true;
			} else {
				actionResult = {
					ok: false,
					notice: e instanceof Error ? e.message : 'repo action failed'
				};
			}
		} finally {
			pendingAction = null;
		}
	}

	function connectInstalled(repo: InstalledRepo) {
		runAction(`connect:${repo.id}`, () =>
			connectRepo({
				repo_full_name: repo.repo_full_name,
				forge_repo_id: repo.forge_repo_id,
				default_branch: repo.default_branch
			})
		);
	}

	function connectManual(event: Event) {
		event.preventDefault();
		const repo = manualRepo.trim();
		if (!repo) {
			actionResult = { ok: false, notice: 'Enter a repo as owner/name.' };
			return;
		}
		runAction('connect:manual', async () => {
			const result = await connectRepo({
				repo_full_name: repo,
				default_branch: manualBranch.trim()
			});
			if (result.ok) {
				manualRepo = '';
				manualBranch = '';
			}
			return result;
		});
	}

	function inviteBot(repo: ConnectedRepo) {
		runAction(`invite:${repo.id}`, () => inviteRepoBot(repo.id));
	}

	function pairTelegram(repo: ConnectedRepo) {
		runAction(`pair:${repo.id}`, () => pairRepoTelegram(repo.id));
	}

	function confirmDisconnect(repo: ConnectedRepo) {
		runAction(`disconnect:${repo.id}`, () => disconnectRepo(repo.id));
	}

	function daemonColor(status: string): string {
		if (status === 'online') return STATUS_GOOD;
		if (status === 'offline') return STATUS_WARN;
		return STATUS_UNKNOWN;
	}

	function gateColor(status: string): string {
		if (status === 'ok') return STATUS_GOOD;
		if (status === 'degraded') return STATUS_WARN;
		return STATUS_UNKNOWN;
	}

	function gateAge(age: number | null): string {
		if (age === null) return 'never';
		if (age < 60) return `${age}s ago`;
		if (age < 3600) return `${Math.floor(age / 60)}m ago`;
		return `${Math.floor(age / 3600)}h ago`;
	}

	function daemonLevel(status: string): string {
		if (status === 'online') return 'ample';
		if (status === 'offline') return 'low';
		return 'unknown';
	}

	function branchLabel(value: string | null): string {
		return value || 'branch unset';
	}

	function actionBusy(label: string): boolean {
		return pendingAction === label;
	}

	onMount(refresh);
</script>

<div class="mx-auto max-w-4xl p-6">
	<div class="flex items-start justify-between gap-4">
		<p class="eyebrow">brnrd · repos</p>
		<a
			href="/"
			class="font-mono text-[11px] tracking-wide text-stone-500 uppercase hover:text-stone-300"
			>dashboard</a
		>
	</div>
	<h1 class="mt-1 font-mono text-2xl font-semibold tracking-tight text-amber-100">
		repository control
	</h1>
	<p class="mt-2 max-w-2xl text-sm text-stone-400">
		Enable GitHub repositories, pair local daemons, and route Telegram chats into brnrd.
	</p>

	{#if unauthenticated}
		<p class="mt-6 text-sm text-stone-400">
			Sign in to manage repos - <a
				class="text-sky-400 underline"
				href="/login?next=/repos"
				rel="external">log in</a
			>.
		</p>
	{:else if error}
		<p class="mt-6 text-sm text-red-400">{error}</p>
	{:else if data === null}
		<p class="mt-6 text-sm text-stone-500">Loading...</p>
	{:else}
		{#if actionResult}
			<div
				class={`subpanel mt-5 p-3 text-sm ${actionResult.ok ? 'border-amber-900/60 text-amber-100' : 'border-stone-700 text-stone-200'}`}
			>
				<div class="flex items-start justify-between gap-3">
					<div>
						<p class="font-mono text-[11px] tracking-wide text-stone-500 uppercase">
							{actionResult.ok ? 'result' : 'error'}
						</p>
						<p class="mt-1">{actionResult.notice}</p>
					</div>
					<button
						type="button"
						class="cursor-pointer font-mono text-[11px] tracking-wide text-stone-500 uppercase hover:text-stone-300"
						onclick={() => (actionResult = null)}>clear</button
					>
				</div>
				{#if actionResult.instructions}
					<div class="mt-3 border-t border-stone-800/70 pt-3">
						{#if actionResult.pairing_code}
							<p class="font-mono text-xs text-amber-200">{actionResult.pairing_code}</p>
						{/if}
						<p class="mt-1 text-sm text-stone-300">{actionResult.instructions}</p>
						{#if actionResult.action_url}
							<a
								class="mt-2 inline-block font-mono text-[11px] tracking-wide text-sky-400 uppercase underline"
								href={actionResult.action_url}
								rel="external noreferrer"
								target="_blank">open telegram</a
							>
						{/if}
					</div>
				{/if}
			</div>
		{/if}

		<!-- grid-cols-1 everywhere below: without a base template the implicit
	     track is max-content-sized, and the nowrap truncate text pushes
	     cards wider than the container on narrow viewports (live-caught on
	     mobile 2026-07-11 — installed-repo cards overflowing the panel). -->
		<div class="mt-5 grid grid-cols-1 gap-2 sm:grid-cols-3">
			<div class="subpanel p-3">
				<p class="font-mono text-[10px] tracking-wide text-stone-500 uppercase">signed in</p>
				<p class="mt-1 font-mono text-sm text-amber-100">@{data.account.github_login}</p>
			</div>
			<div class="subpanel p-3">
				<p class="font-mono text-[10px] tracking-wide text-stone-500 uppercase">enabled repos</p>
				<p class="mt-1 font-mono text-sm text-amber-100">
					{data.connected_count} of {data.installed_repos.length} synced
				</p>
			</div>
			<div class="subpanel p-3">
				<p class="font-mono text-[10px] tracking-wide text-stone-500 uppercase">GitHub App</p>
				<p class="mt-1 truncate font-mono text-sm text-amber-100">{data.github_app_slug}</p>
			</div>
		</div>

		<section class="panel mt-6 p-4">
			<div class="mb-3 flex items-center justify-between gap-3">
				<div>
					<p class="eyebrow">connected</p>
					<h2 class="font-mono text-lg font-semibold tracking-tight text-amber-100">
						daemon pairing
					</h2>
				</div>
				<span
					class="shrink-0 border border-stone-800 px-1.5 py-0.5 font-mono text-[10px] tracking-wide text-stone-500 uppercase"
					>{connectedRepos.length} enabled</span
				>
			</div>

			{#if connectedRepos.length === 0}
				<p class="text-sm text-stone-500">No repos enabled yet.</p>
			{:else}
				<div class="space-y-2">
					{#each connectedRepos as repo (repo.id)}
						{@const statusColor = daemonColor(repo.daemon_status)}
						<div class="subpanel p-3">
							<div class="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
								<div class="min-w-0">
									<div class="flex min-w-0 items-center gap-2">
										<span
											class="inline-block h-2 w-2 shrink-0 rounded-full"
											style={statusDotStyle(daemonLevel(repo.daemon_status), statusColor)}
											aria-hidden="true"
										></span>
										<h3 class="truncate font-mono text-sm font-semibold text-amber-100">
											{repo.repo_full_name}
										</h3>
									</div>
									<div
										class="mt-1 flex flex-wrap gap-x-3 gap-y-1 font-mono text-[11px] text-stone-500"
									>
										<span>{repo.forge}</span>
										<span>{branchLabel(repo.default_branch)}</span>
										<span style={`color: ${statusColor}`}>{repo.daemon_label}</span>
										{#if repo.latest_daemon_name}
											<span>{repo.latest_daemon_name}</span>
										{/if}
										<span>updated {repo.updated_label}</span>
									</div>
									{#if repo.daemon_status === 'online'}
										<p class="mt-2 text-sm text-stone-400">
											Last heartbeat {repo.daemon_last_seen}.
										</p>
									{:else if repo.daemon_status === 'offline'}
										<p class="mt-2 text-sm text-stone-400">
											Last heartbeat {repo.daemon_last_seen}. Start the local daemon to drain queued
											work.
										</p>
									{:else}
										<p class="mt-2 text-sm text-stone-400">
											Pair a local daemon from a checkout when this repo should drain work.
										</p>
									{/if}
									{#if repo.gates.length > 0}
										<div class="mt-3 grid gap-1.5 sm:grid-cols-2">
											{#each repo.gates as gate (gate.gate)}
												{@const color = gateColor(gate.status)}
												<div class="border border-stone-800 bg-stone-950/40 px-2 py-1.5">
													<div
														class="flex items-center justify-between gap-2 font-mono text-[11px]"
													>
														<span class="flex items-center gap-1.5 text-stone-300">
															<span
																class="inline-block h-1.5 w-1.5 rounded-full"
																style={`background: ${color}`}
															></span>
															{gate.gate}
														</span>
														<span style={`color: ${color}`}>{gate.status}</span>
													</div>
													<p class="mt-0.5 font-mono text-[10px] text-stone-600">
														poll {gateAge(gate.age_seconds)}
													</p>
													{#if gate.last_error}
														<p
															class="mt-1 line-clamp-2 text-[11px] text-amber-700"
															title={gate.last_error}
														>
															{gate.last_error}
														</p>
													{/if}
												</div>
											{/each}
										</div>
									{/if}
									{#if repo.daemon_status !== 'online'}
										<details class="mt-2">
											<summary
												class="cursor-pointer font-mono text-[11px] tracking-wide text-stone-500 uppercase hover:text-stone-300"
												>setup command</summary
											>
											<pre
												class="mt-2 overflow-x-auto border border-stone-800 bg-stone-950/50 p-2 font-mono text-[11px] text-stone-300"><code
													>{repo.setup_command}</code
												></pre>
										</details>
									{/if}
								</div>

								<div class="flex shrink-0 flex-wrap gap-2 md:justify-end">
									<button
										type="button"
										class="cursor-pointer border border-stone-800 px-2 py-1 font-mono text-[11px] tracking-wide text-stone-400 uppercase hover:text-stone-200 disabled:cursor-wait disabled:opacity-50"
										disabled={pendingAction !== null}
										onclick={() => pairTelegram(repo)}
										>{actionBusy(`pair:${repo.id}`) ? 'pairing' : 'pair Telegram'}</button
									>
									<button
										type="button"
										class="cursor-pointer border border-stone-800 px-2 py-1 font-mono text-[11px] tracking-wide text-stone-400 uppercase hover:text-stone-200 disabled:cursor-wait disabled:opacity-50"
										disabled={pendingAction !== null || !repo.bot_invite_enabled}
										onclick={() => inviteBot(repo)}
										>{actionBusy(`invite:${repo.id}`)
											? 'inviting'
											: `invite @${data.github_bot_user_login || data.github_bot_login}`}</button
									>
									{#if confirmingDisconnect === repo.id}
										<button
											type="button"
											class="cursor-pointer border border-stone-700 bg-stone-950/70 px-2 py-1 font-mono text-[11px] tracking-wide text-stone-200 uppercase hover:text-amber-100 disabled:cursor-wait disabled:opacity-50"
											disabled={pendingAction !== null}
											onclick={() => confirmDisconnect(repo)}
											>{actionBusy(`disconnect:${repo.id}`)
												? 'disconnecting'
												: 'confirm disconnect'}</button
										>
										<button
											type="button"
											class="cursor-pointer border border-stone-800 px-2 py-1 font-mono text-[11px] tracking-wide text-stone-500 uppercase hover:text-stone-300"
											disabled={pendingAction !== null}
											onclick={() => (confirmingDisconnect = null)}>cancel</button
										>
									{:else}
										<button
											type="button"
											class="cursor-pointer border border-stone-800 px-2 py-1 font-mono text-[11px] tracking-wide text-stone-500 uppercase hover:text-stone-300"
											disabled={pendingAction !== null}
											onclick={() => (confirmingDisconnect = repo.id)}>disconnect</button
										>
									{/if}
								</div>
							</div>
						</div>
					{/each}
				</div>
			{/if}
		</section>

		<section class="panel mt-6 p-4">
			<div class="mb-3 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
				<div>
					<p class="eyebrow">available</p>
					<h2 class="font-mono text-lg font-semibold tracking-tight text-amber-100">
						installed repositories
					</h2>
					{#if data.installations.length > 0}
						<p class="mt-1 text-sm text-stone-500">
							Synced from
							{data.installations
								.map((installation) => installation.target_login || installation.installation_id)
								.join(', ')}.
						</p>
					{/if}
				</div>
				<a
					class="shrink-0 border border-stone-800 px-2 py-1 font-mono text-[11px] tracking-wide text-stone-400 uppercase hover:text-stone-200"
					href={data.install_url}
					rel="external noreferrer"
					target="_blank">{data.installations.length === 0 ? 'install app' : 'manage app'}</a
				>
			</div>

			{#if data.installations.length === 0}
				<p class="text-sm text-stone-500">No GitHub App installation is connected yet.</p>
			{:else if availableInstalled.length === 0}
				<p class="text-sm text-stone-500">
					All {connectedInstalled.length} synced repositories are enabled.
				</p>
			{:else}
				<div class="grid grid-cols-1 gap-2 lg:grid-cols-2">
					{#each availableInstalled as repo (repo.id)}
						<div class="subpanel flex items-center justify-between gap-3 p-3">
							<div class="min-w-0">
								<p class="truncate font-mono text-sm font-semibold text-amber-100">
									{repo.repo_full_name}
								</p>
								<p class="mt-1 truncate font-mono text-[11px] text-stone-500">
									{branchLabel(repo.default_branch)} · pushed {repo.pushed_label}
								</p>
							</div>
							<button
								type="button"
								class="shrink-0 cursor-pointer border border-stone-800 px-2 py-1 font-mono text-[11px] tracking-wide text-stone-400 uppercase hover:text-stone-200 disabled:cursor-wait disabled:opacity-50"
								disabled={pendingAction !== null}
								onclick={() => connectInstalled(repo)}
								>{actionBusy(`connect:${repo.id}`) ? 'enabling' : 'enable'}</button
							>
						</div>
					{/each}
				</div>
			{/if}

			<form class="mt-5 border-t border-stone-800/70 pt-4" onsubmit={connectManual}>
				<p class="font-mono text-[11px] tracking-wide text-stone-500 uppercase">manual connect</p>
				<div class="mt-2 grid grid-cols-1 gap-2 md:grid-cols-[minmax(0,1fr)_180px_auto]">
					<input
						class="border border-stone-800 bg-stone-950/60 px-2 py-1.5 font-mono text-sm text-stone-200 outline-none focus:border-amber-700"
						bind:value={manualRepo}
						placeholder="owner/name"
						autocomplete="off"
					/>
					<input
						class="border border-stone-800 bg-stone-950/60 px-2 py-1.5 font-mono text-sm text-stone-200 outline-none focus:border-amber-700"
						bind:value={manualBranch}
						placeholder="default branch"
						autocomplete="off"
					/>
					<button
						type="submit"
						class="cursor-pointer border border-stone-800 px-3 py-1.5 font-mono text-[11px] tracking-wide text-stone-400 uppercase hover:text-stone-200 disabled:cursor-wait disabled:opacity-50"
						disabled={pendingAction !== null}
						>{actionBusy('connect:manual') ? 'enabling' : 'enable repo'}</button
					>
				</div>
			</form>
		</section>
	{/if}
</div>
