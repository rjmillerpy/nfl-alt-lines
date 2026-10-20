"""Phone-friendly HTML version of the text report.

The games become cards that wrap to the screen; the wide tables go in boxes that scroll sideways.
Styles are inline because mail apps drop style sheets.

Run `uv run python -m nfl_alt_lines.page` to write reports/latest.html from reports/latest.txt in storage.
"""

import html
import logging
import re
from datetime import date

from nfl_alt_lines.common import storage
from nfl_alt_lines.common.logs import configure_logging
from nfl_alt_lines.common.storage import Storage

REPORTS = "reports"  # in storage: the latest report, one dated copy per day, the page and the email mark
REPORT = f"{REPORTS}/latest.txt"
PAGE = f"{REPORTS}/latest.html"
RED, GREEN, BLUE, GREY = "#b00020", "#0a7a2f", "#1a56b0", "#666666"
BORDER = "#d9dce1"
QUIET_LABEL, PRICE_LABEL = "no opinion:", "best price:"
LABELS = {
    "STAY AWAY:": (RED, "Stay away"),
    "WORTH A LOOK:": (GREEN, "Worth a look"),
    PRICE_LABEL: (GREEN, "Best price"),
    "PROP LEAN:": (BLUE, "Prop lean"),
    QUIET_LABEL: (GREY, "No opinion"),
}
GAME = re.compile(r"^(\S+ @ \S+)\s+\((.*?)\)\s*(.*)$")
DAY = re.compile(r"^[A-Z]+DAY [A-Z]{3} \d{2} - ")
ACTIONS = ("SINGLE ", "BY 2s:", "ROUND ROBIN at")
ROBINS = ("SUNDAY ROUND ROBIN", "PAPER ")
PLAN_START = "WHAT TO BET"
GLANCE_START = "AT A GLANCE"
WORTH_UNDER = "WORTH A LOOK: under ("
SUB_INDENT, SUB_INDENT_PX = "    ", 14  # a line under a bet (the round robin's legs) is indented on the page too
BOOK = re.compile(r"^=+ (\w+) =+$")
BODY = "margin:0;padding:10px;font-family:Arial,Helvetica,sans-serif;font-size:15px;color:#111111;background:#f3f4f6"
CARD = f"background:#ffffff;border:1px solid {BORDER};border-radius:8px;padding:10px 12px;margin:0 0 10px 0"
WIDE = "overflow-x:auto;-webkit-overflow-scrolling:touch"
MONO = "margin:0;font-family:Menlo,Consolas,monospace;font-size:11px;line-height:1.35;white-space:pre"


def _line(text: str) -> str:
    """One indented line of a game card."""
    text = text.strip()
    for key, (colour, label) in LABELS.items():
        if text.startswith(key):
            rest = html.escape(text[len(key) :].strip())
            quiet = "nothing flagged" in rest or key == QUIET_LABEL
            weight = "normal" if quiet or key == PRICE_LABEL else "bold"
            shade = GREY if quiet else colour
            return f"<div style='margin:3px 0;color:{shade};font-weight:{weight}'>{label}: {rest}</div>"
    return f"<div style='margin:3px 0'>{html.escape(text)}</div>"


def _has_worth_under(block: list[str]) -> bool:
    return any(line.strip().startswith(WORTH_UNDER) for line in block)


def _game_card(heading: re.Match[str], block: list[str]) -> str:
    game, when, extra = heading.groups()
    weather, _, note = extra.partition("- ")
    details = " &middot; ".join(html.escape(part.strip()) for part in (when, weather, note) if part.strip())
    edge = GREEN if _has_worth_under(block) else BORDER
    return (
        f"<div style='{CARD};border-left:5px solid {edge}'>"
        f"<div style='font-size:17px;font-weight:bold'>{html.escape(game)}</div>"
        f"<div style='font-size:12px;color:{GREY};margin-bottom:6px'>{details}</div>"
        f"{''.join(_line(line) for line in block[1:])}</div>"
    )


def _plan_line(line: str) -> str:
    """One line of the betting plan: day headings bold, bets in green, games with no bet in grey."""
    text = html.escape(line.strip())
    if DAY.match(line):
        return f"<div style='margin:12px 0 4px 0;font-weight:bold;border-bottom:1px solid {BORDER}'>{text}</div>"
    if not line.startswith(" "):
        return f"<div style='margin:3px 0;font-size:12px;color:{GREY}'>{text}</div>"
    indent = SUB_INDENT_PX if line.startswith(SUB_INDENT) else 0
    if any(word in line for word in ACTIONS):
        style = f"color:{GREEN};font-weight:bold"
    elif "no bet" in line:  # also "no bets"
        style = f"color:{GREY}"
    else:
        style = ""
    return f"<div style='margin:4px 0 4px {indent}px;{style}'>{text}</div>"


