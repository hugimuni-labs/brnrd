(() => {
	const canvas = document.querySelector('#crew-scene');
	const ctx = canvas.getContext('2d');
	const W = canvas.width;
	const H = canvas.height;
	const BEAT = 600;
	const CYCLE = 20_000;
	const SCRIPTED_GRANT_AT = 8.88;
	const SEED = 260913;
	const START = performance.now();
	const mono = 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace';
	const colors = {
		void: '#0c0906',
		ink: '#f3e8d8',
		quiet: '#a8a29e',
		mute: '#736d65',
		amber: '#d9a441',
		bright: '#f0c85c',
		ice: '#c6e0eb',
		green: '#8acb78'
	};

	// The tree is the between — it does not scroll, it is the map: a grid,
	// column i at ROOT_X + i*CHAR_W, row r at ROW_Y0 + r*ROW_H. Glyphs are
	// placed one character at a time (never a whole-line fillText) so a
	// font with different advance widths for '│├─' than for letters still
	// lands every column on the grid.
	const FONT_SIZE = 17;
	const ROOT_X = 92;
	const ROW_Y0 = 150;
	const ROW_H = 56;

	ctx.font = `${FONT_SIZE}px ${mono}`;
	const CHAR_W = ctx.measureText('0').width;
	const GUTTER_X = ROOT_X - CHAR_W * 2.5;

	function flatten(node, ancestorIsLast, isLast, isRoot) {
		const stem = ancestorIsLast.map((last) => (last ? '    ' : '│   ')).join('');
		const prefix = isRoot ? '' : stem + (isLast ? '└── ' : '├── ');
		const row = { name: node.name, prefix, text: prefix + node.name, role: node.role ?? 'dir' };
		if ('thread' in node) row.thread = node.thread;
		const children = node.children ?? [];
		const childAncestors = isRoot ? [] : [...ancestorIsLast, isLast];
		return children.reduce(
			(rows, child, index) =>
				rows.concat(flatten(child, childAncestors, index === children.length - 1, false)),
			[row]
		);
	}

	const treeSpec = {
		name: 'brnrd/',
		children: [
			{
				name: 'src/',
				children: [
					{ name: 'brr/', children: [{ name: 'daemon.py', role: 'file' }] },
					{
						name: 'frontend/repro/',
						children: [
							{ name: 'audit-the-rail.mjs', role: 'thread', thread: 0 },
							{ name: 'read-the-seat.mjs', role: 'thread', thread: 1 },
							{ name: 'drive-the-mock.mjs', role: 'thread', thread: 2 }
						]
					}
				]
			},
			{ name: 'tests/', role: 'tests' }
		]
	};

	const rows = flatten(treeSpec, [], true, true).map((row, index) => {
		const y = ROW_Y0 + index * ROW_H;
		const leafEndX = ROOT_X + row.text.length * CHAR_W;
		const connectorX = row.prefix.length
			? ROOT_X + (row.prefix.length - 4 + 1.5) * CHAR_W
			: ROOT_X + CHAR_W * 1.5;
		return { ...row, index, y, leafEndX, connectorX };
	});
	const daemonRow = rows.find((row) => row.role === 'file');
	const threadRows = rows
		.filter((row) => row.role === 'thread')
		.sort((a, b) => a.thread - b.thread);

	const fileTarget = {
		x: ROOT_X - 6,
		y: daemonRow.y - 15,
		w: daemonRow.leafEndX - ROOT_X + 96,
		h: 30
	};
	const menu = {
		x: daemonRow.leafEndX + 36,
		fold: daemonRow.y - 22,
		split: daemonRow.y,
		test: daemonRow.y + 22
	};
	const splitTarget = { x: menu.x - 6, y: menu.split - 12, w: 74, h: 24 };
	const askRow = threadRows[1];
	const askBox = { x: askRow.leafEndX + 230, y: askRow.y - 70, w: 184, h: 104 };
	const grantButton = { x: askBox.x + 76, y: askBox.y + 61, w: 86, h: 32 };
	const pointer = { x: 1080, y: 118, visible: false, manualUntil: 0 };
	const interaction = { menuUntil: 0, splitAt: 0, grantAt: 0, grantPhase: null };
	let previousCycle = -1;

	function rng(seed) {
		let state = seed >>> 0;
		return () => {
			state += 0x6d2b79f5;
			let value = state;
			value = Math.imul(value ^ (value >>> 15), value | 1);
			value ^= value + Math.imul(value ^ (value >>> 7), value | 61);
			return ((value ^ (value >>> 14)) >>> 0) / 4294967296;
		};
	}

	const random = rng(SEED);
	const sparkAngles = Array.from({ length: 20 }, () => random() * Math.PI * 2);
	const sparkRadii = Array.from({ length: 20 }, () => 10 + random() * 35);

	const threads = [
		{ row: threadRows[0], color: colors.amber, loot: '#1965' },
		{ row: threadRows[1], color: colors.ice },
		{ row: threadRows[2], color: colors.green, loot: '#1966' }
	];

	const clamp = (value, low = 0, high = 1) => Math.min(high, Math.max(low, value));
	const ease = (value) => {
		const n = clamp(value);
		return n * n * (3 - 2 * n);
	};
	const range = (time, from, to) => ease((time - from) / (to - from));
	const fadeWindow = (time) => 1 - range(time, 18.9, 20);

	// The shuttle's own beat: it steps line to line, one row per tool
	// boundary, then parks at the dispatch hub while the crew works and
	// again at tests/ once the results are in.
	const SHUTTLE_STOPS = [
		{ from: 0, to: 0.6, row: 0 },
		{ from: 0.6, to: 1.2, row: 1 },
		{ from: 1.2, to: 1.8, row: 2 },
		{ from: 1.8, to: 3.03, row: 3 },
		{ from: 3.03, to: 15.25, row: 4 },
		{ from: 15.25, to: 18.9, row: 8 }
	];
	function shuttleStop(phase) {
		for (const stop of SHUTTLE_STOPS) if (phase >= stop.from && phase < stop.to) return stop;
		return SHUTTLE_STOPS[0];
	}

	function line(a, b, color, width = 1, alpha = 1) {
		ctx.save();
		ctx.globalAlpha = alpha;
		ctx.strokeStyle = color;
		ctx.lineWidth = width;
		ctx.beginPath();
		ctx.moveTo(a.x, a.y);
		ctx.lineTo(b.x, b.y);
		ctx.stroke();
		ctx.restore();
	}

	function text(value, x, y, size = 14, color = colors.ink, align = 'left', alpha = 1) {
		ctx.save();
		ctx.globalAlpha = alpha;
		ctx.fillStyle = color;
		ctx.font = `${size}px ${mono}`;
		ctx.textAlign = align;
		ctx.textBaseline = 'middle';
		ctx.fillText(value, x, y);
		ctx.restore();
	}

	function glowText(value, x, y, size, color = colors.bright, align = 'center', alpha = 1) {
		ctx.save();
		ctx.globalAlpha = alpha;
		ctx.fillStyle = color;
		ctx.shadowColor = color;
		ctx.shadowBlur = 15;
		ctx.font = `700 ${size}px ${mono}`;
		ctx.textAlign = align;
		ctx.textBaseline = 'middle';
		ctx.fillText(value, x, y);
		ctx.restore();
	}

	// One grid cell. Every tree glyph — static or lit — goes through this,
	// so a base character and its overlay always share the exact same x.
	function glyph(ch, col, y, color, alpha = 1, glow = 0) {
		if (ch === ' ') return;
		ctx.save();
		ctx.globalAlpha = alpha;
		ctx.fillStyle = color;
		if (glow) {
			ctx.shadowColor = color;
			ctx.shadowBlur = glow;
		}
		ctx.font = `${FONT_SIZE}px ${mono}`;
		ctx.textAlign = 'left';
		ctx.textBaseline = 'middle';
		ctx.fillText(ch, ROOT_X + col * CHAR_W, y);
		ctx.restore();
	}

	function drawBackdrop() {
		ctx.fillStyle = colors.void;
		ctx.fillRect(0, 0, W, H);

		const haze = ctx.createRadialGradient(ROOT_X, 320, 20, ROOT_X, 320, 520);
		haze.addColorStop(0, 'rgba(217, 164, 65, 0.06)');
		haze.addColorStop(1, 'rgba(12, 9, 6, 0)');
		ctx.fillStyle = haze;
		ctx.fillRect(0, 0, W, H);

		ctx.save();
		ctx.strokeStyle = 'rgba(217, 164, 65, 0.045)';
		ctx.lineWidth = 1;
		for (let y = 29; y < H; y += 29) line({ x: 0, y }, { x: W, y }, ctx.strokeStyle);
		ctx.restore();

		text('NOW', 48, 42, 10, colors.mute);
		text('attested trace', 48, 62, 11, colors.amber);
		text('interpreted plaque', W - 48, 62, 11, colors.ice, 'right');
	}

	// Always fully lit — the tree does not fade, only what moves through it
	// does. Structure glyphs dim, names bright.
	function drawTreeMap() {
		for (const row of rows) {
			for (let i = 0; i < row.text.length; i += 1) {
				const dim = i < row.prefix.length;
				glyph(row.text[i], i, row.y, dim ? colors.mute : colors.ink);
			}
		}
		const annotation = daemonRow;
		text('← place', annotation.leafEndX + 16, annotation.y, 11, colors.mute, 'left', 0.85);
	}

	// A thread's progress: a lit fill along its own row, grown from the
	// leaf (its file, right) toward the trunk (its connector, left).
	function drawLitFill(row, pathProgress, color) {
		const litChars = Math.round(clamp(pathProgress) * row.text.length);
		if (litChars <= 0) return;
		const start = row.text.length - litChars;
		for (let i = start; i < row.text.length; i += 1) glyph(row.text[i], i, row.y, color, 1, 5);
	}

	function drawShuttleCursor(phase) {
		const stop = shuttleStop(phase);
		const justArrived = range(phase, stop.from, stop.from + 0.22);
		const pulse = 0.72 + 0.28 * Math.sin((phase * Math.PI * 2) / (BEAT / 1000));
		const alpha = Math.min(1, justArrived + 0.4) * pulse;
		glowText('▸', GUTTER_X, rows[stop.row].y, 18, colors.bright, 'left', alpha);
	}

	function drawActionMenu(phase) {
		const scripted = phase >= 1.8 && phase < 3.03;
		const manual = performance.now() < interaction.menuUntil;
		if (!scripted && !manual) return;
		const intro = scripted ? range(phase, 1.8, 2.0) : 1;
		const outro = scripted ? 1 - range(phase, 2.86, 3.03) : 1;
		const alpha = Math.min(intro, outro);
		text('fold', menu.x, menu.fold, 12, colors.quiet, 'left', alpha);
		text('split', menu.x, menu.split, 13, colors.bright, 'left', alpha);
		text('test', menu.x, menu.test, 12, colors.quiet, 'left', alpha);
	}

	function drawFuel(x, y, value, color, alpha, bloom = 0) {
		ctx.save();
		ctx.globalAlpha = alpha;
		ctx.strokeStyle = 'rgba(198, 224, 235, 0.24)';
		ctx.strokeRect(x, y, 150, 5);
		ctx.fillStyle = color;
		ctx.shadowColor = color;
		ctx.shadowBlur = bloom * 24;
		ctx.fillRect(x + 1, y + 1, 148 * clamp(value), 3);
		ctx.restore();
	}

	function drawThreadAnnotations(thread, markerProgress, fuel, alpha, bloom = 0) {
		const row = thread.row;
		const barX = row.leafEndX + 55;
		text(`${Math.round(fuel * 150)}k / 150k`, barX, row.y - 14, 9, colors.quiet, 'left', alpha);
		drawFuel(barX, row.y + 2, fuel, thread.color, alpha, bloom);
		const p = clamp(markerProgress);
		if (p > 0 && p < 1) {
			const boundaryCol = row.text.length * (1 - p);
			glowText('▷', ROOT_X + boundaryCol * CHAR_W, row.y, 13, thread.color, 'center', alpha * 0.9);
		}
	}

	function drawLeafBeads(row, color, phase, alpha) {
		const count = Math.min(4, Math.max(0, Math.floor((phase - 3.15) / 0.72)));
		for (let index = 0; index < count; index += 1) {
			const at = 3.15 + index * 0.72;
			const born = range(phase, at, at + 0.22);
			glyph(
				'●',
				row.text.length + 1 + index * 0.9,
				row.y - 15,
				color,
				alpha * born * (1 - index * 0.17)
			);
		}
	}

	function drawAsk(phase, alpha, grantedAt) {
		const enter = range(phase, 8, 8.35);
		const leave = 1 - range(phase, grantedAt + 0.12, grantedAt + 0.48);
		const localAlpha = alpha * Math.min(enter, leave);
		if (localAlpha <= 0) return;
		const x = askBox.x;
		const y = askBox.y - 8 * enter;
		ctx.save();
		ctx.globalAlpha = localAlpha;
		ctx.fillStyle = 'rgba(12, 9, 6, 0.94)';
		ctx.strokeStyle = 'rgba(198, 224, 235, 0.55)';
		ctx.fillRect(x, y, askBox.w, askBox.h);
		ctx.strokeRect(x, y, askBox.w, askBox.h);
		ctx.beginPath();
		ctx.moveTo(x + 86, y + askBox.h);
		ctx.lineTo(x + 108, y + askBox.h + 20);
		ctx.lineTo(x + 122, y + askBox.h);
		ctx.stroke();
		ctx.restore();
		text('RADIO / read the seat', x + 14, y + 18, 9, colors.mute, 'left', localAlpha);
		text('ask +150k', x + 14, y + 46, 14, colors.ice, 'left', localAlpha);
		ctx.save();
		ctx.globalAlpha = localAlpha;
		ctx.strokeStyle = colors.amber;
		ctx.fillStyle = 'rgba(217, 164, 65, 0.09)';
		ctx.fillRect(grantButton.x, grantButton.y, grantButton.w, grantButton.h);
		ctx.strokeRect(grantButton.x, grantButton.y, grantButton.w, grantButton.h);
		ctx.restore();
		text(
			'grant',
			grantButton.x + grantButton.w / 2,
			grantButton.y + 16,
			12,
			colors.bright,
			'center',
			localAlpha
		);
	}

	function drawBloom(x, y, phase, at, color) {
		const progress = range(phase, at, at + 0.72);
		if (progress <= 0 || progress >= 1) return;
		for (let index = 0; index < sparkAngles.length; index += 1) {
			const radius = sparkRadii[index] * progress;
			const px = x + Math.cos(sparkAngles[index]) * radius;
			const py = y + Math.sin(sparkAngles[index]) * radius;
			text('·', px, py, 14, color, 'center', 1 - progress);
		}
	}

	function drawManualBlooms(now) {
		for (const [at, x, y, color] of [
			[interaction.splitAt, menu.x + 20, menu.split, colors.bright],
			[
				interaction.grantAt,
				grantButton.x + grantButton.w / 2,
				grantButton.y + grantButton.h / 2,
				colors.ice
			]
		]) {
			if (!at) continue;
			const progress = ease((now - at) / BEAT);
			if (progress <= 0 || progress >= 1) continue;
			for (let index = 0; index < sparkAngles.length; index += 1) {
				text(
					'·',
					x + Math.cos(sparkAngles[index]) * sparkRadii[index] * progress,
					y + Math.sin(sparkAngles[index]) * sparkRadii[index] * progress,
					14,
					color,
					'center',
					1 - progress
				);
			}
		}
	}

	function drawLoot(thread, phase, at, alpha) {
		const fall = range(phase, at, at + 0.52);
		const settle = range(phase, at + 0.52, at + 0.8);
		if (fall <= 0) return;
		const row = thread.row;
		const x = row.leafEndX + 210;
		const y = row.y - 32 + fall * 32 - Math.sin(fall * Math.PI) * 11;
		glowText('✣ PR', x, y, 19, colors.bright, 'center', alpha);
		text(thread.loot, x, y + 25, 9, colors.quiet, 'center', alpha * settle);
		drawBloom(x, row.y, phase, at + 0.28, colors.bright);
	}

	function drawMergeRing(thread, phase, at, alpha) {
		const progress = range(phase, at, at + 0.55);
		if (progress <= 0) return;
		const row = thread.row;
		ctx.save();
		ctx.globalAlpha = alpha * progress;
		ctx.strokeStyle = colors.bright;
		ctx.lineWidth = 2;
		ctx.shadowColor = colors.amber;
		ctx.shadowBlur = 10;
		ctx.beginPath();
		ctx.ellipse(row.connectorX, row.y, 13 + progress * 5, 8 + progress * 2, 0, 0, Math.PI * 2);
		ctx.stroke();
		ctx.restore();
	}

	function drawCrew(phase) {
		if (phase < 3) return;
		const alpha = fadeWindow(phase);
		const outward = range(phase, 3, 4.3);
		const p0 = Math.min(1, outward * 0.33 + range(phase, 4.3, 11.1) * 0.67);
		const p1Out = Math.min(1, outward * 0.38 + range(phase, 4.3, 8) * 0.62);
		const returnProgress = range(phase, 9.45, 15.25);
		const p1 = returnProgress > 0 ? 1 - returnProgress : p1Out;
		const p2 = Math.min(1, outward * 0.3 + range(phase, 4.3, 14.05) * 0.7);

		const fuels = [
			1 - range(phase, 3, 10.9) * 0.92,
			1 - range(phase, 3, 8),
			1 - range(phase, 3, 13.9) * 0.94
		];
		const grantedAt = interaction.grantPhase ?? SCRIPTED_GRANT_AT;
		const granted = phase >= grantedAt;
		if (granted)
			fuels[1] =
				0.12 +
				range(phase, grantedAt, grantedAt + 0.66) * 0.88 -
				range(phase, grantedAt + 0.66, 15.25) * 0.36;

		const pathProgress = [p0, returnProgress > 0 ? 1 : p1Out, p2];
		const markerProgress = [p0, p1, p2];
		const grantBloom =
			range(phase, grantedAt, grantedAt + 0.72) *
			(1 - range(phase, grantedAt + 0.72, grantedAt + 1.42));

		threads.forEach((thread, index) => {
			drawLitFill(thread.row, pathProgress[index], thread.color);
			drawLeafBeads(thread.row, thread.color, phase, alpha);
			drawThreadAnnotations(
				thread,
				markerProgress[index],
				fuels[index],
				alpha,
				index === 1 ? grantBloom : 0
			);
		});

		drawAsk(phase, alpha, grantedAt);
		const grantX = grantButton.x + grantButton.w / 2;
		const grantY = grantButton.y + grantButton.h / 2;
		drawBloom(grantX, grantY, phase, grantedAt, colors.ice);
		drawLoot(threads[0], phase, 11.15, alpha);
		drawLoot(threads[2], phase, 14.1, alpha);
		drawMergeRing(threads[0], phase, 12.0, alpha);
		drawMergeRing(threads[2], phase, 15.3, alpha);
	}

	function drawCloth(phase) {
		const alpha = fadeWindow(phase);
		const top = 632;
		line({ x: 70, y: top }, { x: W - 70, y: top }, colors.amber, 1, 0.52);
		text('CLOTH', 72, top - 14, 9, colors.mute);
		if (phase >= 12) {
			const first = range(phase, 12, 12.55) * alpha;
			text('✣', 102, top + 22, 13, colors.bright, 'left', first);
			text('#1965 merged', 124, top + 22, 11, colors.ink, 'left', first);
		}
		if (phase >= 15.2) {
			const second = range(phase, 15.2, 15.75) * alpha;
			text('✣', 310, top + 22, 13, colors.bright, 'left', second);
			text('#1966 merged', 332, top + 22, 11, colors.ink, 'left', second);
		}
		if (phase >= 16) {
			text(
				'crew converged · trunk +2',
				W - 78,
				top + 22,
				10,
				colors.quiet,
				'right',
				range(phase, 16, 16.5) * alpha
			);
		}
	}

	function scriptedPointer(phase) {
		if (performance.now() < pointer.manualUntil) return pointer;
		let x = 1080;
		let y = 118;
		let visible = false;
		if (phase >= 1.5 && phase < 3.18) {
			visible = true;
			const firstMove = range(phase, 1.5, 1.9);
			x = 1080 + (fileTarget.x + fileTarget.w / 2 - 1080) * firstMove;
			y = 118 + (fileTarget.y + fileTarget.h / 2 - 118) * firstMove;
			const secondMove = range(phase, 2.1, 2.5);
			x += (menu.x + 20 - x) * secondMove;
			y += (menu.split - y) * secondMove;
		}
		if (phase >= 7.62 && phase < 9.72) {
			visible = true;
			const move = range(phase, 7.62, 8.72);
			x = 760 + (grantButton.x + grantButton.w / 2 - 760) * move;
			y = 344 + (grantButton.y + grantButton.h / 2 - 344) * move;
		}
		return { x, y, visible };
	}

	function drawPointer(phase) {
		const point = scriptedPointer(phase);
		if (!point.visible) return;
		const clickOne = 1 - range(Math.abs(phase - 1.9), 0, 0.22);
		const clickTwo = 1 - range(Math.abs(phase - 2.5), 0, 0.22);
		const clickGrant = 1 - range(Math.abs(phase - 8.88), 0, 0.24);
		const click = Math.max(clickOne, clickTwo, clickGrant);
		if (click > 0) {
			ctx.save();
			ctx.globalAlpha = click * 0.68;
			ctx.strokeStyle = colors.ice;
			ctx.beginPath();
			ctx.arc(point.x, point.y, 10 + (1 - click) * 12, 0, Math.PI * 2);
			ctx.stroke();
			ctx.restore();
		}
		ctx.save();
		ctx.translate(point.x, point.y);
		ctx.fillStyle = colors.ink;
		ctx.strokeStyle = colors.void;
		ctx.lineWidth = 2;
		ctx.beginPath();
		ctx.moveTo(0, 0);
		ctx.lineTo(5, 20);
		ctx.lineTo(10, 13);
		ctx.lineTo(17, 12);
		ctx.closePath();
		ctx.stroke();
		ctx.fill();
		ctx.restore();
	}

	function draw(now) {
		const elapsed = now - START;
		const cycle = Math.floor(elapsed / CYCLE);
		if (cycle !== previousCycle) {
			interaction.grantPhase = null;
			previousCycle = cycle;
		}
		const phase = (elapsed % CYCLE) / 1000;
		drawBackdrop();
		drawTreeMap();
		drawShuttleCursor(phase);
		drawActionMenu(phase);
		drawCrew(phase);
		drawCloth(phase);
		drawManualBlooms(now);
		drawPointer(phase);
		requestAnimationFrame(draw);
	}

	function canvasPoint(event) {
		const bounds = canvas.getBoundingClientRect();
		return {
			x: ((event.clientX - bounds.left) / bounds.width) * W,
			y: ((event.clientY - bounds.top) / bounds.height) * H
		};
	}

	function within(point, target) {
		return (
			point.x >= target.x &&
			point.x <= target.x + target.w &&
			point.y >= target.y &&
			point.y <= target.y + target.h
		);
	}

	canvas.addEventListener('pointermove', (event) => {
		const point = canvasPoint(event);
		pointer.x = point.x;
		pointer.y = point.y;
		pointer.visible = true;
		pointer.manualUntil = performance.now() + 1_200;
	});

	canvas.addEventListener('pointerleave', () => {
		pointer.visible = false;
		pointer.manualUntil = 0;
	});

	canvas.addEventListener('pointerdown', (event) => {
		const point = canvasPoint(event);
		const now = performance.now();
		const phase = ((now - START) % CYCLE) / 1000;
		if (within(point, fileTarget)) {
			interaction.menuUntil = now + 1_800;
			return;
		}
		if (now < interaction.menuUntil && within(point, splitTarget)) {
			interaction.splitAt = now;
			interaction.menuUntil = 0;
			return;
		}
		if (phase >= 8 && phase < 9.7 && within(point, grantButton)) {
			interaction.grantAt = now;
			interaction.grantPhase = phase;
		}
	});

	window.crewScene = {
		beat: BEAT,
		cycle: CYCLE,
		seed: SEED,
		targets: { file: fileTarget, split: splitTarget, grant: grantButton }
	};

	requestAnimationFrame(draw);
})();
