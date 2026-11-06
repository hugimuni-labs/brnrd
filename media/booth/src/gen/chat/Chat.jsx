import {AbsoluteFill, Easing, interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import script from './script.json';

// The phone shots of the booth trailer as motion design, not a screen recording: one stylised chat (no
// messenger's skin), his bubbles right, the resident's left behind the brnrd mark, every line from the ledger
// (script.json). A shot is props-driven: <ChatShot shot="a1-ask" /> draws it; `sec` rescales its beats to a
// slot the edit chooses. Readability rules from script.md: no effect longer than 6 frames, no white strobe.

// The trailer's palette and type (Trailer.jsx).
const BG = '#0c0a08';
const FG = '#f2e8d8';
const DIM = '#8a8070';
const HOT = '#ff8a2a';
const GREEN = '#5bd17a';
const PURPLE = '#b48cff';
const MONO = 'Menlo, "DejaVu Sans Mono", monospace';
const SANS = '"Helvetica Neue", Helvetica, Arial, sans-serif';
const EMOJI = '"Apple Color Emoji", "Noto Color Emoji"';

// The phone ends 196 px above the frame's bottom: the trailer's lower third and QR live there (booth-gen).
const PHONE = {w: 520, h: 860, top: 24, r: 76, bezel: 12};
const HEADER = 100;
const COMPOSE = 104;
const TEXT = 30;
const RATE = 3.5; // characters per frame: a bubble line (~25 characters) in about 8 frames

const clamp = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'};
const EASE = Easing.bezier(0.45, 0, 0.2, 1);
const PR_URL = /https:\/\/github\.com\/hugimuni-labs\/(\w[\w-]*)\/pull\/(\d+)/g;
const graphemes = (s) => Array.from(new Intl.Segmenter('en', {granularity: 'grapheme'}).segment(s), (g) => g.segment);
const hhmm = (iso) => iso.slice(11, 16);

export const shotOf = (key) => script.shots.find((s) => s.key === key);
export const shotFrames = (key, sec) => Math.round((sec ?? shotOf(key).sec) * script.fps);

// ---- the mark ----------------------------------------------------------------------------------------------

// media/brand/mark-screen.svg, the resting frame `b·_·d` in the screen register (the chromatic ghosts kept).
const MarkStrokes = ({stroke, dx = 0, opacity = 1}) => (
  <g fill="none" strokeWidth={22} strokeLinecap="round" strokeLinejoin="round" stroke={stroke} opacity={opacity} transform={`translate(${dx},0)`}>
    <path d="M 72 92 V 420" /><path d="M 48 196 H 96" /><path d="M 48 236 H 96" /><path d="M 72 308 a 62 56 0 0 1 0 112" />
    <path d="M 440 92 V 420" /><path d="M 416 196 H 464" /><path d="M 416 236 H 464" /><path d="M 440 308 a 62 56 0 0 0 0 112" />
    <circle cx={176} cy={322} r={15} fill="#f2ece1" stroke="none" /><path d="M 208 390 H 304" /><circle cx={336} cy={322} r={15} fill="#f2ece1" stroke="none" />
  </g>
);

export const Mark = ({size}) => (
  <svg width={size} height={size} viewBox="0 0 512 512" style={{display: 'block', flex: 'none'}}>
    <rect width={512} height={512} rx={112} fill="#0c0906" />
    <MarkStrokes stroke="#ff3b30" dx={-7} opacity={0.55} />
    <MarkStrokes stroke="#3ad8e6" dx={7} opacity={0.55} />
    <MarkStrokes stroke="#f2ece1" />
  </svg>
);

const MergeIcon = ({size, color}) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={2.4} strokeLinecap="round" style={{flex: 'none'}}>
    <circle cx={6} cy={5} r={2.4} /><circle cx={6} cy={19} r={2.4} /><circle cx={18} cy={12} r={2.4} />
    <path d="M6 7.4v9.2" /><path d="M6 7.4c0 3 4 4.6 9.6 4.6" />
  </svg>
);

const OpenIcon = ({size, color}) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth={2.4} strokeLinecap="round" style={{flex: 'none'}}>
    <circle cx={6} cy={5} r={2.4} /><circle cx={6} cy={19} r={2.4} /><circle cx={18} cy={19} r={2.4} />
    <path d="M6 7.4v9.2" /><path d="M18 16.6V9a3 3 0 0 0-3-3h-4" /><path d="M13 3.6 10.6 6 13 8.4" />
  </svg>
);

