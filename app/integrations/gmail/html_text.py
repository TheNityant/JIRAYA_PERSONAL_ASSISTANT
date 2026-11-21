"""Convert HTML mail to text while retaining visible labels and safe link targets."""

from html.parser import HTMLParser
import re

from app.integrations.gmail.content import valid_web_url


class EmailTextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.ignored: list[str] = []
        self.anchors: list[tuple[str, int]] = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "head"}:
            self.ignored.append(tag)
        if self.ignored:
            return
        if tag in {"br", "p", "div", "li", "hr", "blockquote"}:
            self.parts.append("\n")
        if tag == "blockquote":
            self.parts.append("> ")  # Retain a quote boundary for authored-text extraction.
        if tag == "a":
            target = dict(attrs).get("href") or ""
            self.anchors.append((target if valid_web_url(target) else "", len(self.parts)))

    def handle_endtag(self, tag):
        if self.ignored:
            if tag == self.ignored[-1]:
                self.ignored.pop()
            return
        if tag == "a" and self.anchors:
            target, start = self.anchors.pop()
            if target and target not in "".join(self.parts[start:]):
                self.parts.append(f" <{target}>")
        if tag in {"p", "div", "li", "blockquote"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.ignored:
            self.parts.append(data)


def html_to_text(value: str) -> str:
    parser = EmailTextParser()
    parser.feed(value)
    parser.close()
    text = "".join(parser.parts).replace("\xa0", " ")
    # Branded mail uses nested tables and layout elements, which otherwise
    # produce hundreds of blank lines in the plain-text conversation view.
    text = re.sub(r"[^\S\n]+", " ", text)
    return re.sub(r"\n(?:\s*\n){2,}", "\n\n", text).strip()
