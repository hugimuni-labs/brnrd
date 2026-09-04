import React from "react";
import { AbsoluteFill, Img, Sequence, interpolate, staticFile, useCurrentFrame } from "remotion";
import { Crt } from "../components/Crt";
import { Glitch } from "../components/Glitch";
import { OUTRO_FRAMES, Scene, scenes, sceneFrames, totalFrames, scenesShort, totalFramesShort } from "./scenes";
import { Phone, sourceTimeAt } from "./Phone";
import { Bar, Labels } from "./Overlay";
import { Hud } from "./Hud";
import { SceneV5, scenesV5, totalFramesV5 } from "./v5";

const cutFramesOf = (list: Scene[]) => {
  const out: number[] = [];
  let s = 0;
  for (const sc of list) { s += sceneFrames(sc); out.push(s); }
  return out;
};

const triangle = (frame: number, c: number, w: number) =>
  interpolate(frame, [c - w, c, c + w], [0, 1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });

const Tear: React.FC<{ intensity: number }> = ({ intensity }) => {
  if (intensity < 0.05) return null;
  return (
    <AbsoluteFill style={{ pointerEvents: "none" }}>
      {[0.18, 0.47, 0.71].map((y, i) => (
        <div key={i} style={{ position: "absolute", left: 0, right: 0, top: `${y * 100}%`, height: 3 + i * 2, background: i % 2 ? "#FFB347" : "#7CFF9B", opacity: intensity * 0.9, transform: `translateX(${(i % 2 ? -1 : 1) * intensity * 22}px)`, mixBlendMode: "screen" }} />
      ))}
    </AbsoluteFill>
  );
};

export const CutBody: React.FC<{ list: Scene[]; total: number }> = ({ list, total }) => {
  const frame = useCurrentFrame();
  const cutFrames = cutFramesOf(list);
  const spike = Math.max(0, ...cutFrames.map((c) => triangle(frame, c, 4)));
  const intro = interpolate(frame, [0, 18], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const outroStart = total - OUTRO_FRAMES;
  const outroIn = interpolate(frame, [outroStart, outroStart + 24], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const outroOut = interpolate(frame, [total - 30, total - 1], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  let start = 0;
  return (
    <AbsoluteFill style={{ background: "#07090B" }}>
      <Glitch intensity={Math.max(intro * 0.6, spike)} seed={3} grain={false}>
        <AbsoluteFill>
          {list.map((sc, i) => {
            const n = sceneFrames(sc);
            const prevEnd = i > 0 ? sourceTimeAt(list[i - 1], sceneFrames(list[i - 1]) - 1) : undefined;
            const el = (
              <Sequence key={sc.id} from={start} durationInFrames={n} layout="none">
                <Phone scene={sc} />
                <Labels labels={sc.labels} />
                <Hud scene={sc as SceneV5} />
                <Bar scene={sc} sceneIndex={i} sceneCount={list.length} prevEnd={prevEnd} />
              </Sequence>
            );
            start += n;
            return el;
          })}
          <Sequence from={outroStart} durationInFrames={OUTRO_FRAMES} layout="none">
            <AbsoluteFill style={{ background: "#07090B", opacity: outroIn * outroOut, alignItems: "center", justifyContent: "center", gap: 28 }}>
              <Img src={staticFile("mark-screen.svg")} style={{ width: 260, filter: "drop-shadow(0 0 40px rgba(124,255,155,0.35))" }} />
              <div style={{ fontFamily: "ui-monospace, Menlo, monospace", fontSize: 44, color: "#7CFF9B", textShadow: "0 0 18px rgba(124,255,155,0.6)", letterSpacing: 2 }}>brnrd.dev</div>
            </AbsoluteFill>
          </Sequence>
        </AbsoluteFill>
      </Glitch>
      <Tear intensity={spike} />
      <Crt />
      <AbsoluteFill style={{ background: "#000", opacity: interpolate(frame, [0, 18], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }), pointerEvents: "none" }} />
    </AbsoluteFill>
  );
};

export const Cut: React.FC = () => <CutBody list={scenes} total={totalFrames} />;
export const CutShort: React.FC = () => <CutBody list={scenesShort} total={totalFramesShort} />;
export const CutV5: React.FC = () => <CutBody list={scenesV5} total={totalFramesV5} />;
