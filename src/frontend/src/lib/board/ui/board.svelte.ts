// The board's live state: the parts Store, what the person has opened, the bead sheet and the composer. One instance
// per page, handed to every component by context; the logic it calls is the tested modules one level up.
import { getContext, setContext } from 'svelte';
import { SvelteSet } from 'svelte/reactivity';
import { BASE, getJSON, Store, type Beads } from '../parts.ts';
import type { Letter, Model } from '../types.ts';
import { normalize, panelIds, resolveRef, timeline, type Timeline } from '../bead.ts';
import { armedVerdict, correctDraft, verdictLetter } from '../calls.ts';
import { byAt, dated, refTokens } from '../text.ts';
import { unechoed } from '../ledger.ts';
import { forget, remembering, type Held } from '../cache.ts';
import { EMPTY, PLACE, paneScroll, readPlace, type Place } from '../place.ts';

const KEY = Symbol('board');
const local = (k: string, d: string) =>
	typeof localStorage === 'undefined' ? d : (localStorage.getItem(k) ?? d);

export class Board {
	store: Store;
	// raw: a sync replaces the model whole and nothing writes into it; a deep proxy put a trap under every read of
	// every card, and each tick re-ran them all (the live-path bench)
	model = $state.raw<Model | null>(null);
	live = $state<'connecting' | 'live' | 'polled' | 'offline'>('connecting');
	error = $state('');
	// letters sent from here that the ledger has not shown yet
	pending = $state<Letter[]>([]);
	// wishes whose done/agree is in flight: the button shows it at once and takes no second press (10099)
	sending = new SvelteSet<string>();
	showAll = $state(local('board.showInternal', '0') === '1');
	// opened things, by key (board.html's opened, wopen, fopen, epOpen, beatOpen, stopen, copen, dissected)
	open = new SvelteSet<string>();
	letterView = new SvelteSet<string>(JSON.parse(local('board.letterview', '[]')));
	settledOpen = new SvelteSet<string>(JSON.parse(local('board.settledOpen', '[]')));
	grown = new SvelteSet<string>();
	cunfold = $state(false);
	hovered = $state<string | null>(null);
	// the selected letter and the column it was pressed in (the same letter sits in the inbox and its thread)
	sel = $state<string | null>(null);
	selCol = $state<string | null>(null);
	// the bead sheet
	beadId = $state<string | null>(null);
	beadHist = $state<string[]>([]);
	// raw: a deep proxy over 7k beads made every walk a signal read, and each reassignment a new proxy
	beads = $state.raw<Beads | null>(null);
	// the composer
	draft = $state('');
	armed = $state<string | null>(null);
	force = $state(false);
	sendError = $state('');
	// a newer build is out (SvelteKit's version poll); the page takes it unless the box holds a draft in hand
	newer = $state(false);
	writing = $state(false);
	typedBy = '';
	now = $state(Date.now());
	// the loom clock: when the index last came back whole (board.html read the snapshot's `at`; the index part drops
	// `at` so its hash moves only with the board)
	heard = $state('');

	// Where the last page stood (place.ts), read once; the columns take their scroll from it on their first paint.
	place: Place = EMPTY;

	constructor(get: (path: string) => Promise<unknown> = getJSON, held: Held | null = null) {
		this.store = new Store(remembering(get));
		if (typeof window !== 'undefined') {
			this.place = readPlace(localStorage.getItem(PLACE));
			// the last page's parts: the board paints now, and load() syncs it against the index
			if (held) {
				try {
					this.store.prime(held);
					this.model = this.store.model();
					this.beads = this.store.beads;
					this.land();
				} catch {
					// A torn or older cached part must not prevent mounting and the first network sync.
					// Discard the hashes as well, or sync would treat the broken parts as current.
					this.store = new Store(remembering(get));
					this.model = null;
					this.beads = null;
					void forget();
				}
			}
			// eslint-disable-next-line svelte/prefer-svelte-reactivity -- a local, not state
			const q = new URLSearchParams(location.search).get('typed_by');
			if (q) sessionStorage.setItem('board.typedBy', q);
			this.typedBy = sessionStorage.getItem('board.typedBy') || '';
			try {
				const d = JSON.parse(sessionStorage.getItem('board.draft') || 'null');
				if (d?.body) {
					this.draft = d.body;
					this.armed = d.armed || null;
				}
			} catch {
				/* a torn draft is no draft */
			}
		}
	}

	letters = $derived.by(() => {
		const m = this.model;
		if (!m) return [];
		return [...m.letters, ...unechoed(this.pending, m.letters)].filter(dated).sort(byAt);
	});