// ---- the stage ---------------------------------------------------------------------------------------------

// Out-of-focus fragments of the board drift behind the phone: cards and rows, never legible.
const FRAGMENTS = [
  [140, 160, 360, 120, 0.5], [210, 360, 280, 70, 0.8], [90, 560, 420, 150, 0.35], [260, 820, 300, 90, 0.6],
  [1380, 120, 380, 140, 0.45], [1500, 360, 260, 70, 0.9], [1320, 560, 420, 160, 0.3], [1460, 830, 330, 100, 0.7],
];

const Backdrop = ({frame}) => (
  <AbsoluteFill style={{background: `radial-gradient(ellipse 70% 80% at 50% 55%, #1d1610 0%, ${BG} 70%)`, overflow: 'hidden'}}>
    <div style={{position: 'absolute', left: 960 - 520 + Math.sin(frame / 50) * 40, top: 300, width: 1040, height: 700, borderRadius: '50%',
      background: `radial-gradient(closest-side, ${HOT}22, transparent)`, filter: 'blur(40px)'}} />
    {FRAGMENTS.map(([x, y, w, h, v], i) => (
      <div key={i} style={{position: 'absolute', left: x, top: y - frame * v * 0.6, width: w, height: h, borderRadius: 18,
        background: i % 3 === 0 ? `${HOT}18` : '#f2e8d80c', border: '2px solid #f2e8d810', filter: 'blur(10px)'}}>
        <div style={{margin: 22, height: 14, width: w * 0.55, borderRadius: 7, background: '#f2e8d814'}} />
        <div style={{margin: '0 22px', height: 10, width: w * 0.35, borderRadius: 5, background: '#f2e8d80e'}} />
      </div>
    ))}
  </AbsoluteFill>
);

// ---- bubbles -----------------------------------------------------------------------------------------------

// A message's text with a PR link turned into a short accent token; the PR itself follows as a card.
const tokens = (text) => {
  const out = [];
  let last = 0;
  for (const m of text.matchAll(PR_URL)) {
    out.push(...graphemes(text.slice(last, m.index)).map((c) => ({c})));
    out.push({c: `${m[1]}#${m[2]}`, link: true});
    last = m.index + m[0].length;
  }
  out.push(...graphemes(text.slice(last)).map((c) => ({c})));
  return out;
};

// Every glyph is laid out from the first frame and only revealed, so a typing bubble never reflows.
const Typed = ({text, shown, only}) => {
  const ts = tokens(text);
  let n = 0;
  return (
    <span>
      {ts.map((t, i) => {
        const on = n < shown;
        if (only && !on) return null;
        n += t.link ? Math.max(1, Math.round(t.c.length / 2)) : 1;
        const isEmoji = /\p{Extended_Pictographic}/u.test(t.c);
        return (
          <span key={i} style={{opacity: on ? 1 : 0, color: t.link ? HOT : undefined, fontFamily: isEmoji ? EMOJI : undefined}}>
            {t.c}
          </span>
        );
      })}
    </span>
  );
};

const typedLength = (text) => tokens(text).reduce((n, t) => n + (t.link ? Math.max(1, Math.round(t.c.length / 2)) : 1), 0);

const PRCard = ({pr, state, flip}) => {
  const p = script.prs[pr];
  const merged = state === 'merged';
  const pill = merged
    ? {label: 'Merged', bg: PURPLE, fg: '#1a1030', Icon: MergeIcon}
    : state === 'green'
      ? {label: 'Open · checks green', bg: GREEN, fg: '#0d2414', Icon: OpenIcon}
      : {label: 'Open · conflicts', bg: '#e0b04a', fg: '#2a1e05', Icon: OpenIcon};
  return (
    <div style={{marginTop: 16, borderRadius: 18, background: '#0f0c09', border: `2px solid ${merged ? PURPLE + '88' : '#3a3228'}`, padding: '16px 18px',
      display: 'flex', flexDirection: 'column', gap: 10}}>
      <div style={{fontFamily: MONO, fontSize: 22, color: DIM}}>{`hugimuni-labs/${p.repo}`}</div>
      <div style={{fontSize: 28, fontWeight: 600, lineHeight: 1.2, color: FG}}>{p.title} <span style={{color: DIM, fontWeight: 400}}>#{p.number}</span></div>
      <div style={{display: 'flex', alignItems: 'center', gap: 10}}>
        <div style={{display: 'flex', alignItems: 'center', gap: 8, background: pill.bg, color: pill.fg, borderRadius: 999, padding: '6px 14px 6px 10px',
          fontSize: 22, fontWeight: 700, transform: `scale(${flip})`, transformOrigin: 'left center'}}>
          <pill.Icon size={22} color={pill.fg} />{pill.label}
        </div>
        {merged ? <div style={{fontFamily: MONO, fontSize: 20, color: DIM}}>{hhmm(p.mergedAt)}</div> : null}
      </div>
    </div>
  );
};

