"""Tests for the throwaway scripts in docs/spike/start-instant-meeting/.

They cover only what needs no Google account: reading a Space answer, turning API errors into
plain text, never leaking a secret, refusing a non-local Vexa, and reading a bot's status
timeline (the real E2 captures are the fixtures). The live calls are not tested here.
"""

import base64
import json
import sys
from pathlib import Path

import pytest

SPIKE = Path(__file__).resolve().parent.parent / "docs/spike/start-instant-meeting"
E2_LOGS = Path(__file__).resolve().parent.parent / "docs/spike/meet-join-experiment/logs"
sys.path.insert(0, str(SPIKE))

import instant_common as ic  # noqa: E402
import send_bot  # noqa: E402

# The shape Google documents for a Space (discovery document meet.googleapis.com v2, revision
# 20261029); the values are placeholders.
SPACE_BODY = {
    "name": "spaces/PLACEHOLDERid1",
    "meetingUri": "https://meet.google.com/abc-mnop-xyz",
    "meetingCode": "abc-mnop-xyz",
    "config": {"accessType": "TRUSTED", "entryPointAccess": "ALL"},
}


def jwt(claims: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJSUzI1NiJ9.{body}.signature"


def google_error(code: int, status: str, message: str, reason: str = "", metadata=None) -> str:
    error = {"code": code, "message": message, "status": status}
    if reason:
        error["details"] = [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": reason}]
        if metadata:
            error["details"][0]["metadata"] = metadata
    return json.dumps({"error": error})


# --- parse_space -----------------------------------------------------------------------------


def test_parse_space_reads_link_code_and_access_type():
    space = ic.parse_space(SPACE_BODY)
    assert space.meeting_uri == "https://meet.google.com/abc-mnop-xyz"
    assert space.meeting_code == "abc-mnop-xyz"
    assert space.access_type == "TRUSTED"
    assert space.entry_point_access == "ALL"


def test_parse_space_without_config_has_no_access_type():
    body = {k: v for k, v in SPACE_BODY.items() if k != "config"}
    assert ic.parse_space(body).access_type is None


def test_parse_space_takes_the_code_from_the_link_when_google_omits_it():
    body = {k: v for k, v in SPACE_BODY.items() if k != "meetingCode"}
    assert ic.parse_space(body).meeting_code == "abc-mnop-xyz"


@pytest.mark.parametrize(
    "mutation",
    [
        {"name": "people/123"},
        {"name": None},
        {"meetingUri": "https://evil.example/abc-mnop-xyz"},
        {"meetingUri": "http://meet.google.com/abc-mnop-xyz"},
        {"meetingUri": None},
        {"meetingCode": "abc-mnop-zzz"},  # disagrees with the link
        {"meetingCode": "NOT A CODE", "meetingUri": "https://meet.google.com/NOT A CODE"},
        {"config": "TRUSTED"},
    ],
)
def test_parse_space_rejects_bad_answers(mutation):
    with pytest.raises(ValueError):
        ic.parse_space({**SPACE_BODY, **mutation})


@pytest.mark.parametrize("body", [None, [], "text", 7])
def test_parse_space_rejects_non_objects(body):
    with pytest.raises(ValueError):
        ic.parse_space(body)


# --- explain_error ---------------------------------------------------------------------------


def test_api_not_enabled_points_at_the_activation_page_and_billing_stop():
    text = google_error(
        403,
        "PERMISSION_DENIED",
        "Google Meet REST API has not been used in project 123 before or it is disabled.",
        reason="SERVICE_DISABLED",
        metadata={"activationUrl": "https://console.developers.google.com/apis/api/meet"},
    )
    out = ic.explain_error(403, text)
    assert "https://console.developers.google.com/apis/api/meet" in out
    assert "billing" in out and "STOP" in out


def test_other_403_is_reported_as_the_account_type_answer_with_googles_words():
    out = ic.explain_error(
        403, google_error(403, "PERMISSION_DENIED", "The caller does not have permission")
    )
    assert "REFUSED" in out
    assert "The caller does not have permission" in out


def test_missing_scope_says_to_run_auth_again():
    text = google_error(
        403,
        "PERMISSION_DENIED",
        "Request had insufficient authentication scopes.",
        reason="ACCESS_TOKEN_SCOPE_INSUFFICIENT",
    )
    assert "auth.py" in ic.explain_error(403, text)


def test_401_and_429_have_their_own_advice():
    assert "auth.py" in ic.explain_error(401, google_error(401, "UNAUTHENTICATED", "expired"))
    assert "Wait a few minutes" in ic.explain_error(
        429, google_error(429, "RESOURCE_EXHAUSTED", "quota")
    )


def test_unreadable_error_body_is_still_explained_and_redacted():
    out = ic.explain_error(502, "<html>bad gateway for someone@example.com</html>")
    assert "502" in out
    assert "someone@example.com" not in out


def test_error_messages_never_carry_an_email_or_token():
    message = "token ya29.A0ARrdaMsecretvalue for someone@example.com refused"
    out = ic.explain_error(400, google_error(400, "INVALID_ARGUMENT", message))
    assert "ya29" not in out and "someone@example.com" not in out


# --- redact, account kind, secrets -----------------------------------------------------------


def test_redact_removes_tokens_secrets_and_emails():
    text = "a ya29.AAAA-bbb_c b 1//0gREFRESH-x c GOCSPX-sec_ret d me@gmail.com e " + jwt({"a": 1})
    out = ic.redact(text)
    for leaked in ("ya29", "1//0g", "GOCSPX", "me@gmail.com", "eyJ"):
        assert leaked not in out


def test_account_kind_tells_gmail_workspace_and_other_apart_without_the_address():
    gmail = ic.account_kind(jwt({"email": "someone@gmail.com"}))
    workspace = ic.account_kind(jwt({"email": "someone@corp.example", "hd": "corp.example"}))
    other = ic.account_kind(jwt({"email": "someone@corp.example"}))
    assert "personal Gmail" in gmail and "Workspace" in workspace and "another domain" in other
    assert not any("someone" in x or "corp.example" in x for x in (gmail, workspace, other))
    assert "unknown" in ic.account_kind("not-a-jwt")
    assert "unknown" in ic.account_kind("")


def test_load_client_explains_a_wrong_client_type(tmp_path, monkeypatch):
    monkeypatch.setattr(ic, "LOCAL", tmp_path)
    (tmp_path / "client_secret.json").write_text(json.dumps({"web": {"client_id": "x"}}))
    with pytest.raises(ic.SpikeError, match="Desktop app"):
        ic.load_client()


def test_load_client_reports_a_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(ic, "LOCAL", tmp_path)
    with pytest.raises(ic.SpikeError, match="client_secret.json"):
        ic.load_client()


def test_save_private_is_owner_only(tmp_path):
    target = tmp_path / "local" / "token.json"
    ic.save_private(target, {"a": 1})
    assert target.stat().st_mode & 0o777 == 0o600


# --- the Vexa guard --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url", ["http://localhost:8056", "http://127.0.0.1:8056/", "http://[::1]:8056"]
)
def test_local_vexa_is_accepted(url):
    assert ic.require_local_vexa(url) == url.rstrip("/")


@pytest.mark.parametrize(
    "url",
    [
        "https://notes.beyrictech.com",
        "http://203.0.113.9:8056",
        "http://localhost.evil.example:8056",
        "http://localhost@evil.example:8056",
        "https://localhost:8056",
        "http://vexa:8056",
    ],
)
def test_anything_but_this_mac_is_refused(url):
    with pytest.raises(ic.SpikeError, match="never send a bot from the production server"):
        ic.require_local_vexa(url)


def test_read_vexa_key_accepts_a_bare_key_or_a_named_line(tmp_path):
    bare = tmp_path / "bare"
    bare.write_text("# comment\nvx_PLACEHOLDER\n")
    named = tmp_path / "named"
    named.write_text("OTHER=1\nVEXA_API_KEY='vx_PLACEHOLDER'\n")
    assert ic.read_vexa_key(bare) == "vx_PLACEHOLDER"
    assert ic.read_vexa_key(named) == "vx_PLACEHOLDER"


def test_read_vexa_key_error_names_the_lines_but_never_their_values(tmp_path):
    path = tmp_path / "env"
    path.write_text("FIRST=vx_SECRETONE\nSECOND=vx_SECRETTWO\n")
    with pytest.raises(ic.SpikeError) as caught:
        ic.read_vexa_key(path)
    text = str(caught.value)
    assert "FIRST" in text and "SECOND" in text
    assert "SECRETONE" not in text and "SECRETTWO" not in text


def test_meeting_argument_accepts_a_code_or_a_link_and_nothing_else():
    assert send_bot.meeting_url("abc-mnop-xyz") == (
        "abc-mnop-xyz",
        "https://meet.google.com/abc-mnop-xyz",
    )
    assert send_bot.meeting_url("https://meet.google.com/abc-mnop-xyz?authuser=1")[0] == (
        "abc-mnop-xyz"
    )
    for bad in ("https://zoom.us/j/123", "https://evil.example/abc-mnop-xyz", "hello"):
        with pytest.raises(ic.SpikeError):
            send_bot.meeting_url(bad)


# --- the bot timeline (real E2 captures as fixtures) -----------------------------------------


def e2(name: str) -> dict:
    lines = (E2_LOGS / f"{name}-timeline.txt").read_text().splitlines()
    return ic.summarise_timeline(ic.parse_timeline(lines))


def test_real_capture_b1_knocked_then_was_let_in():
    result = e2("B1")
    assert result["outcome"] == "knocked_then_in"
    assert result["seconds_to_waiting_room"] == 40.7
    assert result["seconds_to_active"] == 46.8


def test_real_capture_b2_knocked_and_was_left_waiting():
    result = e2("B2")
    assert result["outcome"] == "knocked_waiting"
    assert result["knocked"] is True


def test_parse_timeline_keeps_only_status_changes():
    lines = [
        "t+  0.3s status=requested reason=None stage=None",
        "t+  3.0s status=requested reason=None stage=None",
        "t+  6.0s status=joining reason=None stage=None",
    ]
    assert ic.parse_timeline(lines) == [(0.3, "requested", None), (6.0, "joining", None)]


def test_straight_to_active_is_walked_in():
    events = ic.parse_timeline(
        [
            "t+  0.3s status=requested reason=None stage=None",
            "t+  6.0s status=joining reason=None stage=None",
            "t+ 14.0s status=active reason=None stage=None",
        ]
    )
    result = ic.summarise_timeline(events)
    assert (result["outcome"], result["knocked"]) == ("walked_in", False)


def test_typed_google_refusal_is_refused():
    events = ic.parse_timeline(
        [
            "t+  3.0s status=joining reason=None stage=None",
            "t+ 60.0s status=failed reason=google_meet_join_refused: 3 knocks stage=None",
        ]
    )
    assert ic.summarise_timeline(events)["outcome"] == "refused"


def test_knock_then_failure_is_not_admitted():
    events = ic.parse_timeline(
        [
            "t+  3.0s status=joining reason=None stage=None",
            "t+ 20.0s status=awaiting_admission reason=None stage=None",
            "t+170.0s status=failed reason=admission_timeout stage=None",
        ]
    )
    assert ic.summarise_timeline(events)["outcome"] == "knocked_not_admitted"


def test_no_events_is_no_result():
    assert ic.summarise_timeline([])["outcome"] == "no_result"
