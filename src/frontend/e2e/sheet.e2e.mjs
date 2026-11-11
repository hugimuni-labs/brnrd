// A wish's bead sheet (board.html's renderBead, live-board): its five stages as one rail in pip order, times that drop a
// repeated day, the stage that waits on him ringed; then the trace by time, where a stamp's ref opens what it names (a
// PR to the forge, a ledger id to its bead, a receipt to its Plan step), a result no stamp named, and the verdict. The
// ringed stage carries the agree (or done, on an ask) and correct buttons, so he answers from the sheet (10349).
import { after, before, test } from 'node:test';
import { deepEqual, equal, ok } from 'node:assert/strict';
import { open } from './harness.mjs';
import { at, fixture, reindex } from './fixture.mjs';

const BRAVO = 'w-bravo0000000',
	CHARLIE = 'w-charlie0000000',
	FOX = 'w-foxtrot0000000',
	NOTE = 's-test-brav1/note';

// bravo (delivered, waits on his verdict): its stamps carry refs the way the loom writes them
const home = () => {
	const parts = fixture(),
		w = parts['thread/bravo'].wishes[0],
		ref = { read: 'letter:mine:101', granting: 'brnrd#2461', delivered: 'abc1234' };
	for (const s of w.stamps) if (ref[s.stamp]) s.ref = ref[s.stamp];
	w.stamps.find((s) => s.stamp === 'delivered').line = 'the parser is out';
	// a letter hung from the wish between two of its stamps: the trace interleaves it by time
	parts.beads[NOTE] = [BRAVO, 'link', 's-test-brav1', at(9), 'a note on bravo'];
	const reply = {
		id: 's-test-brav1/answered',
		from: 's-test-brav1',
		at: at(11),
		re: 'letter:mine:101',
		body: 'The draft is ready to send.',
		to: 'channel:test/ada',
		threads: ['inbox']
	};
	parts['thread/inbox'].letters.push(reply);
	parts.beads[reply.id] = [reply.re, 're', reply.from, reply.at, reply.body];
	w.results.push({ ref: reply.id, kind: 'letter', delivered: true, at: reply.at });
	const followup = {
		id: 'letter:mine:followup',
		from: 'person:ada',
		person: true,
		to: 'thread:inbox',
		re: reply.id,
		at: at(12),
		body: 'One more question about this draft.',
		threads: ['inbox']
	};
	const previous = {
		id: 's-test-brav1/previous',
		from: reply.from,
		to: reply.to,
		at: at(10),
		body: 'The page is live.',
		threads: ['inbox']
	};
	parts['thread/inbox'].letters.push(previous, followup);
	parts.beads[previous.id] = ['s-test-brav1', 'strand', previous.from, previous.at, previous.body];
	parts.beads[followup.id] = [reply.id, 're', followup.from, followup.at, followup.body];
	return reindex(parts);
};

let B;
before(async () => (B = await open({ parts: home() })));
after(() => B?.close());

const sheet = async (wish) => {
	if (await B.page.locator('#bead.on').count()) await B.page.keyboard.press('Escape');
	await B.page.locator(`.brd [data-pip="${wish}"]`).first().click();
	await B.page.waitForSelector(`#bead.on .bd.me[data-bead="${wish}"]`);
	return B.page.evaluate(() => ({
		rail: [...document.querySelectorAll('#bead .rail .bd.stage')].map((s) => ({
			k: s.dataset.stage,
			not: s.classList.contains('not'),
			wait: s.classList.contains('waitv'),
			when: s.querySelector('em').textContent.trim()
		})),
		down: [...document.querySelectorAll('#bead .down .bd')].map(
			(r) => r.dataset.trace || r.dataset.bead
		),
		trace: [...document.querySelectorAll('#bead .down .bd.trace')].map((r) => ({
			k: r.dataset.trace,
			vb: r.querySelector('.vb').textContent.trim(),
			href: r.querySelector('.x a')?.getAttribute('href') || null,
			bead: r.querySelector('.x .ref')?.dataset.bead || null,
			sha: r.querySelector('.x .sha')?.textContent || null,
			x: [...r.querySelectorAll('.x')].map((x) => x.textContent.trim())
		}))
	}));
};

test('a waiting wish: five stages in pip order, the day said once, the verdict stage ringed', async () => {
	const { rail } = await sheet(BRAVO);
	deepEqual(
		rail.map((s) => s.k),
		['heard', 'read', 'granting', 'delivered', 'agreed']
	);
	deepEqual(
		rail.map((s) => s.not),
		[false, false, false, false, true]
	);
	deepEqual(
		rail.map((s) => s.wait),
		[false, false, false, false, true]
	);
	deepEqual(
		rail.map((s) => s.when),
		[
			`10-27 ${at(6).slice(11, 16)}`,
			at(7).slice(11, 16),
			at(8).slice(11, 16),
			at(10).slice(11, 16),
			'verdict'
		]
	);
});

