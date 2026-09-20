import { ok } from 'node:assert/strict';
import { test } from 'node:test';
import { isAnalyticsPath } from './analytics.ts';

test('GoatCounter is limited to public acquisition and content pages', () => {
	for (const path of [
		'/',
		'/pricing',
		'/learn',
		'/learn/agent-orchestration',
		'/log',
		'/log/some-entry'
	]) {
		ok(isAnalyticsPath(path), `${path} should be counted`);
	}

	for (const path of [
		'/login',
		'/connect',
		'/daily',
		'/new',
		'/garage',
		'/terms',
		'/privacy',
		'/legal-notice',
		'/sub-processors',
		'/beta-hosted-execution'
	]) {
		ok(!isAnalyticsPath(path), `${path} should not load GoatCounter`);
	}
});

test('analytics path matching normalizes slashes and refuses query-bearing input', () => {
	ok(isAnalyticsPath('/pricing/'));
	ok(!isAnalyticsPath('/pricing?token=secret'));
	ok(!isAnalyticsPath('/login?state=secret'));
});
