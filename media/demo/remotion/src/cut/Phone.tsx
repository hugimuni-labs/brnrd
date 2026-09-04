import React from "react";
import { AbsoluteFill, Easing, OffthreadVideo, Sequence, interpolate, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import { CamKey, Scene, VIDEO_H, VIDEO_W, fps, sceneFrames, segFrames } from "./scenes";

// The phone footage fitted to the frame height; the camera is a scale about
// a point of the phone frame (cx, cy in 0..1), eased between keys.
const fitScale = (h: number) => h / VIDEO_H;

const camAt = (keys: CamKey[], t: number): CamKey => {
  const ease = Easing.inOut(Easing.cubic);
  for (let i = 0; i < keys.length - 1; i++) {
    const a = keys[i], b = keys[i + 1];
    if (t >= a.at && t <= b.at) {
      const p = b.at === a.at ? 1 : ease((t - a.at) / (b.at - a.at));
      return { at: t, z: a.z + (b.z - a.z) * p, cx: a.cx + (b.cx - a.cx) * p, cy: a.cy + (b.cy - a.cy) * p };
    }
  }
  return t <= keys[0].at ? keys[0] : keys[keys.length - 1];
};

const Footage: React.FC<{ scene: Scene; blur?: boolean }> = ({ scene, blur }) => {
  let start = 0;
  return (
    <>
      {scene.segs.map((seg, i) => {
        const n = segFrames(seg);
        const el = (
          <Sequence key={i} from={start} durationInFrames={n} layout="none">
            <OffthreadVideo
              src={staticFile(`clips/${seg.clip}`)}
              startFrom={Math.round(seg.from * fps)}
              endAt={Math.round(seg.to * fps) + 2}
              playbackRate={seg.rate}
              muted
              style={{ width: VIDEO_W, height: VIDEO_H, display: "block", filter: blur ? "blur(28px) brightness(0.32) saturate(0.7)" : undefined }}
            />
          </Sequence>
        );
        start += n;
        return el;
      })}
    </>
  );
};

export const Phone: React.FC<{ scene: Scene }> = ({ scene }) => {
  const frame = useCurrentFrame();
  const { width, height } = useVideoConfig();
  const total = sceneFrames(scene);
  const t = Math.min(1, Math.max(0, frame / Math.max(1, total - 1)));
  const cam = camAt(scene.cam, t);
  const base = fitScale(height);
  const z = base * cam.z;
  // place the phone point (cx, cy) at the frame centre
  const tx = width / 2 - cam.cx * VIDEO_W * z;
  const ty = height / 2 - cam.cy * VIDEO_H * z;
  const coverScale = Math.max(width / VIDEO_W, height / VIDEO_H) * 1.08;
  const btx = (width - VIDEO_W * coverScale) / 2;
  const bty = (height - VIDEO_H * coverScale) / 2;
  return (
    <AbsoluteFill style={{ background: "#07090B", overflow: "hidden" }}>
      <div style={{ position: "absolute", left: 0, top: 0, transform: `translate(${btx}px, ${bty}px) scale(${coverScale})`, transformOrigin: "0 0", opacity: 0.85 }}>
        <Footage scene={scene} blur />
      </div>
      <div style={{ position: "absolute", left: 0, top: 0, transform: `translate(${tx}px, ${ty}px) scale(${z})`, transformOrigin: "0 0", boxShadow: "0 0 80px rgba(0,0,0,0.9)" }}>
        <Footage scene={scene} />
      </div>
    </AbsoluteFill>
  );
};

// source time (seconds from recording start) for the clock
export const sourceTimeAt = (scene: Scene, frame: number): number => {
  let start = 0;
  for (const seg of scene.segs) {
    const n = segFrames(seg);
    if (frame < start + n) return seg.clipStart + seg.from + ((frame - start) / fps) * seg.rate;
    start += n;
  }
  const last = scene.segs[scene.segs.length - 1];
  return last.clipStart + last.to;
};