const Stamp = ({at, right}) => (
  <div style={{fontFamily: MONO, fontSize: 20, color: DIM, marginTop: 8, textAlign: right ? 'right' : 'left', paddingLeft: right ? 0 : 64}}>{hhmm(at)}</div>
);

// The loom's own notice, not the resident's voice: a gauge for an avatar, the fuel draining to its last percent.
const GaugeAvatar = ({size}) => (
  <svg width={size} height={size} viewBox="0 0 56 56" style={{display: 'block', flex: 'none'}}>
    <rect width={56} height={56} rx={14} fill="#1d1813" stroke="#3a3228" strokeWidth={2} />
    <path d="M12 38a16 16 0 0 1 32 0" fill="none" stroke={DIM} strokeWidth={4} strokeLinecap="round" />
    <path d="M28 38 15 31" stroke="#ff4a3a" strokeWidth={4} strokeLinecap="round" />
    <circle cx={28} cy={38} r={4} fill={FG} />
  </svg>
);

const Notice = ({m, t}) => {
  const level = interpolate(t, [4, 22], m.gauge, {...clamp, easing: EASE});
  return (
    <div style={{display: 'flex', flexDirection: 'column', gap: 12}}>
      <div style={{fontFamily: MONO, fontSize: 20, color: DIM, letterSpacing: 1}}>LOOM</div>
      <div style={{fontSize: 28, fontWeight: 600, lineHeight: 1.22}}>{m.title}</div>
      <div style={{display: 'flex', alignItems: 'center', gap: 12}}>
        <div style={{flex: 1, height: 14, borderRadius: 7, background: '#2e2720', overflow: 'hidden'}}>
          <div style={{width: `${Math.max(1.5, level * 100)}%`, height: '100%', background: '#ff4a3a', borderRadius: 7}} />
        </div>
        <div style={{fontFamily: MONO, fontSize: 22, color: '#ff6a5a', fontVariantNumeric: 'tabular-nums'}}>{Math.round(level * 100)}%</div>
      </div>
      <div style={{fontSize: 24, color: '#c9bfae', lineHeight: 1.25}}>{m.text}</div>
      <div style={{display: 'flex', flexWrap: 'wrap', gap: 10}}>
        {m.options.map((o) => (
          <div key={o} style={{border: '2px solid #3a3228', borderRadius: 12, padding: '6px 14px', fontFamily: MONO, fontSize: 20, color: FG}}>{o}</div>
        ))}
      </div>
    </div>
  );
};

const Quote = ({m}) => (
  <div style={{borderLeft: `4px solid ${HOT}`, background: '#00000033', borderRadius: 8, padding: '6px 12px', marginBottom: 10}}>
    <div style={{fontSize: 20, fontWeight: 700, color: HOT}}>brnrd</div>
    <div style={{fontSize: 22, color: '#c9bfae', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: 360}}>{m.title}</div>
  </div>
);

