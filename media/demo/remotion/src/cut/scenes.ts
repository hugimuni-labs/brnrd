// The v3 cut — footage first. Every scene is a slice of the maintainer's own
// phone recording (ScreenRecording_09-01-2026 15-06-56_1.MP4, start
// 13:06:56Z), cut into public/clips/*.mp4 by clips.sh. Offsets below are
// seconds *inside the clip*; `rate` is the playback speed of that segment.
// Speed ramps are stepped segments (1× → fast → 1×) so every cut lands on a
// real frame. Camera keys are fractions of the phone frame.

export const fps = 60;
export const RECORDING_START_UTC = Date.UTC(2026, 8, 1, 13, 6, 56); // 13:06:56Z
export const VIDEO_W = 720;
export const VIDEO_H = 1560;

export type Seg = { clip: string; clipStart: number; from: number; to: number; rate: number };
export type CamKey = { at: number; z: number; cx: number; cy: number };
export type Label = { text: string; from: number; to: number; pos?: "tl" | "tr" | "bl"; amber?: boolean };
export type Scene = { id: string; segs: Seg[]; cam: CamKey[]; labels?: Label[] };

const S = (clip: string, clipStart: number) =>
  (from: number, to: number, rate: number): Seg => ({ clip, clipStart, from, to, rate });

const c01 = S("c01_block.mp4", 29);
const c02 = S("c02_ask.mp4", 36);
const c03 = S("c03_wait.mp4", 117);
const c04 = S("c04_reply.mp4", 260);
const c05a = S("c05a_steer.mp4", 295);
const c05b = S("c05b_folded.mp4", 428);
const c06 = S("c06_push.mp4", 2496);
const c07 = S("c07_prup.mp4", 2672);
const c08a = S("c08a_merge.mp4", 2744);
const c08b = S("c08b_ci.mp4", 2808);
const c09 = S("c09_gone.mp4", 2925);

const FULL: CamKey = { at: 0, z: 1, cx: 0.5, cy: 0.5 };

