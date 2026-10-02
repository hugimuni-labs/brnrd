import React, {useLayoutEffect, useRef} from 'react';
import {AbsoluteFill, Audio, continueRender, delayRender, staticFile, useCurrentFrame} from 'remotion';
import {W, H} from './engine';
import {drawCut, finishCut} from './cut';
import {FONTS_READY} from './fonts';
import {IMGS_READY} from './assets';
export const FPS = 30;
const READY = Promise.all([FONTS_READY, IMGS_READY]);
// No sub-frame motion blur: the cut is frame-exact (1–6 frame inserts), and averaging across a hard cut would ghost it.
export const Film: React.FC<{muted?: boolean}> = ({muted}) => {
  const frame = useCurrentFrame();
  const ref = useRef<HTMLCanvasElement>(null);
  useLayoutEffect(() => {
    const h = delayRender('draw ' + frame);
    READY.then(() => {
      const ctx = ref.current!.getContext('2d')!;
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      drawCut(ctx, frame);
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      finishCut(ctx, frame);
      continueRender(h);
    });
  }, [frame]);
  return (
    <AbsoluteFill style={{background: '#000'}}>
      <canvas ref={ref} width={W} height={H} style={{width: '100%', height: '100%'}} />
      {!muted && <Audio src={staticFile('score.wav')} />}
    </AbsoluteFill>
  );
};
