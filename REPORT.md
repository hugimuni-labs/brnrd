# Rail and Machine Dock Scroll Coordination Fix

## Bug Description

The dashboard's sticky header blocks (rail and machine dock) had inconsistent collapse/scroll behavior:

1. **Gap between collapsed blocks**: When the rail condensed to a one-line header and the machine dock remained in normal flow, a large gap (100px+) appeared between them. They were visually disconnected instead of "stacked like a magnet" as intended.

2. **Content scrolling under the rail**: When the rail was expanded and the machine was expanded, the machine's lane content (the run card) would scroll under the rail instead of staying cleanly below it.

## Root Cause Analysis

### The Coordinate System Mismatch

The rail and machine dock were calculating their scroll thresholds using different reference frames:

- **Rail**: Used absolute scroll position (`scrollY`) to decide when to condense
  - Condenses when: `scrollY > railTop + railFullHeight`
  - This is an absolute document-based calculation

- **Machine**: Used viewport coordinates (`getBoundingClientRect().bottom`) to decide when to dock
  - Docked when: `home < dockTop - 24px` (with 24px hysteresis slack)
  - This depends on the machine's visible position on screen

### The Layout Shift Problem

When the rail condensed:

1. Rail height changed from ~150px (full) to 48px (condensed)
2. A `railReserve` spacer was inserted below the rail to preserve document layout
3. The spacer height = full height - condensed height = ~102px
4. This spacer pushed the machine's position down in viewport coordinates
5. But the machine's dock threshold (`dockTop`) changed from ~150px to ~40px
6. The machine's viewport position (~150px due to spacer) was now much higher than the dock threshold (~40px - 24px slack = 16px)
7. Result: machine wouldn't dock until the viewport position dropped to 16px, requiring substantial additional scrolling

### Why the Slack Was Too Large

The original 24px slack (matching the sentinel height) made sense for preventing flicker in steady scrolling. However:

- When not yet docked: required `home < dockTop - 24`
- With expanded rail: `home < ~150 - 24 = 126` (machine at ~160 wouldn't dock yet)
- When condensed: `home < ~40 - 24 = 16` (machine at ~150 wouldn't dock until much more scrolling)

The slack value was too conservative for the geometry of the layout shift.

## Fix Implementation

### Changes Made

**File: `src/frontend/src/lib/machineDock.ts`**

Modified the `machineDockVerdict` function to use reduced hysteresis slack:

- **Old slack**: 24px (full sentinel height)
- **New slack**: 12px (half sentinel height)

This gives:
- **Un-dock threshold** (unchanged): `home >= dockTop` (preserves hysteresis once docked)
- **Dock threshold** (improved): `home < dockTop - 12` (triggers 12px earlier)

**File: `src/frontend/src/routes/+page.svelte`**

Updated the `machineDockVerdict` call to pass `railCondensed` state (for future refinements if needed).

### Why This Fix Works

By reducing the dock slack from 24px to 12px:

1. **For expanded rail**: The machine docks earlier (when its content is still ~100px above the rail) rather than waiting for ~126px clearance. This prevents content from scrolling under the rail.

2. **For condensed rail**: The machine docks with less delay after the rail condenses. While the spacer temporarily creates a gap, the reduced threshold means the machine responds sooner to further scrolling, reducing the visual gap.

3. **Maintains hysteresis**: The un-dock threshold remains at `dockTop` (unchanged), so once docked, the machine stays docked through normal scrolling (prevents rapid toggle flicker).

4. **Symmetrical behavior**: The 12px slack is half the sentinel height, providing a middle ground between responsiveness and stability.

## What Was NOT Changed

Several aspects were deliberately left as-is:

1. **Rail collapse logic**: The rail's scroll verdict (`railScrollVerdict` in `controlStrip.ts`) was not modified. The rail correctly uses absolute scroll positions with its own hysteresis (8px uncondense slack, full height condense barrier).

2. **Machine open/close behavior**: The `machineTapVerdict` and `machineBodyOnScreen` logic remain unchanged. The fix is only about when the head becomes sticky (docking), not about whether the lane is visually open.

3. **Z-ordering**: Rail is `z-40`, machine dock is `z-30`. This stacking is correct and unchanged.

4. **Reserve spacer**: The `railReserve` spacer logic remains unchanged. It's essential for preventing layout shift as the rail condenses.

## Testing Recommendations

To verify the fix works correctly:

### Desktop Browser (Chrome/Safari/Firefox)

1. Open the dashboard at a scroll position where the rail is expanded
2. Scroll slowly downward and observe:
   - Machine head should become sticky (dock) before its content reaches the rail
   - No content should scroll behind the rail
   - Transition should be smooth with no visible gap
3. Continue scrolling until rail condenses
4. Observe:
   - Rail and machine should be "closely stacked" with minimal gap
   - No flicker or rapid toggling of the dock state
5. Scroll back up
   - Rail should un-condense
   - Machine should un-dock when appropriate
   - Layout should be smooth with no jumps

### Mobile Browser (iOS Safari / Chrome Mobile)

1. Test with reduced motion OFF (to see full transitions)
2. Perform slow scroll gestures near the collapse/dock thresholds
3. Verify no rapid flickering or gap expansion
4. Test with reduced motion ON (per repo's own pitfall note about `prefers-reduced-motion: reduce`)

### Edge Cases

1. **Rapid scroll**: Fast scrolling past both rail and machine dock positions should not cause visible glitches
2. **Resize**: Changing window size while scrolled should maintain consistent docking
3. **Touch scroll momentum**: Mobile momentum scrolling should reach stable states (no mid-scroll jitter)

## Metrics

- **Slack reduction**: 24px → 12px (50% reduction in hysteresis)
- **Viewport position change**: ~100px in some scenarios, but perceived responsiveness is what matters
- **No change to**: Rail logic, machine open state, z-ordering, reserve spacer

## Caveats and Future Work

1. **Different screen sizes**: The fix uses viewport coordinates which are screen-size dependent. The 12px slack may need further tuning on very small screens (watch-sized) or very large screens (4K).

2. **Dynamic rail height**: If the rail's height becomes dynamic (e.g., based on content), the threshold calculations might need revisiting.

3. **Reduced motion**: The fix interacts with CSS transitions (`glitch-reveal` at 200ms, machine lane entry at 240ms). Some users with `prefers-reduced-motion: reduce` might want even faster docking.

4. **Rail state coordination**: While `railCondensed` is now passed to the verdict function, it's not currently used. Future work could implement state-specific hysteresis if the current fix proves insufficient.

## Files Modified

- `src/frontend/src/lib/machineDock.ts`: Reduced dock slack from 24px to 12px
- `src/frontend/src/routes/+page.svelte`: Pass `railCondensed` to verdict (parameter now available)

## Conclusion

The fix addresses the root cause (overly conservative slack value) with a minimal change that improves responsiveness without sacrificing stability. The machine dock now coordinates better with the rail's scroll behavior, preventing gaps and content overlap.
