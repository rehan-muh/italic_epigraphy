#!/usr/bin/env python3
"""
Word count of the QDLCA27 abstract body (abstract/qdacl/qdlca27-abstract.tex).

The call allows 400 words excluding references; title, figure captions and
citation keys are left out of the count, citations' visible text is not
(a "(Kaimio 1975)" is words on the page).

    python scripts/check_qdlca_words.py        # exit 1 if over 400
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEX = ROOT / "abstract" / "qdacl" / "qdlca27-abstract.tex"
LIMIT = 400
SECTIONS = ["Introduction", "Data", "Methods", "Results", "Discussion"]


def plain(t: str) -> str:
    t = re.sub(r"%.*", "", t)
    t = re.sub(r"\\(textbf|ref|label|emph|textit)\{([^}]*)\}", r"\2", t)
    t = re.sub(r"\\[a-zA-Z]+\*?(\[[^\]]*\])?", " ", t)
    return t.replace("{", "").replace("}", "").replace("~", " ")


def count(t: str) -> int:
    return len([w for w in re.split(r"\s+", plain(t)) if re.search(r"[A-Za-z0-9]", w)])


def cite_words(t: str) -> int:
    """Visible words a rendered authoryear citation adds: 'Name Year' per key."""
    n = 0
    for keys in re.findall(r"\\(?:paren)?cite\{([^}]*)\}", t):
        n += 2 * len(keys.split(","))
    return n


def main():
    src = TEX.read_text(encoding="utf-8")
    body = src.split(r"\begin{document}")[1].split(r"\clearpage")[0]
    body = re.sub(r"\\begin\{center\}.*?\\end\{center\}", "", body, flags=re.S)
    total = 0
    for i, sec in enumerate(SECTIONS):
        nxt = SECTIONS[i + 1] if i + 1 < len(SECTIONS) else None
        pat = r"\\textbf\{" + sec + r"\.\}(.*?)" + (
            r"(?=\\textbf\{" + nxt + r"\.\})" if nxt else r"(?=%\s*\\posterpreference|\\clearpage|$)")
        m = re.search(pat, body, flags=re.S)
        t = m.group(1)
        n = count(re.sub(r"\\(?:paren)?cite\{[^}]*\}", "", t)) + cite_words(t) + 1  # +1: heading
        print(f"{sec:13s} {n:4d}")
        total += n
    print(f"{'TOTAL':13s} {total:4d}  (limit {LIMIT}, excl. title/captions/references)")
    sys.exit(1 if total > LIMIT else 0)


if __name__ == "__main__":
    main()
