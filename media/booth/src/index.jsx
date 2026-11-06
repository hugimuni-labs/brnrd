import {Composition, registerRoot} from 'remotion';
import {Trailer, cast, frames} from './Trailer';

// cut.py writes the props (the timeline with the shots it found, the numbers
// from git, the PR) and passes them with --props; the defaults only let the
// studio open before a first cut.
const fallback = {variant: 'booth', fps: 30, width: 1920, height: 1080, timeline: [{kind: 'end', sec: 2}]};

const Root = () => (
  <>
    {['booth', 'readme'].map((variant) => (
      <Composition
        key={variant}
        id={variant}
        component={Trailer}
        defaultProps={{...fallback, variant}}
        calculateMetadata={({props}) => {
          const p = cast(props, variant);
          return {fps: p.fps, width: p.width, height: p.height, durationInFrames: frames(p), props: p};
        }}
      />
    ))}
  </>
);

registerRoot(Root);
