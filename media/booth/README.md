# The booth trailer

One 50-second edit, rendered twice: `booth.mp4` (muted, made to loop, a caption on every shot, the brnrd.dev QR in the corner throughout) and `readme.mp4` (the same cut, linear, with a music bed when a licensed track is supplied). Real recordings for the shots, generated cards for everything else; the resident cut it.

```sh
cd media/booth && npm ci          # once: remotion, react, qrcode, local to this folder
python3 cut.py --pr 2623          # raw from ~/Desktop/brnrd/booth/raw, renders to ~/Desktop/brnrd/booth
```

- `cut.json` is the edit: every slot in order with its length, its caption and the raw file it wants. Change the cut there. A shot's `start` (seconds into its recording) picks the moment; the recording is trimmed to its slot, and a short one holds its last frame.
- A shot with no recording renders as a labelled placeholder, so the cut's shape is visible before the shoot. An `optional` shot with no recording is dropped and its seconds go to the shot before it. The guard shot falls back to the real refusal text in `cut.json`.
- The numbers card is counted at render time: every commit since 2026-03-28, the non-merge commits by the resident's author names, and GitHub's merged-PR count (squash merges leave no merge commit, so git alone undercounts; offline it falls back to PR numbers in commit subjects).
- `music.*` in the raw folder goes under `readme.mp4` only, faded out at the end. Only a track he holds a licence for.
- Captions use the words a visitor already has (memory, thread, message, PR), never the product's vocabulary.
- `npm run studio` opens the cut in remotion's editor, after one `cut.py` run has written `props.json`.

Remotion is free for individuals and companies of up to three people; past that it needs a company licence.
