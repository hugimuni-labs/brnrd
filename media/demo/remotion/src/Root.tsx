import React from "react";
import { Composition } from "remotion";
import { Demo } from "./Demo";
import { durationInFrames, fps } from "./data/beats";

export const RemotionRoot: React.FC = () => {
  return (
    <Composition
      id="Demo"
      component={Demo}
      durationInFrames={durationInFrames}
      fps={fps}
      width={1920}
      height={1080}
    />
  );
};
