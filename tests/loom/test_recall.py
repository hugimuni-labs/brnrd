"""related: a new wish, ask or thread is shown its closest threads, settled ones included."""
import pytest

from brr.loom.runtime import recall
from brr.loom.runtime.home import Home
from brr.loom.runtime.ledger import inject_letter
from test_wake import cli, commit, write_thread
from test_wishes import ledger  # noqa: F401  (fixture)


def _thread(root, name, headline, *, status="open", wish=""):
    folder = root / "self" / "threads" / name
    folder.mkdir(parents=True, exist_ok=True)
    extra = f'wish-w-{name[:6]}aaaaaa: "{wish}"\n' if wish else ""
    (folder / "README.md").write_text(
        f"---\nid: {name}\nstatus: {status}\ntense: plan\n{extra}---\n# {headline}\n\n"
        "First paragraph of prose.\n\n## Plan\n\n- something else entirely\n")


@pytest.fixture
def threads(tmp_path):
    root = tmp_path / "home"
    _thread(root, "forge-link", "Every thread opens on the forge in one click",
            status="settled", wish="The board's thread title links to its folder on the forge")
    _thread(root, "machines", "A resident survives its machine",
            wish="One leader loom at a time across installs; a second machine takes over the inbox lease")
    _thread(root, "follow-main", "The loom follows main by itself",
            wish="On a green main CI the loom fast-forwards and re-execs at an idle tick")
    _thread(root, "landing", "brnrd.dev gets a real landing page")
    return Home(root)


def test_the_thread_holding_the_same_wish_comes_first(threads):
    ranked = recall.rank("a second machine takes over the inbox lease, one leader loom",
                         recall.corpus(threads))
    assert ranked[0][1] == "machines"


def test_a_settled_thread_is_recalled_and_says_so(threads):
    line = recall.related_line(threads, "the board links each thread title to the forge")
    assert line.startswith("related: forge-link (settled")


def test_one_shared_word_names_nothing_and_the_target_is_left_out(threads):
    assert recall.related_line(threads, "landing") == ""
    line = recall.related_line(threads, "loom follows main, fast-forwards on green CI",
                               exclude={"follow-main"})
    assert "follow-main" not in line


def test_wish_new_prints_related_beside_the_id(ledger):
    home, seat = ledger
    _thread(home.root, "machines", "A resident survives its machine",
            wish="One leader loom at a time across installs; a second machine takes over the inbox lease")
    commit(home.root / "self", "threads")
    letter = inject_letter(home.root, to="thread:work", body="Please")
    result = cli(["wish", "--room", str(seat), "new", "--line",
                  "A second machine takes over the inbox lease when the leader loom dies", "--say", letter])
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().startswith("w-")
    assert "related: machines" in result.stderr


def test_an_ask_prints_related(ledger):
    home, seat = ledger
    _thread(home.root, "machines", "A resident survives its machine",
            wish="One leader loom at a time across installs; a second machine takes over the inbox lease")
    commit(home.root / "self", "threads")
    letter = inject_letter(home.root, to="thread:work", body="Please")
    parent = cli(["wish", "--room", str(seat), "new", "--line", "Build", "--say", letter]).stdout.strip()
    result = cli(["ask", "--room", str(seat), "Should a second machine take the inbox lease from the leader loom?",
                  "--for", parent])
    assert result.returncode == 0, result.stderr
    assert "related: machines" in result.stderr


def test_opening_a_thread_prints_related(ledger):
    home, seat = ledger
    _thread(home.root, "machines", "A resident survives its machine",
            wish="One leader loom at a time across installs; a second machine takes over the inbox lease")
    commit(home.root / "self", "threads")
    result = cli(["send", "--room", str(seat), "--to", "thread:failover",
                  "--wish", 'new:When the leader loom dies a second machine takes the inbox lease',
                  "Take over the inbox lease on a second machine"])
    assert result.returncode == 0, result.stderr
    assert "related: machines" in result.stderr
