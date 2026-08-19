import { deepEqual, equal } from 'node:assert/strict';
import { test } from 'node:test';
import {
	countdown,
	conversationLink,
	doorLabel,
	doorOffCopy,
	doorOffHasEnablePath,
	orderedDoors
} from './messengerDoors.ts';

test('conversationLink removes the one-shot pairing payload from the return door', () => {
	equal(conversationLink('https://t.me/brnrd_bot?start=PK-AB12'), 'https://t.me/brnrd_bot');
	equal(conversationLink('javascript:alert(1)'), null);
	equal(conversationLink(null), null);
});
import type { MessengerDoor } from './repos.ts';

test('doorLabel names the known platforms and title-cases an unknown one', () => {
	equal(doorLabel('telegram'), 'Telegram');
	equal(doorLabel('whatsapp'), 'WhatsApp');
	equal(doorLabel('slack'), 'Slack');
	equal(doorLabel('signal'), 'Signal');
	equal(doorLabel('carrier-pigeon'), 'Carrier-pigeon');
});

test('doorOffCopy distinguishes not_built from not_configured', () => {
	equal(doorOffCopy('not_built').includes('no connector'), true);
	equal(doorOffCopy('not_configured').includes('not configured'), true);
});

test('doorOffCopy falls back to a generic line for an unrecognized reason', () => {
	equal(doorOffCopy('something-a-future-backend-invented').includes('not available'), true);
	equal(doorOffCopy(null).includes('not available'), true);
	equal(doorOffCopy(undefined).includes('not available'), true);
});

test('only not_configured carries an enable path — nothing to point at for not_built', () => {
	equal(doorOffHasEnablePath('not_configured'), true);
	equal(doorOffHasEnablePath('not_built'), false);
	equal(doorOffHasEnablePath(null), false);
});

function door(over: Partial<MessengerDoor>): MessengerDoor {
	return { platform: 'x', deep_link_available: false, reason: null, ...over };
}

test('orderedDoors keeps registry order and appends an unknown platform rather than dropping it', () => {
	const doors = [
		door({ platform: 'signal' }),
		door({ platform: 'unknown-future-door' }),
		door({ platform: 'telegram', deep_link_available: true }),
		door({ platform: 'slack' }),
		door({ platform: 'whatsapp' })
	];
	deepEqual(
		orderedDoors(doors).map((d) => d.platform),
		['telegram', 'whatsapp', 'slack', 'signal', 'unknown-future-door']
	);
});

// --- the countdown ------------------------------------------------------

test('countdown reads ample well before the low-fraction boundary', () => {
	const mintedAt = 1_000_000;
	const ttl = 180;
	const expiresAtIso = new Date(mintedAt + ttl * 1000).toISOString();
	const cd = countdown(expiresAtIso, mintedAt + 10_000, ttl); // 10s elapsed, 170s left
	equal(cd.tier, 'ample');
	equal(cd.label, '2:50');
});

test('countdown moves to low inside the last third of the TTL', () => {
	const mintedAt = 1_000_000;
	const ttl = 180;
	const expiresAtIso = new Date(mintedAt + ttl * 1000).toISOString();
	// 20s left of 180s ~ 11% — inside the last third.
	const cd = countdown(expiresAtIso, mintedAt + 160_000, ttl);
	equal(cd.tier, 'low');
	equal(cd.label, '0:20');
});

test('countdown reads critical at and past zero, never a negative number', () => {
	const mintedAt = 1_000_000;
	const ttl = 180;
	const expiresAtIso = new Date(mintedAt + ttl * 1000).toISOString();
	const atZero = countdown(expiresAtIso, mintedAt + 180_000, ttl);
	equal(atZero.tier, 'critical');
	equal(atZero.label, '0:00');
	equal(atZero.secondsLeft, 0);

	const wayPast = countdown(expiresAtIso, mintedAt + 400_000, ttl);
	equal(wayPast.tier, 'critical');
	equal(wayPast.secondsLeft, 0);
	equal(wayPast.label, '0:00');
});

test('countdown scales the ample/low boundary to the TTL it was minted with, not a hardcoded 180', () => {
	const mintedAt = 1_000_000;
	const ttl = 30; // a deployment that configured a shorter TTL
	const expiresAtIso = new Date(mintedAt + ttl * 1000).toISOString();
	// 12s left of 30s = 40% remaining — still ample at 30s TTL even though
	// it would read low against a 180s TTL's boundary.
	const cd = countdown(expiresAtIso, mintedAt + 18_000, ttl);
	equal(cd.tier, 'ample');
});
