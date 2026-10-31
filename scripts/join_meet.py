"""Ad-hoc Meet join runner: python scripts/join_meet.py <meet_url> [--headless] [--timeout N]"""

import argparse
import sys
import warnings

# Suppress ResourceWarning and closed pipe destructor noise on Windows in Python 3.13
warnings.filterwarnings("ignore", category=ResourceWarning)
if sys.platform == "win32":
    try:
        from asyncio.proactor_events import _ProactorBasePipeTransport

        _orig_del = getattr(_ProactorBasePipeTransport, "__del__", None)

        def _safe_del(self):
            try:
                if _orig_del:
                    _orig_del(self)
            except Exception:
                pass

        _ProactorBasePipeTransport.__del__ = _safe_del
    except Exception:
        pass

from app.meet_agent.joiner import run_join


def main() -> None:
    parser = argparse.ArgumentParser(description="Join a Google Meet and report the result.")
    parser.add_argument("url", help="Full Google Meet URL")
    parser.add_argument("--headless", action="store_true", help="Run browser headless")
    parser.add_argument("--timeout", type=float, default=300.0, help="Join timeout in seconds")
    parser.add_argument(
        "--auth-grace", type=float, default=300.0,
        help="Seconds to keep the browser open waiting for a manual Google sign-in",
    )
    parser.add_argument(
        "--stay", type=float, default=60.0,
        help="Seconds to stay in the call before leaving; negative = stay until you close the window",
    )
    parser.add_argument(
        "--no-leave", action="store_true",
        help="Skip clicking the Leave call button (window kill only)",
    )
    args = parser.parse_args()

    result = run_join(
        args.url,
        headless=args.headless,
        join_timeout_seconds=args.timeout,
        auth_grace_seconds=args.auth_grace,
        stay_seconds=args.stay,
        leave_call=not args.no_leave,
    )
    print(result)


if __name__ == "__main__":
    sys.exit(main())
