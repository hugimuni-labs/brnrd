// The phone number, covered where it is readable.
//
// Rects are the phone's own coordinate space (720 x 1560), so a rect tracks
// every camera zoom and pan for free. Windows are given in *source seconds* —
// seconds from the start of the original screen recording — which is what a
// Seg already carries (clipStart + from + elapsed * rate), so the same spec
// resolves identically for CutV5, CutShortV6 and any later trim.
//
// Mirrored by media/demo/tools/mask_spec.py, which masks the *already
// rendered* mp4s: the footage this composition reads is no longer on the
// machine, so the published files were masked by that post-pass and this file
// is what keeps the next render from re-introducing the number. Keep the two
// tables in step.

export type Rect = [number, number, number, number];

export const BANNER_TITLE: Rect = [126, 92, 406, 138]; // iOS banner, title line
export const CHAT_HEADER: Rect = [186, 102, 522, 174]; // WhatsApp nav bar title
export const OPEN_SWEEP: Rect = [110, 84, 720, 260]; // the app-open animation
// The banner does not fade where it stands: as the app opens it slides *up*
// and off the top of the screen, and the title crosses y=84 on its way out —
// read off frames 1017 and 1993, where it escaped a rect that stopped at the
// banner.
export const BANNER_EXIT: Rect = [100, 0, 480, 152];
// The list window opens at 37.88 rather than at the frame the list looks
// drawn: on frame 338 the rows render with the avatars still grey
// placeholders, and both numbers are fully legible on it.
// The chat-list rows run to x = 0 on purpose: iOS's push animation slides the
// list left under the incoming chat, and a rect that started at the text let
// the first glyphs out on the left (caught on frame 352 of the 68-s cut).
export const LIST_SELF: Rect = [0, 466, 540, 536];
export const LIST_THIRD_A: Rect = [0, 1076, 540, 1150]; // a third party's mobile
export const LIST_THIRD_B: Rect = [0, 1392, 540, 1456];

type Window = [number | null, number | null, Rect];

export const WINDOWS: Record<string, Window[]> = {
  // the ask, 13:07-13:08Z — home screen, chat list, then the chat
  "c02_ask.mp4": [
    [37.88, 39.2, LIST_SELF],
    [37.88, 39.2, LIST_THIRD_A],
    [37.88, 39.2, LIST_THIRD_B],
    [38.8, 39.62, OPEN_SWEEP],
    [39.4, 999, CHAT_HEADER],
  ],
  "c03_wait.mp4": [[null, null, CHAT_HEADER]],

  // the reply, 13:11Z — banner, the app opens, the chat
  "s_notify.png": [[null, null, BANNER_TITLE]],
  "c04b_notify.mp4": [
    [263.55, 263.98, BANNER_TITLE],
    [263.78, 264.02, BANNER_EXIT],
    [263.8, 264.38, OPEN_SWEEP],
    [264.18, 999, CHAT_HEADER],
  ],
  "c05_reply.mp4": [[null, null, CHAT_HEADER]],
  "s_sent.png": [[null, null, CHAT_HEADER]],

  // the steer folded, 13:14Z — banner, the app opens, the chat
  "s_folded.png": [[null, null, BANNER_TITLE]],
  "c05c_folded.mp4": [
    [428.55, 429.95, BANNER_TITLE],
    [429.72, 430.02, BANNER_EXIT],
    [429.82, 430.5, OPEN_SWEEP],
    [430.3, 999, CHAT_HEADER],
  ],
};

export const MASK_FILL = "#0A0D0F";
export const MASK_EDGE = "rgba(255, 179, 71, 0.27)";

// Overlapping rects merge into their bounding box: the windows overlap on
// purpose (the hand-off between two surfaces is where a number escapes), and
// an inner rect's edge drawn inside an outer one reads as a UI element.
export const rectsFor = (clip: string, t: number): Rect[] => {
  const out: Rect[] = [];
  for (const [t0, t1, rect] of WINDOWS[clip] ?? []) {
    if (t0 === null || t1 === null || (t >= t0 && t <= t1)) out.push([...rect] as Rect);
  }
  for (let changed = true; changed; ) {
    changed = false;
    outer: for (let i = 0; i < out.length; i++) {
      for (let j = i + 1; j < out.length; j++) {
        const a = out[i], b = out[j];
        if (a[0] <= b[2] && b[0] <= a[2] && a[1] <= b[3] && b[1] <= a[3]) {
          out[i] = [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[2], b[2]), Math.max(a[3], b[3])];
          out.splice(j, 1);
          changed = true;
          break outer;
        }
      }
    }
  }
  return out;
};
