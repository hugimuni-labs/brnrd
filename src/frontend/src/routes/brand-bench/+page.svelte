<script lang="ts">
	// /brand-bench is deliberately an unlinked tuning surface. BRNRD keeps its
	// existing generated geometry; HugiMuni now mirrors the canonical three-
	// region master in media/brand/hugimuni/build.py.
	import {
		BRNRD_COLORS,
		BRNRD_DEFAULTS,
		FACES,
		brnrdAberrationSvg,
		brnrdConstantBlock,
		brnrdStoneSvg
	} from '$lib/brandGeometry';
	import type { BrnrdConstants, BrnrdFrame } from '$lib/brandGeometry';
	import {
		HUGIMUNI_DEFAULTS,
		hugimuniConstantBlock,
		hugimuniFlatSvg,
		hugimuniLockupSvg,
		hugimuniScreenSvg
	} from '$lib/hugimuniBrandGeometry';
	import type { HugimuniConstants, HugimuniRegister } from '$lib/hugimuniBrandGeometry';

	type Mark = 'brnrd' | 'hugimuni';
	type BrnrdRegister = 'stone' | 'screen';

	let mark = $state<Mark>('hugimuni');
	let brnrdRegister = $state<BrnrdRegister>('stone');
	let hugimuniRegister = $state<HugimuniRegister>('flat');
	let frame = $state<BrnrdFrame>('rest');
	let brnrdConstants = $state<BrnrdConstants>({ ...BRNRD_DEFAULTS });
	let hugimuniConstants = $state<HugimuniConstants>({ ...HUGIMUNI_DEFAULTS });

	interface SliderSpec {
		key: string;
		label: string;
		min: number;
		max: number;
		step: number;
	}

	const BRNRD_SLIDERS: SliderSpec[] = [
		{ key: 'SLOT', label: 'SLOT — grid cell width', min: 20, max: 140, step: 1 },
		{ key: 'STAVE_TOP', label: 'STAVE_TOP — ascender top', min: 0, max: 260, step: 1 },
		{ key: 'BASELINE', label: 'BASELINE — stroke foot', min: 260, max: 512, step: 1 },
		{ key: 'BOWL_TOP', label: 'BOWL_TOP — bowl start', min: 150, max: 420, step: 1 },
		{ key: 'BOWL_W', label: 'BOWL_W — bowl reach', min: 10, max: 150, step: 1 },
		{ key: 'STAVE_INSET', label: 'STAVE_INSET — stave inset', min: 0, max: 80, step: 1 },
		{ key: 'STROKE', label: 'STROKE — stroke width', min: 2, max: 60, step: 1 },
		{ key: 'XTOP', label: 'XTOP — resting x-height', min: 150, max: 420, step: 1 },
		{ key: 'EYE_Y', label: 'EYE_Y — eye row', min: 200, max: 460, step: 1 },
		{ key: 'MOUTH_Y', label: 'MOUTH_Y — mouth row', min: 250, max: 490, step: 1 },
		{ key: 'EYE_R', label: 'EYE_R — eye radius', min: 2, max: 50, step: 1 }
	];

	const HUGIMUNI_GEOMETRY: SliderSpec[] = [
		{ key: 'LEFT', label: 'LEFT — left stem anchor', min: 80, max: 220, step: 1 },
		{ key: 'RIGHT', label: 'RIGHT — right stem anchor', min: 292, max: 432, step: 1 },
		{ key: 'TOP', label: 'TOP — cap line', min: 80, max: 220, step: 1 },
		{ key: 'BOTTOM', label: 'BOTTOM — stem foot', min: 290, max: 430, step: 1 },
		{ key: 'CROSS', label: 'CROSS — H crossbar', min: 190, max: 330, step: 1 },
		{ key: 'OVERHANG', label: 'OVERHANG — H bar outside stems', min: 0, max: 70, step: 1 },
		{ key: 'SPREAD', label: 'SPREAD — M shoulder overshoot', min: -20, max: 80, step: 1 },
		{ key: 'RISE', label: 'RISE — M shoulder vertical offset', min: -60, max: 60, step: 1 },
		{ key: 'DIP', label: 'DIP — M lower-leg depth', min: -60, max: 80, step: 1 },
		{ key: 'TAIL', label: 'TAIL — lower crossing overshoot', min: 0, max: 80, step: 1 },
		{ key: 'STROKE', label: 'STROKE — bar / diagonal weight', min: 10, max: 60, step: 1 },
		{ key: 'STEM_STROKE', label: 'STEM_STROKE — stem weight', min: 14, max: 80, step: 1 },
		{ key: 'GHOST', label: 'GHOST — H/M registration offset', min: 0, max: 24, step: 1 },
		{ key: 'GROUND_RX', label: 'GROUND_RX — icon corner radius', min: 0, max: 160, step: 1 }
	];

	const HUGIMUNI_SCREEN: SliderSpec[] = [
		{ key: 'BLOOM_BLUR', label: 'BLOOM_BLUR — halo radius', min: 0, max: 18, step: 0.5 },
		{ key: 'BLOOM_OPACITY', label: 'BLOOM_OPACITY — halo strength', min: 0, max: 1, step: 0.02 },
		{ key: 'HOT_BLUR', label: 'HOT_BLUR — overlap glow radius', min: 0, max: 6, step: 0.1 },
		{ key: 'HOT_OPACITY', label: 'HOT_OPACITY — overlap glow strength', min: 0, max: 1, step: 0.02 },
		{ key: 'GRAIN', label: 'GRAIN — phosphor texture', min: 0, max: 100, step: 1 }
	];

	const FACE_NAMES = Object.keys(FACES) as (keyof typeof FACES)[];

	function reset() {
		if (mark === 'brnrd') brnrdConstants = { ...BRNRD_DEFAULTS };
		else hugimuniConstants = { ...HUGIMUNI_DEFAULTS };
	}

	function hugiMarkup(prefix: string) {
		return hugimuniRegister === 'flat'
			? hugimuniFlatSvg(hugimuniConstants, prefix)
			: hugimuniScreenSvg(hugimuniConstants, prefix);
	}

	let mainMarkup = $derived(
		mark === 'brnrd'
			? brnrdRegister === 'stone'
				? brnrdStoneSvg(frame, brnrdConstants)
				: brnrdAberrationSvg(frame, brnrdConstants)
			: hugiMarkup('bench-main')
	);
	let small32Markup = $derived(mark === 'brnrd' ? mainMarkup : hugiMarkup('bench-32'));
	let small16Markup = $derived(mark === 'brnrd' ? mainMarkup : hugiMarkup('bench-16'));
	let lockupMarkup = $derived(
		mark === 'hugimuni'
			? hugimuniLockupSvg(hugimuniConstants, hugimuniRegister, 'bench-lockup')
			: ''
	);
	let constantBlock = $derived(
		mark === 'brnrd' ? brnrdConstantBlock(brnrdConstants) : hugimuniConstantBlock(hugimuniConstants)
	);
	let constantBlockTarget = $derived(
		mark === 'brnrd' ? 'media/brand/build.py' : 'media/brand/hugimuni/build.py'
	);

	let copied = $state(false);
	async function copyConstants() {
		try {
			await navigator.clipboard.writeText(constantBlock);
			copied = true;
			setTimeout(() => (copied = false), 1500);
		} catch {
			copied = false;
		}
	}
