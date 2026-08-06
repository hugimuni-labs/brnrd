<script lang="ts">
	import { page } from '$app/state';
	import { onMount, tick } from 'svelte';
	import { SvelteSet } from 'svelte/reactivity';
	import { resolve } from '$app/paths';
	import {
		ReposAuthError,
		connectRepo,
		disconnectRepo,
		fetchRepos,
		pairRepoTelegram,
		setPublishLayers,
		telegramPairLabel,
		type ConnectedRepo,
		type RepoActionResponse,
		type ReposResponse
	} from '$lib/repos';
	import {
		PUBLISH_LANES,
		PUBLISH_SCOPE_EVERYTHING,
		PUBLISH_SCOPE_OFF,
		connectPublishScopeStorageKey,
		parsePublishLayers,
		presetForValue,
		publishScopeSummary,
		serializePublishLayers,
		storedPublishScopeValue,
		type PublishScopePreset
	} from '$lib/publishScope';
	import { DOCS_URL } from '$lib/publicStats';
	import { STATUS_GOOD, STATUS_UNKNOWN, STATUS_WARN, statusDotStyle } from '$lib/statusPalette';
	import MarkerNotice from '$lib/MarkerNotice.svelte';

	let data = $state<ReposResponse | null>(null);
	let error = $state<string | null>(null);
	let unauthenticated = $state(false);
	let actionResult = $state<RepoActionResponse | null>(null);
	let pendingAction = $state<string | null>(null);
	let confirmingDisconnect = $state<string | null>(null);
	let manualRepo = $state('');
	let manualBranch = $state('');

	// Clipboard for the "connect this repository" command (no-installation
	// state, #1084/#1032 steer) — same copy/flash idiom ColdStart.svelte
	// already uses for its two command boxes; not reinvented.
	let copied = $state<string | null>(null);
	let copyTimer: ReturnType<typeof setTimeout> | undefined;

	async function copy(key: string, text: string) {
		try {
			await navigator.clipboard.writeText(text);
			copied = key;
			clearTimeout(copyTimer);
			copyTimer = setTimeout(() => (copied = null), 1500);
		} catch {
			// Clipboard unavailable or denied — no crash, just no flash.
		}
	}

	// The GitHub Setup URL return (#1084): `routers/github_app.py`'s
	// `github_app_setup` 303s here with `?notice=…` after syncing an
	// installation. Captured once, at mount, from the raw query-param code —
	// not from `data.notice` (the backend-mapped *text*, which this drives
	// the fetch with but which can't be switched on) — so a later plain
	// `refresh()` (no params) naturally clears the banner without extra
	// bookkeeping.
	let setupNoticeCode = $state<string | null>(null);

	// `github-sync-failed` / `github-sync-refused` must not read like
	// `github-installed` — a refusal is not news. Reuses this page's own
	// existing ok/not-ok register (amber = result, stone = not) rather than
	// inventing a third visual language; "warn" (empty/partial) shares the
	// not-ok side since neither is a completed success.
	function setupSeverity(code: string | null): 'ok' | 'warn' | 'refused' | null {
		if (!code) return null;
		if (code === 'github-sync-failed' || code === 'github-sync-refused') return 'refused';
		if (code === 'github-sync-empty' || code === 'github-sync-partial') return 'warn';
		if (code === 'github-installed' || code === 'github-synced') return 'ok';
		return null;
	}

	// "Show what just arrived" — the half of #1084's fix that a bare notice
	// string can't carry: how many repos this installation made visible, and
	// whether any of them still needs a local daemon pointed at it.
	function setupArrivalSummary(d: ReposResponse): string {
		const total = d.installed_repos.length;
		if (total === 0) return 'No repositories are visible from this installation yet.';
		const pending = d.installed_repos.filter((repo) => !repo.connected).length;
		const noun = total === 1 ? 'repository' : 'repositories';
		if (pending === 0) return `${total} ${noun} visible, all connected.`;
		return `${total} ${noun} now visible — ${pending} with no daemon connected yet, below.`;
	}

	// Publish-scope consent for the *next* repo this page connects (legal
	// pack item 2, #417 follow-on) — one shared control above both connect
	// paths (installed-repo buttons + the manual form), since the choice is
	// the same question either way and this page never connects two repos
	// in the same click.
	let connectScopePreset = $state<PublishScopePreset>('none');
	let connectScopeCustom = new SvelteSet<string>();

	function connectPublishLayersValue(): string {
		if (connectScopePreset === 'none') return PUBLISH_SCOPE_OFF;
		if (connectScopePreset === 'everything') return PUBLISH_SCOPE_EVERYTHING;
		return serializePublishLayers(connectScopeCustom);
	}

	function toggleConnectScopeLane(lane: string) {
		if (connectScopeCustom.has(lane)) connectScopeCustom.delete(lane);
		else connectScopeCustom.add(lane);
		rememberConnectScope();
	}

	function selectConnectScopePreset(preset: PublishScopePreset) {
		connectScopePreset = preset;
		rememberConnectScope();
	}

	function rememberConnectScope() {
		if (!data) return;
		try {
			localStorage.setItem(
				connectPublishScopeStorageKey(data.account.id),
				connectPublishLayersValue()
			);
		} catch {
			// Storage can be unavailable in a private/restricted browser.
			// The live choice still feeds the connect request; only reload
			// persistence is lost.
		}
	}

	function restoreConnectScope() {
		if (!data) return;
		let stored: string | null;
		try {
			stored = localStorage.getItem(connectPublishScopeStorageKey(data.account.id));
		} catch {
			return;
		}
		const remembered = storedPublishScopeValue(stored);
		if (remembered === null) return;
		connectScopePreset = presetForValue(remembered);
		connectScopeCustom.clear();
		for (const lane of parsePublishLayers(remembered)) connectScopeCustom.add(lane);
	}

	// Revisiting a connected repo's consent later (the ticket's "settings
	// surface" requirement) — which repo's editor is open, and its draft
	// selection before "save" commits it.
	let editingScopeRepo = $state<string | null>(null);
	let editScopeCustom = new SvelteSet<string>();

	function startEditingScope(repo: ConnectedRepo) {
		editingScopeRepo = repo.id;
		editScopeCustom.clear();
		for (const lane of parsePublishLayers(repo.publish_layers)) editScopeCustom.add(lane);
	}

	function toggleEditScopeLane(lane: string) {
		if (editScopeCustom.has(lane)) editScopeCustom.delete(lane);
		else editScopeCustom.add(lane);
	}

	function saveScope(repo: ConnectedRepo) {
		runAction(`scope:${repo.id}`, async () => {
			const result = await setPublishLayers(repo.id, serializePublishLayers(editScopeCustom));
			if (result.ok) editingScopeRepo = null;
			return result;
		});
	}

	function saveEverything(repo: ConnectedRepo) {
		runAction(`scope:${repo.id}`, async () => {
			const result = await setPublishLayers(repo.id, PUBLISH_SCOPE_EVERYTHING);
			if (result.ok) editingScopeRepo = null;
			return result;
		});
	}

	const CONNECT_SCOPE_PRESETS: { value: PublishScopePreset; label: string }[] = [
		{ value: 'none', label: 'Nothing (default, most private)' },
		{ value: 'custom', label: 'Choose lanes' },
		{ value: 'everything', label: 'Everything (all seven lanes)' }
	];

	const connectedRepos = $derived(data?.connected_repos ?? []);
	const availableInstalled = $derived(
		data?.installed_repos.filter((repo) => !repo.connected) ?? []
	);
	const connectedInstalled = $derived(data?.installed_repos.filter((repo) => repo.connected) ?? []);

	async function refresh(forwardNotice = false) {
		try {
			data = await fetchRepos(
				fetch,
				forwardNotice
					? {
							notice: page.url.searchParams.get('notice'),
							installationId: page.url.searchParams.get('installation_id')
						}
					: undefined
			);
			error = null;
			unauthenticated = false;
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
				default_branch: manualBranch.trim(),
				publish_layers: connectPublishLayersValue()
			});
			if (result.ok) {
				manualRepo = '';
				manualBranch = '';
			}
			return result;
		});
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

	onMount(async () => {
		setupNoticeCode = page.url.searchParams.get('notice');
		await refresh(setupNoticeCode !== null);
		restoreConnectScope();
		const targetId = page.url.searchParams.get('scope');
		const target = connectedRepos.find((repo) => repo.id === targetId);
		if (target) {
			startEditingScope(target);
			await tick();
			document.getElementById(`repo-${encodeURIComponent(target.id)}`)?.scrollIntoView({
				behavior: 'smooth',
				block: 'center'
			});
			return;
		}
		// Landed from a GitHub App install/sync with something still to enable
		// (#1084 "land where the user was") — carry them the rest of the way
		// to the one action left, the same section the cold-start block's own
		// "enable a repository" link points at.
		if (setupSeverity(setupNoticeCode) === 'ok' && availableInstalled.length > 0) {
			await tick();
			document.getElementById('available-repos')?.scrollIntoView({
				behavior: 'smooth',
				block: 'start'
			});
		}
	});
