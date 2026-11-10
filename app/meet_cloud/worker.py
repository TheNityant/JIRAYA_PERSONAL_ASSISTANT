"""Dedicated long-running browser worker. One meeting per process/container."""

import asyncio
import json
import logging
import secrets
import shutil
import signal
import tempfile
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, select

from app.config import get_base_settings, get_settings
from app.db import SessionLocal, init_db
from app.meet_agent.verifier import verify_join
from app.meet_cloud.browser import AudioSink, CloudJoiner
from app.meet_cloud.store import ACTIVE, MEET_READ_SCOPE, aware, claim, claim_summary, enqueue
from app.meet_cloud.transcripts import google_transcript, summarize
from app.models import CloudMeetSession, CloudMeetSettings, RuntimeState, User
from app.providers.nebius import NebiusProvider
from app.routes.meet import upcoming_meetings
from app.security.identity import account_scope
from app.security.tokens import TokenCipher
from app.services.connection_store import ConnectionStore
from app.services.notification_store import NotificationStore
from app.services.runtime_state_store import RuntimeStateStore

logger = logging.getLogger(__name__)


def heartbeat(worker_id):
    with SessionLocal() as db:
        RuntimeStateStore(db).set(
            "cloud-meet-worker", {"at": datetime.now(UTC).isoformat(), "worker": worker_id}
        )


def discover(settings):
    with SessionLocal() as db:
        users = db.execute(
            select(User.id, User.email)
            .join(CloudMeetSettings, CloudMeetSettings.user_id == User.id)
            .where(CloudMeetSettings.enabled.is_(True))
        ).all()
        db.execute(
            delete(CloudMeetSession).where(
                CloudMeetSession.created_at
                < datetime.now(UTC) - timedelta(days=settings.meet_cloud_retention_days),
                CloudMeetSession.status.not_in(ACTIVE),
            )
        )
        for marker in db.scalars(
            select(RuntimeState).where(RuntimeState.key.startswith("cloud-meet-deleted:"))
        ).all():
            if datetime.fromisoformat(marker.value["expires_at"]) < datetime.now(UTC):
                db.delete(marker)
        db.commit()
    if not settings.enable_external_actions:
        return
    for user_id, email in users:
        try:
            with account_scope(user_id, email), SessionLocal() as db:
                preference = db.get(CloudMeetSettings, user_id)
                feed = upcoming_meetings(db)
                if feed.get("status") != "READY" or feed.get(
                    "calendar_account"
                ) != preference.preferences.get("calendar_account"):
                    continue
                now = datetime.now(UTC)
                for event in feed["items"]:
                    if -120 <= (datetime.fromisoformat(event["start"]) - now).total_seconds() <= 30:
                        enqueue(db, user_id, feed["calendar_account"], event)
        except Exception as exc:  # noqa: BLE001 - isolate per-meeting provider failures
            logger.warning("Cloud Meet discovery failed (%s)", type(exc).__name__)


def make_summary(key):
    with SessionLocal() as db:
        row = db.get(CloudMeetSession, key)
        if not row:
            return
        user = db.get(User, row.user_id)
    if not user:
        return
    with account_scope(user.id, user.email), SessionLocal() as db:
        settings = get_settings()
        row = db.get(CloudMeetSession, key)
        cipher = TokenCipher(settings.token_encryption_key)
        turns = (
            json.loads(cipher.decrypt(row.transcript_ciphertext))
            if row.transcript_ciphertext
            else []
        )
        # A failed join has no captured meeting content. Do not imply a Google
        # transcript is pending or call the summary model automatically. An explicit
        # later refresh can still retrieve a transcript the account may access.
        if not turns and row.status == "FAILED" and not row.details.get("explicit_summary_refresh"):
            row.summary_state = "DONE"
            row.transcript_ciphertext = ""
            row.details = {
                **row.details,
                "summary_status": "NO_ATTENDANCE",
                "summary_requested": False,
                "summary_error": "The assistant did not join this meeting. No audio was captured and no automatic summary was requested.",
            }
            db.commit()
            return
        source = "assemblyai"
        google_reason = ""
        if row.details.get("prefer_google_transcript", True):
            try:
                turns = google_transcript(db, settings, row)
                source = "google_meet"
            except Exception:  # noqa: BLE001 - missing permissions/artifacts fall back to live text
                google_reason = "Google transcript unavailable, not generated yet, or access not granted. Using captured speech when available."
                connection = ConnectionStore(db).get_active(
                    user_id=user.id, provider="google_calendar"
                )
                deadline = row.details.get("google_wait_until")
                if (
                    deadline
                    and connection
                    and MEET_READ_SCOPE in connection.scopes
                    and datetime.fromisoformat(deadline) > datetime.now(UTC)
                ):
                    row.summary_state = "QUEUED"
                    row.details = {
                        **row.details,
                        "summary_status": "WAITING_FOR_GOOGLE",
                        "next_summary_at": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
                    }
                    db.commit()
                    return
        try:
            summary = summarize(NebiusProvider(settings), turns)
            row.summary_state = "DONE"
            row.summary_ciphertext = cipher.encrypt(summary)
            # Preserve the live transcript for a later explicit Google refresh.
            row.details = {
                **row.details,
                "summary_status": "READY",
                "summary_source": source,
                "summary_error": "",
                "google_transcript_note": google_reason,
                "summary_requested": False,
            }
            NotificationStore(db).create_once(
                user_id=user.id,
                category="MEETING_SUMMARY",
                title="Meeting notes ready",
                message="Your cloud meeting summary is available in Meet.",
                source="meet_cloud",
                source_entity_id=row.id,
                dedupe_key="meet-summary:" + row.id,
            )
        except Exception:  # noqa: BLE001 - preserve transcript for an explicit summary retry
            row.summary_state = "FAILED"
            row.details = {
                **row.details,
                "summary_status": "UNAVAILABLE",
                "summary_requested": False,
                "summary_error": "No usable transcript or summary provider is unavailable. Configure Nebius and retry after the meeting.",
            }
        db.add(row)
        db.commit()


