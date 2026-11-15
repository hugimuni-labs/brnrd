# Keep media out of the code repository

Images, videos, audio, fonts and PDFs belong in
[hugimuni-labs/animations](https://github.com/hugimuni-labs/animations).
Link to them from code and documentation. Other binary files over 16 KiB
are also refused. SVGs up to 16 KiB are permitted for small code icons.
New or grown files of any type over 3 MiB (3,145,728 bytes), including UTF-8
source and data, require an allow-list exception.

The Repo hygiene CI check compares the PR head with its merge base against
the target branch. Unchanged existing files may stay; media may shrink for
optimisation, but
equal-size replacements and growth are checked under the same rules as new
files. Small SVGs remain permitted. Renaming a file
counts as adding its new path. No history is rewritten or existing asset moved.

After committing, run locally (also run by `python scripts/gate.py --job hygiene`):

```sh
python scripts/check_repo_media.py --base origin/main --head HEAD
```

A genuine code asset can receive a reviewed exception in
[the media allow list](../.github/media-allowlist.json). Keys are exact,
case-sensitive repository paths, never globs. Each entry needs a positive
byte cap and a specific reason explaining why this belongs with the code:

```json
{
  "tests/fixtures/example.png": {
    "max_bytes": 1024,
    "reason": "Minimal decoder fixture required by the image import test"
  }
}
```

The check reads this file from the checked head commit. An exception covers
only that path up to its cap; further growth needs another code review.
A missing revision or malformed allow list fails the check.
