from brr import pending_resume


def test_expired_claim_cannot_be_renewed_by_a_wrong_thread(tmp_path, monkeypatch):
    monkeypatch.setattr(pending_resume.time, 'time', lambda: 100000.0)
    pending_resume.arm(tmp_path, session_id='old', provider='codex',
                       conversation_key='cloud:telegram:owner:', from_run='run-old')
    monkeypatch.setattr(pending_resume.time, 'time',
                        lambda: 100000.0 + pending_resume.MAX_AGE_SECONDS + 1)
    assert pending_resume.consume(tmp_path, conversation_key='run:child') is None
    assert pending_resume.peek(tmp_path) is None


def test_unexpired_claim_keeps_its_age_after_a_wrong_thread(tmp_path, monkeypatch):
    monkeypatch.setattr(pending_resume.time, 'time', lambda: 100000.0)
    original = pending_resume.arm(tmp_path, session_id='old', provider='codex',
                                 conversation_key='cloud:telegram:owner:', from_run='run-old')
    monkeypatch.setattr(pending_resume.time, 'time', lambda: 100100.0)
    assert pending_resume.consume(tmp_path, conversation_key='run:child') is None
    assert pending_resume.peek(tmp_path)['armed_at'] == original['armed_at']


def test_a_new_claim_armed_during_restore_wins(tmp_path, monkeypatch):
    pending_resume.arm(tmp_path, session_id='old', provider='codex',
                       conversation_key='owner', from_run='run-old')
    real_link = pending_resume.os.link

    def arm_then_link(source, target):
        pending_resume.arm(tmp_path, session_id='new', provider='codex',
                           conversation_key='owner', from_run='run-new')
        return real_link(source, target)

    monkeypatch.setattr(pending_resume.os, 'link', arm_then_link)
    assert pending_resume.consume(tmp_path, conversation_key='run:child') is None
    assert pending_resume.consume(tmp_path, conversation_key='owner')['session_id'] == 'new'
    assert pending_resume.peek(tmp_path) is None
    assert not list(tmp_path.glob('*.claimed-*'))