async def attend(key, worker_id, shutdown):
    with SessionLocal() as db:
        row = db.get(CloudMeetSession, key)
        user = db.get(User, row.user_id)
    if not user:
        return
    with account_scope(user.id, user.email):
        settings = get_settings()
        cipher = TokenCipher(settings.token_encryption_key)
        turns = {}
        last_save = 0
        admitted = False

        async def tick():
            nonlocal last_save
            with SessionLocal() as db:
                row = db.get(CloudMeetSession, key)
                preference = db.get(CloudMeetSettings, user.id)
                if not row or row.worker_id != worker_id or row.status not in ACTIVE:
                    return True
                row.heartbeat_at = datetime.now(UTC)
                if asyncio.get_running_loop().time() - last_save > 10:
                    row.transcript_ciphertext = (
                        cipher.encrypt(json.dumps(list(turns.values()))) if turns else ""
                    )
                    last_save = asyncio.get_running_loop().time()
                connection = ConnectionStore(db).get_active(
                    user_id=user.id, provider="google_calendar"
                )
                stop = (
                    not connection
                    or connection.provider_account_id != row.calendar_account
                    or shutdown.is_set()
                    or row.stop_requested
                    or not preference
                    or not preference.enabled
                    or preference.preferences.get("calendar_account") != row.calendar_account
                    or not settings.enable_external_actions
                )
                db.commit()
                return stop

        async def turn(value):
            # Upsert formatted/unformatted versions of one finalized turn.
            order = value.get("order")
            turns[order if order is not None else len(turns)] = value
            if sum(len(t["text"]) for t in turns.values()) > 500_000:
                raise RuntimeError("Transcript size limit reached.")

        def joined():
            nonlocal admitted
            admitted = True
            with SessionLocal() as db:
                session = db.get(CloudMeetSession, key)
                session.status = "JOINED"
                session.heartbeat_at = datetime.now(UTC)
                db.commit()

        with SessionLocal() as db:
            session = db.get(CloudMeetSession, key)
            preference = db.get(CloudMeetSettings, user.id)
            feed = await asyncio.to_thread(upcoming_meetings, db)
            fresh = next(
                (
                    event
                    for event in feed.get("items", [])
                    if event["id"] == session.event_id
                    and datetime.fromisoformat(event["start"]) == aware(session.starts_at)
                    and event["url"] == session.meeting_url
                ),
                None,
            )
            if (
                not fresh
                or not preference
                or not preference.enabled
                or feed.get("calendar_account") != session.calendar_account
            ):
                session.status = "CANCELLED"
                session.details = {
                    "reason": "Event removed, changed, declined or Calendar disconnected."
                }
                db.commit()
                return
            ends = min(
                aware(session.ends_at),
                datetime.now(UTC) + timedelta(minutes=settings.meet_cloud_max_minutes),
            )
            preferences = dict(preference.preferences)
        if await tick():
            with SessionLocal() as db:
                row = db.get(CloudMeetSession, key)
                row.status = "CANCELLED"
                db.commit()
            return
        stop = threading.Event()
        audio = AudioSink("jiraya_" + key[:16])
        joiner = None
        result = None
        reason = ""

        # Heartbeats/cancellation must continue during the browser's admission wait too.
        async def monitor():
            try:
                while not shutdown.is_set():
                    if await tick():
                        stop.set()
                        return
                    await asyncio.sleep(5)
                stop.set()
            except Exception:  # noqa: BLE001 - lose control access -> leave instead of continuing unattended
                stop.set()
                logger.warning("Cloud meeting control heartbeat failed")

        monitor_task = asyncio.create_task(monitor())
        try:
            await audio.setup()
            with tempfile.TemporaryDirectory(prefix="jiraya-meet-") as directory:
                joiner = CloudJoiner(
                    audio=audio,
                    settings=settings,
                    on_turn=turn,
                    tick=tick,
                    leave_on_speech=preferences.get("leave_on_speech", True),
                    profile_dir=str(Path(directory) / "profile"),
                    evidence_dir=str(Path(directory) / "evidence"),
                    stay_seconds=max(1, (ends - datetime.now(UTC)).total_seconds()),
                    stop_event=stop,
                    on_joined=joined,
                    leave_at=ends,
                    join_timeout_seconds=120,
                )
                result = await joiner.join(fresh["url"])
                reason = joiner.leave_reason or result.reason
        except Exception as exc:  # noqa: BLE001 - isolate per-meeting provider failures
            reason = "Cloud runtime unavailable: " + type(exc).__name__
        finally:
            monitor_task.cancel()
            await asyncio.gather(monitor_task, return_exceptions=True)
            await audio.close()
            with SessionLocal() as db:
                row = db.get(CloudMeetSession, key)
                if row and row.worker_id == worker_id:
                    verified = bool(
                        result
                        and verify_join(
                            result.status,
                            result.reason,
                            result.evidence.notes if result.evidence else [],
                        ).verified
                    )
                    row.status = "COMPLETED" if verified else "PARTIAL" if admitted else "FAILED"
                    row.transcript_ciphertext = (
                        cipher.encrypt(json.dumps(list(turns.values()))) if turns else ""
                    )
                    row.summary_state = "QUEUED" if admitted or turns else "DONE"
                    row.details = {
                        "reason": reason,
                        "admitted": admitted,
                        "coverage": "captured while bot was admitted"
                        if verified
                        else "partial or no attendance",
                        "prefer_google_transcript": preferences.get(
                            "prefer_google_transcript", True
                        ),
                        "google_wait_until": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
                        "summary_status": "PENDING" if admitted or turns else "NO_ATTENDANCE",
                        "summary_requested": bool(admitted or turns),
                        "summary_error": ""
                        if admitted or turns
                        else "The assistant did not join this meeting. No audio was captured.",
                        "media_checks": [
                            note
                            for note in (
                                result.evidence.notes if result and result.evidence else []
                            )
                            if note.startswith(("mic_muted_", "camera_off_"))
                        ][-4:],
                    }
                    row.heartbeat_at = datetime.now(UTC)
                    db.commit()


