import assert from 'node:assert/strict';
import { test } from 'node:test';
import { fetchLoginContext } from './login.ts';

test('sign-in context preserves a destination and an explicit GitHub account switch', async () => {
	await fetchLoginContext(
		'/connect/BR-123#proof',
		async (input) => {
			const url = new URL(String(input), 'https://brnrd.dev');
			assert.equal(url.pathname, '/v1/dashboard/login-context');
			assert.equal(url.searchParams.get('next'), '/connect/BR-123#proof');
			assert.equal(url.searchParams.get('switch'), '1');
			return Response.json({ authenticated: true });
		},
		true
	);
});
