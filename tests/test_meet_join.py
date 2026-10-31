"""Tests for the meet_agent layer: safety gate, session heuristics, verifier.

These tests do not require a browser or a live Google account, matching the
project's existing test style.
"""

from app.meet_agent.joiner import JoinResult
from app.meet_agent.link_detector import is_valid_meet_link
from app.meet_agent.safety import evaluate_join_safety
from app.meet_agent.session_check import (
    is_signed_in_evidence,
    looks_like_consent_screen,
    looks_like_login_wall,
)
from app.meet_agent.verifier import verify_join as verify


def test_safety_blocks_dry_run():
    url = "https://meet.google.com/abc-defg-hij"
    decision = evaluate_join_safety(dry_run=True, external_actions_enabled=True, meet_url=url)
    assert not decision.allowed
    assert decision.reason == "DRY_RUN"


def test_safety_blocks_external_actions_disabled():
    url = "https://meet.google.com/abc-defg-hij"
    decision = evaluate_join_safety(dry_run=False, external_actions_enabled=False, meet_url=url)
    assert not decision.allowed
    assert decision.reason == "EXTERNAL_ACTIONS_DISABLED"


def test_safety_blocks_invalid_link():
    decision = evaluate_join_safety(
        dry_run=False, external_actions_enabled=True, meet_url="https://meet.google.com/abc1-efg2-hi3"
    )
    assert not decision.allowed
    assert decision.reason == "INVALID_MEET_LINK"
    assert not is_valid_meet_link("https://meet.google.com/abc1-efg2-hi3")


def test_safety_allows_real_join():
    url = "https://meet.google.com/abc-defg-hij"
    decision = evaluate_join_safety(dry_run=False, external_actions_enabled=True, meet_url=url)
    assert decision.allowed


def test_login_wall_detection():
    assert looks_like_login_wall("https://accounts.google.com/ServiceLogin?continue=meet")
    assert not looks_like_login_wall("https://meet.google.com/abc-defg-hij")


def test_consent_detection():
    assert looks_like_consent_screen("https://accounts.google.com/o/oauth2/approval")


def test_signed_in_evidence_logic():
    live = is_signed_in_evidence(True, False, "https://meet.google.com/")
    assert live
    dead = is_signed_in_evidence(False, True, "https://meet.google.com/")
    assert not dead
    wall = is_signed_in_evidence(True, False, "https://accounts.google.com/ServiceLogin")
    assert not wall


def test_signed_out_marketing_landing_is_not_a_live_session():
    # A signed-out visit to meet.google.com redirects to the Workspace
    # marketing site; that must never count as a live Google session.
    assert not is_signed_in_evidence(False, False, "https://workspace.google.com/products/meet/")
    assert not is_signed_in_evidence(True, False, "https://workspace.google.com/")
    assert not is_signed_in_evidence(False, False, "https://example.com/anything")


def test_non_meet_hosts_are_rejected_before_marker_logic():
    assert not is_signed_in_evidence(False, False, "https://meet.google.com/")
    assert not is_signed_in_evidence(False, False, "https://google.com/")


def test_verifier_accepts_only_backed_success():
    backed = verify("JOINED", "IN_CALL_UI_VISIBLE", ["in_call_ui_visible"])
    assert backed.verified and backed.verdict == "MEET_JOIN_VERIFIED"

    unsupported = verify("JOINED", "IN_CALL_UI_VISIBLE", [])
    assert not unsupported.verified and unsupported.verdict == "MEET_JOIN_FAILED"


def test_verifier_auth_required_and_knocking():
    auth = verify("WAITING_FOR_AUTH", "GOOGLE_SESSION_MISSING")
    assert auth.verdict == "MEET_JOIN_AUTH_REQUIRED" and not auth.verified

    knock = verify("KNOCKING", "WAITING_FOR_HOST_ADMIT", ["knocking_wait_for_host"])
    assert knock.verdict == "MEET_JOIN_KNOCKING" and not knock.verified


def test_joinresult_states_exported():
    assert JoinResult("JOINED", "ok").status == "JOINED"
