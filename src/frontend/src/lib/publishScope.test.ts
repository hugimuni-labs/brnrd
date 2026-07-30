import assert from 'node:assert/strict';
import test from 'node:test';

import {
	PUBLISH_LANES,
	PUBLISH_SCOPE_EVERYTHING,
	PUBLISH_SCOPE_OFF,
	connectPublishScopeStorageKey,
	joinRepoNames,
	optedOutClause,
	parsePublishLayers,
	presetForValue,
	publishScopeSummary,
	serializePublishLayers,
	storedPublishScopeValue,
	unrecordedClause
} from './publishScope.ts';

test('parsePublishLayers treats absent, empty, and "none" as no lanes', () => {
	assert.deepEqual(parsePublishLayers(null), new Set());
	assert.deepEqual(parsePublishLayers(undefined), new Set());
	assert.deepEqual(parsePublishLayers(''), new Set());
	assert.deepEqual(parsePublishLayers(PUBLISH_SCOPE_OFF), new Set());
});

test('parsePublishLayers splits and lowercases a comma list', () => {
	assert.deepEqual(parsePublishLayers('Activity, quota'), new Set(['activity', 'quota']));
});

test('serializePublishLayers round-trips through the canonical lane order', () => {
	const lanes = new Set(['quota', 'activity']);
	const serialized = serializePublishLayers(lanes);
	assert.deepEqual(parsePublishLayers(serialized), lanes);
	// Canonical order follows PUBLISH_LANES, not insertion order.
	assert.equal(serialized, 'activity,quota');
});

test('serializePublishLayers on an empty set is the off sentinel', () => {
	assert.equal(serializePublishLayers(new Set()), PUBLISH_SCOPE_OFF);
});

test('PUBLISH_SCOPE_EVERYTHING names every lane exactly once', () => {
	const lanes = parsePublishLayers(PUBLISH_SCOPE_EVERYTHING);
	assert.equal(lanes.size, PUBLISH_LANES.length);
	for (const lane of PUBLISH_LANES) assert.ok(lanes.has(lane.value));
});

test('presetForValue recognizes the off and everything presets', () => {
	assert.equal(presetForValue(''), 'none');
	assert.equal(presetForValue(PUBLISH_SCOPE_OFF), 'none');
	assert.equal(presetForValue(PUBLISH_SCOPE_EVERYTHING), 'everything');
	assert.equal(presetForValue('activity,quota'), 'custom');
});

test('the enable-scope storage key is stable and account-specific', () => {
	assert.equal(connectPublishScopeStorageKey('acct_1'), 'brnrd.repos.connect-publish-scope.acct_1');
	assert.notEqual(connectPublishScopeStorageKey('acct_1'), connectPublishScopeStorageKey('acct_2'));
});

test('storedPublishScopeValue restores a canonical remembered choice', () => {
	assert.equal(storedPublishScopeValue(null), null);
	assert.equal(storedPublishScopeValue(''), PUBLISH_SCOPE_OFF);
	assert.equal(storedPublishScopeValue('none'), PUBLISH_SCOPE_OFF);
	assert.equal(storedPublishScopeValue('quota, Activity'), 'activity,quota');
	assert.equal(storedPublishScopeValue(PUBLISH_SCOPE_EVERYTHING), PUBLISH_SCOPE_EVERYTHING);
});

test('storedPublishScopeValue rejects a tampered or obsolete lane', () => {
	assert.equal(storedPublishScopeValue('activity,not-a-lane'), null);
	assert.equal(storedPublishScopeValue('not-a-lane'), null);
});

test('publishScopeSummary distinguishes no-consent-recorded from an explicit off', () => {
	// Both publish nothing, but for different reasons, and the owner needs to
	// be able to tell them apart: `none` is a choice they made, `null` is a
	// question they were never asked and can still answer.
	assert.match(publishScopeSummary(null), /no consent recorded/);
	assert.match(publishScopeSummary(null), /paused/);
	assert.match(publishScopeSummary(undefined), /no consent recorded/);
	assert.match(publishScopeSummary(PUBLISH_SCOPE_OFF), /nothing/);
	assert.match(publishScopeSummary(PUBLISH_SCOPE_EVERYTHING), /everything/);
	assert.match(publishScopeSummary('quota'), /1 of \d+ lanes: quota/);
});

// ── consent-gap vocabulary (shared by WithheldNotice and PublishConsentNotice) ──

test('joinRepoNames: one, two, and three-plus repos read as natural language', () => {
	assert.equal(joinRepoNames([]), '');
	assert.equal(joinRepoNames(['a']), 'a');
	assert.equal(joinRepoNames(['a', 'b']), 'a and b');
	assert.equal(joinRepoNames(['a', 'b', 'c']), 'a, b, and c');
});

test('unrecordedClause names the repos and states only what null proves', () => {
	// No causal story — a repo minted through the account API today lands
	// `null` the same as one that predates the consent setting, so the
	// sentence must not claim either history.
	const clause = unrecordedClause(['Gurio/BeCenter']);
	assert.match(clause ?? '', /Gurio\/BeCenter/);
	assert.match(clause ?? '', /never recorded a publish scope/);
	assert.ok(!/connected before|have never been asked/.test(clause ?? ''));
});

test('unrecordedClause and optedOutClause are null, not empty-string, for an empty list', () => {
	// Distinguishes "no gap of this kind" from "a gap naming zero repos" —
	// callers key their rendering off `!== null`.
	assert.equal(unrecordedClause([]), null);
	assert.equal(optedOutClause([]), null);
});

test('optedOutClause names the repos as having chosen, not failed to choose', () => {
	const clause = optedOutClause(['Gurio/other-repo']);
	assert.match(clause ?? '', /Gurio\/other-repo/);
	assert.match(clause ?? '', /chose to publish nothing/);
});
