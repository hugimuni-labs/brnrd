"""The v6 cut's timeline and camera, ported from the Remotion source.

Single purpose: given a render frame of `CutV5` (the 68-s scored cut) or
`CutShortV6` (the 45-s trim), say which clip is on screen and where a given
rectangle of the *phone's own* coordinate space (720x1560) lands in the
1920x1080 frame. The mask geometry in `remotion/src/cut/Phone.tsx` is the
source of truth; this file re-derives the same numbers so a post-pass can be
applied to an already-rendered mp4 (the footage the composition reads is not
on this machine any more — see media/demo/MASK.md).

Ported from: src/cut/scenes.ts (fps, VIDEO_W/H, segFrames), src/cut/v5.ts
(scenesV5, scenesShortV6), src/cut/Phone.tsx (fitScale, camAt, cameraFrame).
"""
from __future__ import annotations

FPS = 60
VIDEO_W = 720
VIDEO_H = 1560
OUTRO_FRAMES = 192  # round(3.2 * 60)  # 192


def S(clip, clip_start):
    def mk(frm, to, rate):
        return {"clip": clip, "clipStart": clip_start, "from": frm, "to": to,
                "rate": rate, "still": False}
    return mk


def F(still, at, seconds):
    return {"clip": still, "clipStart": at, "from": 0, "to": seconds,
            "rate": 1, "still": True}


c01 = S("c01_block.mp4", 29)
c02 = S("c02_ask.mp4", 36)
c03 = S("c03_wait.mp4", 117)
c04b = S("c04b_notify.mp4", 262)
c05 = S("c05_reply.mp4", 290)
c05c = S("c05c_folded.mp4", 425)
c05d = S("c05d_strand.mp4", 383)
c06 = S("c06_push.mp4", 2496)
c07 = S("c07_prup.mp4", 2672)
c08a = S("c08a_merge.mp4", 2744)
c09 = S("c09_gone.mp4", 2925)

FULL = {"at": 0, "z": 1, "cx": 0.5, "cy": 0.5}