// A delivered card: what landed, and his two answers. `tapped` (0..1) presses ok and turns the card accepted.
const Delivered = ({m, tapped}) => {
  const done = tapped >= 1;
  return (
    <div style={{display: 'flex', flexDirection: 'column', gap: 10}}>
      <div style={{display: 'flex', alignItems: 'center', gap: 10, fontFamily: MONO, fontSize: 20, color: done ? GREEN : HOT, letterSpacing: 1}}>
        <span style={{width: 12, height: 12, borderRadius: 6, background: done ? GREEN : HOT}} />{done ? 'ACCEPTED' : 'DELIVERED'}
      </div>
      <div style={{fontSize: 28, fontWeight: 600, lineHeight: 1.2}}>{m.title}</div>
      {m.merged ? (
        <div style={{display: 'flex', alignItems: 'center', gap: 10}}>
          <div style={{display: 'flex', alignItems: 'center', gap: 8, background: PURPLE, color: '#1a1030', borderRadius: 999, padding: '5px 14px 5px 10px',
            fontSize: 21, fontWeight: 700}}><MergeIcon size={21} color="#1a1030" />Merged</div>
          <div style={{fontFamily: MONO, fontSize: 20, color: DIM}}>{hhmm(script.prs[m.merged].mergedAt)}</div>
        </div>
      ) : null}
      <div style={{fontSize: 24, color: '#c9bfae', lineHeight: 1.25}}>{m.text}</div>
      <div style={{display: 'flex', gap: 12, marginTop: 4}}>
        {['ok', 'say what is off'].map((b) => {
          const ok = b === 'ok';
          const press = ok && tapped > 0 && tapped < 1 ? 0.9 : 1;
          return (
            <div key={b} style={{flex: ok ? 'none' : 1, textAlign: 'center', border: `2px solid ${ok ? (done ? GREEN : HOT) : '#3a3228'}`,
              background: ok && done ? GREEN : 'transparent', color: ok ? (done ? '#0d2414' : HOT) : DIM, borderRadius: 14, padding: '8px 22px',
              fontSize: 22, fontWeight: 700, transform: `scale(${press})`, opacity: done && !ok ? 0.4 : 1}}>{ok && done ? 'ok ✓' : b}</div>
          );
        })}
      </div>
    </div>
  );
};

const Bubble = ({m, shown, cardState, flip, t = 99, tapped = 0}) => {
  const him = m.from === 'him';
  const msgs = script.messages;
  const body = m.kind === 'notice' ? <Notice m={m} t={t} /> : m.kind === 'delivered' ? <Delivered m={m} tapped={tapped} />  : (
    <>
      {m.quote ? <Quote m={msgs[m.quote]} /> : null}
      <div style={{fontSize: TEXT, lineHeight: 1.3}}><Typed text={m.text} shown={shown} /></div>
      {m.card ? <PRCard pr={m.card.pr} state={cardState ?? m.card.state} flip={flip} /> : null}
    </>
  );
  return (
    <div style={{display: 'flex', flexDirection: 'column', alignItems: him ? 'flex-end' : 'flex-start', padding: '0 22px 22px'}}>
      <div style={{display: 'flex', alignItems: 'flex-end', gap: 14, flexDirection: him ? 'row-reverse' : 'row', maxWidth: '100%'}}>
        {him ? null : m.from === 'loom' ? <GaugeAvatar size={50} /> : <Mark size={50} />}
        <div style={{maxWidth: him ? 410 : 384, background: him ? '#3b2a1a' : '#211c17', color: FG, fontFamily: SANS, padding: '18px 24px',
          borderRadius: 30, borderBottomRightRadius: him ? 8 : 30, borderBottomLeftRadius: him ? 30 : 8,
          border: him ? `2px solid ${HOT}44` : '2px solid #2e2720', boxShadow: '0 10px 30px rgba(0,0,0,0.35)'}}>
          {body}
        </div>
      </div>
      <Stamp at={m.at} right={him} />
    </div>
  );
};

// ---- the phone ---------------------------------------------------------------------------------------------

const Header = () => (
  <div style={{height: HEADER, flex: 'none', display: 'flex', alignItems: 'center', gap: 18, padding: '14px 28px 0', borderBottom: '2px solid #221d18',
    background: '#120f0c'}}>
    <Mark size={56} />
    <div style={{display: 'flex', flexDirection: 'column', gap: 4}}>
      <div style={{fontFamily: SANS, fontSize: 30, fontWeight: 700, color: FG}}>brnrd</div>
      <div style={{fontFamily: SANS, fontSize: 22, color: GREEN}}>resident · awake</div>
    </div>
  </div>
);

const SendButton = ({press}) => (
  <div style={{width: 66, height: 66, borderRadius: 33, background: HOT, flex: 'none', display: 'flex', alignItems: 'center', justifyContent: 'center',
    transform: `scale(${press})`, position: 'relative'}}>
    <svg width={34} height={34} viewBox="0 0 24 24" fill="none" stroke={BG} strokeWidth={2.8} strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 19V5" /><path d="M5 12l7-7 7 7" />
    </svg>
  </div>
);