test('its trace by time: each ref opens what it names, and the result no stamp named', async () => {
	const { down, trace } = await sheet(BRAVO);
	deepEqual(down, ['thread:bravo', 'read', 'granting', NOTE, 'delivered', 'result', 'result']);
	equal(trace[0].bead, 'letter:mine:101');
	equal(trace[1].href, 'https://github.com/hugimuni-labs/brnrd/pull/2461');
	equal(trace[2].sha, 'abc1234');
	deepEqual(trace[2].x, ['abc1234 write the parser', 'the parser is out']);
	equal(trace[3].x[0], 'threads/bravo/README.md');
	// a ledger ref re-centres the sheet on its bead
	await B.page.locator('#bead .down .bd.trace .x .ref').first().click();
	await B.page.waitForSelector('#bead.on .bd.me[data-bead="letter:mine:101"]');
	equal(
		await B.page.locator('#bead .rail .bd.stage').count(),
		5,
		'the source letter shares the wish rail'
	);
});

test('an agreed wish: every stage filled, nothing ringed, the verdict in its trace', async () => {
	const { rail, trace } = await sheet(FOX);
	ok(rail.every((s) => !s.not && !s.wait));
	equal(trace.at(-1).vb, 'verdict · accepted');
	deepEqual(B.errors, []);
});

const buttons = () =>
	B.page.evaluate(() =>
		[...document.querySelectorAll('#bead .rail .bd.stage button[data-act]')].map((x) => ({
			stage: x.closest('.bd.stage').dataset.stage,
			act: x.dataset.act,
			text: x.textContent.trim()
		}))
	);

test('an open ask: the ringed stage offers done and correct; an agreed wish offers nothing', async () => {
	await sheet(CHARLIE);
	deepEqual(await buttons(), [
		{ stage: 'delivered', act: 'done', text: 'done' },
		{ stage: 'delivered', act: 'correct', text: 'correct' }
	]);
	await sheet(FOX);
	deepEqual(await buttons(), []);
});

test('a waiting wish: one press on the sheet sends his agree and the ring goes', async () => {
	const posts = [];
	await B.page.route('**/v1/loom/letters', async (route) => {
		posts.push(route.request().postDataJSON());
		await route.continue();
	});
	await sheet(BRAVO);
	deepEqual(await buttons(), [
		{ stage: 'agreed', act: 'agree', text: 'agree' },
		{ stage: 'agreed', act: 'correct', text: 'correct' }
	]);
	await B.page.locator('#bead .rail .bd.stage.waitv button[data-act="agree"]').click();
	await B.page.waitForFunction(() => !document.querySelector('#bead .rail .bd.stage.waitv'));
	equal(posts.length, 1);
	equal(posts[0].wish, BRAVO);
	equal(posts[0].verdict, 'agree');
	deepEqual(await buttons(), [], 'answered, the sheet offers nothing more');
	deepEqual(B.errors, []);
});

test('an answered reply shows its exchange wish, verdict rail and answer outcome; wish row re-centres', async () => {
	await B.page.keyboard.press('Escape');
	await B.page.locator('[data-column="inbox"] .heading').click();
	await B.page.locator('.brd [data-letter="s-test-brav1/answered"] .body').first().click();
	await B.page.waitForSelector('#bead.on .bd.me[data-bead="s-test-brav1/answered"]');
	equal(await B.page.locator('#bead .bd.exchange[data-bead="' + BRAVO + '"]').count(), 1);
	equal(await B.page.locator('#bead .rail .bd.stage').count(), 5);
	equal(
		await B.page.locator('#bead .down [data-trace="answered"] .vb').textContent(),
		'this answered it'
	);
	equal(await B.page.locator('#bead .none').count(), 0);
	equal(await B.page.locator('#bead .down [data-bead="letter:mine:followup"]').count(), 1);
	await B.page.locator('#bead .bd.exchange').click();
	await B.page.waitForSelector('#bead .bd.me[data-bead="' + BRAVO + '"]');
	deepEqual(B.errors, []);
});

