# First task

With the daemon running, send a message through the gate you connected:

```text
review PR #84 for the auth regression; show me the risky bit before changing it
```

Three things should happen in the same thread:

1. A progress card appears and changes as the resident reads, plans, and works.
2. You can add a fact or redirect the work; brnrd folds it in at a runner
   boundary without killing the thought in flight.
3. The run closes with a durable receipt: a branch, a pull request, or an
   answer.

Check the daemon if nothing appears:

```bash
brnrd daemon status
brnrd daemon logs --no-follow
```

You can also exercise the same runner locally without a remote gate:

```bash
brnrd run "summarize the test layout; do not change files"
```

The first recorded end-to-end demo is tracked in
[Gurio/brr#28](https://github.com/Gurio/brr/issues/28).
