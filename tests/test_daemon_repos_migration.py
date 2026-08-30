from brnrd import migrations
from brnrd.models import DaemonRepo


def test_daemon_repo_model_and_migration_seed_legacy_rows():
    assert set(DaemonRepo.__table__.c.keys()) == {
        "id", "daemon_id", "repo_id", "last_seen_at",
        "agents_md_missing", "kb_missing",
    }
    statements: list[str] = []

    class FakeConn:
        def execute(self, statement):
            statements.append(str(statement))

    migrations._migrate_daemon_repos(FakeConn())

    seed = next(sql for sql in statements if "INSERT INTO daemon_repos" in sql)
    assert "repo_agents_md_missing" in seed
    assert "repo_kb_missing" in seed
    assert "last_seen_at" in seed
    assert "WHERE repo_id IS NOT NULL" in seed
    assert "ON CONFLICT (daemon_id, repo_id) DO NOTHING" in seed