const Compose = ({text, shown, press, ripple}) => (
  <div style={{height: COMPOSE, flex: 'none', display: 'flex', alignItems: 'flex-end', gap: 14, padding: '12px 18px 22px', background: '#120f0c',
    borderTop: '2px solid #221d18', position: 'relative'}}>
    <div style={{flex: 1, minHeight: 66, maxHeight: 66 + 3 * 34, borderRadius: 33, background: '#1d1813', border: '2px solid #2e2720', padding: '0 26px',
      display: 'flex', flexDirection: 'column', justifyContent: 'flex-end', overflow: 'hidden', fontFamily: SANS, fontSize: 27, lineHeight: '34px', color: FG,
      paddingBottom: 15, paddingTop: 15, boxSizing: 'border-box', position: 'absolute', left: 18, right: 98, bottom: 22}}>
      {text ? (
        <div><Typed text={text} shown={shown} only /><span style={{display: 'inline-block', width: 3, height: 28, background: HOT, marginLeft: 2, verticalAlign: -5}} /></div>
      ) : <div style={{color: '#5a5248'}}>Message</div>}
    </div>
    <div style={{flex: 1}} />
    <div style={{position: 'relative'}}>
      <SendButton press={press} />
      {ripple > 0 && ripple < 1 ? (
        <div style={{position: 'absolute', left: 33 - 33 * (1 + ripple), top: 33 - 33 * (1 + ripple), width: 66 * (1 + ripple), height: 66 * (1 + ripple),
          borderRadius: '50%', border: `3px solid ${HOT}`, opacity: 1 - ripple}} />
      ) : null}
    </div>
  </div>
);

// ---- the shot ----------------------------------------------------------------------------------------------

