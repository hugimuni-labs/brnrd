// v5 — his second review (2026-09-04 14:55Z): freeze-frames with the zoom on
// them, the red crossed triangle around the block, HUD callouts by the phone
// edge, the tape's own notification → switch → scroll beats at 4:23 and 7:07,
// the PR scroll slowed, the ghost of the block dissolving over the empty spot.
import { Seg, CamKey, Scene, fps, sceneFrames, OUTRO_FRAMES } from "./scenes";

export type Callout = { text: string; px: number; py: number; side: "right" | "right"; from: number; to: number; amber?: boolean };
export type Marker = { kind: "triangle" | "ghost"; px0: number; py0: number; px1: number; py1: number; from: number; to: number };
export type SceneV5 = Scene & { callouts?: Callout[]; markers?: Marker[] };

const S = (clip: string, clipStart: number) =>
  (from: number, to: number, rate: number): Seg => ({ clip, clipStart, from, to, rate });
// a freeze: a still held for `seconds`; the clock holds with it
const F = (still: string, at: number, seconds: number): Seg =>
  ({ clip: still, clipStart: at, from: 0, to: seconds, rate: 1, still: true });

const c01 = S("c01_block.mp4", 29);
const c02 = S("c02_ask.mp4", 36);
const c03 = S("c03_wait.mp4", 117);
const c04b = S("c04b_notify.mp4", 262);
const c05 = S("c05_reply.mp4", 290);
const c05c = S("c05c_folded.mp4", 425);
const c05d = S("c05d_strand.mp4", 383);
const c06 = S("c06_push.mp4", 2496);
const c07 = S("c07_prup.mp4", 2672);
const c08a = S("c08a_merge.mp4", 2744);
const c08b = S("c08b_ci.mp4", 2808);
const c09 = S("c09_gone.mp4", 2925);

const FULL: CamKey = { at: 0, z: 1, cx: 0.5, cy: 0.5 };
const BLOCK = { px0: 0.06, py0: 0.47, px1: 0.94, py1: 0.72 }; // the NEEDS YOU block on the 0:33 frame
const BANNER = { px: 0.5, py: 0.07 };

