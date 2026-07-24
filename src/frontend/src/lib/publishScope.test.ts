import assert from 'node:assert/strict';
import test from 'node:test';

import {
	PUBLISH_LANES,
	PUBLISH_SCOPE_EVERYTHING,
	PUBLISH_SCOPE_OFF,
	parsePublishLayers,
	presetForValue,
	publishScopeSummary,
	serializePublishLayers
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

test('publishScopeSummary distinguishes legacy-unset from an explicit off', () => {
	assert.match(publishScopeSummary(null), /not set/);
	assert.match(publishScopeSummary(undefined), /not set/);
	assert.match(publishScopeSummary(PUBLISH_SCOPE_OFF), /nothing/);
	assert.match(publishScopeSummary(PUBLISH_SCOPE_EVERYTHING), /everything/);
	assert.match(publishScopeSummary('quota'), /1 of \d+ lanes: quota/);
});
