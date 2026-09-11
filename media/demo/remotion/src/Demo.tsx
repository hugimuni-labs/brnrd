import React from "react";
import { AbsoluteFill, interpolate, useCurrentFrame } from "remotion";
import { Crt } from "./components/Crt";
import { Glitch } from "./components/Glitch";
import { Typewriter } from "./components/Typewriter";
import { Bubble } from "./components/Bubble";
import { BoundaryBar } from "./components/BoundaryBar";
import { StrandWindow } from "./components/StrandWindow";
import { Mark } from "./components/Mark";
import { beat1, beat2, beat3, beat4, beatFrames, palette } from "./data/beats";

const triangle = (frame: number, center: number, halfWidth: number) =>
  interpolate(frame, [center - halfWidth, center, center + halfWidth], [0, 1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

export const Demo: React.FC = () => {
  const frame = useCurrentFrame();

  // Rune-static glitch-in for beat 1's opening, plus a quick chromatic
  // spike at each beat transition.
  const introDecay = interpolate(frame, [0, 26], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const transitionSpike = Math.max(
    triangle(frame, beatFrames.beat2[0], 5),
    triangle(frame, beatFrames.beat3[0], 5),
    triangle(frame, beatFrames.beat4[0], 5),
  );
  const glitchIntensity = Math.max(introDecay, transitionSpike * 0.85);

  // Beat 4: the ask bubble's glow travelling into the strand window as a
  // light pulse, arriving as the `to:` steer lands.
  const bubble2AppearFrame = beatFrames.beat4[0] + 6;
  const steerAppearFrame = beatFrames.beat4[0] + 140; // lands well inside beat 4's span
  const pulseStart = bubble2AppearFrame + 40;
  const pulseEnd = steerAppearFrame;
  const pulseProgress = interpolate(frame, [pulseStart, pulseEnd], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const pulseVisible = frame >= pulseStart && frame <= pulseEnd + 6;

  return (
    <AbsoluteFill style={{ background: palette.background }}>
      <Glitch intensity={glitchIntensity} seed={1}>
        <AbsoluteFill>
          {/* Beat 1 — the ask, materialising from glyph noise; fades once
              the wake (beat 2) takes the frame, so it never collides with
              the strand card or the beat-4 bubble sharing its screen area */}
          {frame < beatFrames.beat2[0] + 24 ? (
            <div
              style={{
                opacity: interpolate(
                  frame,
                  [beatFrames.beat2[0], beatFrames.beat2[0] + 20],
                  [1, 0],
                  { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
                ),
              }}
            >
              <Bubble
                channel={beat1.channel}
                text={beat1.text}
                timestamp={beat1.timestamp}
                appearFrame={beatFrames.beat1[0]}
                x="50%"
                y="30%"
                width={1160}
              />
            </div>
          ) : null}

          {/* Beat 2 — the mark blooms once behind the bar; .name types */}
          {frame >= beatFrames.beat2[0] ? (
            <div
              style={{
                position: "absolute",
                left: "50%",
                bottom: 190,
                transform: "translateX(-50%)",
              }}
            >
              <Mark size={160} bloomAtFrame={beatFrames.beat2[0]} baseOpacity={0.5} />
            </div>
          ) : null}
          {frame >= beatFrames.beat2[0] + 14 ? (
            <div
              style={{
                position: "absolute",
                left: 80,
                top: 90,
                fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
                fontSize: 22,
                color: palette.phosphor,
                opacity: 0.85,
              }}
            >
              .name:{" "}
              <Typewriter
                text={`"${beat2.name}"`}
                startFrame={beatFrames.beat2[0] + 14}
                charsPerSecond={26}
                fontSize={22}
              />
            </div>
          ) : null}

          {/* Beat 3 — spawn: the strand card unfolds as a second, dimmer window */}
          <StrandWindow
            appearFrame={beatFrames.beat3[0]}
            branch={beat3.branch}
            runner={beat3.runner}
            specQuote={beat3.specQuote}
            specQuoteStartFrame={beatFrames.beat3[0] + 24}
            steerText={frame >= beatFrames.beat4[0] ? beat4.steerText : undefined}
            steerTimestamp={beat4.steerTimestamp}
            steerAppearFrame={frame >= beatFrames.beat4[0] ? steerAppearFrame : undefined}
          />

          {/* Beat 4 — the interactivity beat: a second ask, then the fold */}
          {frame >= beatFrames.beat4[0] ? (
            <Bubble
              channel={beat4.channel}
              text={beat4.bubbleText}
              timestamp={beat4.bubbleTimestamp}
              appearFrame={bubble2AppearFrame}
              x="34%"
              y="30%"
              width={880}
            />
          ) : null}

          {pulseVisible ? (
            <div
              style={{
                position: "absolute",
                left: `${34 + pulseProgress * (100 - 34) * 0.72}%`,
                top: `${30 + pulseProgress * 40}%`,
                width: 26,
                height: 26,
                borderRadius: 13,
                transform: "translate(-50%, -50%)",
                background: palette.phosphor,
                boxShadow: `0 0 ${30 + 30 * (1 - Math.abs(0.5 - pulseProgress) * 2)}px ${palette.phosphor}`,
                opacity: 0.9,
              }}
            />
          ) : null}

          {/* Boundary bar — present from beat 2 onward */}
          <BoundaryBar text={beat2.barText} appearFrame={beatFrames.beat2[0]} />
        </AbsoluteFill>
      </Glitch>
      <Crt />
    </AbsoluteFill>
  );
};
