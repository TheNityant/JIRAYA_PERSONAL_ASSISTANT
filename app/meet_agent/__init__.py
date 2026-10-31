"""Meet agent layer.

1. Detect Google Meet links in Gmail text (link_detector.py)
2. Join meetings with evidence capture (joiner.py)
3. Live meeting assistant: name-call trigger -> LLM reply -> XTTS cloned
   voice of the owner (name_call_detector.py, reply_generator.py,
   voice_engine.py, meeting_assistant.py).

This package is intentionally isolated so the existing project is not affected.
"""

