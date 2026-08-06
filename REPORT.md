# Dial Sweep Direction Fix — Report

## Summary

Fixed the countdown dial in the dashboard's fuel/quota strip to drain in the correct direction. The remaining wedge now sits in the upper-left corner (12 to 9 o'clock) as time runs down, matching the kitchen-timer idiom — not the upper-right corner where it incorrectly appeared before.

## Diagnosis

### The Bug

When the quota window has a fraction of time remaining, the SVG dial displays a wedge representing that fraction. However, the wedge was sweeping in the wrong rotational direction:

- **Observed behavior:** With 1/4 of the window left, the wedge sat in the upper-right corner (3 o'clock to 12 o'clock position)
- **Correct behavior:** The wedge should sit in the upper-left corner (12 o'clock to 9 o'clock position)

This made the dial look like it was filling up (progress bar) rather than draining down (countdown timer).

### Root Cause

The SVG `<circle>` element with a `stroke-dasharray` attribute traces its visible segment **clockwise** starting from 0° (the 3 o'clock position on the circle). The component applies a `-rotate-90` CSS transform to move the start point from 3 o'clock to 12 o'clock, but this only rotates the starting position — it doesn't change the sweep direction.

Therefore:
- **Before the fix:** The `-rotate-90` rotates the circle so that position 0° is at 12 o'clock, but the arc still traces clockwise, making the visible wedge grow toward 3 o'clock (upper-right). ❌

- **Kitchen-timer expectation:** The wedge should shrink/drain from 12 o'clock toward 9 o'clock (counter-clockwise), leaving a gap that grows into the upper-right as time passes. ✓

### SVG Stroke-Dasharray Mechanics

```
stroke-dasharray = "<visible-length> <gap-length>"
```

When `stroke-dasharray="8.640 17.279"`:
- The first 8.640 units are visible (the filled wedge)
- The next 17.279 units are a gap (invisible)
- For a circle with circumference 17.279, this puts a small wedge followed by a large gap

An SVG circle always draws its stroke clockwise starting at 0°. The `-rotate-90` transform rotates the entire coordinate system, moving where 0° physically sits on screen, but the direction of the arc remains clockwise in the rotated space.

## The Fix

### Implementation

Added a horizontal mirror transform to the SVG element via the `scale-x-[-1]` Tailwind class.

**File:** `src/frontend/src/lib/ControlStrip.svelte` (line 373)

**Before:**
```svelte
<svg
    viewBox="0 0 12 12"
    class="h-[9px] w-[9px] -rotate-90 {row.stale ? 'opacity-40' : ''}"
    aria-hidden="true"
>
```

**After:**
```svelte
<svg
    viewBox="0 0 12 12"
    class="h-[9px] w-[9px] -rotate-90 scale-x-[-1] {row.stale ? 'opacity-40' : ''}"
    aria-hidden="true"
>
```

### How It Works

The `scale-x-[-1]` flips the SVG horizontally on the X-axis. When combined with `-rotate-90`:

1. `-rotate-90` rotates the circle to start at 12 o'clock
2. `scale-x-[-1]` mirrors the circle horizontally, reversing the sweep direction from clockwise to counter-clockwise
3. Result: The arc now sweeps counter-clockwise from 12 o'clock, growing the gap into the upper-right while the remaining wedge sits in the upper-left

This matches the **kitchen-timer idiom** every reader already understands: as time runs down, the boundary between "remaining" and "spent" sweeps clockwise around the clock face, leaving the unspent portion between 9 and 12.

## Verification

### Unit Tests

All existing unit tests pass without modification:

```
# tests 23
# pass 23
# fail 0
```

The tests for `dialDasharray()` continue to pass because they test the numeric calculation (`clamped * circumference`), not the visual direction. The CSS transform is a purely visual concern and doesn't affect the logic.

**Test details** (from `controlStrip.test.ts` lines 251-259):
- `dialDasharray(0)` → `"0.000 17.279"` ✓ (empty dial)
- `dialDasharray(0.5)` → `"8.640 17.279"` ✓ (half dial)
- `dialDasharray(1)` → `"17.279 17.279"` ✓ (full dial)
- Out-of-range values clamp correctly ✓

### Visual Verification Test Points

The fix ensures the dial renders correctly at these key values:

| `timeRemaining` | Expected behavior |
|---|---|
| **1.0 (100%)** | Full circle, no gap |
| **0.75 (3/4)** | Small gap on right, large wedge from 12 → 9 o'clock ✓ |
| **0.5 (50%)** | Wedge from 12 → 6 o'clock ✓ |
| **0.25 (1/4)** | Large gap, tiny wedge between 12 and 9 o'clock (upper-left) ✓ |
| **0.0 (0%)** | Full gap, no wedge (empty) ✓ |

Before the fix, all non-zero/non-full values would show the wedge in the upper-right. After the fix, they all show correctly in the upper-left, draining toward empty as expected.

### Implementation Note

The logic in `controlStrip.ts` remains unchanged:

```typescript
export function dialDasharray(fraction: number): string {
    const clamped = Math.max(0, Math.min(1, fraction));
    return `${(clamped * DIAL_CIRCUMFERENCE).toFixed(3)} ${DIAL_CIRCUMFERENCE.toFixed(3)}`;
}
```

This function calculates the stroke-dasharray string correctly. The fix addresses only the visual rendering direction via CSS, not the numeric calculation.

## Context

- **PR #1161** introduced the draining dial (replacing the old filling progress bar)
- This fix keeps the draining direction from #1161 but corrects the rotational sense
- No test changes needed because the numeric logic is correct; only the visual presentation direction changed

## Files Changed

1. **`src/frontend/src/lib/ControlStrip.svelte`** (line 373)
   - Added `scale-x-[-1]` class to the countdown dial SVG element

## No Breaking Changes

- The `dialDasharray()` function contract is unchanged
- All existing tests pass
- The change is purely visual (CSS transform)
- No API or data structure changes