</script>

<svelte:head><title>brand-bench · brnrd</title></svelte:head>

<div class="mx-auto flex max-w-6xl flex-col gap-4 p-6 font-mono text-sm text-stone-200">
	<header>
		<p class="eyebrow">temporary · unlinked · /brand-bench</p>
		<h1 class="text-lg font-semibold text-amber-100">the mark you can drag</h1>
		<p class="mt-1 max-w-3xl text-xs text-ink-quiet">
			HugiMuni now treats the flat H/M overlap as the identity source: amber = H only, sky = M only,
			cream = H∩M. Screen mode is that same mark with atmosphere layered on top.
		</p>
	</header>

	<div class="flex flex-wrap items-center gap-4">
		<label class="flex items-center gap-2">
			<span class="text-ink-quiet">mark</span>
			<select class="panel px-2 py-1" bind:value={mark}>
				<option value="hugimuni">hugimuni</option>
				<option value="brnrd">brnrd</option>
			</select>
		</label>

		{#if mark === 'brnrd'}
			<label class="flex items-center gap-2">
				<span class="text-ink-quiet">register</span>
				<select class="panel px-2 py-1" bind:value={brnrdRegister}>
					<option value="stone">stone</option>
					<option value="screen">screen</option>
				</select>
			</label>
			<label class="flex items-center gap-2">
				<span class="text-ink-quiet">frame</span>
				<select class="panel px-2 py-1" bind:value={frame}>
					<option value="name">name</option>
					{#each FACE_NAMES as name (name)}<option value={name}>{name}</option>{/each}
				</select>
			</label>
			<label class="flex items-center gap-2">
				<span class="text-ink-quiet">crown</span>
				<select class="panel px-2 py-1" bind:value={brnrdConstants.CROWN}>
					<option value="none">none</option><option value="branch">branch</option><option value="fork">fork</option>
				</select>
			</label>
		{:else}
			<label class="flex items-center gap-2">
				<span class="text-ink-quiet">register</span>
				<select class="panel px-2 py-1" bind:value={hugimuniRegister}>
					<option value="flat">flat — canonical</option>
					<option value="screen">screen — atmosphere</option>
				</select>
			</label>
			<label class="flex items-center gap-2">
				<input type="checkbox" bind:checked={hugimuniConstants.GROUND_ON} />
				<span class="text-ink-quiet">ground</span>
			</label>
		{/if}

		<button class="panel panel--pressable px-2 py-1 text-ink-quiet hover:text-amber-100" onclick={reset}>
			reset {mark}
		</button>
	</div>

	{#if mark === 'hugimuni'}
		<div class="panel flex flex-wrap items-center gap-4 p-3">
			{#each [
				['AMBER', 'amber'],
				['SKY', 'sky'],
				['INTERSECTION', 'intersection'],
				['GROUND', 'ground']
			] as [key, label]}
				<label class="flex items-center gap-2">
					<span class="text-[10px] text-ink-quiet">{label}</span>
					<input
						type="color"
						bind:value={hugimuniConstants[key as 'AMBER' | 'SKY' | 'INTERSECTION' | 'GROUND']}
						class="h-7 w-10 cursor-pointer border border-stone-700 bg-transparent p-0.5"
					/>
					<input
						type="text"
						bind:value={hugimuniConstants[key as 'AMBER' | 'SKY' | 'INTERSECTION' | 'GROUND']}
						class="panel w-24 px-2 py-1 text-[10px]"
					/>
				</label>
			{/each}
		</div>
	{/if}

	<div class="flex flex-wrap items-start gap-6">
		<div class="panel flex flex-col items-center gap-3 p-4">
			<!-- eslint-disable-next-line svelte/no-at-html-tags -- locally generated SVG -->
			<div class="mark-frame" style="width: 320px; height: 320px;">{@html mainMarkup}</div>
			<p class="text-[10px] text-ink-mute">320px tuning size</p>
		</div>

		<div class="panel flex flex-col gap-3 p-4">
			<p class="text-[10px] text-ink-mute">small-size survival</p>
			<div class="flex items-end gap-4">
				<div class="flex flex-col items-center gap-1">
					<!-- eslint-disable-next-line svelte/no-at-html-tags -->
					<div class="mark-frame" style="width: 32px; height: 32px;">{@html small32Markup}</div>
					<p class="text-[9px] text-ink-mute">32px</p>
				</div>
				<div class="flex flex-col items-center gap-1">
					<!-- eslint-disable-next-line svelte/no-at-html-tags -->
					<div class="mark-frame" style="width: 16px; height: 16px;">{@html small16Markup}</div>
					<p class="text-[9px] text-ink-mute">16px</p>
				</div>
			</div>
		</div>

		{#if mark === 'hugimuni'}
			<div class="panel flex flex-col items-center gap-3 p-4">
				<p class="text-[10px] text-ink-mute">canonical lockup · one word below symbol</p>
				<!-- eslint-disable-next-line svelte/no-at-html-tags -->
				<div class="lockup-frame" style="width: 274px; height: 300px;">{@html lockupMarkup}</div>
			</div>
		{/if}

		<div class="panel min-w-[320px] flex-1 p-4">
			<div class="mb-2 flex items-center justify-between gap-4">
				<p class="text-[10px] text-ink-mute">copy constants → {constantBlockTarget}</p>
				<button class="panel panel--pressable px-2 py-1 text-amber-100" onclick={copyConstants}>
					{copied ? 'copied ✓' : 'copy constants'}
				</button>
			</div>
			<pre class="max-h-72 overflow-auto rounded bg-black/40 p-2 text-[11px] leading-relaxed text-stone-300">{constantBlock}</pre>
		</div>
	</div>

	<div class="panel flex flex-col gap-3 p-4">
		<p class="text-[10px] text-ink-mute">geometry · every change redraws the actual vector model</p>
		<div class="grid grid-cols-1 gap-x-6 gap-y-2 lg:grid-cols-2">
			{#if mark === 'brnrd'}
				{#each BRNRD_SLIDERS as spec (spec.key)}
					<label class="flex items-center gap-2">
						<span class="w-60 shrink-0 text-[11px] text-ink-quiet">{spec.label}</span>
						<input type="range" min={spec.min} max={spec.max} step={spec.step} bind:value={brnrdConstants[spec.key as keyof BrnrdConstants]} class="min-w-24 flex-1" />
						<input type="number" min={spec.min} max={spec.max} step={spec.step} bind:value={brnrdConstants[spec.key as keyof BrnrdConstants]} class="panel w-20 px-1 py-0.5 text-right" />
					</label>
				{/each}
			{:else}
				{#each HUGIMUNI_GEOMETRY as spec (spec.key)}
					<label class="flex items-center gap-2">
						<span class="w-60 shrink-0 text-[11px] text-ink-quiet">{spec.label}</span>
						<input type="range" min={spec.min} max={spec.max} step={spec.step} bind:value={hugimuniConstants[spec.key as keyof HugimuniConstants]} class="min-w-24 flex-1" />
						<input type="number" min={spec.min} max={spec.max} step={spec.step} bind:value={hugimuniConstants[spec.key as keyof HugimuniConstants]} class="panel w-20 px-1 py-0.5 text-right" />
					</label>
				{/each}
			{/if}
		</div>
	</div>

	{#if mark === 'hugimuni' && hugimuniRegister === 'screen'}
		<div class="panel flex flex-col gap-3 p-4">
			<p class="text-[10px] text-ink-mute">screen material · these do not change the flat logo</p>
			<div class="grid grid-cols-1 gap-x-6 gap-y-2 lg:grid-cols-2">
				{#each HUGIMUNI_SCREEN as spec (spec.key)}
					<label class="flex items-center gap-2">
						<span class="w-60 shrink-0 text-[11px] text-ink-quiet">{spec.label}</span>
						<input type="range" min={spec.min} max={spec.max} step={spec.step} bind:value={hugimuniConstants[spec.key as keyof HugimuniConstants]} class="min-w-24 flex-1" />
						<input type="number" min={spec.min} max={spec.max} step={spec.step} bind:value={hugimuniConstants[spec.key as keyof HugimuniConstants]} class="panel w-20 px-1 py-0.5 text-right" />
					</label>
				{/each}
			</div>
		</div>
	{/if}

	<p class="text-[10px] text-ink-mute">
		{#if mark === 'hugimuni'}
			flat is canonical. Amber means H-only, sky M-only, cream H∩M. The lower M-on-M crossing stays sky by construction.
		{:else}
			brnrd colours: stone {BRNRD_COLORS.STONE}, molten {BRNRD_COLORS.MOLTEN}→{BRNRD_COLORS.EMBER}, screen ghosts {BRNRD_COLORS.RED}/{BRNRD_COLORS.CYAN}.
		{/if}
	</p>
</div>

<style>
	.mark-frame :global(svg),
	.lockup-frame :global(svg) {
		display: block;
		width: 100%;
		height: 100%;
	}
	.mark-frame,
	.lockup-frame {
		background: #030504;
	}
</style>
