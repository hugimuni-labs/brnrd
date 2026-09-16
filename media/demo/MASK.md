# The number, and why the mask is a post-pass

The v6 cut is 68 seconds of the maintainer's own phone, and in every WhatsApp
beat the contact header carries his mobile in plain text. Three surfaces, all
found by looking rather than by grepping:

| surface | where | tape clock |
| --- | --- | --- |
| the iOS notification banner | `s_notify.png`, `s_folded.png`, and the first half-second of `c04b_notify` / `c05c_folded` | 13:11Z, 13:14Z |
| the WhatsApp chat nav bar | `c02_ask`, `c03_wait`, `c04b_notify`, `c05_reply`, `s_sent`, `c05c_folded` | 13:07Z onward |
| the WhatsApp chat list | `c02_ask`, ~0.6 s while the app opens | 13:08Z |

The chat list also carries **a third party's mobile** (and a second one below
it), which is the same defect wearing someone else's name, so it is masked too.

## Why the mask is not simply a re-render

`v5.ts` reads eleven clips out of `public/clips/`. They are cut from a single
~50-minute screen recording that is not on this machine any more, and neither
are the clips: `public/clips/` holds only `clips.sh`, which is itself stale —
it never produced `c04b_notify.mp4`, `c05_reply.mp4` or `c05c_folded.mp4`, the
names the composition actually reads. The nearest surviving footage,
`~/Desktop/brnrd-demo-clips/`, is an earlier cut at different offsets and
covers barely half of what the composition asks for. Rendering from it would
produce a cut that looks fine and shows the wrong moments.

So the published mp4s are masked by a post-pass over the already-approved
renders — and that is exact rather than approximate, because the cut's camera
is a pure function of the frame. `tools/cut_timing.py` ports the timeline and
camera from `scenes.ts`, `v5.ts` and `Phone.tsx`; it reproduces both
compositions' frame counts to the frame (CutV5 4115, CutShortV6 2738, matching
the two rendered files), which is the receipt that the port is faithful.

## The geometry

Rects live in the phone's own coordinate space (720 x 1560) — the space
`Footage` draws into. A rect stated there tracks every zoom and pan for free:
the same four numbers are correct at z=0.69 and at z=2.3. Windows are given in
*source seconds* (seconds from the start of the original recording), which is
what a `Seg` already carries as `clipStart + from + elapsed * rate`, so one
spec resolves identically for `CutV5`, `CutShortV6` and any later trim.

Two things the geometry only learned by being looked at:

- **96% opacity is not opaque.** At `alpha=246` the digits still ghosted
  through the fill on a light chat header. Opaque or nothing.
- **Neither the list nor the banner holds still.** iOS's push animation slides
  the chat list *left* under the incoming chat (so the list rects run to x=0,
  or the first glyphs escape on the left — frame 352), and the banner slides
  *up and off the top* as the app opens, crossing y=84 on the way out (frames
  1017 and 1993, each caught by a contact sheet after the first pass looked
  clean everywhere else).

## The files

| file | what |
| --- | --- |
| `tools/cut_timing.py` | the cut's timeline and camera, ported |
| `tools/mask_spec.py` | the rects and windows (Python side) |
| `tools/mask_render.py` | the post-pass: one RGBA overlay frame per render frame, composited by ffmpeg |
| `tools/mask_stills.py` | masks the three committed stills that carry the number in the asset itself |
| `tools/verify_sheets.py` | contact sheets, cropped to a band of the *phone's* space and labelled, for reading the result with your eyes |
| `remotion/src/cut/mask.ts` | the same table for the composition |
| `remotion/src/cut/Phone.tsx` | draws it, on the sharp layer only |

Keep `mask.ts` and `mask_spec.py` in step; they are two spellings of one table.
