"""loom/sample.request makes the loop sample its own threads, without root."""
import time
from types import SimpleNamespace

from brr.loom.runtime import sampler


def test_a_request_samples_the_main_thread_into_collapsed_stacks(tmp_path):
    (tmp_path / "loom").mkdir()
    (tmp_path / "loom" / "sample.request").write_text("0.3\n")
    logged = []
    home = SimpleNamespace(root=tmp_path)
    s = sampler.Sampler(home, lambda home, text: logged.append(text))
    s.step()
    assert not (tmp_path / "loom" / "sample.request").exists()
    deadline = time.monotonic() + 0.5
    while time.monotonic() < deadline:  # the test is the main thread the sampler reads
        sum(range(1000))
    s.worker.join(5)
    written = list((tmp_path / "loom" / "samples").glob("*.collapsed"))
    assert len(written) == 1
    lines = written[0].read_text().splitlines()
    assert lines and all(line.rsplit(" ", 1)[1].isdigit() for line in lines)
    assert any(line.startswith("MainThread;") and "test_a_request_samples_the_main_thread" in line
               for line in lines)
    assert not any(line.startswith("loom-sampler;") for line in lines)
    assert any(text.startswith("sample: wrote") for text in logged)


def test_a_sample_names_each_thread_it_reads(tmp_path):
    import threading
    stop = threading.Event()
    def spin():
        while not stop.is_set():
            sum(range(1000))
    other = threading.Thread(target=spin, name="loom-spin", daemon=True)
    other.start()
    try:
        stacks, taken = sampler.collect(0.2)
    finally:
        stop.set()
        other.join(5)
    assert taken and any(stack.startswith("loom-spin;") and "spin" in stack for stack in stacks)


def test_no_request_starts_nothing_and_a_bad_one_is_dropped(tmp_path):
    (tmp_path / "loom").mkdir()
    logged = []
    s = sampler.Sampler(SimpleNamespace(root=tmp_path), lambda home, text: logged.append(text))
    s.step()
    assert s.worker is None
    (tmp_path / "loom" / "sample.request").write_text("soon")
    s.step()
    assert s.worker is None and not (tmp_path / "loom" / "sample.request").exists()
    assert "not a number" in logged[0]
