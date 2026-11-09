#!/usr/bin/env python3
"""Rule -> Verify coverage check.

Usage: python coverage.py <spec.md> [--catalog <scenario catalog.md> ...]

Reads section 4 of the spec (from `## 4` to the next `## `):
- Tables: a table whose header row has a `#` column is a rule table; each data row is one rule.
  It must have a `Verify` column (`驗` also accepted); without one, every row reports it.
- Numbered lists (`1.`, `- 1`): each item is one rule and must end with `[verify: ...]` (`〔驗：...〕` also accepted).
Verify values (several may be separated by `,` or `、`):
- `<section> <group>`, e.g. `5.1 G3`: that section must have a heading containing the group ID.
- A scenario ID, e.g. `I14`: it must appear in one of the --catalog files.
- A section number only, e.g. `5.5`: the spec must have that section heading.
- `untested: <reason>` (`不測：` also accepted): passes, but the reason must not be empty.
Each problem prints one line `<file> <section>#<id>: <problem>` and exits 1; otherwise prints `coverage: ok` and exits 0.
"""
import re
import sys
from pathlib import Path

HEADING = re.compile(r"^(#{1,6})\s+(\d+(?:\.\d+)*)?(?=\s|$)")
LIST_ITEM = re.compile(r"^\s*(?:(\d+)\.|-\s+(\d+))\s+(.*)$")
MARKER = re.compile(r"(?:〔驗[：:]\s*(.*?)\s*〕|\[verify:\s*(.*?)\s*\])\s*$", re.I)
UNTESTED = re.compile(r"^(?:不測|untested)[：:]?", re.I)
VERIFY_COLS = {"驗", "verify"}
SEC_CODE = re.compile(r"^(\d+(?:\.\d+)*)\s+([A-Za-z]+\d+)$")
SCENARIO = re.compile(r"^[A-Z]+\d{2}$")
SEC_ONLY = re.compile(r"^\d+(?:\.\d+)+$")


def split_row(line):
    """Split a table row into cells; `|` inside backticks or escaped is not a separator."""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    cells, cur, in_code, i = [], [], False, 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s) and s[i + 1] == "|":
            cur.append("|")
            i += 2
            continue
        if c == "`":
            in_code = not in_code
        if c == "|" and not in_code:
            cells.append("".join(cur).strip())
            cur = []
        else:
            cur.append(c)
        i += 1
    cells.append("".join(cur).strip())
    return cells


def is_separator(line):
    s = line.strip()
    return s.startswith("|") and "-" in s and set(s) <= set("|-: ")


def headings(lines):
    """[(line index, level, section number or None, heading text)]"""
    out = []
    for i, line in enumerate(lines):
        m = HEADING.match(line)
        if m:
            out.append((i, len(m.group(1)), m.group(2), line))
    return out


def block(lines, heads, number):
    """Lines under the heading numbered `number`, up to the next heading of the same or higher level; None if absent."""
    for k, (i, level, num, _) in enumerate(heads):
        if num == number:
            end = len(lines)
            for j, lv, _, _ in heads[k + 1:]:
                if lv <= level:
                    end = j
                    break
            return lines[i + 1:end]
    return None


def has_token(text, code):
    return re.search(r"(?<![A-Za-z0-9])" + re.escape(code) + r"(?![0-9])", text) is not None


def check_value(value, lines, heads, catalogs):
    """Return a list of problem strings; an empty list means it passes."""
    v = value.strip()
    if not v:
        return ["Verify cell is empty"]
    if UNTESTED.match(v):
        reason = UNTESTED.sub("", v).strip()
        return [] if reason else ["untested without a reason"]
    problems = []
    for part in [p.strip() for p in re.split(r"[、，,]", v) if p.strip()]:
        m = SEC_CODE.match(part)
        if m:
            sec, code = m.groups()
            body = block(lines, heads, sec)
            if body is None:
                problems.append(f"no section {sec}")
            elif not any(l.lstrip().startswith("#") and has_token(l, code) for l in body):
                problems.append(f"{sec} has no {code}")
        elif SCENARIO.match(part):
            if catalogs is None or not catalogs:
                problems.append(f"scenario {part} not in catalog (no --catalog given)")
            elif not any(has_token(t, part) for t in catalogs):
                problems.append(f"scenario {part} not in catalog")
        elif SEC_ONLY.match(part):
            if not any(num == part for _, _, num, _ in heads):
                problems.append(f"no section {part}")
        else:
            problems.append(f"cannot parse Verify value: {part}")
    return problems


def scan(text, name, catalogs):
    """Return (rule IDs, problem strings). `catalogs` is a list of scenario-catalog texts."""
    lines = text.splitlines()
    heads = headings(lines)
    start = next((i for i, lv, num, _ in heads if lv == 2 and num == "4"), None)
    if start is None:
        return [], [f"{name}: no section 4"]
    end = next((i for i, lv, _, _ in heads if i > start and lv <= 2), len(lines))
    rules, problems = [], []
    sub = "4"
    table = None  # (# column, Verify column or None); None means not a rule table
    in_table = False
    for idx in range(start + 1, end):
        line = lines[idx]
        m = HEADING.match(line)
        if m and m.group(2):
            sub = m.group(2)
        if line.lstrip().startswith("|"):
            if not in_table:
                in_table = True
                cells = split_row(line)
                v_col = next((i for i, c in enumerate(cells) if c.strip().lower() in VERIFY_COLS), None)
                table = (cells.index("#"), v_col) if "#" in cells else None
                continue
            if is_separator(line) or table is None:
                continue
            cells = split_row(line)
            hash_col, v_col = table
            rid = f"{sub}#{cells[hash_col] if hash_col < len(cells) else '?'}"
            rules.append(rid)
            if v_col is None:
                problems.append(f"{name} {rid}: table has no Verify column")
            else:
                value = cells[v_col] if v_col < len(cells) else ""
                problems += [f"{name} {rid}: {p}" for p in check_value(value, lines, heads, catalogs)]
            continue
        in_table = False
        lm = LIST_ITEM.match(line)
        if lm:
            rid = f"{sub}#{lm.group(1) or lm.group(2)}"
            rules.append(rid)
            mk = MARKER.search(lm.group(3))
            if not mk:
                problems.append(f"{name} {rid}: no [verify: ...] marker")
            else:
                problems += [f"{name} {rid}: {p}" for p in check_value(mk.group(1) or mk.group(2) or "", lines, heads, catalogs)]
    return rules, problems


def main(argv):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if not argv or argv[0].startswith("-"):
        print(__doc__.strip().splitlines()[2])
        return 2
    spec = Path(argv[0])
    catalogs = []
    if "--catalog" in argv:
        catalogs = [Path(p).read_text(encoding="utf-8") for p in argv[argv.index("--catalog") + 1:]]
    _, problems = scan(spec.read_text(encoding="utf-8"), spec.name, catalogs)
    for p in problems:
        print(p)
    if problems:
        return 1
    print("coverage: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
