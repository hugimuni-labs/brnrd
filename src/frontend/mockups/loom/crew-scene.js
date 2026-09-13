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

	const trunk = { x: 640, top: 112, bottom: 616 };
	const fileTarget = { x: trunk.x + 72, y: 246, r: 54 };
	const radial = { x: trunk.x + 4, y: fileTarget.y, r: 82 };
	const grantButton = { x: 906, y: 221, w: 86, h: 32 };
	const pointer = { x: 1080, y: 118, visible: false, manualUntil: 0 };
	const interaction = { radialUntil: 0, splitAt: 0, grantAt: 0, grantPhase: null };
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
		{
			label: 'audit the rail',
			start: { x: trunk.x, y: 320 },
			control: { x: 480, y: 286 },
			end: { x: 292, y: 352 },
			color: colors.amber,
			loot: '#1965'
		},
		{
			label: 'read the seat',
			start: { x: trunk.x, y: 365 },
			control: { x: 782, y: 284 },
			end: { x: 942, y: 330 },
			color: colors.ice
		},
		{
			label: 'drive the mock',
			start: { x: trunk.x, y: 414 },
			control: { x: 790, y: 510 },
			end: { x: 952, y: 538 },
			color: colors.green,
			loot: '#1966'
		}
	];

	const clamp = (value, low = 0, high = 1) => Math.min(high, Math.max(low, value));
	const ease = (value) => {
		const n = clamp(value);
		return n * n * (3 - 2 * n);
	};
	const range = (time, from, to) => ease((time - from) / (to - from));
	const fadeWindow = (time) => 1 - range(time, 18.9, 20);

	function curvePoint(thread, progress) {
		const p = clamp(progress);
		const inverse = 1 - p;
		return {
			x:
				inverse * inverse * thread.start.x +
				2 * inverse * p * thread.control.x +
				p * p * thread.end.x,
			y:
				inverse * inverse * thread.start.y +
				2 * inverse * p * thread.control.y +
				p * p * thread.end.y
		};
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

	function drawBackdrop(phase) {
		ctx.fillStyle = colors.void;
		ctx.fillRect(0, 0, W, H);

		const haze = ctx.createRadialGradient(trunk.x, 320, 20, trunk.x, 320, 420);
		haze.addColorStop(0, 'rgba(217, 164, 65, 0.07)');
		haze.addColorStop(1, 'rgba(12, 9, 6, 0)');
		ctx.fillStyle = haze;
		ctx.fillRect(0, 0, W, H);

		ctx.save();
		ctx.strokeStyle = 'rgba(217, 164, 65, 0.045)';
		ctx.lineWidth = 1;
		for (let y = 29; y < H; y += 29) line({ x: 0, y }, { x: W, y }, ctx.strokeStyle);
		ctx.restore();

		const pulse = 0.56 + 0.22 * Math.sin((phase * Math.PI * 2) / (BEAT / 1000));
		ctx.save();
		ctx.strokeStyle = colors.amber;
		ctx.lineWidth = 2.2;
		ctx.globalAlpha = 0.72;
		ctx.shadowColor = colors.amber;
		ctx.shadowBlur = 9 + pulse * 9;
		ctx.beginPath();
		ctx.moveTo(trunk.x, trunk.top);
		ctx.lineTo(trunk.x, trunk.bottom);
		ctx.stroke();
		ctx.restore();

		text('NOW', 48, 42, 10, colors.mute);
		text('attested trace', 48, 62, 11, colors.amber);
		text('interpreted plaque', W - 48, 62, 11, colors.ice, 'right');
	}

	function drawShuttle(phase) {
		const pulse = 0.72 + 0.28 * Math.sin((phase * Math.PI * 2) / (BEAT / 1000));
		glowText('b·_·d', trunk.x, 92, 21, colors.bright, 'center', pulse);
		text('SHUTTLE', trunk.x, 67, 9, colors.mute, 'center');
	}

	function drawFilePlaque(phase) {
		const arrived = range(phase, 0.9, 1.65);
		const x = trunk.x + 34;
		const y = fileTarget.y;
		line({ x: trunk.x + 4, y }, { x: x - 8, y }, colors.ice, 1, 0.44 * arrived);
		text('│', x, y, 18, colors.ice, 'left', arrived);
		text('daemon.py', x + 17, y, 14, colors.ink, 'left', arrived);
		text('place', x + 17, y + 20, 9, colors.mute, 'left', arrived);
	}

	function drawTrunkBeads(phase) {
		const arrivals = [0.35, 0.95, 1.55, 2.15, 16.65, 17.25, 17.85, 18.45];
		for (const [index, at] of arrivals.entries()) {
			if (phase < at) continue;
			const later = arrivals.slice(index + 1);
			const slot = later.reduce((total, nextAt) => total + range(phase, nextAt, nextAt + 0.24), 0);
			const beadY = 132 + slot * 29;
			const born = range(phase, at, at + 0.18);
			const fallsOffRing = clamp(slot - 3);
			const alpha = (0.94 - Math.min(slot, 3) * 0.18) * born * (1 - fallsOffRing);
			glowText('●', trunk.x, beadY, 11, colors.bright, 'center', alpha);
		}
	}

	function drawRadial(phase) {
		const scripted = phase >= 2.18 && phase < 3.03;
		const manual = performance.now() < interaction.radialUntil;
		if (!scripted && !manual) return;
		const intro = scripted ? range(phase, 2.18, 2.36) : 1;
		const outro = scripted ? 1 - range(phase, 2.86, 3.03) : 1;
		const alpha = Math.min(intro, outro);
		ctx.save();
		ctx.globalAlpha = alpha;
		ctx.strokeStyle = 'rgba(198, 224, 235, 0.48)';
		ctx.lineWidth = 1;
		ctx.beginPath();
		ctx.arc(radial.x, radial.y, radial.r, -0.73, 0.73);
		ctx.stroke();
		ctx.restore();
		text('fold', radial.x + 63, radial.y - 50, 12, colors.quiet, 'center', alpha);
		text('split', radial.x + 86, radial.y, 13, colors.bright, 'center', alpha);
		text('test', radial.x + 63, radial.y + 50, 12, colors.quiet, 'center', alpha);
	}

	function drawCurve(thread, progress, alpha) {
		ctx.save();
		ctx.globalAlpha = alpha;
		ctx.strokeStyle = thread.color;
		ctx.lineWidth = 1.35;
		ctx.shadowColor = thread.color;
		ctx.shadowBlur = 5;
		ctx.beginPath();
		ctx.moveTo(thread.start.x, thread.start.y);
		for (let step = 1; step <= 48; step += 1) {
			const p = (progress * step) / 48;
			const point = curvePoint(thread, p);
			ctx.lineTo(point.x, point.y);
		}
		ctx.stroke();
		ctx.restore();
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

	function drawThread(thread, index, progress, fuel, alpha, bloom = 0) {
		const point = curvePoint(thread, progress);
		const side = index === 0 ? -1 : 1;
		const labelX = point.x + side * 18;
		const align = side < 0 ? 'right' : 'left';
		glowText('▷', point.x, point.y, 16, thread.color, 'center', alpha);
		text(thread.label, labelX, point.y - 18, 12, colors.ink, align, alpha);
		text(`${Math.round(fuel * 150)}k / 150k`, labelX, point.y + 1, 9, colors.quiet, align, alpha);
		const barX = side < 0 ? point.x - 168 : point.x + 18;
		drawFuel(barX, point.y + 15, fuel, thread.color, alpha, bloom);
	}

	function drawBranchBeads(thread, progress, phase, alpha) {
		const count = Math.min(4, Math.max(0, Math.floor((phase - 3.15) / 0.72)));
		for (let index = 0; index < count; index += 1) {
			const beadProgress = clamp(progress - 0.08 - index * 0.13);
			if (beadProgress <= 0) continue;
			const point = curvePoint(thread, beadProgress);
			text('●', point.x, point.y, 8, thread.color, 'center', alpha * (1 - index * 0.17));
		}
	}

	function drawAsk(phase, alpha, grantedAt) {
		const enter = range(phase, 8, 8.35);
		const leave = 1 - range(phase, grantedAt + 0.12, grantedAt + 0.48);
		const localAlpha = alpha * Math.min(enter, leave);
		if (localAlpha <= 0) return;
		const x = 830;
		const y = 164 - 8 * enter;
		ctx.save();
		ctx.globalAlpha = localAlpha;
		ctx.fillStyle = 'rgba(12, 9, 6, 0.94)';
		ctx.strokeStyle = 'rgba(198, 224, 235, 0.55)';
		ctx.fillRect(x, y, 184, 104);
		ctx.strokeRect(x, y, 184, 104);
		ctx.beginPath();
		ctx.moveTo(916, y + 104);
		ctx.lineTo(938, y + 124);
		ctx.lineTo(952, y + 104);
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
			[interaction.splitAt, radial.x + 86, radial.y, colors.bright],
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
				const radius = sparkRadii[index] * progress;
				text(
					'·',
					x + Math.cos(sparkAngles[index]) * radius,
					y + Math.sin(sparkAngles[index]) * radius,
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
		const y = thread.end.y - 32 + fall * 32 - Math.sin(fall * Math.PI) * 11;
		glowText('✣ PR', thread.end.x, y, 19, colors.bright, 'center', alpha);
		text(thread.loot, thread.end.x, y + 25, 9, colors.quiet, 'center', alpha * settle);
		drawBloom(thread.end.x, thread.end.y, phase, at + 0.28, colors.bright);
	}

	function drawMergeRing(phase, y, at, alpha) {
		const progress = range(phase, at, at + 0.55);
		if (progress <= 0) return;
		ctx.save();
		ctx.globalAlpha = alpha * progress;
		ctx.strokeStyle = colors.bright;
		ctx.lineWidth = 2;
		ctx.shadowColor = colors.amber;
		ctx.shadowBlur = 10;
		ctx.beginPath();
		ctx.ellipse(trunk.x, y, 13 + progress * 5, 5 + progress * 2, 0, 0, Math.PI * 2);
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

		const progresses = [p0, p1, p2];
		threads.forEach((thread, index) => {
			drawCurve(thread, index === 1 && returnProgress > 0 ? 1 : progresses[index], alpha);
			drawBranchBeads(thread, index === 1 ? p1Out : progresses[index], phase, alpha);
		});

		drawThread(threads[0], 0, p0, fuels[0], alpha);
		drawThread(
			threads[1],
			1,
			p1,
			fuels[1],
			alpha,
			range(phase, grantedAt, grantedAt + 0.72) *
				(1 - range(phase, grantedAt + 0.72, grantedAt + 1.42))
		);
		drawThread(threads[2], 2, p2, fuels[2], alpha);

		drawAsk(phase, alpha, grantedAt);
		drawBloom(threads[1].end.x, threads[1].end.y + 15, phase, grantedAt, colors.ice);
		drawLoot(threads[0], phase, 11.15, alpha);
		drawLoot(threads[2], phase, 14.1, alpha);
		drawMergeRing(phase, 490, 12.0, alpha);
		drawMergeRing(phase, 542, 15.3, alpha);
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
		if (phase >= 1.78 && phase < 3.18) {
			visible = true;
			const firstMove = range(phase, 1.78, 2.18);
			x = 1080 + (fileTarget.x - 1080) * firstMove;
			y = 118 + (fileTarget.y - 118) * firstMove;
			const secondMove = range(phase, 2.38, 2.76);
			x += (radial.x + 86 - fileTarget.x) * secondMove;
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
		const clickOne = 1 - range(Math.abs(phase - 2.2), 0, 0.22);
		const clickTwo = 1 - range(Math.abs(phase - 2.8), 0, 0.22);
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
		drawBackdrop(phase);
		drawShuttle(phase);
		drawTrunkBeads(phase);
		drawFilePlaque(phase);
		drawRadial(phase);
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
		if ('r' in target) return Math.hypot(point.x - target.x, point.y - target.y) <= target.r;
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
			interaction.radialUntil = now + 1_800;
			return;
		}
		const splitTarget = { x: radial.x + 86, y: radial.y, r: 34 };
		if (now < interaction.radialUntil && within(point, splitTarget)) {
			interaction.splitAt = now;
			interaction.radialUntil = 0;
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
		targets: { file: fileTarget, split: { x: radial.x + 86, y: radial.y }, grant: grantButton }
	};

	requestAnimationFrame(draw);
})();
