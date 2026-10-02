import React, {useLayoutEffect, useRef} from 'react';
import {AbsoluteFill, Audio, continueRender, delayRender, staticFile, useCurrentFrame, getRemotionEnvironment} from 'remotion';
import {W, H, off, ctx2} from './engine';
import {drawFrame, finish, mbSamples} from './scenes';
import {FONTS_READY} from './fonts';
export const FPS = 30;
function render(ctx: CanvasRenderingContext2D, t: number) {
  const n = mbSamples(t);
  if (n <= 1) drawFrame(ctx, t);
  else {
    // shutter: average n sub-frames across ~0.7 of a frame
    const acc = off('mbacc'), ac = ctx2(acc);
    for (let i = 0; i < n; i++) {
      const tt = t - (0.7 / FPS) * (i / n);
      if (i === 0) { drawFrame(ctx, tt); continue; }
      ac.setTransform(1, 0, 0, 1, 0, 0); ac.globalAlpha = 1; ac.globalCompositeOperation = 'source-over';
      drawFrame(ac, tt);
      ctx.save(); ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.globalAlpha = 1 / (i + 1); ctx.globalCompositeOperation = 'source-over'; ctx.drawImage(acc, 0, 0); ctx.restore();
    }
  }
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  finish(ctx, t);
}
export const Film: React.FC<{muted?: boolean}> = ({muted}) => {
  const frame = useCurrentFrame();
  const ref = useRef<HTMLCanvasElement>(null);
  useLayoutEffect(() => {
    const h = delayRender('draw ' + frame);
    FONTS_READY.then(() => {
      const c = ref.current!; const ctx = c.getContext('2d')!;
      render(ctx, frame / FPS);
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
