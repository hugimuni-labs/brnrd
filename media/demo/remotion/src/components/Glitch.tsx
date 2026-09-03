import React from "react";
import { AbsoluteFill, useCurrentFrame } from "remotion";
import { runeGlyphs } from "../data/beats";

/**
 * Chromatic-aberration split (red/cyan drop-shadow duplicates, the classic
 * CSS silhouette trick — cheap and correct for text/shape content) plus a
 * scatter of rune glyphs as transition grain. `intensity` is 0..1 and drives
 * both the split offset and the grain opacity; callers key it off
 * `interpolate` around a transition frame so it flashes rather than sits.
 */
export const Glitch: React.FC<{
  intensity: number;
  seed?: number;
  grain?: boolean;
  children: React.ReactNode;
}> = ({ intensity, seed = 0, grain = true, children }) => {
  const frame = useCurrentFrame();
  const offset = 6 * intensity;

  return (
    <AbsoluteFill>
      <AbsoluteFill
        style={{
          filter:
            intensity > 0.01
              ? `drop-shadow(${-offset}px 0 0 rgba(255,59,48,0.65)) drop-shadow(${offset}px 0 0 rgba(58,216,230,0.65))`
              : undefined,
        }}
      >
        {children}
      </AbsoluteFill>
      {grain && intensity > 0.01 ? (
        <AbsoluteFill style={{ pointerEvents: "none" }}>
          {Array.from({ length: 36 }).map((_, i) => {
            const hash = (n: number) => {
              const x = Math.sin(n * 12.9898 + seed * 78.233) * 43758.5453;
              return x - Math.floor(x);
            };
            const left = hash(i) * 100;
            const top = hash(i + 100) * 100;
            const flicker = hash(i + frame) > 0.5 ? 1 : 0;
            const glyph = runeGlyphs[i % runeGlyphs.length];
            return (
              <div
                key={i}
                style={{
                  position: "absolute",
                  left: `${left}%`,
                  top: `${top}%`,
                  color: i % 2 === 0 ? "#7CFF9B" : "#FFB347",
                  fontSize: 14 + hash(i + 50) * 22,
                  opacity: intensity * 0.7 * flicker,
                  transform: `rotate(${hash(i + 200) * 40 - 20}deg)`,
                  textShadow: "0 0 6px currentColor",
                }}
              >
                {glyph}
              </div>
            );
          })}
        </AbsoluteFill>
      ) : null}
    </AbsoluteFill>
  );
};
