// The board's live state: the parts Store, what the person has opened, the bead sheet and the composer. One instance
// per page, handed to every component by context; the logic it calls is the tested modules one level up.
import { getContext, setContext } from 'svelte';
import { SvelteSet } from 'svelte/reactivity';
import { BASE, Store, type Beads } from '../parts.ts';
import type { Letter, Model } from '../types.ts';
import { normalize, resolveRef, timeline, type Timeline } from '../bead.ts';
import { armedVerdict, correctDraft, verdictLetter } from '../calls.ts';
import { refTokens } from '../text.ts';

const KEY = Symbol('board');
const local = (k: string, d: string) => (typeof localStorage === 'undefined' ? d : localStorage.getItem(k) ?? d);

export class Board {
	store: Store;
	model = $state<Model | null>(null);
	live = $state<'connecting' | 'live' | 'polled' | 'offline'>('connecting');
	error = $state('');
	// letters sent from here that the ledger has not shown yet
	pending = $state<Letter[]>([]);
	showAll = $state(local('board.showInternal', '0') === '1');
	// opened things, by key (board.html's opened, wopen, fopen, epOpen, beatOpen, stopen, copen, dissected)
	open = new SvelteSet<string>();
	letterView = new SvelteSet<string>(JSON.parse(local('board.letterview', '[]')));
	settledOpen = new SvelteSet<string>(JSON.parse(local('board.settledOpen', '[]')));
	grown = new SvelteSet<string>();
	cunfold = $state(false);
	hovered = $state<string | null>(null);
	sel = $state<string | null>(null);
	// the bead sheet
	beadId = $state<string | null>(null);
	beadHist = $state<string[]>([]);
	beads = $state<Beads | null>(null);
	// the composer
	draft = $state('');
	armed = $state<string | null>(null);
	force = $state(false);
	sendError = $state('');
	typedBy = '';
	now = $state(Date.now());

	constructor(get: (path: string) => Promise<unknown> = getJSON) {
		this.store = new Store(get);
		if (typeof window !== 'undefined') {
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
		const have = new Set(m.letters.map((l) => l.id));
		return [...m.letters, ...this.pending.filter((p) => !have.has(p.id))].sort(
			(a, b) => a.at.localeCompare(b.at) || a.id.localeCompare(b.id)
		);
	});

	async load() {
		try {
			if (await this.store.sync()) {
				this.model = this.store.model();
				const have = new Set(this.model!.letters.map((l) => l.id));
				if (this.pending.some((p) => have.has(p.id)))
					this.pending = this.pending.filter((p) => !have.has(p.id));
				if (this.beads && !this.store.beads) this.beads = await this.store.loadBeads();
			}
			this.error = '';
		} catch (e) {
			this.error = String(e);
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
		if (how !== 'back' && this.beadId && this.beadId !== id) this.beadHist = [...this.beadHist, this.beadId];
		this.beadId = id;
		if (!this.beads) this.beads = await this.store.loadBeads();
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
	// Show a bead on the board: its letter or wish row scrolled into the middle of its column, or the thread's column.
	locate(id: string) {
		const q = id.startsWith('thread:')
			? `[data-column="${CSS.escape(id.slice(7))}"]`
			: `[data-letter="${CSS.escape(id)}"],[data-wish="${CSS.escape(id)}"]`;
		const el = document.querySelector(q);
		el?.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'smooth' });
		if (el?.matches('[data-letter]')) this.sel = id;
	}
	timeline = $derived.by((): Timeline | null =>
		this.beadId && this.beads ? timeline(this.beads, this.model, this.beadId) : null
	);

	// The composer's &refs, checked against the beads as he types; an unknown one is named before it goes.
	refs = $derived.by(() =>
		refTokens(this.draft).map((t) => ({
			t,
			id: this.beads ? resolveRef(this.beads, this.model, t) : null
		}))
	);
	keep() {
		if (this.draft) sessionStorage.setItem('board.draft', JSON.stringify({ body: this.draft, armed: this.armed }));
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
		if (!this.beads) this.beads = await this.store.loadBeads();
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
		const { body, ...v } = verdictLetter(act, wish);
		try {
			await this.post(body, v);
		} catch (e) {
			this.sendError = (e as Error).message;
		}
	}
}

async function getJSON(path: string): Promise<unknown> {
	const r = await fetch(path, { cache: 'no-store' });
	if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
	return r.json();
}

export const provide = (b: Board) => setContext(KEY, b);
export const board = () => getContext<Board>(KEY);
