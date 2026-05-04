"""
FAQ answer-format linter.

The site uses FAQPage JSON-LD to capture Google's People Also Ask
SERP feature. SEMrush's April 2026 audit flagged that several FAQ
answers either bury the lede ("It depends on…", "Generally, in
most cases, …") or are too short to give Google a usable PAA
snippet. This script walks every FAQ data source and flags any
answer that violates the editorial format we want:

  • Direct, complete first sentence that answers the question
  • For yes/no questions, the first word should be "Yes" or "No"
  • A second sentence (or short paragraph) of supporting detail

Flag taxonomy:
  YES_NO_NOT_LED   yes/no question but answer doesn't open with Yes/No
  WEASEL_OPENER    first sentence starts with a hedging adverb
  SHORT_LEDE       first sentence < 6 words
  LONG_LEDE        first sentence > 35 words (run-on lede)
  NO_DETAIL        answer is a single sentence with no follow-up
  NO_PUNCT         first "sentence" lacks terminal punctuation

Output:
  Markdown report grouped by file, sorted by severity. Each item
  shows the file:line, question, first sentence, and flag(s).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parent.parent

# Files we know hold q/a-style FAQ data. Templates that *render* FAQs
# are excluded — they don't contain answer text, just bind to data.
FAQ_SOURCES: tuple[Path, ...] = (
    REPO_ROOT / "server.py",
    REPO_ROOT / "src" / "data" / "visa_application_content.py",
    REPO_ROOT / "src" / "data" / "people.py",
)


# ──────────────────────────────────────────────────────────────────
# Extraction
# ──────────────────────────────────────────────────────────────────

# Matches inline dict entries:
#   {"q": "How does X work?", "a": "Explanation."}
# Also handles single quotes.
_QA_DICT_RX = re.compile(
    r'["\']q["\']\s*:\s*["\'](?P<q>[^"\']+)["\']\s*,\s*'
    r'["\']a["\']\s*:\s*["\'](?P<a>(?:\\.|[^"\']|\\\n)+?)["\']\s*[,}\)]',
    re.DOTALL,
)

# Matches FAQ(q="…", a="…") dataclass invocations across multiple lines.
# Captures the q= and a= argument values regardless of order.
_FAQ_CALL_RX = re.compile(
    r"FAQ\s*\(\s*"
    r"(?:q\s*=\s*\(?\s*(?P<q>(?:\"[^\"]*\"|\'[^\']*\'|\s*\n\s*\"[^\"]*\")+)\s*\)?\s*,\s*)"
    r"a\s*=\s*\(?\s*(?P<a>(?:\"[^\"]*\"|\'[^\']*\'|\s*\n\s*\"[^\"]*\")+)\s*\)?\s*,?\s*\)",
    re.DOTALL,
)


def _string_literal_to_text(literal_block: str) -> str:
    """Concatenate a sequence of adjacent Python string literals
    (the multi-line "foo" "bar" pattern used heavily in this codebase)
    into a single string for analysis. Strips leading whitespace
    between literals."""
    pieces = re.findall(r'"((?:\\.|[^"\\])*)"|\'((?:\\.|[^\'\\])*)\'', literal_block)
    return "".join(a or b for a, b in pieces).strip()


def extract_faqs(path: Path) -> list[dict]:
    """Return [{q, a, lineno}] tuples found in `path`."""
    out: list[dict] = []
    text = path.read_text(encoding="utf-8")

    # Build a list of line offsets so we can resolve byte offsets to
    # 1-indexed line numbers for the report.
    line_starts = [0]
    for i, ch in enumerate(text):
        if ch == "\n":
            line_starts.append(i + 1)

    def lineno_of(byte_offset: int) -> int:
        # Binary search would be faster but corpora are small.
        for i, start in enumerate(line_starts):
            if start > byte_offset:
                return i  # i is 1-indexed because we count line breaks
        return len(line_starts)

    for m in _QA_DICT_RX.finditer(text):
        out.append({
            "q": m.group("q").strip(),
            "a": m.group("a").strip(),
            "lineno": lineno_of(m.start()),
        })

    for m in _FAQ_CALL_RX.finditer(text):
        q = _string_literal_to_text(m.group("q"))
        a = _string_literal_to_text(m.group("a"))
        if q and a:
            out.append({"q": q, "a": a, "lineno": lineno_of(m.start())})

    return out


# ──────────────────────────────────────────────────────────────────
# Linting
# ──────────────────────────────────────────────────────────────────

_YES_NO_OPENERS = (
    "is ", "are ", "was ", "were ", "do ", "does ", "did ",
    "has ", "have ", "had ", "can ", "could ", "will ", "would ",
    "should ", "shall ", "may ", "might ",
)

_WEASEL_OPENERS = (
    "well, ", "generally, ", "generally,", "typically, ", "typically,",
    "usually, ", "sometimes, ", "mostly, ", "in some cases", "in most cases",
    "it depends.", "it depends on", "well —", "broadly, ", "in general, ",
    "kind of", "sort of",
)

# Sentence splitter that respects abbreviations and decimal numbers
# better than naive `re.split(r"\.\s")`. Doesn't need to be perfect —
# we only need the first sentence cleanly.
_FIRST_SENTENCE_RX = re.compile(r"^(.+?[.!?])(?:\s|$)", re.DOTALL)


def first_sentence(answer: str) -> str:
    m = _FIRST_SENTENCE_RX.match(answer.strip())
    if m:
        return m.group(1).strip()
    return answer.strip()


def is_yes_no(question: str) -> bool:
    q = question.lstrip().lower()
    return any(q.startswith(p) for p in _YES_NO_OPENERS)


def lint(q: str, a: str) -> list[str]:
    flags: list[str] = []
    a = a.strip()
    if not a:
        return ["EMPTY_ANSWER"]

    lede = first_sentence(a)
    lede_lower = lede.lower()

    # Yes/no question must be answered with a Yes/No first word.
    if is_yes_no(q):
        first_word = lede.split(maxsplit=1)[0].rstrip(",.").lower()
        if first_word not in ("yes", "no"):
            flags.append("YES_NO_NOT_LED")

    if any(lede_lower.startswith(w) for w in _WEASEL_OPENERS):
        flags.append("WEASEL_OPENER")

    word_count = len(lede.split())
    if word_count < 6:
        flags.append("SHORT_LEDE")
    elif word_count > 35:
        flags.append("LONG_LEDE")

    if not lede.endswith((".", "!", "?")):
        flags.append("NO_PUNCT")

    rest = a[len(lede):].strip()
    if not rest:
        flags.append("NO_DETAIL")

    return flags


# ──────────────────────────────────────────────────────────────────
# Reporting
# ──────────────────────────────────────────────────────────────────


def main(argv: list[str]) -> int:
    show_clean = "--all" in argv
    by_file: dict[str, list[dict]] = {}
    total = 0
    flagged = 0
    for path in FAQ_SOURCES:
        if not path.exists():
            print(f"SKIP {path} — not found", file=sys.stderr)
            continue
        faqs = extract_faqs(path)
        for f in faqs:
            total += 1
            f["flags"] = lint(f["q"], f["a"])
            if f["flags"] or show_clean:
                by_file.setdefault(str(path.relative_to(REPO_ROOT)), []).append(f)
            if f["flags"]:
                flagged += 1

    print(f"# FAQ audit\n")
    print(f"Total FAQ entries scanned: **{total}**")
    print(f"Entries flagged: **{flagged}**  ({100 * flagged / max(1, total):.0f}%)")
    print()

    # Severity ordering for sorting within each file
    severity = {
        "YES_NO_NOT_LED": 5,
        "WEASEL_OPENER":  4,
        "SHORT_LEDE":     3,
        "NO_DETAIL":      2,
        "LONG_LEDE":      1,
        "NO_PUNCT":       1,
        "EMPTY_ANSWER":   9,
    }

    for path, items in by_file.items():
        items.sort(key=lambda f: -max((severity.get(x, 0) for x in f["flags"]), default=0))
        print(f"\n## `{path}` — {len(items)} flagged\n")
        for f in items:
            tags = " ".join(f"`{t}`" for t in f["flags"])
            print(f"### {path}:{f['lineno']}  {tags}")
            print(f"**Q:** {f['q']}")
            lede = first_sentence(f["a"])
            print(f"**Lede:** {lede}")
            if f["a"] != lede:
                rest = f["a"][len(lede):].strip()
                if rest:
                    truncated = rest if len(rest) <= 200 else rest[:200] + "…"
                    print(f"**Rest:** {truncated}")
            print()

    # Aggregate flag counts
    print("\n## Flag counts\n")
    counts: dict[str, int] = {}
    for items in by_file.values():
        for f in items:
            for flag in f["flags"]:
                counts[flag] = counts.get(flag, 0) + 1
    for flag, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"- `{flag}`: {n}")

    return 0 if flagged == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
