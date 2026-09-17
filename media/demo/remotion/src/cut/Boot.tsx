import React from "react";
import { AbsoluteFill, interpolate, useCurrentFrame } from "remotion";

// The site's own boot glitch, ported frame for frame from
// src/frontend/src/routes/+layout.svelte + layout.css (.boot-glitch):
// `_` -> `b_d` -> `br_rd` -> `brnrd` -glitch-> `bRnЯd`, 190 ms a frame,
// letters revealed symmetrically around the underscore cursor, then a
// chromatic flicker (±2 px cyan/amber, 220 ms × 2, two steps) and the
// resting phosphor glow. Colour, glow, spacing and timing are the site's.
const FRAMES = ["_", "b_d", "br_rd", "brnrd", "bRnЯd"];
const FRAME_S = 0.19;
const FLICKER_S = 0.44;

export const Boot: React.FC<{ fps: number; total: number }> = ({ fps, total }) => {
  const frame = useCurrentFrame();
  const t = frame / fps;
  const idx = Math.min(FRAMES.length - 1, Math.floor(t / FRAME_S));
  const text = FRAMES[idx];
  const flickerStart = FRAME_S * (FRAMES.length - 1);
  const inFlicker = t >= flickerStart && t < flickerStart + FLICKER_S;
  // steps(2, jump-none) over 220 ms, twice: alternate every 110 ms
  const step = Math.floor((t - flickerStart) / 0.11) % 2;
  const shadow = inFlicker
    ? step === 0 ? "2px 0 #6fd3ff, -2px 0 #e8b34a" : "-2px 0 #6fd3ff, 2px 0 #e8b34a"
    : "0 0 18px rgba(217,164,65,0.35)";
  const dx = inFlicker && step === 1 ? 1 : 0;
  const urlIn = interpolate(frame, [Math.round((flickerStart + FLICKER_S) * fps) + 10, Math.round((flickerStart + FLICKER_S) * fps) + 40], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const out = interpolate(frame, [total - 24, total - 1], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  return (
    <AbsoluteFill style={{ background: "#07090B", alignItems: "center", justifyContent: "center", opacity: out }}>
      <div style={{ fontFamily: "ui-monospace, 'SF Mono', 'Cascadia Code', Menlo, Consolas, monospace", fontSize: 150, letterSpacing: "0.08em", color: "#f3e8d8", textShadow: shadow, transform: `translateX(${dx}px)`, whiteSpace: "pre" }}>
        {text}
      </div>
      <div style={{ marginTop: 28, fontFamily: "ui-monospace, Menlo, monospace", fontSize: 34, color: "#7CFF9B", opacity: urlIn * 0.9, textShadow: "0 0 14px rgba(124,255,155,0.5)", letterSpacing: 2 }}>brnrd.dev</div>
    </AbsoluteFill>
  );
};