export const scenesV5: SceneV5[] = [
  {
    id: "block",
    // scroll to the block (1.3 s) · freeze 3 s zoomed with the triangle · zoom out on the freeze (0.8 s) · the switch
    segs: [c01(0, 4, 3), F("s_block.png", 33, 3.0), F("s_block.png", 33, 0.8), c02(0, 4, 4)],
    cam: [
      FULL, { at: 0.2, z: 1, cx: 0.5, cy: 0.5 },
      { at: 0.3, z: 2.1, cx: 0.5, cy: 0.63 }, { at: 0.72, z: 2.1, cx: 0.5, cy: 0.63 },
      { at: 0.84, z: 1, cx: 0.5, cy: 0.5 }, { at: 1, z: 1, cx: 0.5, cy: 0.5 },
    ],
    markers: [{ kind: "triangle", ...BLOCK, from: 1.5, to: 4.6 }],
    callouts: [{ text: "NEEDS YOU · about to go", px: 0.5, py: 0.63, side: "right", from: 1.7, to: 4.4, amber: true }],
  },
  {
    id: "ask",
    segs: [c02(4, 6, 1), c02(6, 77, 45), c02(77, 81, 2), c03(0, 2.2, 1)],
    cam: [
      { at: 0, z: 2.0, cx: 0.5, cy: 0.62 }, { at: 0.55, z: 2.0, cx: 0.5, cy: 0.62 },
      { at: 0.7, z: 1.7, cx: 0.5, cy: 0.5 }, { at: 1, z: 1.7, cx: 0.5, cy: 0.5 },
    ],
    callouts: [
      { text: "the ask, from the couch · WhatsApp · 13:08Z", px: 0.5, py: 0.6, side: "right", from: 0.2, to: 3.6 },
      { text: "sent. it folds the ask into the run that's already live.", px: 0.5, py: 0.5, side: "right", from: 4.9, to: 6.9, amber: true },
    ],
  },
  {
    id: "reply",
    // 4:23 the reply notification lands (freeze, zoom on the banner) · 4:24 switch · scroll up to 5:00 (narrated) · typing · send 5:45 · hold
    segs: [F("s_notify.png", 263.7, 2.8), c04b(1.6, 3.4, 1), c04b(3.4, 28, 18), c05(0, 10, 8), c05(10, 44.7, 20), F("s_sent.png", 335.1, 1.8)],
    cam: [
      { at: 0, z: 1.2, cx: 0.5, cy: 0.4 }, { at: 0.05, z: 2.2, cx: 0.5, cy: 0.07 }, { at: 0.16, z: 2.2, cx: 0.5, cy: 0.07 },
      { at: 0.24, z: 1, cx: 0.5, cy: 0.5 }, { at: 0.5, z: 1, cx: 0.5, cy: 0.5 },
      { at: 0.62, z: 1.9, cx: 0.5, cy: 0.6 }, { at: 0.83, z: 1.9, cx: 0.5, cy: 0.6 },
      { at: 0.88, z: 1.9, cx: 0.5, cy: 0.5 }, { at: 1, z: 1.9, cx: 0.5, cy: 0.5 },
    ],
    callouts: [
      { text: "3 minutes later: the reply lands · 13:11Z", px: 0.5, py: 0.07, side: "right", from: 0.3, to: 2.7, amber: true },
      { text: "it argues back before it deletes — \"one thing I had it decide rather than delete silently\"", px: 0.5, py: 0.45, side: "right", from: 4.8, to: 7.0 },
      { text: "the steer · \"keep the approvals, rename the block\"", px: 0.5, py: 0.6, side: "right", from: 7.3, to: 8.9 },
      { text: "sent · 13:12Z", px: 0.5, py: 0.47, side: "right", from: 9.1, to: 10.7, amber: true },
    ],
  },
  {
    id: "strand",
    segs: [c05d(0, 2.5, 1.2), c05d(2.5, 6, 1)],
    cam: [FULL, { at: 0.25, z: 1, cx: 0.5, cy: 0.5 }, { at: 0.45, z: 2.0, cx: 0.5, cy: 0.5 }, { at: 1, z: 2.0, cx: 0.5, cy: 0.5 }],
    callouts: [{ text: "the strand it spawned for the job, at work · sonnet", px: 0.5, py: 0.5, side: "right", from: 2.4, to: 5.5, amber: true }],
  },
  {
    id: "folded",
    // 7:07 the notification · 7:10 switch · scroll to 7:27 · hold on the message
    segs: [F("s_folded.png", 428.6, 1.8), c05c(3.6, 5.2, 1), c05c(5.2, 8.2, 1), c05c(8.2, 22, 12)],
    cam: [
      { at: 0, z: 1.2, cx: 0.5, cy: 0.4 }, { at: 0.06, z: 2.2, cx: 0.5, cy: 0.07 }, { at: 0.22, z: 2.2, cx: 0.5, cy: 0.07 },
      { at: 0.32, z: 1, cx: 0.5, cy: 0.5 }, { at: 0.5, z: 1, cx: 0.5, cy: 0.5 },
      { at: 0.58, z: 2.0, cx: 0.5, cy: 0.5 }, { at: 0.86, z: 2.0, cx: 0.5, cy: 0.5 }, { at: 1, z: 1, cx: 0.5, cy: 0.5 },
    ],
    callouts: [
      { text: "4 s after the steer: folded into the running strand. nothing restarts.", px: 0.5, py: 0.07, side: "right", from: 0.3, to: 1.7, amber: true },
      { text: "\"steered mid-flight, folded into its contract rather than re-dispatched\"", px: 0.5, py: 0.5, side: "right", from: 3.6, to: 6.3 },
    ],
  },
  {
    id: "push",
    segs: [c06(0, 6, 3), c06(6, 8, 1), c06(8, 57, 80), c06(57, 60, 1.6), c06(60, 62, 1)],
    cam: [
      FULL, { at: 0.18, z: 1, cx: 0.5, cy: 0.5 },
      { at: 0.3, z: 2.0, cx: 0.5, cy: 0.6 }, { at: 0.75, z: 2.0, cx: 0.5, cy: 0.6 },
      { at: 0.9, z: 1.5, cx: 0.5, cy: 0.62 }, { at: 1, z: 1.5, cx: 0.5, cy: 0.62 },
    ],
    callouts: [
      { text: "40 minutes later · Telegram · 13:48Z", px: 0.5, py: 0.5, side: "right", from: 0.15, to: 1.9, amber: true },
      { text: "the pressure", px: 0.5, py: 0.56, side: "right", from: 2.3, to: 4.3 },
      { text: "sent. 76 seconds start now.", px: 0.5, py: 0.44, side: "right", from: 5.7, to: 8.3, amber: true },
    ],
  },
  {
    id: "prup",
    // the dashboard · the banner drops · freeze on it, zoom · cut to the PR (slow) · merged hold
    segs: [c07(1.5, 5.3, 1.5), F("s_prup.png", 2677.6, 2.2), c08a(5, 22, 6), c08a(22, 24.5, 1)],
    cam: [
      FULL, { at: 0.22, z: 1, cx: 0.5, cy: 0.5 },
      { at: 0.27, z: 2.3, cx: 0.5, cy: 0.07 }, { at: 0.44, z: 2.3, cx: 0.5, cy: 0.07 },
      { at: 0.5, z: 1.2, cx: 0.5, cy: 0.5 }, { at: 0.8, z: 1.2, cx: 0.5, cy: 0.5 },
      { at: 0.88, z: 1.9, cx: 0.5, cy: 0.5 }, { at: 1, z: 1.9, cx: 0.5, cy: 0.5 },
    ],
    callouts: [
      { text: "76 seconds later: the PR is up.", px: 0.5, py: 0.07, side: "right", from: 2.7, to: 4.7, amber: true },
      { text: "reviewed on the phone — the strand's own gates re-run: npm test 1029/1029 · svelte-check 0 errors", px: 0.5, py: 0.45, side: "right", from: 5.0, to: 7.4 },
      { text: "merged · 13:52Z", px: 0.5, py: 0.5, side: "right", from: 8.0, to: 10.0, amber: true },
    ],
  },
  {
    id: "ci",
    segs: [F("s_ci.png", 2915.5, 1.4)],
    cam: [{ at: 0, z: 1.6, cx: 0.5, cy: 0.42 }, { at: 1, z: 1.7, cx: 0.5, cy: 0.42 }],
    callouts: [{ text: "CI: Publish backend container #603 · Success in 2m26s", px: 0.5, py: 0.5, side: "right", from: 0.15, to: 1.3 }],
  },
  {
    id: "gone",
    // reload · scroll · freeze: the ghost of the block fades over the empty spot, struck, dissolves
    segs: [c09(1, 5, 1.8), c09(5, 10, 2.5), F("s_gone.png", 2932, 3.4)],
    cam: [
      FULL, { at: 0.35, z: 1, cx: 0.5, cy: 0.5 },
      { at: 0.55, z: 1.9, cx: 0.5, cy: 0.5 }, { at: 1, z: 1.9, cx: 0.5, cy: 0.5 },
    ],
    markers: [{ kind: "ghost", px0: 0.06, py0: 0.535, px1: 0.94, py1: 0.75, from: 4.4, to: 7.6 }],
    callouts: [
      { text: "reload · 13:55Z", px: 0.5, py: 0.3, side: "right", from: 0.3, to: 2.0 },
      { text: "the block is gone. 47 minutes, from the couch.", px: 0.5, py: 0.5, side: "right", from: 5.0, to: 7.6, amber: true },
    ],
  },
];

