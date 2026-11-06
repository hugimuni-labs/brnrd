import {AbsoluteFill, Easing, interpolate, useCurrentFrame} from 'remotion';

// The terminal shots, drawn instead of recorded (w-glie6g1xti8p): abstract macOS chrome, mono at 2x, every
// line from real data that cut.py reads at render time (gen_term.py). Not a screen recording, a recognizable
// one: the motion carries what a 1 s shot can say, and script.md's readability rules win over any effect.

const BG = '#0c0a08';
const FG = '#f2e8d8';
const DIM = '#8a8070';
const HOT = '#ff8a2a';
const OK = '#5fd38a';
const MONO = 'Menlo, "DejaVu Sans Mono", monospace';
const SANS = '"Helvetica Neue", Helvetica, Arial, sans-serif';
const clamp = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'};
const lerp = (f, a, b, from, to, easing = Easing.bezier(0.45, 0, 0.2, 1)) => interpolate(f, [a, b], [from, to], {...clamp, easing});

// Typed at human cadence with a speed ramp: slow on the first and last keys, fast through the middle.
const typed = (text, frame, from, to) => text.slice(0, Math.round(lerp(frame, from, to, 0, text.length, Easing.inOut(Easing.quad))));

const Caret = ({on = true}) => {
  const frame = useCurrentFrame();
  return on ? <span style={{opacity: frame % 14 < 8 ? 1 : 0, color: FG}}>▌</span> : null;
};