SCENES_V5 = [
    {"id": "block",
     "segs": [c01(0, 4, 3), F("s_block.png", 33, 3.0), F("s_block.png", 33, 0.8), c02(0, 4, 4)],
     "cam": [FULL, {"at": 0.2, "z": 1, "cx": 0.5, "cy": 0.5},
             {"at": 0.3, "z": 2.1, "cx": 0.5, "cy": 0.63}, {"at": 0.72, "z": 2.1, "cx": 0.5, "cy": 0.63},
             {"at": 0.84, "z": 1, "cx": 0.5, "cy": 0.5}, {"at": 1, "z": 1, "cx": 0.5, "cy": 0.5}]},
    {"id": "ask",
     "segs": [c02(4, 6, 1), c02(6, 77, 45), c02(77, 81, 2), c03(0, 2.2, 1)],
     "cam": [{"at": 0, "z": 2.0, "cx": 0.5, "cy": 0.62}, {"at": 0.55, "z": 2.0, "cx": 0.5, "cy": 0.62},
             {"at": 0.7, "z": 1.7, "cx": 0.5, "cy": 0.5}, {"at": 1, "z": 1.7, "cx": 0.5, "cy": 0.5}]},
    {"id": "reply",
     "segs": [F("s_notify.png", 263.7, 2.8), c04b(1.6, 3.4, 1), c04b(3.4, 28, 18),
              c05(0, 10, 8), c05(10, 44.7, 20), F("s_sent.png", 335.1, 1.8)],
     "cam": [{"at": 0, "z": 1.2, "cx": 0.5, "cy": 0.4}, {"at": 0.05, "z": 2.2, "cx": 0.5, "cy": 0.07},
             {"at": 0.16, "z": 2.2, "cx": 0.5, "cy": 0.07},
             {"at": 0.24, "z": 1, "cx": 0.5, "cy": 0.5}, {"at": 0.5, "z": 1, "cx": 0.5, "cy": 0.5},
             {"at": 0.62, "z": 1.9, "cx": 0.5, "cy": 0.6}, {"at": 0.83, "z": 1.9, "cx": 0.5, "cy": 0.6},
             {"at": 0.88, "z": 1.9, "cx": 0.5, "cy": 0.5}, {"at": 1, "z": 1.9, "cx": 0.5, "cy": 0.5}]},
    {"id": "strand",
     "segs": [c05d(0, 2.5, 1.2), c05d(2.5, 6, 1)],
     "cam": [FULL, {"at": 0.25, "z": 1, "cx": 0.5, "cy": 0.5},
             {"at": 0.45, "z": 2.0, "cx": 0.5, "cy": 0.5}, {"at": 1, "z": 2.0, "cx": 0.5, "cy": 0.5}]},
    {"id": "folded",
     "segs": [F("s_folded.png", 428.6, 1.8), c05c(3.6, 5.2, 1), c05c(5.2, 8.2, 1), c05c(8.2, 22, 12)],
     "cam": [{"at": 0, "z": 1.2, "cx": 0.5, "cy": 0.4}, {"at": 0.06, "z": 2.2, "cx": 0.5, "cy": 0.07},
             {"at": 0.22, "z": 2.2, "cx": 0.5, "cy": 0.07},
             {"at": 0.32, "z": 1, "cx": 0.5, "cy": 0.5}, {"at": 0.5, "z": 1, "cx": 0.5, "cy": 0.5},
             {"at": 0.58, "z": 2.0, "cx": 0.5, "cy": 0.5}, {"at": 0.86, "z": 2.0, "cx": 0.5, "cy": 0.5},
             {"at": 1, "z": 1, "cx": 0.5, "cy": 0.5}]},
    {"id": "push",
     "segs": [c06(0, 6, 3), c06(6, 8, 1), c06(8, 57, 80), c06(57, 60, 1.6), c06(60, 62, 1)],
     "cam": [FULL, {"at": 0.18, "z": 1, "cx": 0.5, "cy": 0.5},
             {"at": 0.3, "z": 2.0, "cx": 0.5, "cy": 0.6}, {"at": 0.75, "z": 2.0, "cx": 0.5, "cy": 0.6},
             {"at": 0.9, "z": 1.5, "cx": 0.5, "cy": 0.62}, {"at": 1, "z": 1.5, "cx": 0.5, "cy": 0.62}]},
    {"id": "prup",
     "segs": [c07(1.5, 5.3, 1.5), F("s_prup.png", 2677.6, 2.2), c08a(5, 22, 6), c08a(22, 24.5, 1)],
     "cam": [FULL, {"at": 0.22, "z": 1, "cx": 0.5, "cy": 0.5},
             {"at": 0.27, "z": 2.3, "cx": 0.5, "cy": 0.07}, {"at": 0.44, "z": 2.3, "cx": 0.5, "cy": 0.07},
             {"at": 0.5, "z": 1.2, "cx": 0.5, "cy": 0.5}, {"at": 0.8, "z": 1.2, "cx": 0.5, "cy": 0.5},
             {"at": 0.88, "z": 1.9, "cx": 0.5, "cy": 0.5}, {"at": 1, "z": 1.9, "cx": 0.5, "cy": 0.5}]},
    {"id": "ci",
     "segs": [F("s_ci.png", 2915.5, 1.4)],
     "cam": [{"at": 0, "z": 1.6, "cx": 0.5, "cy": 0.42}, {"at": 1, "z": 1.7, "cx": 0.5, "cy": 0.42}]},
    {"id": "gone",
     "segs": [c09(1, 5, 1.8), c09(5, 10, 2.5), F("s_gone.png", 2932, 3.4)],
     "cam": [FULL, {"at": 0.35, "z": 1, "cx": 0.5, "cy": 0.5},
             {"at": 0.55, "z": 1.9, "cx": 0.5, "cy": 0.5}, {"at": 1, "z": 1.9, "cx": 0.5, "cy": 0.5}]},
]

SHORT_SEGS = {
    "ask": [c02(4, 5.6, 1), c02(5.6, 77, 90), c02(77, 81, 2.2), c03(0, 1.6, 1)],
    "reply": [F("s_notify.png", 263.7, 2.0), c04b(1.6, 3.4, 1), c04b(3.4, 28, 26),
              c05(0, 10, 12), c05(10, 44.7, 30), F("s_sent.png", 335.1, 1.4)],
    "folded": [F("s_folded.png", 428.6, 1.4), c05c(3.6, 5.2, 1.2), c05c(5.2, 8.2, 1.2)],
    "push": [c06(0, 6, 4), c06(6, 8, 1), c06(8, 57, 110), c06(57, 60, 2), c06(60, 61.5, 1)],
    "prup": [c07(1.5, 5.3, 1.8), F("s_prup.png", 2677.6, 1.8)],
    "gone": [c09(1, 5, 2.2), c09(5, 10, 3.5), F("s_gone.png", 2932, 3.0)],
}

