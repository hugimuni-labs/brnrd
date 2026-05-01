import pytest

from brr import envs


def test_get_env_returns_real_builtins():
    assert envs.get_env("local").name == "local"
    assert envs.get_env("worktree").name == "worktree"


def test_get_env_rejects_future_backend_until_implemented():
    with pytest.raises(envs.UnsupportedEnvironmentError) as exc:
        envs.get_env("docker")

    assert "environment backend 'docker' is not available yet" in str(exc.value)
