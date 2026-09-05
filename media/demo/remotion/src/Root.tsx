import React from "react";
import { Composition } from "remotion";
import { Demo } from "./Demo";
import { durationInFrames, fps } from "./data/beats";
import { Cut, CutShort, CutV5, CutShortV6 } from "./cut/Cut";
import { totalFrames, totalFramesShort, fps as cutFps } from "./cut/scenes";
import { totalFramesV5, totalFramesShortV6 } from "./cut/v5";

export const RemotionRoot: React.FC = () => {
  return (
    <>
    <Composition id="Cut" component={Cut} durationInFrames={totalFrames} fps={cutFps} width={1920} height={1080} />
    <Composition id="CutV5" component={CutV5} durationInFrames={totalFramesV5} fps={cutFps} width={1920} height={1080} />
    <Composition id="CutShortV6" component={CutShortV6} durationInFrames={totalFramesShortV6} fps={cutFps} width={1920} height={1080} />
    <Composition id="CutShort" component={CutShort} durationInFrames={totalFramesShort} fps={cutFps} width={1920} height={1080} />
    <Composition
      id="Demo"
      component={Demo}
      durationInFrames={durationInFrames}
      fps={fps}
      width={1920}
      height={1080}
    />
    </>
  );
};