// A window: three dots, a title bar, a dark body. `style` places it.
export const Window = ({title, children, style, glow = 0, body = {}}) => (
  <div style={{position: 'absolute', borderRadius: 16, overflow: 'hidden', background: '#14100c', border: '1px solid #2e281f',
    boxShadow: `0 40px 120px rgba(0,0,0,0.75)${glow ? `, 0 0 0 2px rgba(255,138,42,${glow})` : ''}`, display: 'flex', flexDirection: 'column', ...style}}>
    <div style={{height: 52, flex: 'none', background: '#1e1913', borderBottom: '1px solid #2e281f', display: 'flex', alignItems: 'center', position: 'relative'}}>
      <div style={{display: 'flex', gap: 12, marginLeft: 22}}>
        {['#ff5f57', '#febc2e', '#28c840'].map((c) => <div key={c} style={{width: 18, height: 18, borderRadius: 9, background: c}} />)}
      </div>
      <div style={{position: 'absolute', left: 120, right: 120, textAlign: 'center', fontFamily: SANS, fontSize: 24, fontWeight: 500,
        color: glow ? FG : DIM, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis'}}>{title}</div>
    </div>
    <div style={{flex: 1, position: 'relative', overflow: 'hidden', fontFamily: MONO, fontSize: 34, lineHeight: 1.45, color: FG, ...body}}>{children}</div>
  </div>
);

// one window's place in the frame: clear of the lower third (bottom 200 px) and the corner QR
const FRAME = {left: 210, top: 50, width: 1500, height: 780};

const Prompt = ({cwd, children}) => (
  <div style={{whiteSpace: 'pre'}}><span style={{color: OK}}>{cwd}</span> <span style={{color: HOT}}>$</span> {children}</div>
);

// ---- pain 1: Claude Code's memory, 65 session files named by UUID ------------------------------------------

// `ls -lh` of brnrd's folder under ~/.claude/projects/ scrolls past too fast to read, then a crash zoom into
// one UUID: the match cut lands on the self's git log.
const MemDir = ({rows, cwd, title, pick, sec, fps}) => {
  const frame = useCurrentFrame();
  const len = Math.round(sec * fps);
  const LH = 46;
  const VIS = 15;
  const shown = Math.round(lerp(frame, 7, len - 9, 0, rows.length, Easing.in(Easing.quad)));
  const scroll = Math.max(0, shown - VIS + 1) * LH;
  const p = pick ?? rows.length - 8;
  const zoom = lerp(frame, len - 6, len, 1, 3.2, Easing.in(Easing.cubic));
  // the picked name's place on screen once the listing has stopped: the zoom dives at it
  const py = 52 + 40 + (p + 1) * LH - Math.max(0, rows.length - VIS + 1) * LH + LH / 2;
  return (
    <AbsoluteFill style={{background: BG}}>
      <AbsoluteFill style={{transform: `scale(${zoom})`, transformOrigin: `${FRAME.left + 1000}px ${FRAME.top + py}px`}}>
        <Window title={title} style={FRAME}>
          <div style={{position: 'absolute', left: 36, top: 20 - scroll, whiteSpace: 'pre', lineHeight: `${LH}px`, fontSize: 28}}>
            <Prompt cwd="brnrd">{typed('ls -lh', frame, 0, 6)}<Caret on={frame < 7} /></Prompt>
            {rows.slice(0, shown).map(([meta, name], i) => (
              <div key={i}>
                <span style={{color: DIM}}>{meta} </span>
                <span style={{color: i === p && frame >= len - 8 ? HOT : FG}}>{name}</span>
              </div>
            ))}
          </div>
        </Window>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

// ---- answer 1: the self's git log, landing on act 1's commit, its README opening ---------------------------

const ReadmeCard = ({path, lines, at, frame, style}) => {
  const open = lerp(frame, at, at + 9, 0, 1, Easing.out(Easing.cubic));
  return (
    <div style={{position: 'absolute', transformOrigin: '50% 0', transform: `scaleY(${open})`, opacity: open, background: '#f6efe2',
      color: '#1b1612', borderRadius: 14, boxShadow: '0 30px 90px rgba(0,0,0,0.7)', overflow: 'hidden', ...style}}>
      <div style={{background: '#e9dfcd', padding: '12px 28px', fontFamily: MONO, fontSize: 24, color: '#5a5045'}}>{path}</div>
      <div style={{padding: '20px 32px', fontFamily: SANS, fontSize: 30, lineHeight: 1.4}}>
        {lines.map((l, i) => (
          <div key={i} style={{fontWeight: i === 0 ? 700 : 400, fontSize: i === 0 ? 40 : 28, marginBottom: i === 0 ? 14 : 6,
            color: i === 0 ? '#1b1612' : '#3c342b', opacity: lerp(frame, at + 4 + i * 2, at + 8 + i * 2, 0, 1)}}>{l}</div>
        ))}
      </div>
    </div>
  );
};

const SelfLog = ({lines, target, readme, sec, fps}) => {
  const frame = useCurrentFrame();
  const len = Math.round(sec * fps);
  const LH = 52;
  const t = target ?? 0;
  const land = Math.round(len * 0.48);
  // from deep in the log (the oldest of the lines read) up to act 1's commit, fast then braking
  const top = interpolate(frame, [0, land], [(lines.length - 12) * LH, Math.max(0, t - 3) * LH], {...clamp, easing: Easing.out(Easing.cubic)});
  const hit = frame >= land;
  return (
    <AbsoluteFill style={{background: BG}}>
      <Window title="brnrd-self — git log --oneline" style={FRAME}>
        <div style={{position: 'absolute', left: 36, right: 36, top: 24 - top, whiteSpace: 'pre', lineHeight: `${LH}px`, fontSize: 32}}>
          {lines.map((l, i) => {
            const [sha, ...rest] = l.split(' ');
            const me = i === t && hit;
            return (
              <div key={i} style={{background: me ? `rgba(255,138,42,${lerp(frame, land, land + 4, 0, 0.22)})` : 'none', borderRadius: 6,
                overflow: 'hidden', textOverflow: 'ellipsis'}}>
                <span style={{color: HOT}}>{sha}</span>
                {i === 0 ? <span style={{color: '#6fd3c4'}}> (HEAD)</span> : null} <span style={{color: me || !hit ? FG : DIM}}>{rest.join(' ')}</span>
              </div>
            );
          })}
        </div>
      </Window>
      {readme ? <ReadmeCard path={readme.path} lines={readme.lines} at={land + 6} frame={frame}
        style={{left: FRAME.left + 160, top: FRAME.top + 52 + 24 + 4 * LH, width: 1180}} /> : null}
    </AbsoluteFill>
  );
};

// ---- pain 2: /compact, the session summarized away ---------------------------------------------------------

// The transcript above is abstract bars (the words are not the point, their loss is); the command and the
// line Claude Code writes are real (gen_term.py reads them from a compact_boundary in brnrd's sessions).
const Compact = ({said, tokens, summary, title, sec, fps}) => {
  const frame = useCurrentFrame();
  const len = Math.round(sec * fps);
  const gone = lerp(frame, 10, 17, 0, 1, Easing.in(Easing.cubic));
  const bars = [0.82, 0.64, 0.9, 0.48, 0.74, 0.88, 0.56, 0.7, 0.42, 0.8];
  const spin = '✻✽✶✳'[Math.floor(frame / 2) % 4];
  return (
    <AbsoluteFill style={{background: BG}}>
      <Window title={title} style={FRAME}>
        <div style={{position: 'absolute', left: 44, right: 44, top: 30, transformOrigin: '50% 100%',
          transform: `scaleY(${1 - gone * 0.92}) translateY(${gone * 200}px)`, opacity: 1 - gone}}>
          {bars.map((w, i) => (
            <div key={i} style={{display: 'flex', gap: 18, alignItems: 'center', height: 44}}>
              <span style={{color: i % 3 === 0 ? FG : HOT, width: 24, fontSize: 30}}>{i % 3 === 0 ? '>' : '●'}</span>
              <div style={{height: 16, width: `${w * 92}%`, borderRadius: 8, background: i % 3 === 0 ? '#3a332a' : '#2a241d'}} />
            </div>
          ))}
        </div>
        <div style={{position: 'absolute', left: 44, right: 44, bottom: 40, whiteSpace: 'pre'}}>
          {frame >= 9 && frame < 17 ? <div style={{color: HOT}}>{spin} Compacting conversation…</div> : null}
          {frame >= 17 ? (
            <div style={{opacity: lerp(frame, 17, 20, 0, 1)}}>
              <div style={{color: DIM}}>{'✻ '}<span style={{color: FG}}>{said}</span>
                <span style={{color: DIM}}>{` · ${tokens.toLocaleString('en-US')} tokens → one summary`}</span></div>
              <div style={{color: DIM, fontSize: 26, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', opacity: lerp(frame, 20, len, 0.4, 0.9)}}>
                {'  ⎿ '}{summary}</div>
            </div>
          ) : null}
          <div style={{marginTop: 18, border: '2px solid #3a332a', borderRadius: 12, padding: '10px 22px'}}>
            <span style={{color: DIM}}>{'> '}</span>{typed('/compact', frame, 0, 7)}<Caret on={frame < 9} />
          </div>
        </div>
      </Window>
    </AbsoluteFill>
  );
};

// ---- pain 4: five repos, five terminals ---------------------------------------------------------------------

const Repos = ({repos, base, sec, fps}) => {
  const frame = useCurrentFrame();
  return (
    <AbsoluteFill style={{background: BG}}>
      {repos.map((r, i) => {
        const at = i * 4;
        const k = lerp(frame, at, at + 4, 0, 1, Easing.out(Easing.back(1.4)));
        const last = i === repos.length - 1 || frame < (i + 1) * 4;
        return frame < at ? null : (
          <Window key={r} title={`${r} — -zsh`} glow={last ? 0.7 : 0}
            style={{left: 170 + i * 110, top: 50 + i * 72, width: 1000, height: 520, opacity: k, transform: `scale(${0.9 + 0.1 * k})`, transformOrigin: '0 0'}}>
            <div style={{position: 'absolute', left: 32, top: 24, whiteSpace: 'pre'}}>
              <Prompt cwd={`${base}/${r}`}><Caret on={last} /></Prompt>
            </div>
          </Window>
        );
      })}
    </AbsoluteFill>
  );
};

// ---- answer 6: act 1's thread README, the wish line and the PR link -----------------------------------------

// The self is private on GitHub, so its page is drawn: the real README of the thread act 1 delivered on, at
// the commit that recorded the merge; the page scroll is the move, then a freeze while the PR link underlines.
const Readme = ({path, sha, lines, link, sec, fps}) => {
  const frame = useCurrentFrame();
  const len = Math.round(sec * fps);
  const freeze = Math.round(len * 0.55);
  const y = lerp(frame, 0, freeze, 60, 0, Easing.out(Easing.cubic));
  const under = lerp(frame, freeze, freeze + 10, 0, 1);
  return (
    <AbsoluteFill style={{background: '#0d1117'}}>
      <div style={{position: 'absolute', left: 160, right: 160, top: 70 - y, background: '#0d1117', border: '1px solid #30363d', borderRadius: 12,
        fontFamily: SANS, color: '#e6edf3'}}>
        <div style={{padding: '18px 32px', borderBottom: '1px solid #30363d', fontFamily: MONO, fontSize: 26, color: '#9198a1', background: '#151b23',
          borderRadius: '12px 12px 0 0'}}>
          hugimuni-labs/brnrd-self / {path} <span style={{color: '#6e7681'}}>· {sha}</span>
        </div>
        <div style={{padding: '28px 40px 40px', fontSize: 30, lineHeight: 1.5}}>
          {lines.map(([kind, text], i) => {
            if (kind === 'link') {
              return (
                <div key={i} style={{marginTop: 18, fontSize: 34}}>
                  <span style={{color: '#9198a1'}}>{text} </span>
                  <span style={{color: '#4493f8', position: 'relative'}}>{link}
                    <span style={{position: 'absolute', left: 0, bottom: -4, height: 3, width: `${under * 100}%`, background: HOT}} />
                  </span>
                </div>
              );
            }
            return <div key={i} style={{fontSize: kind === 'h' ? 44 : kind === 'wish' ? 32 : 28, fontWeight: kind === 'h' ? 600 : 400,
              color: kind === 'wish' ? FG : kind === 'meta' ? '#9198a1' : '#e6edf3', fontFamily: kind === 'meta' ? MONO : SANS,
              marginBottom: kind === 'h' ? 18 : 8, padding: kind === 'wish' ? '14px 22px' : 0, borderLeft: kind === 'wish' ? `5px solid ${HOT}` : 'none',
              background: kind === 'wish' ? 'rgba(255,138,42,0.08)' : 'none'}}>{text}</div>;
          })}
        </div>
      </div>
    </AbsoluteFill>
  );
};

export const term = {'t1-memdir': MemDir, 'self-log': SelfLog, 't2-compact': Compact, 't4-repos': Repos, 'g6-readme': Readme};