	async load() {
		try {
			if (await this.store.sync()) {
				this.model = this.store.model();
				this.pending = unechoed(this.pending, this.model!.letters);
				if (this.store.beads) this.beads = this.store.beads;
				else if (this.beads && !this.store.beads) this.beads = await this.store.loadBeads();
			}
			this.land();
			// eslint-disable-next-line svelte/prefer-svelte-reactivity -- a temporary, not state
			this.heard = new Date().toISOString();
			this.error = '';
		} catch (e) {
			this.error = String(e);
		}
	}

	// Scrolled to the top: the 200 letters before the oldest held. One load per thread at a time; true when any came.
	loading = new SvelteSet<string>();
	async older(thread: string): Promise<boolean> {
		if (this.loading.has(thread)) return false;
		this.loading.add(thread);
		try {
			if (!(await this.store.older(thread))) return false;
			this.model = this.store.model();
			return true;
		} catch (e) {
			this.error = String(e);
			return false;
		} finally {
			this.loading.delete(thread);
		}
	}

	// SSE first; a tunnel may hold the stream back, so a silent stream falls back to polling the index.
	connect(): () => void {
		let heard = 0;
		const es = new EventSource(`${BASE}/events`);
		es.addEventListener('index', () => {
			heard = Date.now();
			this.live = 'live';
			this.load();
		});
		es.onerror = () => (this.live = 'offline');
		const poll = setInterval(() => {
			if (Date.now() - heard < 20000 || document.hidden) return;
			this.load().then(() => (this.live = this.error ? 'offline' : 'polled'));
		}, 3000);
		const tick = setInterval(() => (this.now = Date.now()), 1000);
		return () => {
			es.close();
			clearInterval(poll);
			clearInterval(tick);
		};
	}

	// Land where the last page stood, once the model is in: the sheet, the selection, the grown columns and opened
	// rows. The columns' scrolls are theirs (Column's first paint); the board's is Board.svelte's.
	landed = false;
	land() {
		if (this.landed || !this.model) return;
		this.landed = true;
		const p = this.place;
		for (const id of p.grown) this.grown.add(id);
		for (const k of p.open) this.open.add(k);
		if (p.sel) this.select(p.sel, p.selCol);
		if (p.bead) {
			this.beadId = normalize(p.bead, this.model);
			this.beadHist = p.hist;
			if (!this.store.beads) this.store.loadBeads().then((x) => (this.beads ??= x));
			else this.beads = this.store.beads;
		}
	}
	// Written as the page hides and after a scroll or a press, so the next page (this phone drops the tab on every
	// app switch) lands here. Column scrolls are read off the DOM.
	savePlace() {
		if (typeof document === 'undefined' || !this.model) return;
		const y: Place['y'] = {};
		for (const c of document.querySelectorAll<HTMLElement>('.brd .column[data-column]')) {
			const pane = c.querySelector<HTMLElement>('.messages');
			if (pane)
				y[c.dataset.column!] = paneScroll(pane.scrollTop, pane.scrollHeight, pane.clientHeight);
		}
		const place: Place = {
			bead: this.beadId,
			hist: this.beadHist,
			sel: this.sel,
			selCol: this.selCol,
			grown: [...this.grown],
			open: [...this.open],
			x: Math.round(document.getElementById('board')?.scrollLeft ?? 0),
			y
		};
		try {
			localStorage.setItem(PLACE, JSON.stringify(place));
		} catch {
			/* a full storage keeps the last place */
		}
	}

	toggle(set: SvelteSet<string>, k: string) {
		if (set.has(k)) set.delete(k);
		else set.add(k);
	}
	persist() {
		localStorage.setItem('board.letterview', JSON.stringify([...this.letterView]));
		localStorage.setItem('board.settledOpen', JSON.stringify([...this.settledOpen]));
		localStorage.setItem('board.showInternal', this.showAll ? '1' : '0');
	}

