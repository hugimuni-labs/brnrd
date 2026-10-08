# The loom contract

A wake is this directory's `wake` script, run with the working directory set to a clone of the self at one commit. The script prints the prompt. brnrd does not assemble that prompt itself, and it does not read the seed again after `init`.

The script takes a thread id and a path to `owed.json` (`[{id, from, body}]`). `LOOM_BUDGET_BYTES` defaults to 24000. `LOOM_PART` is a file the loom writes; its contents are printed whole.

Order: identity, stance, this thread's README and its notes, then one line for every other markdown file under `memory/`, `threads/` and `people/` (nothing under `hearth/`), then the loom part, then the owed letters. Identity, stance, the thread, the loom part and the letters are never cut. If those alone exceed the budget the script exits 3 and names what did not fit. Otherwise the tree is cut from the oldest end, and the last line says how many entries were left out. Each of those is one read away.

Exit 0 is a prompt. Exit 3 is over budget. Any other exit is a broken recipe.
