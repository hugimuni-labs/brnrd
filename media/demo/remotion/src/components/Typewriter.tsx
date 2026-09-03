import React from "react";
import { useCurrentFrame, useVideoConfig } from "remotion";

/**
 * Monospace text typing itself, one character per tick, with a blinking
 * phosphor cursor. `startFrame` is when the first character appears;
 * `charsPerSecond` sets the rate. Once fully typed the cursor keeps
 * blinking (a live terminal, not a finished caption).
 */
export const Typewriter: React.FC<{
  text: string;
  startFrame: number;
  charsPerSecond?: number;
  color?: string;
  fontSize?: number;
  cursorColor?: string;
  style?: React.CSSProperties;
}> = ({
  text,
  startFrame,
  charsPerSecond = 28,
  color = "#7CFF9B",
  fontSize = 28,
  cursorColor,
  style,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const elapsedFrames = Math.max(0, frame - startFrame);
  const charsShown = Math.min(
    text.length,
    Math.floor((elapsedFrames / fps) * charsPerSecond),
  );
  const shown = text.slice(0, charsShown);
  const blinkOn = Math.floor(frame / (fps * 0.4)) % 2 === 0;
  const visible = frame >= startFrame;

  return (
    <span
      style={{
        fontFamily:
          '"JetBrains Mono", "Berkeley Mono", ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
        color,
        fontSize,
        whiteSpace: "pre-wrap",
        ...style,
      }}
    >
      {visible ? shown : ""}
      {visible ? (
        <span
          style={{
            display: "inline-block",
            width: "0.55em",
            height: "1em",
            marginLeft: 2,
            transform: "translateY(0.15em)",
            background: cursorColor ?? color,
            opacity: blinkOn ? 0.9 : 0,
            boxShadow: `0 0 8px ${cursorColor ?? color}`,
          }}
        />
      ) : null}
    </span>
  );
};
