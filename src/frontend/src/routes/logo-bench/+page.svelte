<script lang="ts">
	// /logo-bench: an unlinked tuning surface for the brnrd pixel wordmark.
	// Geometry lives in $lib/brnrdLogoGeometry (pure, importable elsewhere);
	// this page is controls and previews only. Settings ride in the URL.
	import { onMount } from 'svelte';
	import {
		AWAKE,
		DEFAULTS,
		FX_KEYS,
		cellAt,
		canvas,
		frameKey,
		NO_FX,
		PALETTES,
		RANGES,
		REST,
		WINK,
		fromQuery,
		layout,
		toQuery,
		toSvg
	} from '$lib/brnrdLogoGeometry';
	import type { Effects, Params, Pose, Settings } from '$lib/brnrdLogoGeometry';

	const BASE: Settings = {
		p: { ...DEFAULTS },
		pose: { ...REST },
		fg: PALETTES[1].fg,
		bg: PALETTES[1].bg,
		accent: PALETTES[1].accent,
		glowColor: '',
		ov: {},
		fx: { ...NO_FX }
	};

	let s = $state<Settings>(structuredClone(BASE));
	let playing = $state(false);
	let playPose = $state<Pose | null>(null);
	let scale = $state(4);
	let note = $state('');
	let ready = $state(false);

	onMount(() => {
		s = fromQuery(new URLSearchParams(location.search), BASE);
		ready = true;
		return () => stop();
	});

	// keep the URL a shareable link of the current settings
	$effect(() => {
		const q = toQuery($state.snapshot(s) as Settings, BASE);
		if (ready) history.replaceState(history.state, '', q ? `?${q}` : location.pathname);
	});

	let pose = $derived(playPose ?? s.pose);
	let palette = $derived({ fg: s.fg, bg: s.bg, accent: s.accent });
	const svg = (p: Pose, o: Partial<Parameters<typeof toSvg>[1]> = {}, id = 'm') =>
		toSvg(s.p, { pose: p, palette, fx: s.fx, id, ov: s.ov, glowColor: s.glowColor, ...o });

	let timer: ReturnType<typeof setTimeout> | undefined;
	function stop() {
		clearTimeout(timer);
		playing = false;
		playPose = null;
	}
	function play() {
		if (playing) return stop();
		playing = true;
		let i = 0;
		const step = () => {
			if (!playing) return;
			const f = WINK[i % WINK.length];
			playPose = f.pose;
			i += 1;
			timer = setTimeout(step, f.ms);
		};
		step();
	}

	function pick(name: string) {
		const c = PALETTES.find((x) => x.name === name)!;
		s.fg = c.fg;
		s.bg = c.bg;
		s.accent = c.accent;
	}
	// click a pixel in the main view: force it on or off for this frame
	function toggleCell(e: MouseEvent) {
		const el = (e.target as Element).closest('svg');
		if (!el) return;
		const r = el.getBoundingClientRect();
		const hit = cellAt(
			s.p,
			pose,
			s.ov,
			(e.clientX - r.left) / r.width,
			(e.clientY - r.top) / r.height
		);
		if (!hit) return;
		const { x, y } = hit;
		const k = `${x},${y}`;
		const f = frameKey(pose, s.p.density);
		const on = layout(s.p, pose, s.ov).cells.some((c) => c.x === x && c.y === y);
		const base = layout(s.p, pose).cells.some((c) => c.x === x && c.y === y);
		const m = { ...(s.ov[f] ?? {}) };
		if (!on === base) delete m[k];
		else m[k] = !on;
		s.ov[f] = m;
	}
	// a new density keeps the letter's size: the pixel shrinks by the same factor
	function setDensity(n: number) {
		const old = s.p.density;
		s.p.cell = Math.max(2, Math.min(48, Math.round((s.p.cell * old) / n)));
		s.p.density = n;
	}
	const nOv = $derived(Object.values(s.ov).reduce((n, m) => n + Object.keys(m).length, 0));
	const cols = $derived(layout(s.p, REST).cols);

	// export
	const exportSvg = () => svg(pose, { id: 'x' });
	function download(name: string, blob: Blob) {
		const a = document.createElement('a');
		a.href = URL.createObjectURL(blob);
		a.download = name;
		a.click();
		setTimeout(() => URL.revokeObjectURL(a.href), 1000);
	}
	const downloadSvg = () =>
		download('brnrd-logo.svg', new Blob([exportSvg()], { type: 'image/svg+xml' }));
	function downloadPng() {
		const h = canvas(s.p, pose, s.ov).h * s.p.cell * scale;
		const markup = svg(pose, { id: 'x', height: h });
		const img = new Image();
		img.onload = () => {
			const c = document.createElement('canvas');
			c.width = img.naturalWidth;
			c.height = img.naturalHeight;
			c.getContext('2d')!.drawImage(img, 0, 0);
			c.toBlob((b) => b && download(`brnrd-logo@${scale}x.png`, b));
		};
		img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(markup);
	}
	async function copyJson() {
		const j = JSON.stringify($state.snapshot(s), null, 2);
		try {
			await navigator.clipboard.writeText(j);
			note = 'copied ✓';
		} catch {
			note = 'clipboard blocked';
		}
		setTimeout(() => (note = ''), 1500);
	}

	const HEIGHTS = [16, 32, 64, 128, 320];
