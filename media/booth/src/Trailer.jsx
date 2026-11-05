import {AbsoluteFill, OffthreadVideo, Sequence, interpolate, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import {Gif} from '@remotion/gif';
import QRCode from 'qrcode';

// The palette is the boot gif's: warm near-black, cream type, one molten accent.
const BG = '#0c0a08';
const FG = '#f2e8d8';
const DIM = '#8a8070';
const HOT = '#ff8a2a';
const MONO = 'Menlo, "DejaVu Sans Mono", monospace';
const SANS = '"Helvetica Neue", Helvetica, Arial, sans-serif';

export const frames = (p) => p.timeline.reduce((n, e) => n + Math.round(e.sec * p.fps), 0);

const fadeIn = (frame, n = 4) => interpolate(frame, [0, n], [0, 1], {extrapolateRight: 'clamp'});

const Caption = ({text}) => {
  const frame = useCurrentFrame();
  if (!text) return null;
  return (
    <div style={{position: 'absolute', left: 0, right: 0, bottom: 70, display: 'flex', justifyContent: 'center', opacity: fadeIn(frame, 3)}}>
      <div style={{background: 'rgba(12,10,8,0.82)', color: FG, fontFamily: SANS, fontWeight: 600, fontSize: 58,
        padding: '18px 40px', borderRadius: 14, letterSpacing: -0.5, maxWidth: 1600, textAlign: 'center'}}>
        {text}
      </div>
    </div>
  );
};

const QR = ({url, size}) => {
  const {modules} = QRCode.create(url, {errorCorrectionLevel: 'M'});
  const n = modules.size;
  const q = 2; // quiet zone, in modules
  const rects = [];
  for (let y = 0; y < n; y++) for (let x = 0; x < n; x++) {
    if (modules.get(y, x)) rects.push(<rect key={`${x}.${y}`} x={x + q} y={y + q} width={1.02} height={1.02} />);
  }
  return (
    <svg width={size} height={size} viewBox={`0 0 ${n + 2 * q} ${n + 2 * q}`} style={{background: FG, borderRadius: size / 18}}>
      <g fill={BG}>{rects}</g>
    </svg>
  );
};

// The booth render carries the way in on every frame: a visitor looks up at any second.
const Corner = ({url}) => (
  <div style={{position: 'absolute', top: 40, right: 40, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8}}>
    <QR url={url} size={150} />
    <div style={{fontFamily: MONO, fontSize: 24, color: FG, textShadow: '0 0 8px #000'}}>brnrd.dev</div>
  </div>
);

const Stamps = ({stamps, shown}) => {
  const frame = useCurrentFrame();
  return (
    <div style={{position: 'absolute', top: 60, left: 80, display: 'flex', gap: 34, fontFamily: MONO, fontSize: 40}}>
      {stamps.slice(0, shown).map(([t, what], i) => (
        <div key={i} style={{color: i === shown - 1 ? HOT : DIM, opacity: i === shown - 1 ? fadeIn(frame, 6) : 1}}>
          <span style={{color: i === shown - 1 ? HOT : FG}}>{t}</span> {what}
        </div>
      ))}
    </div>
  );
};

const Placeholder = ({e}) => (
  <AbsoluteFill style={{alignItems: 'center', justifyContent: 'center'}}>
    <div style={{width: e.phone ? 440 : 1500, height: e.phone ? 780 : 760, marginTop: e.phone ? -110 : -90, border: `4px dashed ${DIM}`, borderRadius: e.phone ? 48 : 18,
      display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 22, padding: 40, textAlign: 'center'}}>
      <div style={{fontFamily: MONO, fontSize: 34, color: HOT}}>raw/{e.raw}</div>
      <div style={{fontFamily: SANS, fontSize: e.phone ? 32 : 40, color: FG, maxWidth: 1200}}>{e.capture}</div>
      <div style={{fontFamily: MONO, fontSize: 24, color: DIM}}>placeholder · not recorded yet</div>
    </div>
  </AbsoluteFill>
);

const Terminal = ({lines, size = 34}) => {
  const frame = useCurrentFrame();
  return (
    <AbsoluteFill style={{alignItems: 'center', justifyContent: 'center'}}>
      <div style={{background: '#16130f', border: '2px solid #2a251e', borderRadius: 16, padding: '44px 54px', width: 1700,
        fontFamily: MONO, fontSize: size, lineHeight: 1.5, color: FG}}>
        {lines.map((l, i) => (
          <div key={i} style={{opacity: fadeIn(frame - i * 3, 3), color: l.startsWith('refused') ? HOT : l.startsWith('$') ? FG : '#d8ccb8', whiteSpace: 'pre-wrap'}}>{l}</div>
        ))}
      </div>
    </AbsoluteFill>
  );
};

const Shot = ({e, props}) => {
  if (!e.file && e.fallback === 'guard') return <Terminal lines={props.guard} size={30} />;
  if (!e.file) return <Placeholder e={e} />;
  const video = <OffthreadVideo src={staticFile(e.file)} muted />;
  if (!e.phone) return <AbsoluteFill style={{alignItems: 'center', justifyContent: 'center'}}>{video}</AbsoluteFill>;
  return (
    <AbsoluteFill style={{alignItems: 'center', justifyContent: 'center', background: `radial-gradient(circle at 50% 45%, #2a2016 0%, ${BG} 70%)`}}>
      <div style={{height: 800, marginTop: -110, borderRadius: 48, overflow: 'hidden', border: '6px solid #2a251e', boxShadow: '0 30px 80px rgba(0,0,0,0.6)'}}>
        <OffthreadVideo src={staticFile(e.file)} muted style={{height: '100%'}} />
      </div>
    </AbsoluteFill>
  );
};

const Pain = ({text, sec, fps}) => {
  const frame = useCurrentFrame();
  const typed = Math.round(interpolate(frame, [0, Math.min(12, sec * fps * 0.5)], [0, text.length], {extrapolateRight: 'clamp'}));
  return (
    <AbsoluteFill style={{alignItems: 'center', justifyContent: 'center'}}>
      <div style={{fontFamily: MONO, fontSize: 64, color: DIM, maxWidth: 1600, textAlign: 'center'}}>
        “{text.slice(0, typed)}{typed < text.length ? '▌' : ''}”
      </div>
    </AbsoluteFill>
  );
};

const Title = () => (
  <AbsoluteFill style={{alignItems: 'center', justifyContent: 'center'}}>
    <Gif src={staticFile('boot.gif')} width={1600} height={600} fit="contain" loopBehavior="pause-after-finish" playbackRate={1.25} />
  </AbsoluteFill>
);

const Prompt = ({e}) => {
  const frame = useCurrentFrame();
  return (
    <AbsoluteFill style={{alignItems: 'center', justifyContent: 'center', fontFamily: MONO}}>
      <div style={{width: 1500}}>
        <div style={{fontSize: 52, color: FG}}><span style={{color: HOT}}>›</span> {e.ask}</div>
        <div style={{marginTop: 50, opacity: fadeIn(frame - 18, 5), display: 'flex', alignItems: 'baseline', gap: 30}}>
          <span style={{fontSize: 70, color: HOT}}>{e.out[0]}</span>
          <span style={{fontSize: 36, color: DIM}}>{e.out[1]}</span>
        </div>
      </div>
    </AbsoluteFill>
  );
};

const Numbers = ({n, fps}) => {
  const frame = useCurrentFrame();
  const up = (v) => Math.round(interpolate(frame, [4, 2.2 * fps], [0, v], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'})).toLocaleString('en-US');
  const rows = [
    [n.commits, 'commits'],
    [n.resident, `by the resident · ${Math.round((100 * n.resident) / n.authored)}% of authored`],
    [n.prs, 'merged PRs'],
  ];
  return (
    <AbsoluteFill style={{alignItems: 'center', justifyContent: 'center', fontFamily: MONO}}>
      <div style={{display: 'flex', flexDirection: 'column', gap: 26, marginTop: -120}}>
        {rows.map(([v, label]) => (
          <div key={label} style={{display: 'flex', alignItems: 'baseline', gap: 36}}>
            <span style={{fontSize: 120, color: FG, width: 520, textAlign: 'right', fontVariantNumeric: 'tabular-nums'}}>{up(v)}</span>
            <span style={{fontSize: 48, color: DIM}}>{label}</span>
          </div>
        ))}
        <div style={{fontSize: 34, color: DIM, marginLeft: 556, opacity: fadeIn(frame - 2.2 * fps, 6)}}>since {n.since} · counted at {n.at}</div>
      </div>
    </AbsoluteFill>
  );
};

const Install = ({props}) => (
  <AbsoluteFill style={{alignItems: 'center', justifyContent: 'center'}}>
    <div style={{display: 'flex', alignItems: 'center', gap: 110, marginTop: -110}}>
      <div style={{fontFamily: MONO, fontSize: 44, color: FG, lineHeight: 1.9}}>
        {props.install.map((l) => <div key={l}><span style={{color: HOT}}>$</span> {l}</div>)}
      </div>
      <div style={{display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 16}}>
        <QR url={props.url} size={330} />
        <div style={{fontFamily: MONO, fontSize: 34, color: FG}}>brnrd.dev</div>
      </div>
    </div>
  </AbsoluteFill>
);

const End = ({pr}) => {
  const frame = useCurrentFrame();
  return (
    <AbsoluteFill style={{alignItems: 'center', justifyContent: 'center', gap: 30, opacity: fadeIn(frame, 6)}}>
      <div style={{fontFamily: SANS, fontWeight: 600, fontSize: 76, color: FG}}>This trailer was cut by the resident.</div>
      <div style={{fontFamily: MONO, fontSize: 40, color: DIM}}>{pr ? `github.com/hugimuni-labs/brnrd/pull/${pr}` : 'its PR: not opened yet'}</div>
    </AbsoluteFill>
  );
};

const Entry = ({e, props}) => {
  const {fps} = useVideoConfig();
  switch (e.kind) {
    case 'shot': return <Shot e={e} props={props} />;
    case 'pain': return <Pain text={e.text} sec={e.sec} fps={fps} />;
    case 'title': return <Title />;
    case 'prompt': return <Prompt e={e} />;
    case 'numbers': return <Numbers n={props.numbers} fps={fps} />;
    case 'install': return <Install props={props} />;
    case 'end': return <End pr={props.pr} />;
    default: return null;
  }
};

export const Trailer = (props) => {
  let from = 0;
  return (
    <AbsoluteFill style={{background: BG}}>
      {props.timeline.map((e, i) => {
        const len = Math.round(e.sec * props.fps);
        const at = from;
        from += len;
        const corner = props.variant === 'booth' && !['install', 'end'].includes(e.kind);
        return (
          <Sequence key={i} from={at} durationInFrames={len}>
            <AbsoluteFill style={{background: BG}}>
              <Entry e={e} props={props} />
              {e.stamps ? <Stamps stamps={props.stamps} shown={e.stamps} /> : null}
              <Caption text={e.caption} />
              {corner ? <Corner url={props.url} /> : null}
            </AbsoluteFill>
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};
