import React from "react";
import { Img, interpolate, staticFile, useCurrentFrame } from "remotion";

/**
 * The brnrd mark, with a bloom. Source: media/brand/mark-screen.svg — the
 * "screen register" variant (near-black rounded-square ground, the
 * five-slot glyph already rendered as a red/cyan chromatic-aberration pair
 * plus a white core layer), copied verbatim into public/. It already reads
 * as this deck's glitch aesthetic; no recolor needed.
 *
 * `bloomAtFrame` (optional) makes the glow bloom once — a fast rise, slow
 * settle — instead of holding a static glow.
 */
export const Mark: React.FC<{
  size?: number;
  bloomAtFrame?: number;
  baseOpacity?: number;
  style?: React.CSSProperties;
}> = ({ size = 220, bloomAtFrame, baseOpacity = 0.9, style }) => {
  const frame = useCurrentFrame();

  const bloom =
    bloomAtFrame === undefined
      ? 0.4
      : interpolate(
          frame,
          [bloomAtFrame - 4, bloomAtFrame + 6, bloomAtFrame + 40],
          [0.15, 1, 0.35],
          { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
        );

  return (
    <div
      style={{
        width: size,
        height: size,
        opacity: baseOpacity,
        filter: `drop-shadow(0 0 ${18 * bloom}px rgba(124,255,155,${0.55 * bloom})) drop-shadow(0 0 ${44 * bloom}px rgba(58,216,230,${0.35 * bloom}))`,
        ...style,
      }}
    >
      <Img src={staticFile("mark-screen.svg")} style={{ width: "100%", height: "100%" }} />
    </div>
  );
};
