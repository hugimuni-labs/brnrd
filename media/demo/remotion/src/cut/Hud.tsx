import React from "react";
import { AbsoluteFill, Img, interpolate, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import { VIDEO_H, VIDEO_W, fps } from "./scenes";
import { cameraFrame } from "./Phone";
import { Callout, Marker, SceneV5 } from "./v5";

const mono = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace";
const PH = "#7CFF9B";
const AM = "#FFB347";
const RED = "#FF3B30";
// where the block starts on the 0:33 still (just under the HEDDLES row)
const GHOST_SRC_PY0 = 0.505;

const fade = (frame: number, f0: number, f1: number) =>
  interpolate(frame, [f0, f0 + 8, f1 - 8, f1], [0, 1, 1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });

// HUD callout: corner brackets around a label box by the phone's edge, a thin
// leader line to the point it names. Coordinates come from the same camera the
// footage is drawn with, so the line lands on the thing.
const CalloutBox: React.FC<{ c: Callout; scene: SceneV5 }> = ({ c, scene }) => {
  const frame = useCurrentFrame();
  const { width, height } = useVideoConfig();
  const f0 = Math.round(c.from * fps), f1 = Math.round(c.to * fps);
  if (frame < f0 || frame > f1) return null;
  const o = fade(frame, f0, f1);
  const cam = cameraFrame(scene, frame, width, height);
  const sx = cam.tx + c.px * VIDEO_W * cam.z;
  const sy = cam.ty + c.py * VIDEO_H * cam.z;
  const phoneLeft = cam.tx, phoneRight = cam.tx + VIDEO_W * cam.z;
  const boxW = 440, pad = 18;
  const color = c.amber ? AM : PH;
  const gap = 28;
  const bx = c.side === "left" ? Math.max(24, phoneLeft - gap - boxW) : Math.min(width - 24 - boxW, phoneRight + gap);
  const by = Math.min(height - 200, Math.max(40, sy - 40));
  const anchorX = c.side === "left" ? bx + boxW : bx;
  const grow = interpolate(frame, [f0, f0 + 12], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const lx = anchorX + (sx - anchorX) * grow;
  const ly = by + 40 + (sy - (by + 40)) * grow;
  const bracket = (x: number, y: number, dx: number, dy: number) => (
    <path d={`M ${x + dx * 16} ${y} L ${x} ${y} L ${x} ${y + dy * 16}`} stroke={color} strokeWidth={2.5} fill="none" />
  );
  return (
    <AbsoluteFill style={{ pointerEvents: "none", opacity: o }}>
      <svg width={width} height={height} style={{ position: "absolute", left: 0, top: 0 }}>
        <line x1={anchorX} y1={by + 40} x2={lx} y2={ly} stroke={color} strokeWidth={2} opacity={0.9} />
        <circle cx={lx} cy={ly} r={5} fill={color} />
        <circle cx={lx} cy={ly} r={11} fill="none" stroke={color} strokeWidth={1.5} opacity={0.7} />
      </svg>
      <div style={{ position: "absolute", left: bx, top: by, width: boxW, padding: pad, fontFamily: mono, fontSize: 30, lineHeight: 1.3, color, background: "rgba(7,9,11,0.78)", textShadow: `0 0 12px ${color}88`, letterSpacing: 0.3 }}>
        {c.text}
      </div>
      <svg width={width} height={height} style={{ position: "absolute", left: 0, top: 0 }}>
        {bracket(bx - 6, by - 6, 1, 1)}
        {bracket(bx + boxW + 6, by - 6, -1, 1)}
        {bracket(bx - 6, by + 40 + 26 * Math.ceil(c.text.length / 22) + 6, 1, -1)}
        {bracket(bx + boxW + 6, by + 40 + 26 * Math.ceil(c.text.length / 22) + 6, -1, -1)}
      </svg>
    </AbsoluteFill>
  );
};

// the red crossed triangle around the block, and the ghost of the block over the empty spot
const MarkerLayer: React.FC<{ m: Marker; scene: SceneV5 }> = ({ m, scene }) => {
  const frame = useCurrentFrame();
  const { width, height } = useVideoConfig();
  const f0 = Math.round(m.from * fps), f1 = Math.round(m.to * fps);
  if (frame < f0 || frame > f1) return null;
  const cam = cameraFrame(scene, frame, width, height);
  const x0 = cam.tx + m.px0 * VIDEO_W * cam.z, x1 = cam.tx + m.px1 * VIDEO_W * cam.z;
  const y0 = cam.ty + m.py0 * VIDEO_H * cam.z, y1 = cam.ty + m.py1 * VIDEO_H * cam.z;
  const o = fade(frame, f0, f1);
  if (m.kind === "triangle") {
    const draw = interpolate(frame, [f0, f0 + 20], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
    const pulse = 0.75 + 0.25 * Math.sin((frame - f0) / 6);
    const cx = (x0 + x1) / 2, w = (x1 - x0) * 1.08, hgt = (y1 - y0) * 1.15;
    const apexY = y0 - hgt * 0.22, baseY = y1 + hgt * 0.08;
    const tri = `M ${cx} ${apexY} L ${cx + w / 2} ${baseY} L ${cx - w / 2} ${baseY} Z`;
    const len = 3 * Math.max(w, hgt) * 1.5;
    return (
      <AbsoluteFill style={{ pointerEvents: "none", opacity: o * pulse }}>
        <svg width={width} height={height} style={{ position: "absolute", left: 0, top: 0 }}>
          <path d={tri} stroke={RED} strokeWidth={5} fill="rgba(255,59,48,0.06)" strokeDasharray={len} strokeDashoffset={len * (1 - draw)} style={{ filter: `drop-shadow(0 0 10px ${RED})` }} />
          <line x1={x0} y1={y0} x2={x1} y2={y1} stroke={RED} strokeWidth={5} opacity={draw} style={{ filter: `drop-shadow(0 0 8px ${RED})` }} />
          <line x1={x1} y1={y0} x2={x0} y2={y1} stroke={RED} strokeWidth={5} opacity={draw} style={{ filter: `drop-shadow(0 0 8px ${RED})` }} />
        </svg>
      </AbsoluteFill>
    );
  }
  // ghost: the block's own pixels from the 0:33 frame, laid over the empty spot, then struck and dissolved
  const ghostIn = interpolate(frame, [f0, f0 + 24], [0, 0.55], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const strike = interpolate(frame, [f0 + 40, f0 + 70], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const dissolve = interpolate(frame, [f0 + 90, f1 - 6], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  const bw = x1 - x0, bh = y1 - y0;
  // same fractions on the source still as on the current frame: the still,
  // scaled by the camera, translated so (px0, py0) lands at (x0, y0)
  return (
    <AbsoluteFill style={{ pointerEvents: "none", opacity: o * dissolve }}>
      <div style={{ position: "absolute", left: x0, top: y0, width: bw, height: bh, overflow: "hidden", opacity: ghostIn, filter: "saturate(0.6) contrast(1.1)", boxShadow: `0 0 30px ${RED}55` }}>
        <Img src={staticFile("stills/s_block.png")} style={{ position: "absolute", left: -m.px0 * VIDEO_W * cam.z, top: -GHOST_SRC_PY0 * VIDEO_H * cam.z, width: VIDEO_W * cam.z, height: VIDEO_H * cam.z }} />
      </div>
      <svg width={width} height={height} style={{ position: "absolute", left: 0, top: 0 }}>
        <line x1={x0} y1={y0} x2={x0 + (x1 - x0) * strike} y2={y0 + (y1 - y0) * strike} stroke={RED} strokeWidth={5} style={{ filter: `drop-shadow(0 0 8px ${RED})` }} />
        <line x1={x1} y1={y0} x2={x1 - (x1 - x0) * strike} y2={y0 + (y1 - y0) * strike} stroke={RED} strokeWidth={5} style={{ filter: `drop-shadow(0 0 8px ${RED})` }} />
      </svg>
    </AbsoluteFill>
  );
};

export const Hud: React.FC<{ scene: SceneV5 }> = ({ scene }) => (
  <>
    {(scene.markers ?? []).map((m, i) => <MarkerLayer key={`m${i}`} m={m} scene={scene} />)}
    {(scene.callouts ?? []).map((c, i) => <CalloutBox key={`c${i}`} c={c} scene={scene} />)}
  </>
);
