import { test } from 'node:test';
import { deepStrictEqual as eq, strictEqual as is } from 'node:assert';
import { dayTotals, filterByHost, hostLabel, hostTotals, parsePageViews } from './pageViews.ts';

const rows = [
	{ day: '2026-11-17', path: '/', referer_host: 't.co', views: 7, bot_views: 9 },
	{ day: '2026-11-17', path: '/', referer_host: '', views: 1, bot_views: 0 },
	{ day: '2026-11-17', path: '/pricing', referer_host: 't.co', views: 3, bot_views: 0 },
	{ day: '2026-11-16', path: '/', referer_host: 'news.ycombinator.com', views: 4, bot_views: 0 }
];

test('parse keeps well-formed rows and refuses a foreign body', () => {
	const ok = parsePageViews({ since: 'a', until: 'b', rows: [...rows, { day: 1 }] });
	is(ok?.rows.length, 4);
	is(parsePageViews({ detail: 'Not Found' }), null);
	is(parsePageViews(null), null);
});

test('t.co is one filter away, and the direct row has a name', () => {
	eq(
		filterByHost(rows, ' T.CO ').map((r) => r.path),
		['/', '/pricing']
	);
	is(filterByHost(rows, '').length, 4);
	is(hostLabel(''), '(direct)');
	is(filterByHost(rows, 'direct').length, 1);
});

test('host totals are busiest first; day totals newest first; bots never counted', () => {
	eq(hostTotals(rows), [
		{ host: 't.co', views: 10 },
		{ host: 'news.ycombinator.com', views: 4 },
		{ host: '', views: 1 }
	]);
	eq(dayTotals(filterByHost(rows, 't.co')), [{ day: '2026-11-17', views: 10 }]);
});
