import {Composition, registerRoot} from 'remotion';
import {ChatShot, shotFrames} from './Chat';
import script from './script.json';

// One composition per phone shot, by key: `npx remotion render src/gen/chat/index.jsx a1-ask out.mp4`.
// The trailer can instead import ChatShot and place <ChatShot shot="a1-ask" sec={1.5} /> in its own timeline.
const Root = () => (
  <>
    {script.shots.map((s) => (
      <Composition
        key={s.key}
        id={s.key}
        component={ChatShot}
        defaultProps={{shot: s.key}}
        fps={script.fps}
        width={script.width}
        height={script.height}
        durationInFrames={shotFrames(s.key)}
        calculateMetadata={({props}) => ({durationInFrames: shotFrames(props.shot, props.sec)})}
      />
    ))}
  </>
);

registerRoot(Root);
