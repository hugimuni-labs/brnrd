"""The daemon's own log lines, stamped — and the wrapper that nearly ate the
line-buffering that protects them.

Two fixes written the same night, hours apart, for one failure: after a daemon
died mid-flight, `brr.out.log` could not say *when* any of its lines were
written, and its final line was a splice of two processes' output. "Did the
daemon die?" had no answer in the daemon's own record.

- #1928 stamps every line with a UTC time.
- #1932 line-buffers the streams so an `execve` or a `SIGKILL` cannot destroy
  an 8 KB block buffer of the newest lines.

Neither knew about the other. The stamping wrapper replaced `sys.stdout`
*before* the buffering call reached it, had no `reconfigure`, and the
`AttributeError` landed in an `except` clause written for a test's `StringIO`.
The guard for an exotic stream swallowed a real instruction.
"""

import io
import re

from brr import daemon


STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z ")


class _Recon(io.StringIO):
    """A StringIO that records `reconfigure` calls the way a real stream takes them."""

    def __init__(self) -> None:
        super().__init__()
        self.reconfigured: list[dict] = []

    def reconfigure(self, **kwargs) -> None:
        self.reconfigured.append(kwargs)


class TestTheStamp:
    def test_every_line_carries_a_utc_time(self):
        raw = io.StringIO()
        out = daemon._StampedStream(raw)
        out.write("[brnrd] tick\n[brnrd] dispatched\n")
        lines = raw.getvalue().splitlines()
        assert len(lines) == 2
        assert all(STAMP.match(line) for line in lines)
        assert lines[0].endswith("[brnrd] tick")

    def test_a_progression_written_in_pieces_stays_one_line(self):
        """`print(..., end="")` must not grow a stamp per fragment."""
        raw = io.StringIO()
        out = daemon._StampedStream(raw)
        out.write("[brnrd] scanning")
        out.write("...")
        out.write(" done\n")
        assert len(STAMP.findall(raw.getvalue())) == 1
        assert raw.getvalue().rstrip("\n").endswith("scanning... done")

    def test_a_blank_line_is_not_stamped(self):
        raw = io.StringIO()
        daemon._StampedStream(raw).write("\n")
        assert raw.getvalue() == "\n"

    def test_installing_twice_does_not_double_wrap(self, monkeypatch):
        raw = io.StringIO()
        monkeypatch.setattr("sys.stdout", raw)
        monkeypatch.setattr("sys.stderr", io.StringIO())
        daemon._install_log_stamps()
        import sys
        first = sys.stdout
        daemon._install_log_stamps()
        assert sys.stdout is first


class TestTheReconfigureThatWasSwallowed:
    def test_line_buffering_reaches_the_stream_under_the_wrapper(self):
        """The whole point: `start()` calls this on `sys.stdout`, which by then
        is a `_StampedStream`. Before the passthrough it raised
        `AttributeError` into a `pass`, and the buffering fix became a no-op on
        exactly the deployment it was written for."""
        raw = _Recon()
        daemon._StampedStream(raw).reconfigure(line_buffering=True)
        assert raw.reconfigured == [{"line_buffering": True}]

    def test_the_starts_own_call_is_not_swallowed(self, monkeypatch):
        """Driven through the two calls in the order `start()` makes them."""
        raw_out, raw_err = _Recon(), _Recon()
        monkeypatch.setattr("sys.stdout", raw_out)
        monkeypatch.setattr("sys.stderr", raw_err)
        daemon._install_log_stamps()
        import sys
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(line_buffering=True)
            except (AttributeError, OSError, ValueError):
                pass
        assert raw_out.reconfigured == [{"line_buffering": True}]
        assert raw_err.reconfigured == [{"line_buffering": True}]
