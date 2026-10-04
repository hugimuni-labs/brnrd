# The mythos — visual grammar (#2158)

The opening of the manifesto film (#2154), as a self-contained study: matter shaken into legibility,
then measured, written down, manufactured, and sent. 36.9 s. This page is the grammar; the code in
`src/` is one execution of it.

## The rule everything else follows

**No form is drawn as a letter.** Every recurring shape is a *nodal set of a vibrating circular
membrane*, a superposition of drum modes `J_n(k r) cos(n(θ − φ))` (`src/forms.py`). Where the
membrane doesn't move, grains settle. The same contours are then reused by every other physical
system in the film: the crack's path, the erosion channels, the die, the conductor. Letters arrive
only late, as a *human reading* printed beside a form that already existed.

The previous attempt (`act1.py`, now only in git history) is the negative reference. It typed
glyphs with a font (`ᚠ`, `r`, `n`, `b`), warped them and carved them into stone, and once drew a
`b` with a rectangle across it and called it a transistor. Forms that were designed, not found.

## Shape families → the physics that produces them

| family | nodal form (`FORMS`) | what it is physically | readings (not before act 2) |
|---|---|---|---|
| **A · stem / hook / fork** | `fork` = J₁₁ + 0.6 J₂₁(φ=0.7) | a straight node that bends off at the junction | `r` `ᚠ` `Y` `^` `λ` `⤙` `l` |
| **B · bowl / cavity + stem** | `bowl` = J₁₂ + 0.6 J₂₁(φ=0.7) | a straight node running into a closed oval node | `b` `d` `p` `q` `o` `a` `J` `ᛒ` |
| **B′ · mirrored lobes** | `mirror` = 0.6 J₀₃ − J₂₁(φ=π/4) | two nodes facing across an axis | `b│d` |
| **C · channels / prongs** | `channels` = J₂₁ + 0.3 J₀₃ | an n-node above a u-node | `n` `u` `ω` `ᚢ`; unrolled: a serpentine `n u n u` |
| **D · arcs / disks / angles** | rings, ripples, J₁ and J₂ curves, 63.4° | the geometric substrate, never a letter | orbit, wave, bifurcation |

The user's relatives list (the issue comment) maps directly: bd → p o l J a q c; r → ^ l Y V A ⤙;
n → u ω. The barrage cycles through them family by family.

## Materials

1. **Particulate field.** Metallic ash at several depths under a hot source above frame. Volumetric
   god rays (`stage.dust_volume`).
2. **Dark geological / machined matter.** Fractal rock heightfields, raking light, heat in the
   creases. Brushed metal for the press.
3. **Living membrane.** A dark taut skin with 160k simulated grains (`membrane.py`). Each step a
   grain is kicked in proportion to the local amplitude |z| and drifts down z². Change the mode and
   the old pattern is kicked apart while the new one forms. The membrane is a real node at its
   clamped edge, so the framing keeps the rim mostly out of shot.
4. **Polymeric capture medium.** Milky, wrinkled, backlit film; a thermal print head writes the
   specimen into it. This is the ancestor of the later work strips and receipts.
5. **Measurement surfaces.** Etched reticle glass, registration brackets, grease pencil, an SEM
   data bar, a blueprint, a photomask.

Light is allowed to destroy information. `optics.filmic` clips with halation, the crack's flash
blows the frame out, and DOF sits on a tilted macro focal plane (`tilt_dof`).

## Transition grammar: each cut preserves one property

| cut | preserved property |
|---|---|
| void → crack | position: the discharge flash lights the terrain the stare shot will show |
| crack → water | position: the drop lands exactly where the crack forked |
| water → membrane | **shape**: rings = the bowl, the glitter path = the stem |
| membrane modes | **shape** across the drive tone: fork → bowl → mirror → channels |
| membrane → SEM grains → SEM crystal → Lichtenberg | **texture** and scale: grain → facet → bifurcation (63.4°) |
| Lichtenberg → stare | **shape**: the branching becomes the drainage network |
| stare → measurement → specimen → barrage | **position**: one placement `SP` for the whole act; every letter is fitted to the found form's own bbox |
| barrage → press | **position**: the die lands where the letters were |
| press → conduction → photomask → wafer | **line**: the channel |
| wafer → serpentine | **shape**: the channels family unrolled (n u n u) |
| serpentine → weave | **line** + rhythm: the meander flattens into a weft going over and under |
| weave → wire → register | **rhythm**: the pulse becomes a Morse mark pressed into tape |

## Observation perturbs the observed

In the measurement shot, each registration bracket that snaps closer (3 snaps) shakes the surface,
pulses the heat in the channels and lifts exposure. Focus racks from the glass to the ground. Only
after measurement can the form be printed, and only after printing is it read (`READS b d p q`).

## The chain (frames @ 30 fps)

| act | shot | f | what happens |
|---|---|---|---|
| 1 matter | void | 64 | near black, a ray through particulate; hidden terrain flashes for single frames |
|  | crack | 90 + 1 | a fracture runs in from the left, **hesitates** at the junction (flickering, the rock heating), then hooks up; the frame blows out |
|  | water | 56 | a drop lands where it forked; rings (bowl) and the sun's glitter path (stem) |
|  | membrane | 226 + 3 | grains, driven by an audible tone, find fork → bowl (an optical jump onto the bowl, square-on) → mirror → channels; a 2f printed J₁ plot, a 1f negative |
|  | scale collapse | 50 | SEM grains → 2f blueprint angle 63.4° → SEM crystal with the fork running through it → Lichtenberg discharge in resin at 63.4° → 2f logistic map |
| 2 observation | stare | 72 | dark eroded rock; one channel among many half-suggests the bowl. Nothing happens but light moving |
|  | measurement | 72 | reticle glass, three bracket snaps, each one disturbing the ground, then grease pencil |
|  | specimen | 70 | film settles, a print head writes the form, a label is typed, then the first letters |
|  | barrage | ~55 | 2–3f states, each letter fitted to the found form and rendered in a material (groove, emboss, ink with a misregistered ghost, film print, phosphor, stencil, xerox), with grain frames between families |
| 3 signal | press | 60 | the die is never seen: its shadow takes the light in 3 stepped poses, then impact, a dust burst, the bowl pressed into metal with a lip of displaced metal |
|  | circuit | 26 + 2 + 20 + 34 | conduction runs the channel → photomask → optical pull-back to a wafer of the same die → serpentine trace |
|  | weave | 52 | the meander flattens into a weft; one copper fibre carries the pulse |
|  | signal | 26 + 2 + 104 + 18 | a pulse on a sagging wire at dusk → a register embosses `-... .-. -. .-. -..` into tape. The first time **brnrd** appears, it is rhythm |

## Sound

`audio/score.py` is keyed to the edit list and is mostly diegetic:

- crackle that peaks at the crack's hesitation;
- a drip on the water;
- **the membrane's drive tone**, which changes pitch with each mode (the grains move because of it);
- clicks on the bracket snaps and a thermal print-head buzz;
- rumble into the strike;
- mains hum on the circuit;
- the register sounding `brnrd` in Morse, synchronised with the marks it presses.
