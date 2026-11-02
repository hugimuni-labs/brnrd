import { test } from 'node:test';
import { equal } from 'node:assert/strict';
import { isOutbound } from './outbound.ts';
import { forgeHref } from './board/bead.ts';

const HERE = 'https://brnrd.dev';

test('a link to another origin is outbound; a path, an anchor or mailto is not', () => {
	equal(isOutbound('https://github.com/hugimuni-labs/brnrd/pull/2584', HERE), true);
	equal(isOutbound('http://example.com/', HERE), true);
	equal(isOutbound('/dashboard', HERE), false);
	equal(isOutbound('https://brnrd.dev/pricing', HERE), false);
	equal(isOutbound('#web-main', HERE), false);
	equal(isOutbound('mailto:a@b.c', HERE), false);
});

test("a granting stamp's owner/repo#N ref is a forge link", () => {
	equal(forgeHref('hugimuni-labs/brnrd#2584'), 'https://github.com/hugimuni-labs/brnrd/pull/2584');
	equal(forgeHref('PR #12'), 'https://github.com/hugimuni-labs/brnrd/pull/12');
	equal(forgeHref('brnrd#7'), 'https://github.com/hugimuni-labs/brnrd/pull/7');
	equal(forgeHref('https://x.y/z'), 'https://x.y/z');
	equal(forgeHref('threads/web-main/README.md'), null);
	equal(forgeHref('s-test-char2/a9'), null);
	equal(forgeHref(null), null);
});
