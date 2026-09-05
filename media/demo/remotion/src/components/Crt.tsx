import React from "react";
import { AbsoluteFill } from "remotion";

/**
 * Scanlines + slight barrel (via a radial vignette that darkens the corners
 * and edges, reading as lens curvature without an actual pixel warp) +
 * vignette overlay. Sits above scene content, pointer-events none.
 */
export const Crt: React.FC = () => {
  return (
    <AbsoluteFill style={{ pointerEvents: "none" }}>
      {/* scanlines */}
      <AbsoluteFill
        style={{
          backgroundImage:
            "repeating-linear-gradient(to bottom, rgba(0,0,0,0.0) 0px, rgba(0,0,0,0.16) 1px, rgba(0,0,0,0.0) 2px, rgba(0,0,0,0.0) 3px)",
          mixBlendMode: "multiply",
          opacity: 0.55,
        }}
      />
      {/* faint phosphor glow banding, offset from the scanlines */}
      <AbsoluteFill
        style={{
          backgroundImage:
            "repeating-linear-gradient(to bottom, rgba(124,255,155,0.02) 0px, rgba(124,255,155,0.02) 1px, transparent 1px, transparent 4px)",
          mixBlendMode: "screen",
        }}
      />
      {/* barrel-read vignette: darker, slightly desaturated corners */}
      <AbsoluteFill
        style={{
          background:
            "radial-gradient(ellipse 78% 78% at 50% 50%, rgba(0,0,0,0) 55%, rgba(0,0,0,0.35) 88%, rgba(0,0,0,0.72) 100%)",
        }}
      />
      {/* screen edge frame, a hair of curvature at the very border */}
      <AbsoluteFill
        style={{
          boxShadow: "inset 0 0 140px rgba(0,0,0,0.55)",
        }}
      />
    </AbsoluteFill>
  );
};
