"""Generate committed profile SVGs from public GitHub data, without a PAT."""
from __future__ import annotations

import io
import json
import re
import time
from datetime import date, datetime, timedelta, timezone
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[2]
USER = "mang-os"
PALETTE = ["#161b22", "#0e4429", "#006d32", "#26a641", "#7ee787"]


def download(url):
    for attempt in range(3):
        try:
            req = Request(url, headers={"User-Agent": "mang-os-profile-art", "Accept": "*/*"})
            with urlopen(req, timeout=30) as response:
                return response.read()
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)


class CalendarParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.cells = {}
        self.tooltips = {}
        self.tooltip_id = None
        self.tooltip_text = []

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if "data-date" in attrs and "data-level" in attrs:
            self.cells[attrs["data-date"]] = {
                "id": attrs.get("id"),
                "level": int(attrs["data-level"]),
                "count": int(attrs["data-count"]) if attrs.get("data-count", "").isdigit() else None,
            }
        if tag == "tool-tip":
            self.tooltip_id = attrs.get("for")
            self.tooltip_text = []

    def handle_data(self, text):
        if self.tooltip_id is not None:
            self.tooltip_text.append(text)

    def handle_endtag(self, tag):
        if tag == "tool-tip" and self.tooltip_id is not None:
            self.tooltips[self.tooltip_id] = " ".join(self.tooltip_text)
            self.tooltip_id = None


def fetch_calendar():
    today = datetime.now(timezone.utc).date()
    parser = CalendarParser()
    parser.feed(download(f"https://github.com/users/{USER}/contributions?to={today.isoformat()}").decode("utf-8"))
    days = []
    for day_text, cell in sorted(parser.cells.items()):
        day = date.fromisoformat(day_text)
        if day > today:
            continue
        level = cell["level"]
        if level not in range(5):
            raise ValueError(f"Unexpected contribution level: {level}")
        count = cell["count"]
        tooltip = parser.tooltips.get(cell["id"], "")
        match = re.search(r"\b([\d,]+)\s+contributions?\b", tooltip, re.IGNORECASE)
        if count is None and match:
            count = int(match.group(1).replace(",", ""))
        if count is None and level == 0:
            count = 0
        days.append({"date": day_text, "level": level, "count": count})
    if not 350 <= len(days) <= 372:
        raise ValueError(f"Calendar HTML changed or is incomplete: {len(days)} days")
    for previous, current in zip(days, days[1:]):
        if date.fromisoformat(current["date"]) - date.fromisoformat(previous["date"]) != timedelta(days=1):
            raise ValueError("Calendar contains missing dates")
    return {"user": USER, "updated": today.isoformat(), "days": days}


