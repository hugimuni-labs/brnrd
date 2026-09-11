// Verbatim strings and real timestamps, quoted from
// demo-shot-script-2026-09-03.md, which quotes them from run-260901-1101-fzef's
// conversation store and boundaries.jsonl. Every string below is copied
// character-for-character from the script — do not paraphrase, do not add
// beats. Only the motion is code.

export const palette = {
  phosphor: "#7CFF9B",
  amber: "#FFB347",
  background: "#07090B",
  paper: "#f2ece1",
} as const;

export const fps = 60;

// Beat boundaries in seconds, per the shot script table (beats 1-4 only —
// the proof render stops at 12.0s).
export const beatSeconds = {
  beat1: [0.0, 3.0],
  beat2: [3.0, 5.5],
  beat3: [5.5, 8.5],
  beat4: [8.5, 12.0],
} as const;

export const beatFrames = Object.fromEntries(
  Object.entries(beatSeconds).map(([k, [start, end]]) => [
    k,
    [Math.round(start * fps), Math.round(end * fps)] as const,
  ]),
) as Record<keyof typeof beatSeconds, readonly [number, number]>;

export const durationInFrames = beatFrames.beat4[1]; // 720 @ 60fps = 12.0s

// Beat 1 — WhatsApp bubble, glitch-in from rune static.
export const beat1 = {
  text: "Okay let's do demo, could you please remove the NEEDS YOU block from UI? It renders poorly on the phone…",
  timestamp: "13:10:47Z",
  channel: "whatsapp" as const,
  source: "WA evt-…-nx69 13:10:47Z",
};

// Beat 2 — the wake: boundary bar slides in, .name types, mark blooms once.
export const beat2 = {
  barText: "⌁[b·_·d]: ⏱ 0m │ q S82 │ pending 1",
  name: "the strip the warp replaced",
  source: "boundaries.jsonl 13:09:26Z–13:10:48Z",
};

// Beat 3 — spawn: a strand card unfolds as a second, dimmer terminal window.
export const beat3 = {
  branch: "brr/the-strip-the-warp-replaced",
  runner: "sonnet",
  specQuote: beat1.text,
  source: "B 13:10:48Z",
};

// Beat 4 — interactivity: a second WhatsApp bubble, then a `to:` steer
// landing inside the strand window 4 seconds later.
export const beat4 = {
  bubbleText:
    "Ok, let's keep the approvals, but at least rename the block (it reads weird especially when empty)",
  bubbleTimestamp: "13:13:35Z",
  channel: "whatsapp" as const,
  steerText: "Maintainer steer, folded…",
  steerTimestamp: "13:13:39Z",
  source: "WA evt-…-co79; B 13:13:39Z",
};

export const runeGlyphs = ["⌁", "🧵", "🪢", "🧶", "◎", "◆", "●"] as const;
