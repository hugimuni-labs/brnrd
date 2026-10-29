<script lang="ts">
	// The dotted lines (board.html's draw, 53vuyk): from the selected letter to each card it links to in another column,
	// redrawn on any scroll or resize. Geometry is the DOM's, so the lines are drawn after the cards: `.sel` and
	// `.linked` come from the Board's selection, and a column off screen or a card under the pinned inbox gets none.
	import { board } from './board.svelte.ts';
	const b = board();
	type Line = { d: string; x1: number; y1: number; x2: number; y2: number; c: string };
	let lines = $state<Line[]>([]);

	const sel = () => document.querySelector<HTMLElement>('.brd article.letter.sel');
	const targets = (s: HTMLElement) =>
		[...document.querySelectorAll<HTMLElement>('.brd .linked')].filter(
			(t) => t.offsetParent && !t.contains(s) && !s.contains(t)
		);
	const pane = (e: Element) => e.closest('.messages,.calls,.band') || e.closest('.column')!;

	let q = 0;
	function draw() {
		if (q) return;
		q = requestAnimationFrame(() => {
			q = 0;
			const s = sel();
			if (!s?.offsetParent) return (lines = []);
			const box = (e: Element) => {
				const r = pane(e).getBoundingClientRect(),
					c = e.closest('.column')!.getBoundingClientRect();
				return { top: r.top, bottom: r.bottom, left: c.left, right: c.right };
			};
			const inbox = document.querySelector('.brd .column.inbox')?.getBoundingClientRect(),
				W = document.querySelector('#board')!.getBoundingClientRect().right,
				clamp = (y: number, x: { top: number; bottom: number }) =>
					Math.max(x.top + 6, Math.min(x.bottom - 6, y)),
				sr = s.getBoundingClientRect(),
				y1 = clamp(sr.top + Math.min(sr.height, 40) / 2 + 8, box(s)),
				col = s.closest('.column'),
				out: Line[] = [];
			for (const t of targets(s)) {
				const r = t.getBoundingClientRect(),
					x = box(t);
				if (x.right < 0 || x.left > W) continue;
				if (!t.closest('.inbox') && inbox && r.right <= inbox.right + 4) continue;
				if (t.closest('.column') === col) continue;
				const right = r.left > sr.left,
					x1 = right ? sr.right : sr.left,
					x2 = right ? r.left : r.right,
					y2 = clamp(r.top + Math.min(r.height, 40) / 2 + 8, x),
					dx = (x2 - x1) / 2;
				out.push({
					d: `M${x1} ${y1}C${x1 + dx} ${y1},${x2 - dx} ${y2},${x2} ${y2}`,
					x1,
					y1,
					x2,
					y2,
					c: getComputedStyle(t).getPropertyValue('--h')
				});
			}
			lines = out;
		});
	}

	// Bring each other pane's first link into view unless one already is: once per selection, and again when the
	// beads arrive and the links become the panel's.
	function bring() {
		const s = sel();
		if (!s) return;
		// eslint-disable-next-line svelte/prefer-svelte-reactivity -- a local, not state
		const by = new Map<Element, HTMLElement[]>();
		for (const t of targets(s)) {
			const p = t.closest('.messages,.calls,.band');
			if (p && !p.closest('.column')?.contains(s)) by.set(p, [...(by.get(p) || []), t]);
		}
		for (const [p, ts] of by) {
			const pr = p.getBoundingClientRect();
			if (
				ts.some((t) => {
					const r = t.getBoundingClientRect();
					return r.bottom > pr.top && r.top < pr.bottom;
				})
			)
				continue;
			const r = ts[0].getBoundingClientRect();
			p.scrollTop += r.top - pr.top - p.clientHeight / 2 + Math.min(r.height, p.clientHeight) / 2;
		}
	}
	let brought = '';
	$effect(() => {
		const key = `${b.sel}|${b.selCol}|${!!b.beads}`;
		void b.linked;
		void b.letters;
		requestAnimationFrame(() => {
			if (key !== brought && b.sel) bring();
			brought = key;
			draw();
		});
	});
	$effect(() => {
		addEventListener('scroll', draw, { capture: true, passive: true });
		addEventListener('resize', draw);
		return () => {
			removeEventListener('scroll', draw, { capture: true });
			removeEventListener('resize', draw);
		};
	});
</script>

<svg id="links"
	>{#each lines as l, i (i)}<path
			d={l.d}
			fill="none"
			stroke={l.c}
			stroke-width="1.6"
			stroke-dasharray="2 4"
			stroke-linecap="round"
		/><circle cx={l.x1} cy={l.y1} r="3" fill={l.c} /><circle
			cx={l.x2}
			cy={l.y2}
			r="3"
			fill={l.c}
		/>{/each}</svg
>
