import React from "react";
import { interpolate, useCurrentFrame, useVideoConfig } from "remotion";
import { Typewriter } from "./Typewriter";
import { palette } from "../data/beats";

const CHANNEL_STYLE = {
  whatsapp: {
    bg: "#1c3a2e",
    border: "rgba(124,255,155,0.55)",
    text: palette.phosphor,
    label: "whatsapp",
  },
  telegram: {
    bg: "#3a2c14",
    border: "rgba(255,179,71,0.65)",
    text: palette.amber,
    label: "telegram",
  },
} as const;

/**
 * A chat bubble: WhatsApp (phosphor-green) or Telegram (amber), with the
 * UTC timestamp typing itself after the body text.
 */
export const Bubble: React.FC<{
  channel: keyof typeof CHANNEL_STYLE;
  text: string;
  timestamp: string;
  appearFrame: number;
  charsPerSecond?: number;
  x?: number | string;
  y?: number | string;
  width?: number;
}> = ({
  channel,
  text,
  timestamp,
  appearFrame,
  charsPerSecond = 34,
  x = "50%",
  y = "50%",
  width = 900,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const style = CHANNEL_STYLE[channel];

  const materialize = interpolate(frame, [appearFrame, appearFrame + 14], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  if (frame < appearFrame) return null;

  const bodyStart = appearFrame;
  const bodyDurationFrames = Math.ceil((text.length / charsPerSecond) * fps);
  const timestampStart = bodyStart + bodyDurationFrames + Math.round(fps * 0.15);

  return (
    <div
      style={{
        position: "absolute",
        left: x,
        top: y,
        transform: `translate(-50%, -50%) scale(${0.94 + materialize * 0.06})`,
        opacity: materialize,
        width,
        maxWidth: "80%",
        background: style.bg,
        border: `1px solid ${style.border}`,
        borderRadius: 18,
        padding: "22px 26px",
        boxShadow: `0 0 40px ${style.border}, 0 12px 30px rgba(0,0,0,0.5)`,
      }}
    >
      <div
        style={{
          fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
          fontSize: 15,
          letterSpacing: 1,
          textTransform: "uppercase",
          color: style.text,
          opacity: 0.65,
          marginBottom: 10,
        }}
      >
        {style.label}
      </div>
      <Typewriter
        text={text}
        startFrame={bodyStart}
        charsPerSecond={charsPerSecond}
        color={style.text}
        fontSize={26}
      />
      <div style={{ marginTop: 12 }}>
        <Typewriter
          text={timestamp}
          startFrame={timestampStart}
          charsPerSecond={18}
          color={style.text}
          fontSize={16}
          style={{ opacity: 0.7 }}
        />
      </div>
    </div>
  );
};