</script>

<!-- eslint-disable svelte/no-at-html-tags -- SVG is generated locally from numbers -->
<svelte:head><title>logo-bench · brnrd</title></svelte:head>

<div class="mx-auto flex max-w-6xl flex-col gap-4 p-6 font-mono text-sm text-stone-200">
	<header>
		<p class="eyebrow">temporary · unlinked · /logo-bench</p>
		<h1 class="text-lg font-semibold text-amber-100">the pixel mark you can drag</h1>
		<p class="mt-1 max-w-3xl text-xs text-ink-quiet">
			brnrd and b^n^d are one shape: the n drops, the r stems go, the ^ stays. Every setting is in
			the URL; copy the link to share it.
		</p>
	</header>

	<div class="grid gap-4 lg:grid-cols-[1fr_320px]">
		<div class="flex flex-col gap-4">
			<div
				class="panel flex items-center justify-center overflow-auto p-4"
				style="background:{s.bg}"
			>
				<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
				<div class="cursor-crosshair" onclick={toggleCell}>
					{@html svg(pose, { background: false }, 'main')}
				</div>
			</div>
			<div class="grid grid-cols-2 gap-4">
				{#each [['brnrd', REST], ['b^n^d', AWAKE]] as [label, p] (label)}
					<div class="panel flex flex-col items-center gap-1 p-3">
						<div class="max-w-full overflow-auto">
							{@html svg(p as Pose, { height: 120 }, `f${label}`)}
						</div>
						<p class="text-[10px] text-ink-mute">{label}</p>
					</div>
				{/each}
			</div>
		</div>

		<div class="panel flex flex-col gap-2 p-4">
			<div class="flex items-center gap-2">
				<button
					class="panel panel--pressable px-2 py-1"
					onclick={() => {
						stop();
						s.pose = { ...REST };
					}}>brnrd</button
				>
				<button
					class="panel panel--pressable px-2 py-1"
					onclick={() => {
						stop();
						s.pose = { ...AWAKE };
					}}>b^n^d</button
				>
				<button class="panel panel--pressable px-2 py-1 text-amber-100" onclick={play}>
					{playing ? 'stop' : '▶ wink'}
				</button>
			</div>
			<label class="flex items-center gap-2">
				<span class="w-24 shrink-0 text-[11px] text-ink-quiet">frame {s.pose.t.toFixed(2)}</span>
				<input
					class="w-full"
					type="range"
					min="0"
					max="1"
					step="0.05"
					value={s.pose.t}
					oninput={(e) => {
						stop();
						s.pose = { t: +e.currentTarget.value, eyeL: 'caret', eyeR: 'caret' };
					}}
				/>
			</label>
			<label class="flex flex-col gap-1">
				<span class="text-xs font-semibold text-amber-100">pixel size · {s.p.cell}px</span>
				<input class="w-full" type="range" min="4" max="48" step="1" bind:value={s.p.cell} />
			</label>
			<hr class="border-stone-800" />
			{#each RANGES.filter((x) => x.key !== 'cell' && x.key !== 'nStyle' && x.key !== 'density') as r (r.key)}
				<label class="flex items-center gap-2">
					<span class="w-32 shrink-0 text-[11px] text-ink-quiet">{r.label}</span>
					<input
						class="w-full"
						type="range"
						min={r.min}
						max={r.max}
						step={r.step}
						bind:value={s.p[r.key as keyof Params]}
					/>
					<span class="w-8 text-right text-[10px] text-ink-mute">{s.p[r.key as keyof Params]}</span>
				</label>
			{/each}
			<button
				class="panel panel--pressable px-2 py-1 text-ink-quiet"
				onclick={() => (s.p = { ...DEFAULTS })}>reset geometry</button
			>
			<hr class="border-stone-800" />
			<div class="flex items-center gap-3">
				{#each [['fg', 'ink'], ['bg', 'ground'], ['accent', 'accent']] as [k, l] (k)}
					<label class="flex items-center gap-1 text-[10px] text-ink-quiet">
						{l}
						<input
							type="color"
							bind:value={s[k as 'fg' | 'bg' | 'accent']}
							class="h-6 w-8 cursor-pointer border border-stone-700 bg-transparent p-0.5"
						/>
					</label>
				{/each}
			</div>
			<label class="flex items-center gap-2 text-[11px] text-ink-quiet">
				density
				<select
					class="panel px-1 py-1"
					value={s.p.density}
					onchange={(e) => setDensity(+e.currentTarget.value)}
				>
					{#each [1, 2, 3, 4] as n (n)}<option value={n}>{n}</option>{/each}
				</select>
			</label>
			<label class="flex items-center gap-2 text-[11px] text-ink-quiet">
				n style
				<select class="panel px-1 py-1" bind:value={s.p.nStyle}>
					<option value={0}>arch</option>
					<option value={1}>mirror</option>
				</select>
			</label>
			<label class="flex items-center gap-2 text-[10px] text-ink-quiet">
				glow colour
				<input
					type="color"
					value={s.glowColor || s.fg}
					oninput={(e) => (s.glowColor = e.currentTarget.value)}
					class="h-6 w-8 cursor-pointer border border-stone-700 bg-transparent p-0.5"
				/>
				<button class="underline" onclick={() => (s.glowColor = '')}>ink</button>
			</label>
			<div class="flex items-center gap-2 text-[10px] text-ink-quiet">
				click a pixel in the big view to flip it ({nOv} flipped)
				<button class="underline" onclick={() => (s.ov = {})}>clear</button>
			</div>
			<div class="flex flex-wrap gap-3">
				{#each FX_KEYS as k (k)}
					<label class="flex items-center gap-1 text-[11px] text-ink-quiet">
						<input type="checkbox" bind:checked={s.fx[k as keyof Effects]} />
						{{ scan: 'scanlines', grain: 'grain', jitter: 'jitter' }[k]}
					</label>
				{/each}
			</div>
			<hr class="border-stone-800" />
			<div class="flex flex-wrap items-center gap-2">
				<button class="panel panel--pressable px-2 py-1" onclick={copyJson}
					>{note || 'copy JSON'}</button
				>
				<button class="panel panel--pressable px-2 py-1" onclick={downloadSvg}>SVG</button>
				<button class="panel panel--pressable px-2 py-1" onclick={downloadPng}>PNG</button>
				<select class="panel px-1 py-1" bind:value={scale}>
					{#each [1, 2, 4, 8] as n (n)}<option value={n}>×{n}</option>{/each}
				</select>
			</div>
		</div>
	</div>

	<section class="panel flex flex-col gap-3 p-4">
		<p class="text-[10px] text-ink-mute">colours · click to wear one ({cols} cells wide)</p>
		<div class="grid grid-cols-2 gap-3 md:grid-cols-3">
			{#each PALETTES as c (c.name)}
				<button class="flex flex-col items-center gap-1" onclick={() => pick(c.name)}>
					<div>
						{@html toSvg(s.p, {
							pose,
							palette: c,
							fx: s.fx,
							ov: s.ov,
							glowColor: s.glowColor,
							height: 96,
							id: 'c' + c.name.replace(/\W/g, '')
						})}
					</div>
					<span class="text-[10px] text-ink-mute">{c.name}</span>
				</button>
			{/each}
		</div>
	</section>

	<section class="panel flex flex-col gap-3 p-4">
		<p class="text-[10px] text-ink-mute">density · 1 to 4, same letter, finer pixels</p>
		<div class="flex flex-wrap items-end gap-4">
			{#each [1, 2, 3, 4] as n (n)}
				<div class="flex flex-col items-center gap-1">
					{@html toSvg(
						{ ...s.p, density: n },
						{
							pose,
							palette,
							fx: s.fx,
							glowColor: s.glowColor,
							ov: s.ov,
							height: 110,
							id: 'dn' + n
						}
					)}
					<span class="text-[9px] text-ink-mute">density {n}</span>
				</div>
			{/each}
		</div>
	</section>

	<section class="panel flex flex-col gap-3 p-4">
		<p class="text-[10px] text-ink-mute">sizes · rectangles</p>
		<div class="flex flex-wrap items-end gap-4">
			{#each HEIGHTS as h (h)}
				<div class="flex flex-col items-center gap-1">
					<div>{@html svg(pose, { height: h, id: 'z' + h })}</div>
					<span class="text-[9px] text-ink-mute">{h}px</span>
				</div>
			{/each}
		</div>
		<div class="flex flex-wrap items-end gap-4">
			<div class="flex flex-col items-center gap-1">
				<div>{@html svg(pose, { frame: 'square', height: 64, id: 'fav' })}</div>
				<span class="text-[9px] text-ink-mute">favicon tile</span>
			</div>
			<div class="flex flex-col items-center gap-1">
				<div>
					{@html svg(pose, {
						frame: 'square',
						shape: 'circle',
						height: 96,
						id: 'av',
						background: true
					})}
				</div>
				<span class="text-[9px] text-ink-mute">avatar</span>
			</div>
			<div class="flex flex-col items-center gap-1">
				<div>{@html svg(pose, { frame: 'wide', height: 100, id: 'ban' })}</div>
				<span class="text-[9px] text-ink-mute">banner</span>
			</div>
		</div>
	</section>
</div>