export const totalFramesV5 = scenesV5.reduce((n, sc) => n + sceneFrames(sc), 0) + OUTRO_FRAMES;
export { fps };


// The strict README trim on the v6 grammar: merge, CI and the strand beat out; the
// folded scroll shortened; the ask and push ramps tightened. Same captions.
const shortSegs: Record<string, Seg[]> = {
  ask: [c02(4, 5.6, 1), c02(5.6, 77, 90), c02(77, 81, 2.2), c03(0, 1.6, 1)],
  reply: [F("s_notify.png", 263.7, 2.0), c04b(1.6, 3.4, 1), c04b(3.4, 28, 26), c05(0, 10, 12), c05(10, 44.7, 30), F("s_sent.png", 335.1, 1.4)],
  folded: [F("s_folded.png", 428.6, 1.4), c05c(3.6, 5.2, 1.2), c05c(5.2, 8.2, 1.2)],
  push: [c06(0, 6, 4), c06(6, 8, 1), c06(8, 57, 110), c06(57, 60, 2), c06(60, 61.5, 1)],
  prup: [c07(1.5, 5.3, 1.8), F("s_prup.png", 2677.6, 1.8)],
  gone: [c09(1, 5, 2.2), c09(5, 10, 3.5), F("s_gone.png", 2932, 3.0)],
};
export const scenesShortV6: SceneV5[] = scenesV5
  .filter((sc) => !["merge", "ci", "strand"].includes(sc.id))
  .map((sc) => (shortSegs[sc.id] ? { ...sc, segs: shortSegs[sc.id] } : sc));
export const totalFramesShortV6 = scenesShortV6.reduce((n, sc) => n + sceneFrames(sc), 0) + OUTRO_FRAMES;
