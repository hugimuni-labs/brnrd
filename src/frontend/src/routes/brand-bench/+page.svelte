<script lang="ts">
	// /brand-bench — a tuning bench, not a product surface.
	//
	// His ask, verbatim: "a temp web app on one of our routes, where I could
	// move the values with sliders and inputs and see it renders differently"
	// — "something simple". This route renders the brnrd and hugimuni marks
	// as live inline SVG from the same named constants `media/brand/build.py`
	// and `media/brand/hugimuni/build.py` draw from (geometry ported to
	// `$lib/brandGeometry.ts` — see that file's module doc for the vendoring
	// note and the one formatting quirk that resisted a byte-for-byte port).
	//
	// Deliberately unstyled beyond legibility, deliberately unlinked from any
	// nav — reachable only by typing the URL. No auth guard: the task said
	// "does not need auth", and gating a route nobody links to behind a
	// dev-only check adds a decision (what "dev" means in this deploy) this
	// bench doesn't need to make. If this route needs to outlive the current
	// tuning pass, that's the moment to reconsider — a bench with no auth and
	// no nav entry is still a real URL if the app is public.
	import {
		BRNRD_COLORS,
		BRNRD_DEFAULTS,
		FACES,
		HUGIMUNI_DEFAULTS,
		HUGIMUNI_PALETTES,
		brnrdAberrationSvg,
		brnrdConstantBlock,
		brnrdStoneSvg,
		hugimuniConstantBlock,
		hugimuniSvg
	} from '$lib/brandGeometry';
	import type {
		BrnrdConstants,
		BrnrdFrame,
		HugimuniConstants,
		HugimuniPaletteName
	} from '$lib/brandGeometry';

	type Mark = 'brnrd' | 'hugimuni';
	type Register = 'stone' | 'screen';

	let mark = $state<Mark>('brnrd');
	let register = $state<Register>('stone'); // hugimuni only has one register (see below)
	let frame = $state<BrnrdFrame>('rest');
	let palette = $state<HugimuniPaletteName>('amber-sky');

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
		{ key: 'STAVE_TOP', label: 'STAVE_TOP — top of b/d ascender', min: 0, max: 260, step: 1 },
		{ key: 'BASELINE', label: 'BASELINE — foot of every stroke', min: 260, max: 512, step: 1 },
		{ key: 'BOWL_TOP', label: 'BOWL_TOP — x-height / bowl start', min: 150, max: 420, step: 1 },
		{ key: 'BOWL_W', label: 'BOWL_W — bowl reach off the stave', min: 10, max: 150, step: 1 },
		{
			key: 'STAVE_INSET',
			label: 'STAVE_INSET — stave inset from cell edge',
			min: 0,
			max: 80,
			step: 1
		},
		{ key: 'STROKE', label: 'STROKE — stroke width', min: 2, max: 60, step: 1 },
		{ key: 'XTOP', label: 'XTOP — resting-frame x-height', min: 150, max: 420, step: 1 },
		{ key: 'EYE_Y', label: 'EYE_Y — eye row', min: 200, max: 460, step: 1 },
		{ key: 'MOUTH_Y', label: 'MOUTH_Y — mouth row', min: 250, max: 490, step: 1 },
		{ key: 'EYE_R', label: 'EYE_R — eye/dot radius', min: 2, max: 50, step: 1 }
	];

	const HUGIMUNI_SLIDERS: SliderSpec[] = [
		{ key: 'LEFT', label: 'LEFT — left stem x', min: 0, max: 256, step: 1 },
		{ key: 'RIGHT', label: 'RIGHT — right stem x', min: 256, max: 512, step: 1 },
		{ key: 'TOP', label: 'TOP — stem top y', min: 0, max: 256, step: 1 },
		{ key: 'BOTTOM', label: 'BOTTOM — stem foot y', min: 256, max: 512, step: 1 },
		{ key: 'CROSS', label: "CROSS — H's crossbar y", min: 0, max: 512, step: 1 },
		{ key: 'OVERHANG', label: 'OVERHANG — crossbar past both stems', min: 0, max: 100, step: 1 },
		{ key: 'SPREAD', label: "SPREAD — M's shoulders past the stems", min: 0, max: 150, step: 1 },
		{ key: 'RISE', label: 'RISE — leg crossing offset, top', min: -100, max: 100, step: 1 },
		{ key: 'DIP', label: 'DIP — leg crossing offset, foot', min: -100, max: 100, step: 1 },
		{
			key: 'TAIL',
			label: 'TAIL — how far each leg runs past the other',
			min: 0,
			max: 150,
			step: 1
		},
		{ key: 'STROKE', label: 'STROKE — stroke width', min: 2, max: 80, step: 1 },
		{ key: 'GHOST', label: 'GHOST — chromatic-aberration offset', min: 0, max: 40, step: 1 }
	];

	function resetBrnrd() {
		brnrdConstants = { ...BRNRD_DEFAULTS };
	}
	function resetHugimuni() {
		hugimuniConstants = { ...HUGIMUNI_DEFAULTS };
	}

	// The live render — every slider above feeds this, so any change redraws
	// it instantly. No pre-rendered file anywhere in this route: the SVG
	// markup is built fresh from state on every derive.
	let svgMarkup = $derived(
		mark === 'brnrd'
			? register === 'stone'
				? brnrdStoneSvg(frame, brnrdConstants)
				: brnrdAberrationSvg(frame, brnrdConstants)
			: hugimuniSvg(hugimuniConstants, palette)
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
			// Clipboard permission can be denied in some embeds — the block is
			// already visible in the <pre> below and manually selectable, so
			// this isn't a dead end, just a quieter one.
			copied = false;
		}
	}

	const FACE_NAMES = Object.keys(FACES) as (keyof typeof FACES)[];
