"""Opt-in device attendance: poll the user's Calendar and join due meetings once."""

import hashlib
import json
import threading
from datetime import UTC, datetime
from urllib.request import HTTPRedirectHandler, Request, build_opener

from app.meet_agent.joiner import MeetJoiner
from app.meet_agent.link_detector import is_valid_meet_link


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def open_private(request, timeout):
    return build_opener(NoRedirect).open(request, timeout=timeout)


def owner_key(config):
    return hashlib.sha256(
        (
            config.get("api_base", "")
            + config.get("account_id", "")
            + config.get("calendar_account", config.get("account_id", ""))
        ).encode()
    ).hexdigest()[:32]


class MeetDevice:
    def __init__(self, state):
        self.state = state
        self.shutdown = threading.Event()
        self.call_stop = threading.Event()
        self.thread = None
        self.last_check = None
        self.error = None
        self.active = None
        with state.guard:
            changed = False
            for row in state.config.get("meet_history", {}).values():
                if row.get("status") in {"STARTING", "JOINED"}:
                    row.update(status="REVIEW_REQUIRED", reason="DEVICE_RESTARTED")
                    changed = True
            if changed:
                state.save()

    def feed(self, config):
        request = Request(
            config["api_base"] + "/meet/device/events",
            headers={
                "Authorization": "Bearer " + config["device_token"],
                "Accept": "application/json",
            },
        )
        with open_private(request, timeout=20) as response:
            return json.load(response)

    def configure(self, api_base, token):
        config = {"api_base": api_base.rstrip("/"), "device_token": token, "enabled": True}
        feed = self.feed(config)  # Validate the scoped credential before saving.
        if feed.get("status") != "READY":
            raise ValueError(
                "Enable external actions and reconnect Calendar before enabling attendance"
            )
        if not feed.get("account_id"):
            raise ValueError("Device authorization did not identify an account")
        config["account_id"] = feed["account_id"]
        config["calendar_account"] = feed.get("calendar_account", feed["account_id"])
        with self.state.guard:
            if self.active:
                raise ValueError("Leave the current meeting before changing device accounts")
            previous = self.state.config.get("meet") or {}
            if previous.get("device_token"):
                self.revoke(previous)
            self.state.config["meet"] = config
            self.state.save()
        self.start()
        return self.status()

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.shutdown.clear()
        self.thread = threading.Thread(target=self.run, daemon=True, name="meet-attendance")
        self.thread.start()

    def stop(self):
        self.shutdown.set()
        self.call_stop.set()

    def run(self):
        while not self.shutdown.is_set():
            try:
                self.poll()
            except Exception as exc:  # noqa: BLE001 - isolate browser/provider failures from the device service
                self.error = (
                    "Attendance check failed: "
                    + type(exc).__name__
                    + ". Reconnect Meet if authorization expired."
                )
            self.shutdown.wait(20)

    def poll(self, *, now=None, joiner_factory=MeetJoiner):
        with self.state.guard:
            config = dict(self.state.config.get("meet") or {})
        if not config.get("enabled"):
            return
        data = self.feed(config)
        if data.get("account_id") != config["account_id"]:
            raise ValueError("Device account changed")
        now = now or datetime.now(UTC)
        self.last_check, self.error = now.isoformat(), None
        if data.get("status") != "READY":
            self.error = data.get("status", "Calendar is not ready")
            return
        if data.get("calendar_account", data.get("account_id")) != config.get(
            "calendar_account", config["account_id"]
        ):
            raise ValueError("Calendar account changed. Re-enable attendance for this account.")
        for row in sorted(
            data.get("items", []), key=lambda item: datetime.fromisoformat(item["start"])
        ):
            if not is_valid_meet_link(row.get("url", "")):
                continue
            start, end = datetime.fromisoformat(row["start"]), datetime.fromisoformat(row["end"])
            if start.tzinfo is None or end.tzinfo is None or end <= now:
                continue
            owner = owner_key(config)
            key = hashlib.sha256((owner + row["id"] + start.isoformat()).encode()).hexdigest()
            with self.state.guard:
                current = self.state.config.get("meet") or {}
                if (
                    not current.get("enabled")
                    or current.get("device_token") != config.get("device_token")
                    or self.shutdown.is_set()
                ):
                    return
                history = self.state.config.setdefault("meet_history", {})
                retry = history.get(key, {}).get("status") == "RETRY_REQUESTED"
                if not -120 <= (start - now).total_seconds() <= 30 and not (retry and start <= now):
                    continue
                if key in history and not retry:
                    continue
                # Persist before touching the browser: crashes must never auto-rejoin.
                history[key] = {
                    "key": key,
                    "owner": owner,
                    "status": "STARTING",
                    "event_id": row["id"],
                    "start": row["start"],
                }
                while len(history) > 200:
                    history.pop(next(iter(history)))
                self.state.save()
                self.active = key
                self.call_stop.clear()

            def joined(history=history, key=key):
                with self.state.guard:
                    history[key]["status"] = "JOINED"
                    self.state.save()

            try:
                import asyncio

                joiner = joiner_factory(
                    profile_dir=str(self.state.directory / "meet_profiles" / owner),
                    evidence_dir=str(self.state.directory / "meet_evidence" / owner),
                    headless=False,
                    stay_seconds=min(14400, max(1, (end - now).total_seconds())),
                    stop_event=self.call_stop,
                    on_joined=joined,
                    leave_at=end,
                )
                result = asyncio.run(joiner.join(row["url"]))
                from app.meet_agent.verifier import verify_join

                verified = verify_join(
                    result.status, result.reason, result.evidence.notes if result.evidence else []
                ).verified
                with self.state.guard:
                    history[key].update(
                        status="COMPLETED"
                        if verified
                        else "UNVERIFIED"
                        if result.status == "JOINED"
                        else result.status,
                        reason=result.reason,
                    )
                    self.state.save()
            except Exception as exc:  # noqa: BLE001 - isolate browser/provider failures from the device service
                with self.state.guard:
                    history[key].update(status="FAILED", reason=type(exc).__name__)
                    self.state.save()
            finally:
                self.active = None
            break  # One browser/profile at a time; never join overlapping meetings.

    def retry(self, key):
        with self.state.guard:
            config = self.state.config.get("meet") or {}
            owner = owner_key(config)
            row = self.state.config.get("meet_history", {}).get(key)
            if self.active or not config.get("enabled") or not row or row.get("owner") != owner:
                raise ValueError(
                    "Enable this device and finish any active call before retrying its meeting"
                )
            if row["status"] not in {
                "FAILED",
                "WAITING_FOR_AUTH",
                "KNOCKING",
                "CANCELLED",
                "UNVERIFIED",
                "REVIEW_REQUIRED",
            }:
                raise ValueError("Only failed or unverified attendance may be retried")
            row["status"] = "RETRY_REQUESTED"
            self.state.save()
        return self.status()

    def revoke(self, config):
        try:
            request = Request(
                config["api_base"] + "/meet/device/authorization",
                method="DELETE",
                headers={"Authorization": "Bearer " + config["device_token"]},
            )
            with open_private(request, timeout=10):
                pass
        except (OSError, ValueError):
            self.error = "Attendance paused locally. Cloud revocation was unavailable; that authorization will expire."

    def pause(self):
        with self.state.guard:
            if self.state.config.get("meet"):
                self.revoke(dict(self.state.config["meet"]))
                self.state.config["meet"].pop("device_token", None)
                self.state.config["meet"]["enabled"] = False
                self.state.save()
        self.call_stop.set()
        return self.status()

    def status(self):
        with self.state.guard:
            config = self.state.config.get("meet") or {}
            owner = owner_key(config)
            history = [
                row
                for row in self.state.config.get("meet_history", {}).values()
                if row.get("owner") == owner
            ]
            return {
                "configured": bool(config.get("device_token")),
                "enabled": bool(config.get("enabled")),
                "active": bool(self.active),
                "last_check": self.last_check,
                "error": self.error,
                "recent": history[-10:][::-1],
            }
