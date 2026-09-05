import React from "react";
import { interpolate, useCurrentFrame } from "remotion";
import { Typewriter } from "./Typewriter";
import { palette } from "../data/beats";

/**
 * A second, dimmer terminal window — the strand card. Unfolds beside the
 * primary window in beat 3; in beat 4 a steer line lands inside it as a
 * light pulse (no restart — the fold is visual, not a re-open).
 */
export const StrandWindow: React.FC<{
  appearFrame: number;
  branch: string;
  runner: string;
  specQuote: string;
  specQuoteStartFrame: number;
  steerText?: string;
  steerTimestamp?: string;
  steerAppearFrame?: number;
}> = ({
  appearFrame,
  branch,
  runner,
  specQuote,
  specQuoteStartFrame,
  steerText,
  steerTimestamp,
  steerAppearFrame,
}) => {
  const frame = useCurrentFrame();
  if (frame < appearFrame) return null;

  const unfold = interpolate(frame, [appearFrame, appearFrame + 20], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  const pulse =
    steerAppearFrame !== undefined
      ? interpolate(
          frame,
          [steerAppearFrame - 6, steerAppearFrame, steerAppearFrame + 24],
          [0, 1, 0],
          { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
        )
      : 0;

  return (
    <div
      style={{
        position: "absolute",
        right: 80,
        bottom: 200,
        width: 760,
        transform: `translateX(${(1 - unfold) * 60}px) scale(${0.9 + unfold * 0.1})`,
        opacity: unfold,
        background: "rgba(10,13,11,0.88)",
        border: `1px solid ${palette.phosphor}44`,
        borderRadius: 10,
        padding: "20px 24px",
        boxShadow: pulse > 0.01 ? `0 0 ${40 * pulse}px ${palette.phosphor}` : "0 8px 26px rgba(0,0,0,0.5)",
        fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
      }}
    >
      <div style={{ display: "flex", gap: 8, marginBottom: 14 }}>
        <div style={{ width: 10, height: 10, borderRadius: 5, background: "#FF5F56" }} />
        <div style={{ width: 10, height: 10, borderRadius: 5, background: "#FFBD2E" }} />
        <div style={{ width: 10, height: 10, borderRadius: 5, background: "#27C93F" }} />
      </div>
      <div style={{ color: palette.phosphor, fontSize: 22, opacity: 0.85, marginBottom: 6 }}>
        {branch} <span style={{ opacity: 0.55 }}>· {runner}</span>
      </div>
      <div
        style={{
          color: palette.phosphor,
          opacity: 0.55,
          fontSize: 16,
          lineHeight: 1.5,
          marginTop: 10,
        }}
      >
        <Typewriter
          text={`"${specQuote}"`}
          startFrame={specQuoteStartFrame}
          charsPerSecond={70}
          color={palette.phosphor}
          fontSize={16}
          style={{ opacity: 0.7 }}
        />
      </div>
      {steerText && steerAppearFrame !== undefined ? (
        <div
          style={{
            marginTop: 18,
            paddingTop: 14,
            borderTop: `1px solid ${palette.phosphor}33`,
          }}
        >
          <Typewriter
            text={`to: ${steerText}`}
            startFrame={steerAppearFrame}
            charsPerSecond={30}
            color={palette.amber}
            fontSize={18}
          />
          {steerTimestamp ? (
            <div style={{ marginTop: 4 }}>
              <Typewriter
                text={steerTimestamp}
                startFrame={steerAppearFrame + 30}
                charsPerSecond={18}
                color={palette.amber}
                fontSize={14}
                style={{ opacity: 0.6 }}
              />
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
};
