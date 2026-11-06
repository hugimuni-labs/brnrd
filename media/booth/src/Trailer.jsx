import {AbsoluteFill, Easing, Img, OffthreadVideo, Sequence, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import QRCode from 'qrcode';

// The footage is the stage, the graphics live inside it (script.md, draft 2): no full-frame card between
// shots, the camera is the narrator, every effect is a per-frame transform here. ffmpeg only normalizes the
// recordings and finishes the file.

// The palette is the boot gif's: warm near-black, cream type, one molten accent.
const BG = '#0c0a08';
const FG = '#f2e8d8';
const DIM = '#8a8070';
const HOT = '#ff8a2a';
const MONO = 'Menlo, "DejaVu Sans Mono", monospace';
const SANS = '"Helvetica Neue", Helvetica, Arial, sans-serif';
const W = 1920;
const H = 1080;
const EASE = Easing.bezier(0.45, 0, 0.2, 1);
const clamp = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'};

export const frames = (p) => p.timeline.reduce((n, e) => n + Math.round(e.sec * p.fps), 0);

const lerp = (frame, a, b, from, to, easing = EASE) => interpolate(frame, [a, b], [from, to], {...clamp, easing});

// ---- type -------------------------------------------------------------------------------------------------

// A lower third: one line, types on over 8 frames, slides out with the shot's last 6.
const Lower = ({text, len}) => {
  const frame = useCurrentFrame();
  if (!text) return null;
  const n = Math.round(lerp(frame, 0, 8, 0, text.length, Easing.linear));
  const out = lerp(frame, len - 6, len, 0, -60);
  return (
    <div style={{position: 'absolute', left: 90, bottom: 84, transform: `translateX(${out}px)`, opacity: n === 0 ? 0 : lerp(frame, len - 6, len, 1, 0)}}>
      <div style={{background: 'rgba(12,10,8,0.86)', borderLeft: `6px solid ${HOT}`, color: FG, fontFamily: SANS, fontWeight: 600,
        fontSize: 60, padding: '16px 34px', letterSpacing: -0.5, whiteSpace: 'pre'}}>
        {text.slice(0, n)}<span style={{opacity: 0}}>{text.slice(n)}</span>
      </div>
    </div>
  );
};

// The stamp strip along the bottom of act 1's last shot: one real minute from the ledger per beat.
const Stamps = ({stamps, fps}) => {
  const frame = useCurrentFrame();
  const beat = fps / 2;
  return (
    <div style={{position: 'absolute', left: 90, right: 90, bottom: 84, display: 'flex', gap: 46, fontFamily: MONO, fontSize: 44,
      background: 'rgba(12,10,8,0.86)', padding: '16px 34px', width: 'fit-content'}}>
      {stamps.map(([t, what], i) => {
        const at = 4 + i * beat;
        const live = frame >= at && frame < at + beat;
        return (
          <div key={i} style={{opacity: lerp(frame, at, at + 4, 0, 1), transform: `translateY(${lerp(frame, at, at + 6, 14, 0)}px)`}}>
            <span style={{color: live ? HOT : FG}}>{t}</span> <span style={{color: DIM}}>{what}</span>
          </div>
        );
      })}
    </div>
  );
};

// ---- the QR -----------------------------------------------------------------------------------------------

const QR = ({url, size}) => {
  const {modules} = QRCode.create(url, {errorCorrectionLevel: 'M'});
  const n = modules.size;
  const q = 2;
  const rects = [];
  for (let y = 0; y < n; y++) for (let x = 0; x < n; x++) {
    if (modules.get(y, x)) rects.push(<rect key={`${x}.${y}`} x={x + q} y={y + q} width={1.02} height={1.02} />);
  }
  return (
    <svg width={size} height={size} viewBox={`0 0 ${n + 2 * q} ${n + 2 * q}`} style={{background: FG, borderRadius: size / 18, display: 'block'}}>
      <g fill={BG}>{rects}</g>
    </svg>
  );
};

// The corner QR's place, bottom right where no board or GitHub header sits; the install shot's QR grows from
// here, and the booth loop returns to it.
const CORNER = {x: W - 40 - 150, y: H - 40 - 150 - 34, size: 150};

const Corner = ({url, fade}) => {
  const frame = useCurrentFrame();
  return (
    // on a dark plate: the board's footer under it is near white
    <div style={{position: 'absolute', left: CORNER.x - 10, top: CORNER.y - 10, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6,
      padding: '10px 10px 6px', borderRadius: 14, background: 'rgba(12,10,8,0.9)', opacity: fade ? lerp(frame, 15, 27, 0, 1) : 1}}>
      <QR url={url} size={CORNER.size} />
      <div style={{fontFamily: MONO, fontSize: 24, color: FG}}>brnrd.dev</div>
    </div>
  );
};

// ---- the board at 12 %: under the phone, the title and the loop seam --------------------------------------

const Backdrop = ({props, opacity = 0.12, blur = 6}) => {
  const b = props.backdrop;
  if (!b) return null;
  return (
    <AbsoluteFill style={{overflow: 'hidden', opacity}}>
      <Img src={staticFile(b.file)} style={{position: 'absolute', height: H, left: (W - (b.size[0] / b.size[1]) * H) / 2, filter: `blur(${blur}px)`}} />
    </AbsoluteFill>
  );
};

// ---- camera -----------------------------------------------------------------------------------------------

// A key is [second, x, y, scale]: the image point (css px) at the frame centre. x and y may be 'f' (the
// subject's centre, from capture.py's focus box) or 'c' (the image centre). The camera never shows past
// the image's edge.
const camera = (keys, frame, fps, img) => {
  const pt = (v, axis) => (v === 'f' ? img.focus[axis] : v === 'c' ? img[axis ? 'h' : 'w'] / 2 : v);
  const ks = keys.map(([t, x, y, s]) => [t * fps, pt(x, 0), pt(y, 1), s]);
  let i = 0;
  while (i < ks.length - 2 && frame > ks[i + 1][0]) i++;
  const [a, b] = ks.length > 1 ? [ks[i], ks[i + 1]] : [ks[0], ks[0]];
  const k = (j) => (a[0] === b[0] ? a[j] : lerp(frame, a[0], b[0], a[j], b[j]));
  let [x, y, s] = [k(1), k(2), k(3)];
  const fit = (v, span, size) => (size * s >= span ? Math.min(Math.max(v, span / (2 * s)), size - span / (2 * s)) : size / 2);
  return {x: fit(x, W, img.w), y: fit(y, H, img.h), s};
};

const place = ({x, y, s}) => `translate(${W / 2 - x * s}px, ${H / 2 - y * s}px) scale(${s})`;

// A still from capture.py under the camera, with the shot's move, in and out transitions, ring, echo and flash.
const Still = ({e, props}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const len = Math.round(e.sec * fps);
  const sh = props.shots[e.still];
  if (!sh) return <Missing e={e} what={`shots/${e.still}.png`} note="still not captured" />;
  const fb = sh.focus ? sh.focus.map((v) => v / 2) : null;
  // e.focus (css px) overrides the capture's subject: where the camera's 'f' and the crash zoom go
  const img = {w: sh.size[0] / 2, h: sh.size[1] / 2,
    focus: e.focus || (fb ? [fb[0] + fb[2] / 2, fb[1] + fb[3] / 2] : [sh.size[0] / 4, sh.size[1] / 4])};
  const at = (f) => {
    const c = camera(e.camera || [[0, 'c', 'c', 1]], f, fps, img);
    // crash zoom: the last 5 frames dive 2.4x into the subject
    if (e.out === 'crash') {
      const z = lerp(f, len - 5, len, 1, 2.4, Easing.in(Easing.cubic));
      c.x = c.x + (img.focus[0] - c.x) * lerp(f, len - 5, len, 0, 1);
      c.y = c.y + (img.focus[1] - c.y) * lerp(f, len - 5, len, 0, 1);
      c.s *= z;
    }
    return c;
  };
  // whip pan: 1400 px over 6 frames, blurred by four trailing copies
  const whip = (f) => (e.in === 'whip' ? lerp(f, 0, 6, 1400, 0, Easing.out(Easing.cubic)) : 0) + (e.out === 'whip' ? lerp(f, len - 6, len, 0, -1400, Easing.in(Easing.cubic)) : 0);
  const layer = (f, opacity, key) => (
    <div key={key} style={{position: 'absolute', left: 0, top: 0, width: img.w, height: img.h, transformOrigin: '0 0', opacity,
      transform: `translateX(${whip(f)}px) ${place(at(f))}`}}>
      <Img src={staticFile(sh.file)} style={{width: img.w, height: img.h}} />
      {e.ring && fb ? <Ring box={fb} start={Math.round(e.ring * fps)} f={f} /> : null}
    </div>
  );
  const moving = Math.abs(whip(frame) - whip(frame - 1)) > 1;
  const echo = e.echo !== undefined && frame >= e.echo * fps && frame < e.echo * fps + 10;
  return (
    <AbsoluteFill style={{overflow: 'hidden', background: BG}}>
      {moving ? [4, 3, 2, 1].map((d) => layer(frame - d * 0.5, 0.18, `b${d}`)) : null}
      {echo ? [3, 2, 1].map((d) => layer(frame - d * 2, 0.12 * (4 - d), `e${d}`)) : null}
      {layer(frame, moving ? 0.6 : 1, 'main')}
      {e.clock ? <Clock span={e.clock} /> : null}
      {e.flash !== undefined && frame >= e.flash * fps && frame < e.flash * fps + 2 ? <AbsoluteFill style={{background: '#fff', opacity: 0.15}} /> : null}
    </AbsoluteFill>
  );
};

// The night passing over the dip: a corner stamp runs from his sign-off minute to the board's.
const Clock = ({span}) => {
  const frame = useCurrentFrame();
  const m = (t) => +t.slice(0, 2) * 60 + +t.slice(3);
  const now = Math.round(lerp(frame, 0, 8, m(span[0]), m(span[1]), Easing.inOut(Easing.cubic)));
  const hhmm = `${String(Math.floor(now / 60)).padStart(2, '0')}:${String(now % 60).padStart(2, '0')}`;
  return (
    <div style={{position: 'absolute', left: 90, top: 60, fontFamily: MONO, fontSize: 46, color: HOT, background: 'rgba(12,10,8,0.86)', padding: '10px 24px'}}>
      {hhmm}Z
    </div>
  );
};

// The highlight ring: a 2 px rectangle around the subject, drawn round in 8 frames.
const Ring = ({box, start, f}) => {
  const [x, y, w, h] = box;
  const p = 2 * (w + h) + 32;
  const drawn = lerp(f, start, start + 8, 0, p);
  return (
    <svg style={{position: 'absolute', left: 0, top: 0, overflow: 'visible'}} width={1} height={1}>
      <rect x={x - 8} y={y - 8} width={w + 16} height={h + 16} rx={10} fill="none" stroke={HOT} strokeWidth={2}
        strokeDasharray={`${drawn} ${p}`} style={{filter: `drop-shadow(0 0 6px ${HOT})`}} />
    </svg>
  );
};

// ---- his recordings ---------------------------------------------------------------------------------------

const Missing = ({e, what, note}) => (
  <AbsoluteFill style={{alignItems: 'center', justifyContent: 'center'}}>
    <div style={{width: e.phone ? 440 : 1500, height: e.phone ? 780 : 700, marginTop: e.phone ? -40 : -60, border: `4px dashed ${DIM}`,
      borderRadius: e.phone ? 48 : 18, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 22,
      padding: 40, textAlign: 'center', background: 'rgba(12,10,8,0.7)'}}>
      <div style={{fontFamily: MONO, fontSize: 34, color: HOT}}>{what}</div>
      <div style={{fontFamily: SANS, fontSize: e.phone ? 30 : 40, color: FG, maxWidth: 1200}}>{e.capture}</div>
      <div style={{fontFamily: MONO, fontSize: 24, color: DIM}}>{note}</div>
    </div>
  </AbsoluteFill>
);

const Rec = ({e, props}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const len = Math.round(e.sec * fps);
  const media = e.file
    ? e.image ? <Img src={staticFile(e.file)} style={{height: '100%'}} /> : <OffthreadVideo src={staticFile(e.file)} muted style={{height: '100%'}} />
    : null;
  const z = e.push ? lerp(frame, 0, len, e.push[0], e.push[1]) : 1;
  const crash = e.out === 'crash' ? lerp(frame, len - 5, len, 1, 2.4, Easing.in(Easing.cubic)) : 1;
  const body = !media
    ? <Missing e={e} what={`raw/${e.raw}`} note={e.held || 'his recording · not in raw/ yet'} />
    : e.phone
      ? <AbsoluteFill style={{alignItems: 'center', justifyContent: 'center'}}>
          <div style={{height: 900, borderRadius: 54, overflow: 'hidden', border: '8px solid #2a251e', boxShadow: '0 30px 90px rgba(0,0,0,0.7)'}}>{media}</div>
        </AbsoluteFill>
      : <AbsoluteFill style={{alignItems: 'center', justifyContent: 'center'}}>{media}</AbsoluteFill>;
  return (
    <AbsoluteFill style={{background: BG}}>
      {e.phone ? <Backdrop props={props} /> : null}
      <AbsoluteFill style={{transform: `scale(${z * crash})`, transformOrigin: e.origin || '50% 50%'}}>{body}</AbsoluteFill>
    </AbsoluteFill>
  );
};

// ---- generated from git -----------------------------------------------------------------------------------

// A real `git log --oneline` (cut.py reads it at render): HEAD first, then the scroll is the camera.
const GitLog = ({lines, sec, speed = 9, dim = 1, start = 10}) => {
  const frame = useCurrentFrame();
  const y = Math.max(0, frame - start) ** 1.25 * speed * 0.35;
  return (
    <AbsoluteFill style={{background: '#100d0a', opacity: dim, overflow: 'hidden'}}>
      <div style={{position: 'absolute', left: 120, top: 120 - y, fontFamily: MONO, fontSize: 36, lineHeight: 1.55, color: FG, whiteSpace: 'pre'}}>
        {lines.map((l, i) => {
          const [sha, ...rest] = l.split(' ');
          return (
            <div key={i}>
              <span style={{color: HOT}}>{sha}</span>
              {i === 0 ? <span style={{color: '#6fd3c4'}}> (HEAD)</span> : null} {rest.join(' ')}
              {i === 0 && frame < start ? <span style={{opacity: frame % 16 < 8 ? 1 : 0}}>▌</span> : null}
            </div>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};

// An odometer: each digit is a strip 0..9 rolled by the value's fraction at that place.
const Odometer = ({value, size}) => {
  const digits = String(Math.round(value.target)).length;
  const v = value.now;
  const cols = [];
  for (let k = digits - 1; k >= 0; k--) {
    const place = 10 ** k;
    // a place turns only while the places below it carry, as on a real odometer; the ones step whole, or the
    // eased tail leaves the last digit half a cell low for a second
    const roll = (Math.floor(v / place) % 10) + (k === 0 ? 0 : Math.max(0, (v % place) - (place - 1)));
    const shown = v < place && k > 0 ? null : roll;
    cols.push(
      <div key={k} style={{height: size * 1.1, overflow: 'hidden', width: size * 0.62, opacity: shown === null ? 0.15 : 1}}>
        <div style={{transform: `translateY(${-(shown ?? 0) * size * 1.1}px)`}}>
          {[0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 0].map((d, i) => <div key={i} style={{height: size * 1.1, lineHeight: `${size * 1.1}px`}}>{d}</div>)}
        </div>
      </div>,
    );
    // the separator gets its own centred box: a mono comma is a full cell wide and would lean into its neighbour
    if (k % 3 === 0 && k > 0) cols.push(<div key={`s${k}`} style={{width: size * 0.34, display: 'flex', justifyContent: 'center', lineHeight: `${size * 1.1}px`}}>,</div>);
  }
  return <div style={{display: 'flex', fontSize: size, fontFamily: MONO, color: FG}}>{cols}</div>;
};

const Numbers = ({props}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const n = props.numbers;
  const rows = [
    [n.commits, `commits since ${n.since}`],
    [n.resident, `by the resident, ${Math.round((100 * n.resident) / n.authored)}% of authored`],
    [n.prs, 'merged pull requests'],
  ];
  return (
    <AbsoluteFill>
      <GitLog lines={props.logs.repo} dim={0.13} speed={14} start={0} />
      {/* a scrim under the figures so the log reads as texture, not as text competing with the labels */}
      <AbsoluteFill style={{background: 'radial-gradient(ellipse 62% 48% at 50% 46%, rgba(16,13,10,0.94) 40%, rgba(16,13,10,0.55) 75%, rgba(16,13,10,0) 100%)'}} />
      <AbsoluteFill style={{alignItems: 'center', justifyContent: 'center'}}>
        <div style={{display: 'flex', flexDirection: 'column', gap: 18, marginTop: -60}}>
          {rows.map(([v, label], i) => {
            const at = 6 + i * (fps / 2);
            // staggered starts, one landing: all three settle on the same frame
            const now = lerp(frame, at, 6 + fps + 1.5 * fps, 0, v, Easing.out(Easing.cubic));
            return (
              <div key={label} style={{display: 'flex', alignItems: 'center', gap: 40, opacity: lerp(frame, at - 4, at, 0, 1)}}>
                <div style={{width: 560, display: 'flex', justifyContent: 'flex-end'}}><Odometer value={{now, target: v}} size={118} /></div>
                <span style={{fontFamily: SANS, fontSize: 50, color: DIM}}>{label}</span>
              </div>
            );
          })}
          <div style={{fontFamily: MONO, fontSize: 30, color: DIM, marginLeft: 600, opacity: lerp(frame, 3 * fps, 3 * fps + 6, 0, 1)}}>
            git log of github.com/hugimuni-labs/brnrd · counted {n.at}
          </div>
        </div>
      </AbsoluteFill>
      <Checks checks={props.checks} at={Math.round(3.5 * fps)} />
    </AbsoluteFill>
  );
};

// Act 1's PR, its checks as GitHub reports them, along the bottom for the last beat of the numbers.
const Checks = ({checks, at}) => {
  const frame = useCurrentFrame();
  if (!checks || !checks.rows.length) return null;
  return (
    <div style={{position: 'absolute', left: 90, right: 300, bottom: 70, display: 'flex', flexWrap: 'wrap', gap: '10px 26px', fontFamily: MONO, fontSize: 26,
      opacity: lerp(frame, at, at + 6, 0, 1), transform: `translateY(${lerp(frame, at, at + 8, 20, 0)}px)`}}>
      <span style={{color: FG}}>#{checks.pr}</span>
      {checks.rows.map(([name, state], i) => (
        <span key={i} style={{color: DIM, opacity: lerp(frame, at + i, at + i + 3, 0, 1)}}>
          <span style={{color: state === 'SUCCESS' ? '#5fd38a' : HOT}}>{state === 'SUCCESS' ? '✓' : '✗'}</span> {name}
        </span>
      ))}
    </div>
  );
};

// ---- act 3 ------------------------------------------------------------------------------------------------

// Video feedback, one level: remotion studio with this cut open, and in its preview the cut's act 1 itself,
// played by this same component. The camera pushes into the preview until the trailer fills the frame, then
// pulls out to show it was the preview.
const Studio = ({e, props}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const st = props.studio;
  if (!st) return <Missing e={e} what="shots/studio.png" note="studio still not captured" />;
  const [px, py, pw, ph] = st.preview.map((v) => v / 2);
  const img = {w: st.size[0] / 2, h: st.size[1] / 2};
  const full = Math.min(W / pw, H / ph);
  const cx = px + pw / 2;
  const cy = py + ph / 2;
  const turn = Math.round(2.5 * fps);
  const s = frame < turn ? lerp(frame, 6, turn, 1, full) : lerp(frame, turn, Math.round(e.sec * fps) - 4, full, 1);
  const x = interpolate(s, [1, full], [img.w / 2, cx]);
  const y = interpolate(s, [1, full], [img.h / 2, cy]);
  const act1 = props.timeline.filter((t) => t.act === 1);
  return (
    <AbsoluteFill style={{overflow: 'hidden', background: BG}}>
      <div style={{position: 'absolute', left: 0, top: 0, width: img.w, height: img.h, transformOrigin: '0 0', transform: place({x, y, s})}}>
        <Img src={staticFile(st.file)} style={{width: img.w, height: img.h}} />
        <div style={{position: 'absolute', left: px, top: py, width: pw, height: ph, overflow: 'hidden', background: BG}}>
          <div style={{width: W, height: H, transform: `scale(${pw / W})`, transformOrigin: '0 0'}}>
            <Timeline props={{...props, timeline: act1, nested: true}} />
          </div>
        </div>
      </div>
    </AbsoluteFill>
  );
};

const Title = ({props, e}) => {
  const frame = useCurrentFrame();
  const line = e.text[0];
  return (
    <AbsoluteFill>
      <Backdrop props={props} />
      <AbsoluteFill style={{alignItems: 'center', justifyContent: 'center', flexDirection: 'column', gap: 26}}>
        <div style={{display: 'flex', fontFamily: SANS, fontWeight: 700, fontSize: 132, color: FG, letterSpacing: -3}}>
          {[...line].map((ch, i) => (
            <span key={i} style={{whiteSpace: 'pre', opacity: lerp(frame, i * 0.45, i * 0.45 + 3, 0, 1),
              marginLeft: lerp(frame, i * 0.45, i * 0.45 + 6, 24, 0), color: ch === '.' ? HOT : FG}}>{ch}</span>
          ))}
        </div>
        <div style={{fontFamily: SANS, fontWeight: 500, fontSize: 64, color: DIM, opacity: lerp(frame, 20, 26, 0, 1),
          transform: `translateY(${lerp(frame, 20, 28, 30, 0)}px)`}}>{e.text[1]}</div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

const Install = ({props, e}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const len = Math.round(e.sec * fps);
  const grow = spring({frame: frame - 4, fps, config: {damping: 14, mass: 0.8}, durationInFrames: 2 * fps});
  // booth: the last 12 frames take the QR back to the corner over the board at 12 %, frame 0's picture
  const back = props.variant === 'booth' ? lerp(frame, len - 12, len - 1, 0, 1) : 0;
  // the QR stays right of the longest install line (46 px Menlo, about 1310 px), so the line a visitor copies is whole
  const big = {x: 1420, y: 290, size: 420};
  const k = grow * (1 - back);
  const qx = CORNER.x + (big.x - CORNER.x) * k;
  const qy = CORNER.y + (big.y - CORNER.y) * k;
  const qs = CORNER.size + (big.size - CORNER.size) * k;
  let typed = 0;
  return (
    <AbsoluteFill>
      <AbsoluteFill style={{opacity: back}}><Backdrop props={props} /></AbsoluteFill>
      <div style={{position: 'absolute', left: 120, top: 330, fontFamily: MONO, fontSize: 46, color: FG, lineHeight: 2, opacity: 1 - back}}>
        {props.install.map((l) => {
          const from = typed;
          typed += l.length + 6;
          const n = Math.round(interpolate(frame, [6 + from * 0.5, 6 + (from + l.length) * 0.5], [0, l.length], clamp));
          return <div key={l}><span style={{color: HOT}}>$</span> {l.slice(0, n)}</div>;
        })}
        <div style={{fontFamily: SANS, fontSize: 44, color: DIM, marginTop: 36, opacity: lerp(frame, 1.6 * fps, 1.6 * fps + 8, 0, 1)}}>{e.line}</div>
        {props.pr ? <div style={{fontSize: 28, color: DIM, marginTop: 10, opacity: lerp(frame, 1.9 * fps, 1.9 * fps + 8, 0, 1)}}>this cut: github.com/hugimuni-labs/brnrd/pull/{props.pr}</div> : null}
      </div>
      <div style={{position: 'absolute', left: qx, top: qy, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8 + 8 * k}}>
        <QR url={props.url} size={qs} />
        <div style={{fontFamily: MONO, fontSize: 24 + 18 * k, color: FG}}>brnrd.dev</div>
      </div>
    </AbsoluteFill>
  );
};

// ---- transitions on the way in ----------------------------------------------------------------------------

// glitch: 2 frames of RGB split at 6 px, 1 frame of slice offset, then clean. dip: 10 frames up from the board.
const In = ({kind, children}) => {
  const frame = useCurrentFrame();
  if (kind === 'glitch' && frame < 2) {
    return (
      <AbsoluteFill>
        <AbsoluteFill style={{filter: 'url(#rgb)'}}>{children}</AbsoluteFill>
      </AbsoluteFill>
    );
  }
  if (kind === 'glitch' && frame === 2) {
    return (
      <AbsoluteFill>
        <AbsoluteFill style={{clipPath: 'inset(0 0 55% 0)'}}>{children}</AbsoluteFill>
        <AbsoluteFill style={{clipPath: 'inset(45% 0 0 0)', transform: 'translateX(-38px)'}}>{children}</AbsoluteFill>
      </AbsoluteFill>
    );
  }
  if (kind === 'dip') return <AbsoluteFill style={{opacity: lerp(frame, 0, 10, 0, 1)}}>{children}</AbsoluteFill>;
  return children;
};

const RGB = () => (
  <svg width={0} height={0} style={{position: 'absolute'}}>
    <filter id="rgb" x="0" y="0" width="100%" height="100%">
      <feColorMatrix in="SourceGraphic" type="matrix" values="1 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 1 0" result="r" />
      <feOffset in="r" dx="-6" result="r2" />
      <feColorMatrix in="SourceGraphic" type="matrix" values="0 0 0 0 0  0 1 0 0 0  0 0 0 0 0  0 0 0 1 0" result="g" />
      <feColorMatrix in="SourceGraphic" type="matrix" values="0 0 0 0 0  0 0 0 0 0  0 0 1 0 0  0 0 0 1 0" result="b" />
      <feOffset in="b" dx="6" result="b2" />
      <feBlend in="r2" in2="g" mode="screen" result="rg" />
      <feBlend in="rg" in2="b2" mode="screen" />
    </filter>
  </svg>
);

// ---- the cut ----------------------------------------------------------------------------------------------

const Entry = ({e, props}) => {
  switch (e.kind) {
    case 'rec': return <Rec e={e} props={props} />;
    case 'still': return <Still e={e} props={props} />;
    case 'gitlog': return <GitLog lines={props.logs[e.log]} sec={e.sec} />;
    case 'title': return <Title e={e} props={props} />;
    case 'studio': return <Studio e={e} props={props} />;
    case 'numbers': return <Numbers props={props} />;
    case 'install': return <Install e={e} props={props} />;
    default: return null;
  }
};

const Timeline = ({props}) => {
  const {fps} = props;
  let from = 0;
  return props.timeline.map((e, i) => {
    const len = Math.round(e.sec * fps);
    const at = from;
    from += len;
    const lower = Array.isArray(e.lower) ? e.lower : e.lower ? [[0, e.lower]] : [];
    return (
      <Sequence key={i} from={at} durationInFrames={len}>
        <AbsoluteFill style={{background: BG}}>
          <In kind={e.in}><Entry e={e} props={props} /></In>
          {lower.map(([t, text], j) => {
            const a = Math.round(t * fps);
            const b = j + 1 < lower.length ? Math.round(lower[j + 1][0] * fps) : len;
            return <Sequence key={j} from={a} durationInFrames={b - a} layout="none"><Lower text={text} len={b - a} /></Sequence>;
          })}
          {e.stamps ? <Stamps stamps={props.stamps} fps={fps} /> : null}
          {!props.nested && e.kind !== 'install' ? <Corner url={props.url} fade={props.variant === 'readme' && i === 0} /> : null}
        </AbsoluteFill>
      </Sequence>
    );
  });
};

export const Trailer = (props) => (
  <AbsoluteFill style={{background: BG}}>
    <RGB />
    <Timeline props={props} />
  </AbsoluteFill>
);