test('pressing a second letter while the sheet is open replaces its body, source, outcomes and stages together', async () => {
	const press = async (id) => {
		await B.page
			.locator('.brd article.letter[data-letter="' + id + '"]')
			.first()
			.evaluate((e) => e.click());
		await B.page.waitForFunction(
			(id) =>
				window.__board.timeline?.me.id === id &&
				document.querySelector('#bead .me')?.dataset.bead === id,
			id
		);
	};
	await press('s-test-brav1/previous');
	const old = await B.page.locator('#bead .up').elementHandle();
	equal(await B.page.locator('#bead .me .x').textContent(), 'The page is live.');
	await press('s-test-brav1/answered');
	equal(await old.evaluate((e) => e.isConnected), false, 'the old selection subtree was discarded');
	equal(await B.page.locator('#bead .bid').textContent(), 's-test-brav1/answered');
	equal(await B.page.locator('#bead .me .x').textContent(), 'The draft is ready to send.');
	equal(await B.page.locator('#bead .up [data-bead="letter:mine:101"]').count(), 1);
	equal(await B.page.locator('#bead .down [data-bead="letter:mine:followup"]').count(), 1);
	equal(await B.page.locator('#bead .rail .stage').count(), 5);
	await press('letter:mine:followup');
	equal(await B.page.locator('#bead .me .x').textContent(), 'One more question about this draft.');
	equal(
		await B.page.locator('#bead .bd.exchange').count(),
		1,
		'a person reply shares the exchange wish'
	);
	// The former inline action is still there, without stealing the main press.
	await B.page
		.locator('.brd article[data-letter="letter:mine:followup"] button.disposition')
		.click();
	equal(
		await B.page.locator('.brd article[data-letter="letter:mine:followup"] .dissect').count(),
		1
	);
	equal(await B.page.locator('#bead .bid').textContent(), 'letter:mine:followup');
	deepEqual(B.errors, []);
});

test('late beads from the first press and a part refresh keep the second letter selected', async () => {
	const parts = home(),
		P = await open({ parts });
	let release;
	const gate = new Promise((resolve) => (release = resolve));
	let requested = 0;
	try {
		await P.page.route('**/v1/loom/beads', async (route) => {
			requested++;
			await gate;
			await route.fulfill({ json: parts.beads });
		});
		await P.page.locator('[data-column="inbox"] .heading').click();
		const press = (id) =>
			P.page
				.locator('article.letter[data-letter="' + id + '"]')
				.first()
				.evaluate((e) => e.click());
		await press('s-test-brav1/previous');
		await P.page.waitForFunction(() => window.__board.beadId === 's-test-brav1/previous');
		await press('s-test-brav1/answered');
		const fresh = structuredClone(parts);
		fresh['thread/inbox'].letters.find((l) => l.id === 's-test-brav1/answered').body += ' Updated.';
		P.loom.set(reindex(fresh));
		await P.page.waitForFunction(() =>
			window.__board.model.byId.get('s-test-brav1/answered').body.endsWith('Updated.')
		);
		release();
		await P.page.waitForFunction(
			() => !!window.__board.beads && window.__board.timeline.me.id === 's-test-brav1/answered'
		);
		equal(requested, 1, 'two presses share the same in-flight beads request');
		equal(await P.page.locator('#bead .bid').textContent(), 's-test-brav1/answered');
		equal(
			await P.page.locator('#bead .me .x').textContent(),
			'The draft is ready to send. Updated.'
		);
		equal(await P.page.locator('#bead .up [data-bead="letter:mine:101"]').count(), 1);
		equal(await P.page.locator('#bead .down [data-bead="letter:mine:followup"]').count(), 1);
		await P.page.locator('#bead .rail button[data-act="correct"]').click();
		equal(await P.page.evaluate(() => window.__board.armed), BRAVO);
		await P.page.locator('#bead .rail button[data-act="agree"]').click();
		await P.page.waitForFunction(() => !document.querySelector('#bead .rail .waitv'));
		equal(P.loom.sent.at(-1).wish, BRAVO);
		equal(P.loom.sent.at(-1).verdict, 'agree');
		deepEqual(P.errors, []);
	} finally {
		release();
		await P.close();
	}
});

test('several exchange wishes each get a pressable row, with no ambiguous verdict rail', async () => {
	const parts = home();
	parts['thread/bravo'].wishes.push({
		...structuredClone(parts['thread/bravo'].wishes[0]),
		wish: 'w-second000000',
		line: 'A second wish in the same exchange',
		results: []
	});
	const P = await open({ parts: reindex(parts) });
	try {
		await P.page.locator('[data-column="inbox"] .heading').click();
		await P.page.locator('article[data-letter="s-test-brav1/answered"]').evaluate((e) => e.click());
		await P.page.waitForSelector('#bead .bd.me[data-bead="s-test-brav1/answered"]');
		equal(await P.page.locator('#bead .bd.exchange').count(), 2);
		equal(await P.page.locator('#bead .rail').count(), 0);
		equal(await P.page.locator('#bead .none').count(), 0);
		await P.page.locator('#bead .bd.exchange[data-bead="w-second000000"]').click();
		await P.page.waitForSelector('#bead .bd.me[data-bead="w-second000000"]');
		deepEqual(P.errors, []);
	} finally {
		await P.close();
	}
});
