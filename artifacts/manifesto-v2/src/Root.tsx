import React from 'react';
import {Composition} from 'remotion';
import {Film, FPS} from './Film';
import {DURATION_F} from './cut';
export const Root: React.FC = () => (
  <>
    <Composition id="Film" component={Film} durationInFrames={DURATION_F} fps={FPS} width={1920} height={1080} defaultProps={{muted: false}} />
  </>
);