</script>

<svelte:head><title>brand-bench (temporary) · brnrd</title></svelte:head>

<div class="mx-auto flex max-w-5xl flex-col gap-4 p-6 font-mono text-sm text-stone-200">
	<header>
		<p class="eyebrow">temporary · unlinked · /brand-bench</p>
		<h1 class="text-lg font-semibold text-amber-100">the mark you can drag</h1>
		<p class="mt-1 text-xs text-ink-quiet">
			Every constant `media/brand/build.py` and `media/brand/hugimuni/build.py` draw from, as a
			slider. Move one, the preview redraws — nothing here shells out to Python or serves a file.
		</p>
	</header>

	<div class="flex flex-wrap items-center gap-4">
		<label class="flex items-center gap-2">
			<span class="text-ink-quiet">mark</span>
			<select class="panel px-2 py-1" bind:value={mark}>
				<option value="brnrd">brnrd</option>
				<option value="hugimuni">hugimuni</option>
			</select>
		</label>

		{#if mark === 'brnrd'}
			<label class="flex items-center gap-2">
				<span class="text-ink-quiet">frame</span>
				<select class="panel px-2 py-1" bind:value={frame}>
					<option value="name">name (bRnЯd, resting)</option>
					{#each FACE_NAMES as name (name)}
						<option value={name}>{name}</option>
					{/each}
				</select>
			</label>
			<label class="flex items-center gap-2">
				<span class="text-ink-quiet">crown</span>
				<select class="panel px-2 py-1" bind:value={brnrdConstants.CROWN}>
					<option value="none">none</option>
					<option value="branch">branch</option>
					<option value="fork">fork</option>
				</select>
			</label>
		{/if}

		<label class="flex items-center gap-2" class:opacity-40={mark === 'hugimuni'}>
			<span class="text-ink-quiet">register</span>
			<select
				class="panel px-2 py-1"
				bind:value={register}
				disabled={mark === 'hugimuni'}
				title={mark === 'hugimuni'
					? 'hugimuni only has one register in build.py — always the chromatic-aberration weave'
					: undefined}
			>
				<option value="stone">stone</option>
				<option value="screen">screen (chromatic aberration)</option>
			</select>
		</label>

		<label class="flex items-center gap-2" class:opacity-40={mark === 'brnrd'}>
			<span class="text-ink-quiet">palette</span>
			<select class="panel px-2 py-1" bind:value={palette} disabled={mark === 'brnrd'}>
				{#each Object.keys(HUGIMUNI_PALETTES) as name (name)}
					<option value={name}>{name}</option>
				{/each}
			</select>
		</label>

		<button
			class="panel panel--pressable px-2 py-1 text-ink-quiet hover:text-amber-100"
			onclick={mark === 'brnrd' ? resetBrnrd : resetHugimuni}
		>
			reset {mark} constants
		</button>
	</div>

	<div class="flex flex-wrap items-start gap-6">
		<div class="panel flex flex-col items-center gap-3 p-4">
			<!-- eslint-disable-next-line svelte/no-at-html-tags -- locally generated SVG, no user input -->
			<div class="mark-frame" style="width: 320px; height: 320px;">{@html svgMarkup}</div>
			<p class="text-[10px] text-ink-mute">320px — the tuning size</p>
		</div>

		<div class="panel flex flex-col gap-3 p-4">
			<p class="text-[10px] text-ink-mute">the sizes that actually decide whether a mark works</p>
			<div class="flex items-end gap-4">
				<div class="flex flex-col items-center gap-1">
					<!-- eslint-disable-next-line svelte/no-at-html-tags -->
					<div class="mark-frame" style="width: 32px; height: 32px;">{@html svgMarkup}</div>
					<p class="text-[9px] text-ink-mute">32px</p>
				</div>
				<div class="flex flex-col items-center gap-1">
					<!-- eslint-disable-next-line svelte/no-at-html-tags -->
					<div class="mark-frame" style="width: 16px; height: 16px;">{@html svgMarkup}</div>
					<p class="text-[9px] text-ink-mute">16px</p>
				</div>
			</div>
		</div>

		<div class="panel flex min-w-[320px] flex-1 flex-col gap-2 p-4">
			<div class="flex items-center justify-between">
				<p class="text-[10px] text-ink-mute">copy constants → paste into {constantBlockTarget}</p>
				<button
					class="panel panel--pressable px-2 py-1 text-amber-100 hover:text-amber-50"
					onclick={copyConstants}
				>
					{copied ? 'copied ✓' : 'copy constants'}
				</button>
			</div>
			<pre
				class="max-h-64 overflow-auto rounded bg-black/40 p-2 text-[11px] leading-relaxed text-stone-300">{constantBlock}</pre>
		</div>
	</div>

	<div class="panel flex flex-col gap-2 p-4">
		<p class="text-[10px] text-ink-mute">
			every constant, live — drag a slider or type in the number beside it, either drives the other
		</p>
		<div class="grid grid-cols-1 gap-x-6 gap-y-2 sm:grid-cols-2">
			{#if mark === 'brnrd'}
				{#each BRNRD_SLIDERS as spec (spec.key)}
					<label class="flex items-center gap-2">
						<span class="w-56 shrink-0 text-[11px] text-ink-quiet">{spec.label}</span>
						<input
							type="range"
							min={spec.min}
							max={spec.max}
							step={spec.step}
							bind:value={brnrdConstants[spec.key as keyof BrnrdConstants]}
							class="flex-1"
						/>
						<input
							type="number"
							min={spec.min}
							max={spec.max}
							step={spec.step}
							bind:value={brnrdConstants[spec.key as keyof BrnrdConstants]}
							class="panel w-20 px-1 py-0.5 text-right"
						/>
					</label>
				{/each}
			{:else}
				{#each HUGIMUNI_SLIDERS as spec (spec.key)}
					<label class="flex items-center gap-2">
						<span class="w-56 shrink-0 text-[11px] text-ink-quiet">{spec.label}</span>
						<input
							type="range"
							min={spec.min}
							max={spec.max}
							step={spec.step}
							bind:value={hugimuniConstants[spec.key as keyof HugimuniConstants]}
							class="flex-1"
						/>
						<input
							type="number"
							min={spec.min}
							max={spec.max}
							step={spec.step}
							bind:value={hugimuniConstants[spec.key as keyof HugimuniConstants]}
							class="panel w-20 px-1 py-0.5 text-right"
						/>
					</label>
				{/each}
			{/if}
		</div>
	</div>

	<p class="text-[10px] text-ink-mute">
		colours are fixed, not sliders here — brnrd: stone {BRNRD_COLORS.STONE}, molten {BRNRD_COLORS.MOLTEN}→{BRNRD_COLORS.EMBER},
		screen ghosts {BRNRD_COLORS.RED}/{BRNRD_COLORS.CYAN} on {BRNRD_COLORS.CREAM}; the task's slider
		list didn't name them, and neither build.py treats them as tuning knobs — they're identity, not
		geometry.
	</p>
</div>

<style>
	/* The generated SVG carries its own width="512" height="512" (straight
	   off the Python's f-string, faithfully) — without this it overflows
	   its wrapping div instead of scaling down, which is exactly the "32px
	   row that isn't actually 32px" failure this bench exists to catch. */
	.mark-frame :global(svg) {
		display: block;
		width: 100%;
		height: 100%;
	}
</style>