async def preflight():
    if not all(shutil.which(command) for command in ("pactl", "ffmpeg")):
        raise RuntimeError("Use Dockerfile.meet: the cloud worker requires PulseAudio and FFmpeg.")
    process = await asyncio.create_subprocess_exec(
        "pactl", "info", stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
    )
    try:
        await asyncio.wait_for(process.wait(), 10)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise RuntimeError("PulseAudio is not ready in the cloud worker.") from None
    if process.returncode:
        raise RuntimeError("PulseAudio is not ready in the cloud worker.")
    from playwright.async_api import async_playwright

    async with async_playwright() as browser:
        if not Path(browser.chromium.executable_path).exists():
            raise RuntimeError("Install Chromium in Dockerfile.meet before starting the worker.")


async def serve(settings, shutdown=None):
    if not settings.meet_cloud_enabled or not settings.assemblyai_api_key:
        raise RuntimeError(
            "Set MEET_CLOUD_ENABLED=true and ASSEMBLYAI_API_KEY on the dedicated cloud Meet worker."
        )
    TokenCipher(settings.token_encryption_key)
    await preflight()
    init_db()
    shutdown = shutdown or asyncio.Event()
    worker_id = secrets.token_hex(16)
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, shutdown.set)

    async def health():
        while not shutdown.is_set():
            try:
                await asyncio.to_thread(heartbeat, worker_id)
            except Exception:  # noqa: BLE001 - lost control database must stop attendance
                logger.warning("Cloud worker lost database control access; stopping")
                shutdown.set()
                return
            await asyncio.sleep(10)

    task = asyncio.create_task(health())
    summary_task = None
    try:
        while not shutdown.is_set():
            try:
                await asyncio.to_thread(discover, settings)
                with SessionLocal() as db:
                    key = claim(db, worker_id)
                if key:
                    await attend(key, worker_id, shutdown)
                if summary_task is None or summary_task.done():
                    if summary_task is not None:
                        try:
                            summary_task.result()
                        except Exception:  # noqa: BLE001 - isolate failed summary jobs
                            logger.warning("Cloud summary task failed")
                    with SessionLocal() as db:
                        pending = db.scalars(
                            select(CloudMeetSession)
                            .where(CloudMeetSession.summary_state == "QUEUED")
                            .order_by(CloudMeetSession.created_at)
                            .limit(50)
                        ).all()
                        for candidate in pending:
                            next_at = candidate.details.get("next_summary_at")
                            if next_at and datetime.fromisoformat(next_at) > datetime.now(UTC):
                                continue
                            pending_key = candidate.id
                            if claim_summary(db, pending_key):
                                summary_task = asyncio.create_task(
                                    asyncio.to_thread(make_summary, pending_key)
                                )
                                break
            except Exception as exc:  # noqa: BLE001 - isolate per-meeting provider failures
                logger.warning("Cloud Meet worker cycle failed (%s)", type(exc).__name__)
            try:
                await asyncio.wait_for(shutdown.wait(), 10)
            except TimeoutError:
                pass
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if summary_task is not None:
            await summary_task


def main():
    logging.basicConfig(level=logging.INFO)
    asyncio.run(serve(get_base_settings()))


if __name__ == "__main__":
    main()