SCENES_SHORT_V6 = [
    dict(sc, segs=SHORT_SEGS[sc["id"]]) if sc["id"] in SHORT_SEGS else sc
    for sc in SCENES_V5 if sc["id"] not in ("merge", "ci", "strand")
]


def _jsround(x):
    """JS Math.round: halves go up. Python's round() is banker's and drops a
    frame on c06(57, 60, 1.6) = 112.5 — one frame short of the real render."""
    import math
    return math.floor(x + 0.5)


def seg_frames(s):
    return _jsround(((s["to"] - s["from"]) / s["rate"]) * FPS)


def scene_frames(sc):
    return sum(seg_frames(s) for s in sc["segs"])


def total_frames(scene_list):
    return sum(scene_frames(sc) for sc in scene_list) + OUTRO_FRAMES


def _ease_in_out_cubic(t):
    # Remotion's Easing.inOut(Easing.cubic)
    if t < 0.5:
        return 4 * t * t * t
    return 1 - pow(-2 * t + 2, 3) / 2


def cam_at(keys, t):
    for i in range(len(keys) - 1):
        a, b = keys[i], keys[i + 1]
        if a["at"] <= t <= b["at"]:
            p = 1 if b["at"] == a["at"] else _ease_in_out_cubic((t - a["at"]) / (b["at"] - a["at"]))
            return {"z": a["z"] + (b["z"] - a["z"]) * p,
                    "cx": a["cx"] + (b["cx"] - a["cx"]) * p,
                    "cy": a["cy"] + (b["cy"] - a["cy"]) * p}
    k = keys[0] if t <= keys[0]["at"] else keys[-1]
    return {"z": k["z"], "cx": k["cx"], "cy": k["cy"]}


def camera_frame(scene, frame, width=1920, height=1080):
    """(z, tx, ty) — Phone.tsx's main (unblurred) layer transform."""
    total = scene_frames(scene)
    t = min(1.0, max(0.0, frame / max(1, total - 1)))
    cam = cam_at(scene["cam"], t)
    z = (height / VIDEO_H) * cam["z"]
    return z, width / 2 - cam["cx"] * VIDEO_W * z, height / 2 - cam["cy"] * VIDEO_H * z


def timeline(scene_list):
    """[(global_frame_start, scene, frame_in_scene_start)] per scene."""
    out, start = [], 0
    for sc in scene_list:
        out.append((start, sc))
        start += scene_frames(sc)
    return out


def at_frame(scene_list, frame):
    """(scene, frame_in_scene, seg) for a global render frame, or None in the outro."""
    start = 0
    for sc in scene_list:
        n = scene_frames(sc)
        if frame < start + n:
            fis = frame - start
            s0 = 0
            for seg in sc["segs"]:
                sn = seg_frames(seg)
                if fis < s0 + sn:
                    return sc, fis, seg
                s0 += sn
            return sc, fis, sc["segs"][-1]
        start += n
    return None


if __name__ == "__main__":
    print("CutV5      ", total_frames(SCENES_V5))
    print("CutShortV6 ", total_frames(SCENES_SHORT_V6))


def source_time_at(scene, frame):
    """Seconds from the start of the original recording, for a frame inside a
    scene — Phone.tsx's `sourceTimeAt`, and what mask_spec's windows speak."""
    start = 0
    for seg in scene["segs"]:
        n = seg_frames(seg)
        if frame < start + n:
            if seg["still"]:
                return seg["clipStart"], seg
            return seg["clipStart"] + seg["from"] + ((frame - start) / FPS) * seg["rate"], seg
        start += n
    seg = scene["segs"][-1]
    return seg["clipStart"] + seg["to"], seg


def resolve(scene_list, frame):
    """(clip, source_seconds, z, tx, ty) for a global render frame, or None."""
    start = 0
    for sc in scene_list:
        n = scene_frames(sc)
        if frame < start + n:
            fis = frame - start
            t, seg = source_time_at(sc, fis)
            z, tx, ty = camera_frame(sc, fis)
            return seg["clip"], t, z, tx, ty
        start += n
    return None
