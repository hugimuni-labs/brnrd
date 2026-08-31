"""Cache policy for the static SvelteKit shell.

The shell is the indirection between a deployment and its content-hashed JS
chunks. If ``index.html`` is reused heuristically after a rollout, a browser can
keep executing the previous dashboard even though the running container and
backend already report the new commit.
"""

from fastapi.testclient import TestClient

from brnrd.app import create_app
from brnrd.config import Settings


def test_spa_shell_must_revalidate_across_deploys(tmp_path):
    build = tmp_path / "build"
    build.mkdir()
    (build / "index.html").write_text("<!doctype html><title>brnrd</title>", encoding="utf-8")
    (build / "robots.txt").write_text("User-agent: *\n", encoding="utf-8")

    settings = Settings(
        database_url="sqlite://",
        telegram_auto_webhook=False,
        frontend_dir=str(build),
    )

    with TestClient(create_app(settings)) as client:
        # Every spelling that can return index.html has the same policy: the
        # browser may keep a copy, but it must ask the current container before
        # reusing it and therefore receives the new build's hashed chunk URLs.
        for path in ("/", "/repos", "/index.html"):
            response = client.get(path)
            assert response.status_code == 200, path
            assert response.headers["cache-control"] == "no-cache", path

        # This is deliberately a shell rule, not a blanket static-file rule.
        # Build assets can keep their independent caching semantics.
        static = client.get("/robots.txt")
        assert static.status_code == 200
        assert "cache-control" not in static.headers
