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

export const Bar: React.FC<{ scene: Scene; sceneIndex: number; sceneCount: number; prevEnd?: number }> = ({ scene, sceneIndex, sceneCount, prevEnd }) => {
  const frame = useCurrentFrame();
  const here = sourceTimeAt(scene, frame);
  // a cut jumps the tape's clock by minutes; roll the digits across the first
  // 14 frames so the jump reads as a jump, not a glitch
  const roll = prevEnd !== undefined && frame < 14 ? interpolate(frame, [0, 14], [prevEnd, here], { extrapolateRight: "clamp" }) : here;
  const secs = roll;
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
        const o = interpolate(frame, [f0, f0 + 10, f1 - 10, f1], [0, 1, 1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
        const slide = interpolate(frame, [f0, f0 + 14], [18, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
        const color = l.amber ? AM : PH;
        // the empty side panel is the caption's home: big, plated, left of the phone
        const pos: React.CSSProperties =
          l.pos === "tr" ? { right: 60, top: 120, width: 520 } : l.pos === "bl" ? { left: 60, bottom: 150, width: 520 } : { left: 60, top: 120, width: 520 };
        return (
          <div key={i} style={{ position: "absolute", ...pos, opacity: o, transform: `translateY(${slide}px)`, fontFamily: mono, fontSize: 46, lineHeight: 1.25, color, textShadow: `0 0 18px ${color}99`, background: "rgba(7,9,11,0.86)", padding: "22px 28px", borderLeft: `6px solid ${color}`, boxShadow: "0 0 40px rgba(0,0,0,0.6)" }}>
            {l.text}
          </div>
        );
      })}
    </AbsoluteFill>
  );
};
