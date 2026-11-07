"""Tests du client Gmail."""

from unittest.mock import patch

import pytest

from custom_components.hydroqc_mfa.const import REASON_MESSAGES
from custom_components.hydroqc_mfa.gmail import (
    GmailCode,
    GmailError,
    GmailInbox,
    extract_code,
    validate_gmail,
)

from .fake_imap import PASSWORD, FakeImap, hq_message


def _inbox(fake: FakeImap) -> GmailInbox:
    with patch("custom_components.hydroqc_mfa.gmail.imaplib.IMAP4_SSL", new=fake):
        inbox = GmailInbox("olivier@gmail.com", PASSWORD)
        inbox.connect()
    return inbox


def test_extract_code_accepts_expected_message():
    assert extract_code(hq_message("123456")) == "123456"


def test_extract_code_html_visible_text_only():
    assert extract_code(hq_message("123456", html=True)) == "123456"
    body = (
        "<html><head><style>.a{width:111111px}</style></head><body>"
        '<p class="222222" title="333333">Votre code est 123456</p>'
        "<script>var x = 444444;</script></body></html>"
    )
    assert extract_code(hq_message("", html=True, body=body)) == "123456"
    attr_only = '<html><body><p title="555555">Aucun code</p></body></html>'
    assert extract_code(hq_message("", html=True, body=attr_only)) is None


def test_extract_code_rejects_wrong_sender():
    assert extract_code(hq_message("123456", sender="phisher@example.com")) is None


def test_extract_code_rejects_wrong_subject():
    assert extract_code(hq_message("123456", subject="Autre sujet")) is None


def test_extract_code_ambiguous():
    with pytest.raises(GmailError) as err:
        extract_code(hq_message("", body="Codes 123456 et 654321"))
    assert err.value.reason == "ambiguous"
    assert str(err.value) == "ambiguous"
    assert "ambiguous" in REASON_MESSAGES


def test_connect_bad_password():
    fake = FakeImap(password="other")
    with pytest.raises(GmailError) as err:
        _inbox(fake)
    assert err.value.reason == "gmail_auth_failed"
    assert PASSWORD not in str(err.value)
    assert PASSWORD not in repr(err.value)
    assert fake.logged_out


def test_connect_network_failure_is_unavailable():
    def boom(*args, **kwargs):
        raise OSError("down")

    with patch("custom_components.hydroqc_mfa.gmail.imaplib.IMAP4_SSL", new=boom):
        with pytest.raises(GmailError) as err:
            GmailInbox("u", "p").connect()
    assert err.value.reason == "gmail_unavailable"


def test_connect_uses_verified_tls_and_timeout():
    fake = FakeImap()
    _inbox(fake)
    assert fake.connect_args == ("imap.gmail.com", 993)
    assert fake.connect_kwargs["timeout"] == 10
    assert fake.connect_kwargs["ssl_context"].check_hostname is True


def test_poll_ignores_messages_before_checkpoint():
    fake = FakeImap([(1, hq_message("123456"))])
    inbox = _inbox(fake)
    inbox.checkpoint()
    assert inbox.poll() is None
    fake.add(hq_message("654321"))
    receipt = inbox.poll()
    assert receipt is not None
    assert receipt.code == "654321"
    assert receipt.uid == 2


def test_poll_ignores_non_matching_and_already_examined():
    fake = FakeImap()
    inbox = _inbox(fake)
    inbox.checkpoint()
    fake.add(hq_message("111111", sender="phisher@example.com"))
    assert inbox.poll() is None
    fetches = [c for c in fake.commands if c.startswith("FETCH")]
    assert inbox.poll() is None
    assert [c for c in fake.commands if c.startswith("FETCH")] == fetches


def test_poll_ambiguous_across_messages():
    fake = FakeImap()
    inbox = _inbox(fake)
    inbox.checkpoint()
    fake.add(hq_message("111111"))
    fake.add(hq_message("222222"))
    with pytest.raises(GmailError) as err:
        inbox.poll()
    assert err.value.reason == "ambiguous"


def test_poll_oversized_message_is_unavailable():
    fake = FakeImap()
    inbox = _inbox(fake)
    inbox.checkpoint()
    fake.add(hq_message("", body="x" * 300000))
    with pytest.raises(GmailError) as err:
        inbox.poll()
    assert err.value.reason == "gmail_unavailable"


def test_poll_uses_peek():
    fake = FakeImap()
    inbox = _inbox(fake)
    inbox.checkpoint()
    fake.add(hq_message("654321"))
    inbox.poll()
    fetches = [c for c in fake.commands if c.startswith("FETCH")]
    assert fetches
    assert all("BODY.PEEK" in c for c in fetches)
    assert "readonly=True" in next(c for c in fake.commands if c.startswith("SELECT"))


def test_trash_uses_label_never_expunge():
    fake = FakeImap()
    inbox = _inbox(fake)
    inbox.checkpoint()
    uid = fake.add(hq_message("654321"))
    receipt = inbox.poll()
    inbox.trash(receipt)
    assert f"STORE {uid} +X-GM-LABELS (\\Trash)" in fake.commands
    assert not any("EXPUNGE" in c or "DELETE" in c for c in fake.commands)


def test_trash_refuses_when_uidvalidity_changed():
    fake = FakeImap()
    inbox = _inbox(fake)
    inbox.checkpoint()
    fake.add(hq_message("654321"))
    receipt = inbox.poll()
    fake.uidvalidity = 99
    with pytest.raises(GmailError) as err:
        inbox.trash(receipt)
    assert err.value.reason == "gmail_unavailable"
    assert not any(c.startswith("STORE") for c in fake.commands)


def test_gmail_code_repr_hides_code():
    assert "123456" not in repr(GmailCode(1, b"1", "123456"))


def test_validate_gmail_connects_checks_and_closes():
    fake = FakeImap()
    with patch("custom_components.hydroqc_mfa.gmail.imaplib.IMAP4_SSL", new=fake):
        validate_gmail("olivier@gmail.com", PASSWORD)
    assert fake.logged_out
    with patch("custom_components.hydroqc_mfa.gmail.imaplib.IMAP4_SSL", new=FakeImap(password="x")):
        with pytest.raises(GmailError):
            validate_gmail("olivier@gmail.com", PASSWORD)
