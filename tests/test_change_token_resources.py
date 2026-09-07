"""The portal change_token must not move because the resident's last reply cost tokens."""
from brr import daemon


def test_change_token_ignores_resources():
    base = {"inbound": {"pending": 0}, "resources": {"context_window": {"tokens": 1}},
            "generated_at": "t1"}
    moved = {**base, "resources": {"context_window": {"tokens": 999}, "allowance": {"spent": 5}},
             "generated_at": "t2"}
    assert daemon._change_token(base) == daemon._change_token(moved)
    assert daemon._change_token(base) != daemon._change_token({**base, "inbound": {"pending": 1}})