	// The bead sheet (4920): a pressed item, the chain up to its source and what came out of it. A press on the
	// item it already shows closes it; ‹ walks back.
	async openBead(id: string, how?: 'back' | 'keep') {
		id = normalize(id, this.model);
		if (!how && this.beadId === id) return this.closeBead();
		if (how !== 'back' && this.beadId && this.beadId !== id)
			this.beadHist = [...this.beadHist, this.beadId];
		this.beadId = id;
		if (this.store.beads) return void (this.beads = this.store.beads);
		const got = await this.store.loadBeads();
		// closed (or moved on) while the part was in flight: the beads are kept, the sheet stays as he left it
		this.beads ??= got;
	}
	back() {
		const h = [...this.beadHist],
			id = h.pop();
		this.beadHist = h;
		if (id) this.openBead(id, 'back');
	}
	closeBead() {
		this.beadId = null;
		this.beadHist = [];
	}
	select(id: string | null, col: string | null = null) {
		this.sel = id;
		this.selCol = id ? col : null;
		if (id && !this.store.beads) this.store.loadBeads().then((x) => (this.beads ??= x));
	}
	// Thread chain roots plus the beads part, so a press draws the chain before the 1 MB part lands.
	merged = $derived.by((): Beads | null => {
		void this.model;
		const chain: Beads = {};
		for (const p of this.store.threads.values()) Object.assign(chain, p.chain || {});
		if (!this.beads && !Object.keys(chain).length) return null;
		return { ...chain, ...(this.beads || {}) };
	});
	// What the sheet (or, with it closed, the selection) links to; the item alone until the beads are in.
	// The same set again keeps its identity, so a sync that moves nothing near the item re-runs no card.
	// eslint-disable-next-line svelte/prefer-svelte-reactivity -- the last answer, not state
	#linked: ReadonlySet<string> = new Set();
	linked = $derived.by<ReadonlySet<string>>(() => {
		const n = panelIds(this.merged || {}, this.beadId || this.sel),
			o = this.#linked;
		if (n.size === o.size && [...n].every((x) => o.has(x))) return o;
		return (this.#linked = n);
	});
	isSel(id: string, col: string) {
		return this.sel === id && this.selCol === col;
	}
	// Show a bead on the board: its letter or wish row scrolled into the middle of its column, or the thread's column.
	locate(id: string) {
		const q = id.startsWith('thread:')
			? `[data-column="${CSS.escape(id.slice(7))}"]`
			: `[data-letter="${CSS.escape(id)}"],[data-wish="${CSS.escape(id)}"]`;
		const el = document.querySelector(q);
		el?.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'smooth' });
		if (el?.matches('[data-letter]'))
			this.select(id, el.closest<HTMLElement>('[data-column]')?.dataset.column ?? null);
	}
	timeline = $derived.by((): Timeline | null =>
		this.beadId && this.merged ? timeline(this.merged, this.model, this.beadId) : null
	);

	// The composer's &refs, checked against the beads as he types; an unknown one is named before it goes.
	refs = $derived.by(() =>
		refTokens(this.draft).map((t) => ({
			t,
			id: this.merged ? resolveRef(this.merged, this.model, t) : null
		}))
	);
	keep() {
		if (this.draft)
			sessionStorage.setItem(
				'board.draft',
				JSON.stringify({ body: this.draft, armed: this.armed })
			);
		else sessionStorage.removeItem('board.draft');
	}
	async post(body: string, extra: { wish?: string; verdict?: string }) {
		const r = await fetch(`${BASE}/letters`, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify({ body, ...extra, typed_by: this.typedBy || undefined })
		});
		const j = await r.json().catch(() => ({ error: `HTTP ${r.status}` }));
		if (!r.ok) throw new Error(j.error || `HTTP ${r.status}`);
		this.pending = [
			...this.pending,
			{
				id: j.id,
				// eslint-disable-next-line svelte/prefer-svelte-reactivity -- a local, not state
				at: new Date().toISOString(),
				from: 'person:' + (this.model?.person || ''),
				to: 'thread:' + j.thread,
				threads: [j.thread],
				person: true,
				body,
				pendingNote: true,
				typed_by: this.typedBy || undefined,
				...extra
			}
		];
		return j;
	}
	async send() {
		const body = this.draft;
		if (!body.trim()) return;
		this.sendError = '';
		if (!this.store.beads) this.beads = await this.store.loadBeads();
		else this.beads = this.store.beads;
		const miss = this.refs.filter((r) => !r.id).map((r) => r.t);
		if (miss.length && !this.force) {
			this.force = true;
			this.sendError = `no ${miss.map((t) => '&' + t).join(', ')} in the ledger: send again to send it unlinked (the loom tells the thread)`;
			return;
		}
		this.force = false;
		try {
			await this.post(body, armedVerdict(this.armed, body));
			this.draft = '';
			this.armed = null;
			this.keep();
		} catch (e) {
			this.sendError = (e as Error).message;
		}
	}
	// A call's buttons: done/agree goes at once, correct arms the composer.
	async answer(wish: string, act: 'done' | 'agree' | 'correct') {
		if (act === 'correct') {
			this.armed = wish;
			this.draft = correctDraft(wish);
			this.keep();
			return;
		}
		if (this.sending.has(wish)) return;
		this.sending.add(wish);
		const { body, ...v } = verdictLetter(act, wish);
		try {
			await this.post(body, v);
		} catch (e) {
			this.sendError = (e as Error).message;
		} finally {
			this.sending.delete(wish);
		}
	}
}

export const provide = (b: Board) => setContext(KEY, b);
export const board = () => getContext<Board>(KEY);
