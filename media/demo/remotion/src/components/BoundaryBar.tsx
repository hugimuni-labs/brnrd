import React from "react";
import { interpolate, useCurrentFrame } from "remotion";
import { palette } from "../data/beats";

/**
 * The `⌁[b·_·d]: ⏱ 0m │ q S82 │ pending 1` bar at the bottom. Slides in
 * from below at `appearFrame`. `tickTimes` (ISO strings, real boundary
 * timestamps) is accepted for later beats that accelerate the ticker;
 * beats 1-4 pass a single-element array and the bar just holds text.
 */
export const BoundaryBar: React.FC<{
  text: string;
  appearFrame: number;
  tickTimes?: string[];
}> = ({ text, appearFrame }) => {
  const frame = useCurrentFrame();
  const slide = interpolate(frame, [appearFrame, appearFrame + 18], [40, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const opacity = interpolate(frame, [appearFrame, appearFrame + 10], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  if (frame < appearFrame) return null;

  return (
    <div
      style={{
        position: "absolute",
        left: 0,
        right: 0,
        bottom: 0,
        transform: `translateY(${slide}px)`,
        opacity,
        padding: "14px 40px",
        background: "rgba(7,9,11,0.92)",
        borderTop: `1px solid ${palette.phosphor}55`,
        fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
        fontSize: 22,
        color: palette.phosphor,
        letterSpacing: 0.5,
        textShadow: `0 0 10px ${palette.phosphor}88`,
      }}
    >
      {text}
    </div>
  );
};
