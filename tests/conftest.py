import os

# Keep the automated test suite deterministic and safe regardless of the developer's
# local .env. Individual tests can still override these settings explicitly.
os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
os.environ["ENABLE_EXTERNAL_ACTIONS"] = "false"
os.environ["GMAIL_BACKGROUND_MONITOR_ENABLED"] = "false"
os.environ["CALENDAR_BACKGROUND_MONITOR_ENABLED"] = "false"