export const scenes: Scene[] = [
  {
    id: "block",
    segs: [c01(0, 7, 3)],
    cam: [FULL, { at: 0.35, z: 1, cx: 0.5, cy: 0.5 }, { at: 1, z: 2.1, cx: 0.5, cy: 0.6 }],
    labels: [{ text: "the NEEDS YOU block · about to go", from: 0.4, to: 2.3, pos: "tl" }],
  },
  {
    id: "ask",
    segs: [c02(0, 4, 4), c02(4, 6, 1), c02(6, 77, 90), c02(77, 81, 2), c03(0, 2.2, 1)],
    cam: [
      FULL,
      { at: 0.14, z: 1, cx: 0.5, cy: 0.5 },
      { at: 0.22, z: 2.0, cx: 0.5, cy: 0.62 },
      { at: 0.6, z: 2.0, cx: 0.5, cy: 0.62 },
      { at: 0.74, z: 1.7, cx: 0.5, cy: 0.5 },
      { at: 1, z: 1.7, cx: 0.5, cy: 0.5 },
    ],
    labels: [{ text: "the ask, from the couch · WhatsApp · 13:08Z", from: 1.2, to: 5.6, pos: "tl" }, { text: "sent. the resident wakes on the other end.", from: 6.3, to: 8.1, pos: "tl", amber: true }],
  },
  {
    id: "wait",
    segs: [c03(128, 143, 10)],
    cam: [FULL, { at: 1, z: 1, cx: 0.5, cy: 0.5 }],
    labels: [
      { text: "it dispatches a strand for the job · 13:10Z", from: 0.1, to: 1.5, pos: "tl", amber: true },
    ],
  },
  {
    id: "reply",
    segs: [c04(0, 2.5, 1), c04(2.5, 6.5, 4)],
    cam: [FULL, { at: 0.3, z: 1, cx: 0.5, cy: 0.5 }, { at: 0.55, z: 2.4, cx: 0.5, cy: 0.4 }, { at: 1, z: 2.4, cx: 0.5, cy: 0.44 }],
    labels: [{ text: "the reply · 13:11Z — it argues back before it deletes", from: 0.4, to: 3.3, pos: "tl" }],
  },
  {
    id: "steer",
    segs: [c05a(0, 1.6, 1), c05a(1.6, 39, 75), c05a(39, 40.6, 1.6), c05a(40.6, 43, 1)],
    cam: [{ at: 0, z: 1.9, cx: 0.5, cy: 0.6 }, { at: 0.55, z: 1.9, cx: 0.5, cy: 0.6 }, { at: 0.75, z: 1.7, cx: 0.5, cy: 0.5 }, { at: 1, z: 1.7, cx: 0.5, cy: 0.5 }],
    labels: [{ text: "the steer · 13:12Z — \"keep the approvals, rename the block\"", from: 0.2, to: 5.0, pos: "tl" }],
  },
  {
    id: "folded",
    segs: [c05b(1, 6, 2)],
    cam: [{ at: 0, z: 2.0, cx: 0.5, cy: 0.42 }, { at: 1, z: 2.2, cx: 0.5, cy: 0.42 }],
    labels: [{ text: "the steer folds into the running strand · 4 s later. nothing restarts.", from: 0.3, to: 2.5, pos: "tl", amber: true }],
  },
  {
    id: "push",
    segs: [c06(0, 6, 3), c06(6, 8, 1), c06(8, 57, 80), c06(57, 60, 1.6), c06(60, 63, 1)],
    cam: [
      FULL,
      { at: 0.18, z: 1, cx: 0.5, cy: 0.5 },
      { at: 0.3, z: 2.0, cx: 0.5, cy: 0.6 },
      { at: 0.75, z: 2.0, cx: 0.5, cy: 0.6 },
      { at: 0.9, z: 1.5, cx: 0.5, cy: 0.62 },
      { at: 1, z: 1.5, cx: 0.5, cy: 0.62 },
    ],
    labels: [{ text: "40 minutes later · the pressure, on Telegram · 13:49Z", from: 0.2, to: 2.6, pos: "tl", amber: true }, { text: "sent. 76 seconds start now.", from: 6.6, to: 8.6, pos: "tl", amber: true }],
  },
  {
    id: "prup",
    segs: [c07(1.5, 6.9, 1.6)],
    cam: [FULL, { at: 0.55, z: 1, cx: 0.5, cy: 0.5 }, { at: 0.8, z: 2.2, cx: 0.5, cy: 0.07 }, { at: 1, z: 2.2, cx: 0.5, cy: 0.07 }],
    labels: [{ text: "76 seconds later: the PR is up.", from: 1.9, to: 3.4, pos: "tl", amber: true }],
  },
  {
    id: "merge",
    segs: [c08a(5, 22, 9), c08a(22, 25.5, 1)],
    cam: [FULL, { at: 0.4, z: 1, cx: 0.5, cy: 0.5 }, { at: 0.6, z: 1.9, cx: 0.5, cy: 0.5 }, { at: 1, z: 1.9, cx: 0.5, cy: 0.5 }],
    labels: [{ text: "reviewed and merged, from the phone · 13:52Z", from: 2.0, to: 5.2, pos: "tl", amber: true }],
  },
  {
    id: "ci",
    segs: [c08b(0, 109, 72)],
    cam: [FULL, { at: 1, z: 1, cx: 0.5, cy: 0.5 }],
    labels: [{ text: "Publish backend container #603 · Success 2m26s", from: 0.4, to: 2.0, pos: "tl" }],
  },
  {
    id: "gone",
    segs: [c09(1, 5, 1.8), c09(5, 10, 2.5), c09(10, 13, 1)],
    cam: [FULL, { at: 0.3, z: 1, cx: 0.5, cy: 0.5 }, { at: 0.6, z: 1.9, cx: 0.5, cy: 0.5 }, { at: 1, z: 1.9, cx: 0.5, cy: 0.5 }],
    labels: [{ text: "reload · 13:55Z", from: 0.3, to: 2.0, pos: "tl" }, { text: "the block is gone. 47 minutes, from the couch.", from: 4.4, to: 7.2, pos: "tl", amber: true }],
  },
];

export const segFrames = (s: Seg) => Math.round(((s.to - s.from) / s.rate) * fps);
export const sceneFrames = (sc: Scene) => sc.segs.reduce((n, s) => n + segFrames(s), 0);
export const OUTRO_FRAMES = Math.round(1.8 * fps);
export const totalFrames = scenes.reduce((n, sc) => n + sceneFrames(sc), 0) + OUTRO_FRAMES;


// The strict README trim: merge + CI dropped, the reply and gone holds tightened.
const tighten: Record<string, Seg[]> = {
  reply: [c04(0, 2.2, 1), c04(2.2, 6.5, 6)],
  gone: [c09(1, 5, 2.2), c09(5, 11, 3.2)],
  wait: [c03(0, 143, 100)],
  ask: [c02(0, 4, 4), c02(4, 5.5, 1), c02(5.5, 77.5, 110), c02(77.5, 81, 2.2)],
  push: [c06(0, 6, 7), c06(6, 7.5, 1), c06(7.5, 57, 95), c06(57, 62, 2.6)],
};
export const scenesShort: Scene[] = scenes
  .filter((sc) => sc.id !== "merge" && sc.id !== "ci")
  .map((sc) => (tighten[sc.id] ? { ...sc, segs: tighten[sc.id] } : sc));
export const totalFramesShort = scenesShort.reduce((n, sc) => n + sceneFrames(sc), 0) + OUTRO_FRAMES;