def ascii_portrait():
    user = json.loads(download(f"https://api.github.com/users/{USER}"))
    avatar = Image.open(io.BytesIO(download(user["avatar_url"]))).convert("L")
    avatar = ImageOps.fit(avatar, (48, 32), method=Image.Resampling.LANCZOS)
    avatar = ImageOps.autocontrast(avatar)
    ramp = " .:-=+*#%@"
    rows = []
    for y in range(32):
        line = ""
        for x in range(48):
            # Blank the corners to echo the circular GitHub avatar.
            circular = ((x - 23.5) / 24) ** 2 + ((y - 15.5) / 16) ** 2 <= 1
            pixel = avatar.getpixel((x, y))
            line += ramp[min(len(ramp) - 1, pixel * len(ramp) // 256)] if circular else " "
        rows.append(
            f'<text xml:space="preserve" x="69" y="{91 + y * 6}" '
            f'font-size="7.5" fill="#c9d1d9" class="reveal" '
            f'style="animation-delay:{y * .025:.3f}s">{escape(line)}</text>'
        )
    template = (ROOT / "scripts/profile/terminal-template.svg").read_text(encoding="utf-8")
    if template.count("{{PORTRAIT}}") != 1:
        raise ValueError("Expected one portrait placeholder")
    return template.replace("{{PORTRAIT}}", "\n".join(rows))


def heatmap(calendar):
    days = calendar["days"]
    first = date.fromisoformat(days[0]["date"])
    origin = first - timedelta(days=(first.weekday() + 1) % 7)
    weeks = (date.fromisoformat(days[-1]["date"]) - origin).days // 7 + 1
    if weeks > 53:
        raise ValueError("Calendar exceeds 53 columns")
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="860" height="232" viewBox="0 0 860 232" role="img" aria-labelledby="title desc">',
        '<title id="title">mang-os public contribution calendar</title>',
        '<desc id="desc">Public GitHub contribution activity. Each square is one day.</desc>',
        '<style>text{font-family:ui-monospace,SFMono-Regular,Consolas,"Liberation Mono",monospace}.cell{animation:enter .45s ease-out both}@keyframes enter{from{opacity:0;transform:translateY(3px)}to{opacity:1;transform:translateY(0)}}@media(prefers-reduced-motion:reduce){.cell{animation:none!important}}</style>',
        '<rect x="1" y="1" width="858" height="230" rx="14" fill="#0d1117" stroke="#30363d"/>',
        '<path d="M1 43H859" stroke="#30363d"/>',
        '<circle cx="23" cy="23" r="4" fill="#f47067"/><circle cx="39" cy="23" r="4" fill="#e3b341"/><circle cx="55" cy="23" r="4" fill="#57ab5a"/>',
        '<text x="82" y="28" font-size="12" fill="#7ee787">mang-os ~ $ ./activity</text>',
        '<text x="811" y="28" font-size="11" text-anchor="end" fill="#8b949e">public contributions</text>',
    ]
    for label, row in [("Mon", 1), ("Wed", 3), ("Fri", 5)]:
        parts.append(f'<text x="17" y="{92 + row * 13}" font-size="10" fill="#8b949e">{label}</text>')
    labelled_months = set()
    for day in days:
        current = date.fromisoformat(day["date"])
        delta = (current - origin).days
        column, row = divmod(delta, 7)
        x, y = 58 + column * 14, 82 + row * 13
        month = (current.year, current.month)
        if current.day <= 7 and month not in labelled_months:
            labelled_months.add(month)
            parts.append(f'<text x="{x}" y="69" font-size="10" fill="#8b949e">{current.strftime("%b")}</text>')
        delay = (column + row) * .017
        title = f'{day["count"]} contributions' if day["count"] is not None else f'activity level {day["level"]}'
        parts.append(
            f'<rect x="{x}" y="{y}" width="11" height="10" rx="2" fill="{PALETTE[day["level"]]}" '
            f'class="cell" style="animation-delay:{delay:.3f}s"><title>{escape(day["date"] + ": " + title)}</title></rect>'
        )
    active = sum(day["level"] > 0 for day in days)
    if all(day["count"] is not None for day in days):
        summary = f'{sum(day["count"] for day in days):,} contributions / {active} active days'
    else:
        summary = f"{active} active days"
    parts.append(f'<text x="28" y="204" font-size="12" fill="#c9d1d9">{summary}</text>')
    parts.append(f'<text x="28" y="221" font-size="10" fill="#8b949e">Updated {calendar["updated"]} UTC</text>')
    parts.append('<text x="680" y="205" font-size="10" fill="#8b949e">Less</text>')
    for index, color in enumerate(PALETTE):
        parts.append(f'<rect x="{714 + index * 13}" y="195" width="10" height="10" rx="2" fill="{color}"/>')
    parts.append('<text x="785" y="205" font-size="10" fill="#8b949e">More</text></svg>')
    return "\n".join(parts) + "\n"


def write_atomic(path, contents):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(contents, encoding="utf-8")
    temporary.replace(path)


def main():
    # Finish and validate all generation before changing any published artwork.
    calendar = fetch_calendar()
    terminal = ascii_portrait()
    graph = heatmap(calendar)
    for document in [terminal, graph]:
        ElementTree.fromstring(document)
    readme_path = ROOT / "README.md"
    readme = readme_path.read_text(encoding="utf-8")
    if "<!-- PROFILE-CONTRIBUTIONS -->" not in readme:
        readme = (
            '<!-- PROFILE-CONTRIBUTIONS -->\n'
            '<p align="center">\n'
            '  <img src="./assets/contribution-calendar.svg" width="860" alt="mang-os public GitHub contribution calendar, refreshed daily." />\n'
            '</p>\n\n' + readme
        )
    write_atomic(ROOT / "assets/profile-terminal.svg", terminal)
    write_atomic(ROOT / "assets/contribution-calendar.svg", graph)
    write_atomic(ROOT / "data/contributions.json", json.dumps(calendar, indent=2) + "\n")
    write_atomic(readme_path, readme)
    print(f'Generated avatar and calendar for {len(calendar["days"])} real days.')


if __name__ == "__main__":
    main()