export const ChatShot = ({shot: key, sec}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const shot = shotOf(key);
  const scale = sec ? sec / shot.sec : 1;
  const F = (s) => s * scale * fps;
  const total = F(shot.sec);
  const msgs = script.messages;

  // What is on screen at this frame: the history, then each landed beat with its spring.
  const rows = shot.before.map((id) => ({id, enter: 1, shown: Infinity, t: 99}));
  let tap = null;
  const tapped = {};
  let compose = null;
  let press = 1;
  let ripple = 0;
  const cardState = {...shot.cards};
  const flip = {};
  let dip = 0;
  let clock = null;
  let sleep = 0;
  for (const b of shot.beats) {
    if (b.compose) {
      const a = F(b.from), z = F(b.to);
      const u = interpolate(frame, [a, z], [0, 1], clamp);
      const n = typedLength(msgs[b.compose].text);
      // speed ramp 1x -> 3x: the typed share is the integral of a rate rising linearly from r to 3r
      compose = {text: msgs[b.compose].text, shown: frame < a ? 0 : Math.ceil(n * (u + u * u) / 2)};
    }
    if (b.send) {
      const t = F(b.at);
      press = frame < t - 3 ? 1 : frame < t ? interpolate(frame, [t - 3, t], [1, 0.84]) : interpolate(frame, [t, t + 3], [0.84, 1], clamp);
      ripple = interpolate(frame, [t, t + 6], [0, 1], clamp);
      if (frame >= t) {
        compose = null;
        rows.push({id: b.send, enter: spring({frame: frame - t, fps, config: {damping: 18, stiffness: 260, mass: 0.6}, durationInFrames: 7}), shown: Infinity, t: frame - t});
      }
    }
    if (b.land) {
      const t = F(b.at);
      if (frame >= t) {
        const enter = spring({frame: frame - t, fps, config: {damping: 18, stiffness: 260, mass: 0.6}, durationInFrames: 7});
        rows.push({id: b.land, enter, shown: b.type ? Math.floor((frame - t - 3) * (b.rate ?? RATE) / scale) : Infinity, t: frame - t});
      }
    }
    if (b.merge) {
      const t = F(b.at);
      if (frame >= t) {
        cardState[b.merge] = 'merged';
        flip[b.merge] = spring({frame: frame - t, fps, from: 0.6, to: 1, config: {damping: 9, stiffness: 300}, durationInFrames: 6});
      }
    }
    if (b.dip) {
      const t = F(b.at);
      // 6 frames: 3 down to 85 % dark, 3 back
      dip = interpolate(frame, [t, t + 3, t + 6], [0, 0.85, 0], clamp);
      const [h0, m0] = b.clock[0].split(':').map(Number);
      const [h1, m1] = b.clock[1].split(':').map(Number);
      const mins = Math.round(interpolate(frame, [t, t + 6], [h0 * 60 + m0, h1 * 60 + m1], clamp));
      const show = interpolate(frame, [t - 24, t - 20, t + 30, t + 34], [0, 1, 1, 0], clamp);
      clock = {text: `${String(Math.floor(mins / 60)).padStart(2, '0')}:${String(mins % 60).padStart(2, '0')}`, show, ticking: frame >= t && frame < t + 6};
    }
    if (b.tap) {
      // the thumb: a soft cream disc comes down on ok, holds 3 frames, lifts; the card turns at the lift
      const t = F(b.at);
      tap = interpolate(frame, [t - 4, t, t + 3, t + 7], [0, 1, 1, 0], clamp);
      tapped[b.tap] = frame < t ? 0 : frame < t + 3 ? 0.5 : 1;
    }
    if (b.sleep) sleep = interpolate(frame, [F(b.at), F(b.at) + 6], [0, 0.7], clamp);
  }

  // Camera: a push between two scales, anchored on the compose box or the chat, eased over the whole shot.
  const cam = shot.camera;
  const s = interpolate(frame, [0, total], [cam.from, cam.to], {...clamp, easing: EASE});
  // anchored at the phone's foot, so a push never carries the chat into the trailer's bottom band
  const originY = PHONE.top + PHONE.h - COMPOSE / 2;

  return (
    <AbsoluteFill style={{background: BG}}>
      <Backdrop frame={frame} />
      <AbsoluteFill style={{transform: `scale(${s})`, transformOrigin: `960px ${originY}px`}}>
        <div style={{position: 'absolute', left: 960 - PHONE.w / 2, top: PHONE.top, width: PHONE.w, height: PHONE.h, borderRadius: PHONE.r,
          background: '#2a251e', padding: PHONE.bezel, boxSizing: 'border-box', boxShadow: '0 40px 120px rgba(0,0,0,0.75), inset 0 0 0 2px #3a332a'}}>
          <div style={{width: '100%', height: '100%', borderRadius: PHONE.r - PHONE.bezel, overflow: 'hidden', background: '#0e0b09',
            display: 'flex', flexDirection: 'column', position: 'relative'}}>
            <Header />
            <div style={{flex: 1, minHeight: 0, overflow: 'hidden', display: 'flex', flexDirection: 'column', justifyContent: 'flex-end', paddingTop: 20}}>
              {rows.map((r) => (
                <div key={r.id} style={{display: 'grid', gridTemplateRows: `${Math.min(1, Math.max(0, r.enter))}fr`, flex: 'none'}}>
                  <div style={{minHeight: 0, overflow: r.enter < 1 ? 'hidden' : 'visible', opacity: Math.min(1, r.enter * 1.4),
                    transform: `translateY(${(1 - r.enter) * 50}px) scale(${0.94 + 0.06 * r.enter})`,
                    transformOrigin: msgs[r.id].from === 'him' ? 'right bottom' : 'left bottom'}}>
                    <Bubble m={msgs[r.id]} shown={r.shown} cardState={cardState[r.id]} flip={flip[r.id] ?? 1} t={r.t} tapped={tapped[r.id] ?? 0} />
                  </div>
                </div>
              ))}
            </div>
            <Compose text={compose?.text} shown={compose?.shown ?? 0} press={press} ripple={ripple} />
            {tap ? (
              <div style={{position: 'absolute', left: 92, bottom: COMPOSE + 52, width: 84, height: 84, borderRadius: 42,
                background: 'rgba(242,232,216,0.3)', border: '2px solid rgba(242,232,216,0.5)', opacity: tap, transform: `scale(${1.3 - 0.3 * tap})`}} />
            ) : null}
            {sleep ? <AbsoluteFill style={{background: '#000', opacity: sleep}} /> : null}
          </div>
        </div>
      </AbsoluteFill>
      {dip ? <AbsoluteFill style={{background: '#000', opacity: dip}} /> : null}
      {clock && clock.show > 0 ? (
        <div style={{position: 'absolute', left: 90, top: 60, fontFamily: MONO, fontSize: 46, color: clock.ticking ? HOT : FG, opacity: clock.show,
          background: 'rgba(12,10,8,0.86)', padding: '10px 24px', borderRadius: 8, fontVariantNumeric: 'tabular-nums'}}>{clock.text}</div>
      ) : null}
    </AbsoluteFill>
  );
};