def _plan_card(block: list[str]) -> str:
    title = (
        f"<div style='font-size:18px;font-weight:bold;color:{GREEN};margin-bottom:4px'>{html.escape(block[0])}</div>"
    )
    return (
        f"<div style='{CARD};border:2px solid {GREEN}'>{title}{''.join(_plan_line(line) for line in block[1:])}</div>"
    )


def _wide(lines: list[str]) -> str:
    return f"<div style='{CARD};{WIDE}'><pre style='{MONO}'>{html.escape(chr(10).join(lines))}</pre></div>"


def _heading(text: str) -> str:
    return f"<div style='font-size:16px;font-weight:bold;margin:16px 0 8px 2px'>{html.escape(text)}</div>"


def _blocks(lines: list[str]) -> list[list[str]]:
    """Runs of non-blank lines."""
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line.strip():
            current.append(line)
        elif current:
            blocks.append(current)
            current = []
    return [*blocks, current] if current else blocks


def _split_at(lines: list[str], prefix: str | tuple[str, ...]) -> list[list[str]]:
    """Sections that each begin at a line starting with `prefix`."""
    sections: list[list[str]] = []
    for line in lines:
        if line.startswith(prefix) or not sections:
            sections.append([])
        sections[-1].append(line)
    return ["\n".join(section).rstrip().splitlines() for section in sections]


def _top(lines: list[str]) -> list[str]:
    """Everything above the round-robin details: the plan card, a summary of unders, game cards and notes."""
    parts = []
    unders = []
    lead = []
    for block in _blocks(lines):
        heading = GAME.match(block[0])
        if block[0].startswith(PLAN_START):
            lead.append(_plan_card(block))
        elif heading:
            parts.append(_game_card(heading, block))
            if _has_worth_under(block):
                unders.append(heading.group(1))
        elif block[0].startswith(GLANCE_START):
            parts.append(_heading("This week at a glance"))
            parts.append(f"<div style='font-size:12px;color:{GREY};margin:0 2px 8px 2px'>{html.escape(block[0])}</div>")
        else:
            notes = "".join(f"<div style='margin:3px 0'>{html.escape(line)}</div>" for line in block)
            parts.append(f"<div style='font-size:12px;color:{GREY};margin:0 2px 10px 2px'>{notes}</div>")
    if unders:
        summary = f"<b style='color:{GREEN}'>Unders worth a look:</b> {html.escape(', '.join(unders))}"
        lead.append(f"<div style='{CARD};border-left:5px solid {GREEN}'>{summary}</div>")
    return [*lead, *parts]


def _book_tables(lines: list[str]) -> list[str]:
    """Each book's section (from its ===== BOOK ===== line to the next) as boxes that scroll sideways."""
    books = [(index, match.group(1)) for index, line in enumerate(lines) if (match := BOOK.match(line))]
    if not books:  # without this guard a report with no book section crashed render()
        return []
    ends = [index for index, _ in books[1:]] + [len(lines)]
    parts = []
    for (start, book), end in zip(books, ends, strict=True):
        parts.append(_heading(f"{book.title()} tables (scroll sideways)"))
        parts += [_wide(block) for block in _blocks(lines[start + 1 : end])]
    return parts


def render(report: str) -> str:
    """The whole report as one HTML document."""
    lines = report.splitlines()
    first_book = next((i for i, line in enumerate(lines) if BOOK.match(line)), len(lines))
    robin = next((i for i, line in enumerate(lines[:first_book]) if line.startswith(ROBINS)), first_book)
    parts = _top(lines[:robin])
    if robin < first_book:
        parts.append(_heading("Round robin details (scroll sideways)"))
        parts += [_wide(block) for block in _split_at(lines[robin:first_book], ROBINS)]
    parts += _book_tables(lines)
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'><title>NFL alt lines</title></head>"
        f"<body style='{BODY}'><div style='max-width:640px;margin:0 auto'>{''.join(parts)}</div></body></html>"
    )


log = logging.getLogger(__name__)


def dated_report(day: date) -> str:
    """The key of the day's copy of the report."""
    return f"{REPORTS}/{day:%Y-%m-%d}.txt"


def write_page(store: Storage) -> None:
    """Render the latest text report into the HTML page."""
    store.write(PAGE, render(store.read(REPORT).decode()).encode())
    log.info("page written", extra={"page": PAGE})


def main(store: Storage | None = None) -> None:
    configure_logging("page")
    write_page(store or storage.default_storage())


if __name__ == "__main__":
    main()
