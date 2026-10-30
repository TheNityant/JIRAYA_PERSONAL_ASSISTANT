"""Preserve source meeting links independently of model-generated event summaries."""

from urllib.parse import urlsplit

from app.integrations.gmail.content import source_links

MEETING_HOSTS = (
    "meet.google.com",
    "zoom.us",
    "zoom.com",
    "teams.microsoft.com",
    "teams.live.com",
    "teams.cloud.microsoft",
    "webex.com",
    "meet.jit.si",
    "whereby.com",
)


def is_meeting_link(value: str) -> bool:
    host = (urlsplit(value).hostname or "").lower()
    return any(host == domain or host.endswith("." + domain) for domain in MEETING_HOSTS)


def calendar_link_details(description: str, body: str) -> tuple[dict, list[str]]:
    links = source_links(body)
    meetings = [url for url in links if is_meeting_link(url)]
    # Put meeting links first so long source emails cannot crowd them out.
    ordered = meetings + [url for url in links if url not in meetings]
    selected: list[str] = []
    budget = 6000
    for url in ordered:
        if len(url) + 1 <= budget:
            selected.append(url)
            budget -= len(url) + 1
    if selected and not all(url in description for url in selected):
        suffix = "\n\nLinks from email:\n" + "\n".join(selected)
        description = description[: 8000 - len(suffix)].rstrip() + suffix
    return {
        "description": description,
        "location": meetings[0] if len(meetings) == 1 else "",
    }, selected


def links_verified(event: dict, proposal: dict, links: list[str]) -> bool:
    from html import unescape

    return all(url in unescape(str(event.get("description") or "")) for url in links) and (
        not proposal.get("location") or event.get("location") == proposal["location"]
    )
