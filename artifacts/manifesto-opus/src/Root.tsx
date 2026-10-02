import React from 'react';
import {Composition} from 'remotion';
import {Film, FPS} from './Film';
import {DURATION} from './scenes';
export const Root: React.FC = () => (
  <>
    <Composition id="Film" component={Film} durationInFrames={DURATION * FPS} fps={FPS} width={1920} height={1080} defaultProps={{muted: false}} />
  </>
);