</script>

<div class="mx-auto max-w-4xl p-6">
	<div class="flex items-start justify-between gap-4">
		<p class="eyebrow">brnrd · repos</p>
		<!-- Same third entry as the dashboard header (2026-08-03): this is
		     the other signed-in screen, and it was the other one with no way
		     out to the documentation. -->
		<div class="flex items-center gap-4">
			<a
				href={DOCS_URL}
				rel="external"
				class="font-mono text-[11px] tracking-wide text-ink-quiet uppercase hover:text-stone-300"
				>docs</a
			>
			<a
				href={resolve('/')}
				class="font-mono text-[11px] tracking-wide text-ink-quiet uppercase hover:text-stone-300"
				>dashboard</a
			>
		</div>
	</div>
	<h1 class="mt-1 font-mono text-2xl font-semibold tracking-tight text-amber-100">
		repository control
	</h1>
	<p class="mt-2 max-w-2xl text-sm text-stone-400">
		Connect repositories, pair local daemons, and route Telegram chats into brnrd.
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
		<p class="mt-6 text-sm text-ink-quiet">Loading...</p>
	{:else}
		{#if data.notice}
			<!-- The GitHub Setup URL return (#1084). `data.notice` is the
			     backend-mapped text (`_notice_text`, `routers/_session.py`);
			     `setupNoticeCode` (the raw query-param code, captured at mount
			     before a plain refresh() can clear it) drives which register it
			     renders in — a refusal must not read like a success. -->
			<div
				class={`subpanel mt-5 p-3 text-sm ${setupSeverity(setupNoticeCode) === 'ok' ? 'border-amber-900/60 text-amber-100' : 'border-stone-700 text-stone-200'}`}
			>
				<div class="flex items-start justify-between gap-3">
					<div>
						<p class="font-mono text-[11px] tracking-wide text-ink-quiet uppercase">
							{setupSeverity(setupNoticeCode) === 'ok' ? 'github' : 'github — not news'}
						</p>
						<p class="mt-1">{data.notice}</p>
						{#if setupSeverity(setupNoticeCode) === 'ok'}
							<p class="mt-1 text-stone-400">{setupArrivalSummary(data)}</p>
						{/if}
					</div>
					<button
						type="button"
						class="cursor-pointer font-mono text-[11px] tracking-wide text-ink-quiet uppercase hover:text-stone-300"
						onclick={() => data && (data = { ...data, notice: null })}>clear</button
					>
				</div>
			</div>
		{/if}
		{#if actionResult}
			<div
				class={`subpanel mt-5 p-3 text-sm ${actionResult.ok ? 'border-amber-900/60 text-amber-100' : 'border-stone-700 text-stone-200'}`}
			>
				<div class="flex items-start justify-between gap-3">
					<div>
						<p class="font-mono text-[11px] tracking-wide text-ink-quiet uppercase">
							{actionResult.ok ? 'result' : 'error'}
						</p>
						<p class="mt-1">{actionResult.notice}</p>
					</div>
					<button
						type="button"
						class="cursor-pointer font-mono text-[11px] tracking-wide text-ink-quiet uppercase hover:text-stone-300"
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
				<p class="font-mono text-[10px] tracking-wide text-ink-quiet uppercase">signed in</p>
				<p class="mt-1 font-mono text-sm text-amber-100">@{data.account.github_login}</p>
			</div>
			<div class="subpanel p-3">
				<p class="font-mono text-[10px] tracking-wide text-ink-quiet uppercase">connected repos</p>
				<p class="mt-1 font-mono text-sm text-amber-100">
					{data.connected_count} of {data.installed_repos.length} synced
				</p>
			</div>
			<div class="subpanel p-3">
				<!-- Fact, not a control (#1084 finding — "the control lives in the
				     indicator grid"): the slug used to double as the install link,
				     inheriting this row's read-only meaning. The actual install CTA
				     now lives in "connect this repository" below, with its own
				     position and weight. -->
				<p class="font-mono text-[10px] tracking-wide text-ink-quiet uppercase">GitHub App</p>
				<p class="mt-1 truncate font-mono text-sm text-amber-100">{data.github_app_slug}</p>
			</div>
		</div>

		{#if data.installations.length === 0 && data.connected_count > 0}
			<!-- The identity nag (2026-08-06 steer): the GitHub App is a pure
			     convenience — repo connect and daemon pairing both work fully
			     without it — but skipping it means every commit and comment
			     rides *your own* GitHub identity rather than brnrd's bot (#1135
			     lived this exact gap: a strand committed as the maintainer, not
			     `brnrd-bot`). Only worth saying once something is actually
			     connected; an empty account has nothing to nag about yet. -->
			<div class="subpanel mt-5 p-3 text-sm">
				<p class="font-mono text-[11px] tracking-wide text-amber-400 uppercase">no github app</p>
				<p class="mt-1 text-stone-300">
					Work on {data.connected_count === 1 ? 'this repo' : 'these repos'} pushes under
					<strong class="text-stone-100">your own</strong> GitHub identity — every commit is you, not
					brnrd. Install the app below to push as its own bot instead; nothing else here changes.
				</p>
			</div>
		{/if}

		{#if data.connected_count === 0}
			<!-- The connect action, with its own position and weight (#1084):
			     nothing connected ⇒ this is the page's primary act, not a cell
			     in a readout grid. 2026-08-06: the website side of "enable" is
			     retired — running this command *is* the connect, no separate
			     click here first or after. GitHub App install is real but
			     genuinely optional (identity/security convenience, not a rung
			     this blocks on), so it is offered, not numbered as a required
			     step 2. -->
			<section class="panel mt-5 p-4" aria-labelledby="connect-heading">
				<p class="eyebrow">start here</p>
				<h2
					id="connect-heading"
					class="font-mono text-lg font-semibold tracking-tight text-amber-100"
				>
					connect this repository
				</h2>
				<p class="mt-2 max-w-2xl text-sm text-stone-400">
					Run this from a checkout — it registers the repo and pairs a daemon in one shot, no
					separate step here. This assumes <code class="text-stone-400">brnrd</code> is already on
					that machine — no CLI yet? The
					<a href={resolve('/')} class="text-sky-400 underline hover:text-sky-300">dashboard</a>'s
					cold-start block starts one rung earlier, with the install command.
				</p>

				<div class="mt-4">
					<div class="mt-1.5 flex items-start gap-2">
						<pre
							class="min-w-0 grow border border-stone-800 bg-stone-950/50 p-2 font-mono text-[11px] wrap-anywhere whitespace-pre-wrap text-stone-300"><code
								>{data.pairing_command}</code
							></pre>
						<button
							type="button"
							class="shrink-0 cursor-pointer border border-stone-800 px-2 py-2 font-mono text-[10px] tracking-wide text-ink-quiet uppercase hover:text-stone-300"
							onclick={() => data && copy('connect-cmd', data.pairing_command)}
							>{copied === 'connect-cmd' ? 'copied' : 'copy'}</button
						>
					</div>
					<p class="mt-1 font-mono text-[11px] text-ink-mute">
						No <code class="text-stone-400">brnrd</code> on this machine yet?
						<code class="text-stone-400">npx brnrd account connect …</code> does the whole cold start
						in one line.
					</p>
				</div>
			</section>
		{/if}

		{#if data.installations.length === 0}
			<section class="panel mt-5 p-4" aria-labelledby="install-heading">
				<p class="eyebrow">optional</p>
				<h2
					id="install-heading"
					class="font-mono text-lg font-semibold tracking-tight text-amber-100"
				>
					install the github app
				</h2>
				<p class="mt-1.5 max-w-2xl text-sm text-stone-400">
					Not required — repos connect and daemons pair fully without it. What it buys: this trades
					a personal access token sitting on your laptop for a short-lived, repo-scoped installation
					token, so commits and comments post as a separate bot account instead of you, and revoking
					access is one click on github.com instead of a credential hunt.
				</p>
				<a
					class="mt-3 inline-flex items-center border border-amber-700 bg-amber-950/40 px-4 py-2 font-mono text-sm tracking-wide text-amber-100 uppercase hover:border-amber-500"
					href={data.install_url}
					rel="external noreferrer"
					target="_blank">install the github app</a
				>
				<p class="mt-2 font-mono text-[11px] text-ink-mute">
					Opens github.com — that's where you choose repositories, not here.
				</p>
			</section>
		{/if}

		<section class="panel mt-6 p-4">
			<div class="mb-3 flex items-center justify-between gap-3">
				<div>
					<p class="eyebrow">connected</p>
					<h2 class="font-mono text-lg font-semibold tracking-tight text-amber-100">
						daemon pairing
					</h2>
				</div>
				<span
					class="shrink-0 border border-stone-800 px-1.5 py-0.5 font-mono text-[10px] tracking-wide text-ink-quiet uppercase"
					>{connectedRepos.length} enabled</span
				>
			</div>

			{#if connectedRepos.length === 0}
				<p class="text-sm text-ink-quiet">No repos enabled yet.</p>
			{:else}
				<div class="space-y-2">
					{#each connectedRepos as repo (repo.id)}
						{@const statusColor = daemonColor(repo.daemon_status)}
						<div class="subpanel p-3" id={`repo-${encodeURIComponent(repo.id)}`}>
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
										class="mt-1 flex flex-wrap gap-x-3 gap-y-1 font-mono text-[11px] text-ink-quiet"
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
									<div class="mt-2 border-t border-stone-800/70 pt-2">
										<p
											class="font-mono text-[11px] {repo.publish_layers == null
												? 'text-amber-400'
												: 'text-ink-quiet'}"
										>
											<span class="text-ink-mute uppercase tracking-wide">publish scope</span>
											— {publishScopeSummary(repo.publish_layers)}
										</p>
										{#if editingScopeRepo === repo.id}
											<div class="mt-2 grid grid-cols-1 gap-1 sm:grid-cols-2">
												{#each PUBLISH_LANES as lane (lane.value)}
													<label class="flex items-start gap-2 text-[11px] text-stone-300">
														<input
															type="checkbox"
															class="mt-0.5"
															checked={editScopeCustom.has(lane.value)}
															onchange={() => toggleEditScopeLane(lane.value)}
														/>
														<span>{lane.label}</span>
													</label>
												{/each}
											</div>
											<div class="mt-2 flex gap-2">
												<button
													type="button"
													class="cursor-pointer border border-amber-700 bg-amber-950/40 px-2 py-1 font-mono text-[11px] tracking-wide text-amber-100 uppercase hover:border-amber-500 disabled:cursor-wait disabled:opacity-50"
													disabled={pendingAction !== null}
													onclick={() => saveEverything(repo)}
													>{actionBusy(`scope:${repo.id}`)
														? 'saving'
														: 'publish everything'}</button
												>
												<button
													type="button"
													class="cursor-pointer border border-amber-700 bg-amber-950/40 px-2 py-1 font-mono text-[11px] tracking-wide text-amber-100 uppercase hover:border-amber-500 disabled:cursor-wait disabled:opacity-50"
													disabled={pendingAction !== null}
													onclick={() => saveScope(repo)}
													>{actionBusy(`scope:${repo.id}`) ? 'saving' : 'save scope'}</button
												>
												<button
													type="button"
													class="cursor-pointer border border-stone-800 px-2 py-1 font-mono text-[11px] tracking-wide text-ink-quiet uppercase hover:text-stone-300"
													disabled={pendingAction !== null}
													onclick={() => (editingScopeRepo = null)}>cancel</button
												>
											</div>
										{:else}
											<button
												type="button"
												class="mt-1 cursor-pointer font-mono text-[11px] tracking-wide text-ink-quiet uppercase underline hover:text-stone-300"
												onclick={() => startEditingScope(repo)}>revisit scope</button
											>
										{/if}
									</div>
									<MarkerNotice
										status={repo.github_bot_status}
										botLogin={data.github_bot_login}
										repoFullName={repo.repo_full_name}
										collaborator={repo.github_bot_collaborator}
										checkedLabel={repo.github_bot_checked_label}
									/>
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
													<p class="mt-0.5 font-mono text-[10px] text-ink-mute">
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
												class="cursor-pointer font-mono text-[11px] tracking-wide text-ink-quiet uppercase hover:text-stone-300"
												>setup command</summary
											>
											<pre
												class="mt-2 overflow-x-auto border border-stone-800 bg-stone-950/50 p-2 font-mono text-[11px] text-stone-300"><code
													>{repo.setup_command}</code
												></pre>
										</details>
									{/if}
								</div>

								<div class="flex w-full shrink-0 flex-col gap-2 md:w-auto md:items-end">
									{#if repo.telegram_paired}
										<!-- #885 follow-up: re-pairing is an exception (the chat moved, the route
										     broke), not a routine act, so it lives inside the status it changes —
										     the same disclosure idiom this card already uses for `setup command`.
										     An idle paired repo shows state and one destructive action, nothing
										     else to press. -->
										<details class="w-full md:w-auto md:text-right">
											<summary
												class="cursor-pointer font-mono text-[11px] tracking-wide text-ink-quiet uppercase hover:text-stone-300"
												>telegram paired</summary
											>
											<button
												type="button"
												class="mt-2 cursor-pointer border border-stone-800 px-2 py-1 font-mono text-[11px] tracking-wide text-stone-400 uppercase hover:text-stone-200 disabled:cursor-wait disabled:opacity-50"
												disabled={pendingAction !== null}
												onclick={() => pairTelegram(repo)}
												>{telegramPairLabel(true, actionBusy(`pair:${repo.id}`))}</button
											>
										</details>
									{/if}
									<div class="grid grid-cols-2 gap-2 md:flex md:justify-end">
										{#if !repo.telegram_paired && confirmingDisconnect !== repo.id}
											<button
												type="button"
												class="cursor-pointer border border-stone-800 px-2 py-1 font-mono text-[11px] tracking-wide text-stone-400 uppercase hover:text-stone-200 disabled:cursor-wait disabled:opacity-50"
												disabled={pendingAction !== null}
												onclick={() => pairTelegram(repo)}
												>{telegramPairLabel(false, actionBusy(`pair:${repo.id}`))}</button
											>
										{/if}
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
												class="cursor-pointer border border-stone-800 px-2 py-1 font-mono text-[11px] tracking-wide text-ink-quiet uppercase hover:text-stone-300"
												disabled={pendingAction !== null}
												onclick={() => (confirmingDisconnect = null)}>cancel</button
											>
										{:else}
											<button
												type="button"
												class="cursor-pointer border border-stone-800 px-2 py-1 font-mono text-[11px] tracking-wide text-ink-quiet uppercase hover:text-stone-300"
												disabled={pendingAction !== null}
												onclick={() => (confirmingDisconnect = repo.id)}>disconnect</button
											>
										{/if}
									</div>
								</div>
							</div>
						</div>
					{/each}
				</div>
			{/if}
		</section>

		<section id="available-repos" class="panel mt-6 p-4">
			<div class="mb-3 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
				<div>
					<p class="eyebrow">available</p>
					<h2 class="font-mono text-lg font-semibold tracking-tight text-amber-100">
						installed repositories
					</h2>
					{#if data.installations.length > 0}
						<p class="mt-1 text-sm text-ink-quiet">
							Synced from
							{data.installations
								.map((installation) => installation.target_login || installation.installation_id)
								.join(', ')}.
						</p>
					{/if}
				</div>
				{#if data.installations.length > 0}
					<div class="flex shrink-0 flex-wrap items-center gap-2">
						<!-- Secondary once installed (#1084): the primary install CTA
						     only exists above while there is no installation. From here
						     on this is a maintenance link, worded to what's actually left
						     — more repos to add, or nothing left but managing the grant. -->
						<a
							class="border border-stone-800 px-2 py-1 font-mono text-[11px] tracking-wide text-stone-400 uppercase hover:text-stone-200"
							href={data.install_url}
							rel="external noreferrer"
							target="_blank"
							>{availableInstalled.length > 0 ? 'add more repositories' : 'manage installation'}</a
						>
						{#if data.github_sync_configured}
							<!-- #1084's own escape hatch: `POST /api/github/sync` exists,
							     is tested, and had no button anywhere in the frontend.
							     A plain form post, not a fetch — the endpoint answers with
							     a redirect back to this page (`?notice=…`), the same
							     pattern the GitHub Setup URL return already uses, so a
							     stale installation is one page load away from a fix
							     without waiting on the webhook or the background
							     staleness check to notice on their own. Gated on
							     `github_sync_configured` (App credentials present
							     server-side) — offering a control that can only ever
							     fail closed is worse than no control. -->
							<form method="POST" action="/api/github/sync">
								<button
									type="submit"
									class="cursor-pointer border border-stone-800 px-2 py-1 font-mono text-[11px] tracking-wide text-stone-400 uppercase hover:text-stone-200"
									>recheck repos on github</button
								>
							</form>
						{/if}
					</div>
				{/if}
			</div>

			<div class="subpanel mb-4 p-3">
				<p class="eyebrow">for the next repo you enable</p>
				<h3 class="font-mono text-sm font-semibold tracking-tight text-amber-100">
					publish scope at enable
				</h3>
				<p class="mt-2 max-w-2xl text-sm text-stone-400">
					A paired daemon mirrors data here every few seconds, including the live progress card (<code
						class="font-mono text-amber-200">.card</code
					>)
					<strong class="text-stone-200">unredacted, while a run is live</strong> — full per-lane
					table in <code class="font-mono text-amber-200">SECURITY.md</code>. Pick what the next
					repo you enable may mirror; nothing you don't name here ships. Revisit any connected
					repo's scope above, any time. This browser remembers the choice below for future enables
					on this account.
				</p>
				<div class="mt-3 flex flex-wrap gap-2">
					{#each CONNECT_SCOPE_PRESETS as option (option.value)}
						<button
							type="button"
							class={`cursor-pointer border px-2 py-1 font-mono text-[11px] tracking-wide uppercase ${
								connectScopePreset === option.value
									? 'border-amber-700 bg-amber-950/40 text-amber-100'
									: 'border-stone-800 text-stone-400 hover:text-stone-200'
							}`}
							onclick={() => selectConnectScopePreset(option.value)}>{option.label}</button
						>
					{/each}
				</div>
				{#if connectScopePreset === 'custom'}
					<div class="subpanel mt-3 grid grid-cols-1 gap-1 p-3 sm:grid-cols-2">
						{#each PUBLISH_LANES as lane (lane.value)}
							<label class="flex items-start gap-2 text-[11px] text-stone-300">
								<input
									type="checkbox"
									class="mt-0.5"
									checked={connectScopeCustom.has(lane.value)}
									onchange={() => toggleConnectScopeLane(lane.value)}
								/>
								<span>{lane.label}</span>
							</label>
						{/each}
					</div>
				{/if}
			</div>

			{#if data.installations.length === 0}
				<p class="text-sm text-ink-quiet">
					Nothing to connect yet — connect this repository above first.
				</p>
			{:else if availableInstalled.length === 0}
				<p class="text-sm text-ink-quiet">
					All {connectedInstalled.length} synced repositories are connected.
				</p>
			{:else}
				<!-- No "enable" button (retired 2026-08-06): the website never did
				     anything GitHub-specific here, it only recorded a name — and
				     running the command below from that checkout now does the
				     recording itself, the moment it actually pairs a daemon.
				     Purely informational: which repos GitHub already shows us,
				     and the one line to run for each. -->
				<div class="grid grid-cols-1 gap-2 lg:grid-cols-2">
					{#each availableInstalled as repo (repo.id)}
						<div class="subpanel p-3">
							<p class="truncate font-mono text-sm font-semibold text-amber-100">
								{repo.repo_full_name}
							</p>
							<p class="mt-1 truncate font-mono text-[11px] text-ink-quiet">
								{branchLabel(repo.default_branch)} · pushed {repo.pushed_label}
							</p>
							<details class="mt-2">
								<summary
									class="cursor-pointer font-mono text-[11px] tracking-wide text-ink-quiet uppercase hover:text-stone-300"
									>connect from a checkout</summary
								>
								<pre
									class="mt-2 overflow-x-auto border border-stone-800 bg-stone-950/50 p-2 font-mono text-[11px] text-stone-300"><code
										>{repo.setup_command}</code
									></pre>
							</details>
						</div>
					{/each}
				</div>
			{/if}

			<form class="mt-5 border-t border-stone-800/70 pt-4" onsubmit={connectManual}>
				<p class="font-mono text-[11px] tracking-wide text-ink-quiet uppercase">
					advanced: pre-register without running anything locally
				</p>
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
						>{actionBusy('connect:manual') ? 'registering' : 'register repo'}</button
					>
				</div>
			</form>
		</section>
	{/if}
</div>
