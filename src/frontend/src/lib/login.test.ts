import assert from 'node:assert/strict';
import test from 'node:test';

import { resolveSigninHref } from './login.ts';
import type { LoginContext } from './login.ts';

const readyContext: LoginContext = {
	authenticated: false,
	oauth_ready: true,
	signin_url: '/auth/github/start?next=%2F',
	next: '/'
};

test('OAuth confirmed live: the landing skips /login and jumps straight to the handshake', () => {
	assert.equal(resolveSigninHref(readyContext, '/login'), '/auth/github/start?next=%2F');
});

test('OAuth not configured (self-hosted, no client secret): falls back to /login', () => {
	const notReady: LoginContext = { ...readyContext, oauth_ready: false };
	assert.equal(resolveSigninHref(notReady, '/login'), '/login');
});

test('context not loaded yet: falls back to /login rather than a broken href', () => {
	assert.equal(resolveSigninHref(null, '/login'), '/login');
});
