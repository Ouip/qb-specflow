#!/usr/bin/env python3
"""Couplings-table check: everything named in each row's Side A and Side B can be found.

Usage: python couplings.py <couplings.md> --specs <spec folder> [--code <code repo root> ...] [--list]

Takes three kinds of reference from each data row's Side A and Side B cells
(the headers `A 端` and `B 端` are also accepted):
- Spec sections `<ID> <section>` (ID is 1-3 capital letters plus 2-3 digits, e.g. `X07 3.3`;
  `X07 3.3/4.3` counts as two): a file in --specs named `<ID>-*.md` must have a heading starting with that number.
- Symbols in backticks (e.g. `FRAME_W`; `*` is a wildcard): must appear verbatim in a --specs .md or a --code file.
- Paths in backticks (containing `/`, or ending in .py .c .h .csv; a `file:line` suffix is ignored):
  must exist under a --code root; a bare file name matches any file of that name.
Each miss prints `row <#>: <reference> not found` and exits 1; otherwise prints `couplings: ok` and exits 0.
Without --code, path references print `skipped (no --code)` and do not fail.
--list prints every reference instead, one `row <#> <A|B>: <reference>` line each, checks nothing and exits 0.
"""
import argparse
import importlib.util
import os
import re
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("specflow_coverage", Path(__file__).with_name("coverage.py"))
_cov = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_cov)
split_row, is_separator = _cov.split_row, _cov.is_separator

SECTION = re.compile(r"(?<![A-Za-z0-9])([A-Z]{1,3}\d{2,3})\s+(\d+(?:\.\d+)*(?:[／/、]\d+(?:\.\d+)*)*)")
BACKTICK = re.compile(r"`([^`]+)`")
PATH_EXT = re.compile(r"\.(py|c|h|csv)$")
LINE_SUFFIX = re.compile(r":\d+(?:[–-]\d+)?$")
SKIP_DIRS = {".git", "managed_components", "node_modules", "__pycache__", ".venv"}


def refs(cell):
    """[(kind, value, section or None)]: spec sections first, then backtick symbols and paths, each in order."""
    out = []
    plain = BACKTICK.sub(" ", cell)
    for m in SECTION.finditer(plain):
        for num in re.split(r"[／/、]", m.group(2)):
            out.append(("section", m.group(1), num))
    for m in BACKTICK.finditer(cell):
        tok = m.group(1).strip()
        bare = LINE_SUFFIX.sub("", tok)
        kind = "path" if ("/" in bare and " " not in bare) or PATH_EXT.search(bare) else "symbol"
        out.append((kind, tok, None))
    return out


class Index:
    """Spec and code contents, read on first use."""

    def __init__(self, specs, code, table=None):
        self.specs, self.code = Path(specs), [Path(c) for c in code]
        self.table = Path(table).resolve() if table else None
        self._texts, self._names = None, None

    def _walk(self):
        # Skip the couplings table itself: a symbol in the table is not evidence for itself
        texts = [p.read_text(encoding="utf-8", errors="ignore")
                 for p in self.specs.rglob("*.md") if p.resolve() != self.table]
        names = set()
        for root in self.code:
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith("build")]
                for f in filenames:
                    names.add(f)
                    p = Path(dirpath) / f
                    try:
                        if p.stat().st_size < 2_000_000:
                            texts.append(p.read_text(encoding="utf-8", errors="ignore"))
                    except OSError:
                        pass
        self._texts, self._names = texts, names

    def has_symbol(self, sym):
        if self._texts is None:
            self._walk()
        if "*" in sym:
            pat = re.compile("[A-Za-z0-9_]*".join(re.escape(s) for s in sym.split("*")))
            return any(pat.search(t) for t in self._texts)
        return any(sym in t for t in self._texts)

    def has_path(self, tok):
        bare = LINE_SUFFIX.sub("", tok)
        if "/" in bare:
            return any((root / bare).exists() for root in self.code)
        if self._names is None:
            self._walk()
        return bare in self._names

    def has_section(self, code, num):
        head = re.compile(r"^#{1,6}\s+" + re.escape(num) + r"(?=\s|$)", re.M)
        return any(head.search(p.read_text(encoding="utf-8", errors="ignore")) for p in self.specs.glob(f"{code}-*.md"))


def rows(text):
    """[(#, Side A, Side B)] from tables whose header row has #, Side A and Side B columns."""
    out, cols, in_table = [], None, False
    for line in text.splitlines():
        if not line.lstrip().startswith("|"):
            in_table = False
            continue
        cells = split_row(line)
        if not in_table:
            in_table = True
            find = lambda *keys: next((i for i, c in enumerate(cells) if c.replace(" ", "").lower().startswith(keys)), None)
            a, b = find("a端", "sidea"), find("b端", "sideb")
            cols = (cells.index("#"), a, b) if "#" in cells and a is not None and b is not None else None
            continue
        if cols is None or is_separator(line):
            continue
        get = lambda i: cells[i] if i < len(cells) else ""
        out.append((get(cols[0]), get(cols[1]), get(cols[2])))
    return out


def main(argv):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="couplings-table check")
    ap.add_argument("table")
    ap.add_argument("--specs", required=True)
    ap.add_argument("--code", nargs="+", default=[])
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args(argv)
    table = rows(Path(args.table).read_text(encoding="utf-8"))
    if not table:
        print(f"{Path(args.table).name}: no table with #, Side A and Side B columns")
        return 1
    if args.list:
        for num, a, b in table:
            for side, cell in (("A", a), ("B", b)):
                for kind, val, sec in refs(cell):
                    print(f"row {num} {side}: {val} {sec}" if kind == "section" else f"row {num} {side}: {val}")
        return 0
    idx, bad = Index(args.specs, args.code, args.table), False
    for num, a, b in table:
        for kind, val, sec in refs(a) + refs(b):
            if kind == "section":
                ok, label = idx.has_section(val, sec), f"{val} {sec}"
            elif kind == "symbol":
                ok, label = idx.has_symbol(val), val
            elif not args.code:
                print(f"row {num}: {val} skipped (no --code)")
                continue
            else:
                ok, label = idx.has_path(val), val
            if not ok:
                print(f"row {num}: {label} not found")
                bad = True
    if bad:
        return 1
    print("couplings: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
