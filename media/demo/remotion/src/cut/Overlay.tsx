import React from "react";
import { AbsoluteFill, interpolate, useCurrentFrame } from "remotion";
import { Label, RECORDING_START_UTC, Scene, fps } from "./scenes";
import { sourceTimeAt } from "./Phone";

const mono = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace";
const PH = "#7CFF9B";
const AM = "#FFB347";

const pad = (n: number) => String(n).padStart(2, "0");
export const clockText = (secs: number) => {
  const d = new Date(RECORDING_START_UTC + secs * 1000);
  return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}Z`;
};

export const Bar: React.FC<{ scene: Scene; sceneIndex: number; sceneCount: number }> = ({ scene, sceneIndex, sceneCount }) => {
  const frame = useCurrentFrame();
  const secs = sourceTimeAt(scene, frame);
  const tick = Math.floor(secs * 2) % 2 === 0 ? "⌁" : "·";
  return (
    <div
      style={{
        position: "absolute", left: 0, right: 0, bottom: 0, padding: "18px 44px",
        background: "rgba(7,9,11,0.88)", borderTop: `1px solid ${PH}55`,
        fontFamily: mono, fontSize: 30, color: PH, letterSpacing: 0.5, textShadow: `0 0 12px ${PH}77`,
        display: "flex", justifyContent: "space-between",
      }}
    >
      <span>{tick}[b·_·d]: brnrd.dev │ the resident, from a phone</span>
      <span style={{ color: AM, textShadow: `0 0 12px ${AM}77` }}>{clockText(secs)}</span>
    </div>
  );
};

export const Labels: React.FC<{ labels?: Label[] }> = ({ labels }) => {
  const frame = useCurrentFrame();
  if (!labels) return null;
  return (
    <AbsoluteFill style={{ pointerEvents: "none" }}>
      {labels.map((l, i) => {
        const f0 = Math.round(l.from * fps), f1 = Math.round(l.to * fps);
        if (frame < f0 || frame > f1) return null;
        const o = interpolate(frame, [f0, f0 + 8, f1 - 8, f1], [0, 1, 1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
        const slide = interpolate(frame, [f0, f0 + 10], [14, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
        const color = l.amber ? AM : PH;
        const pos: React.CSSProperties =
          l.pos === "tr" ? { right: 60, top: 54 } : l.pos === "bl" ? { left: 60, bottom: 120 } : { left: 60, top: 54 };
        return (
          <div key={i} style={{ position: "absolute", ...pos, opacity: o, transform: `translateY(${slide}px)`, fontFamily: mono, fontSize: 38, color, textShadow: `0 0 14px ${color}88`, background: "rgba(7,9,11,0.72)", padding: "10px 18px", border: `1px solid ${color}44` }}>
            {l.text}
          </div>
        );
      })}
    </AbsoluteFill>
  );
};
