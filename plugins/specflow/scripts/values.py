"""Value check for couplings rows; used by `couplings.py --values`. Not run on its own.

Value file (JSON):
  {"format": 1,
   "variants": {"<name>": {"defines": {"BOARD_X": "1"}}, ...},          optional
   "couplings": [
     {"id": "<row # in the couplings table>", "check": "value", "relation": "==",
      "variants": ["<name>", ...],                                       optional, see below
      "a": <side>, "b": <side>},
     {"id": "<row #>", "check": "manual", "kind": "protocol", "how": "<who checks it and how>"}]}
  side = {"file": "<path under a --code root>", "symbol": "<macro>", "unit": "<unit>",
          "strategy": "define" | "cpp", "include": ["<dir under the same root>", ...]}
       | {"value": <number>, "unit": "<unit>", "source": "<where a person read it, e.g. H01 3.1>"}

relation: == (or equal), <, <=, >, >=; it reads "A <relation> B".
Units: ns us ms s min, Hz kHz MHz, B KiB MiB are converted; any other unit compares only with the same unit
string; a side with no unit is UNVERIFIED (write "1" for a plain count).

Strategies (how a side's value is read):
- define: the file's single `#define SYMBOL <expr>`, read as text, no compiler. UNVERIFIED when the macro is
  defined more than once, #undef'd, function-like, inside #if/#ifdef/#else (an include guard is fine), or its
  expression uses anything the evaluator does not support (another macro, sizeof, an enum...).
- cpp: runs the gcc-compatible preprocessor given by `--cpp` (gcc, clang, arm-none-eabi-gcc...) with `-E -P`, the
  variant's -D defines and the side's include dirs, and evaluates what SYMBOL expands to. Conditional
  compilation and nested macros are resolved by the preprocessor, not by this script. A #error (a variant
  not decided) or a symbol that stays undefined is UNVERIFIED; no --cpp, a missing include or a timeout is BLOCKED.

Variants: a coupling's "variants" are checked one by one, each with its own result. With no list, every
variant in "variants" is checked; with none declared, one run named "default" with no defines. A listed
variant that is not declared is UNVERIFIED.

The evaluator never calls eval(). It accepts integer and decimal literals (with u/l/f suffixes), ( ), unary
+ -, binary + - * / % << >>, and casts to fixed-width integer types, int, unsigned, long, long long, float and
double. Integer division truncates as in C. It refuses, as UNVERIFIED: identifiers, an unsigned result
below 0, an integer outside 32 bits without an LL suffix or 64-bit cast, a cast whose value does not fit the
type's guaranteed range. Decimal literals compare as exact decimals, not as the target's float rounding.

Statuses: PASS, FAIL, UNVERIFIED, BLOCKED, MANUAL. Only PASS means the two values were read and compared.
MANUAL rows (protocols, struct layouts, timing order, API meaning) go to a person or another tool.
"""
import re
import subprocess
import tempfile
from fractions import Fraction
from pathlib import Path

UNITS = {
    "ns": ("time", Fraction(1, 10**9)), "us": ("time", Fraction(1, 10**6)), "µs": ("time", Fraction(1, 10**6)),
    "ms": ("time", Fraction(1, 1000)), "s": ("time", Fraction(1)), "min": ("time", Fraction(60)),
    "Hz": ("frequency", Fraction(1)), "kHz": ("frequency", Fraction(1000)), "MHz": ("frequency", Fraction(10**6)),
    "B": ("size", Fraction(1)), "KiB": ("size", Fraction(1024)), "MiB": ("size", Fraction(1024**2)),
}
BASE = {"time": "s", "frequency": "Hz", "size": "B"}
RELATIONS = {"==": lambda a, b: a == b, "equal": lambda a, b: a == b, "<": lambda a, b: a < b,
             "<=": lambda a, b: a <= b, ">": lambda a, b: a > b, ">=": lambda a, b: a >= b}
# name -> (min, max, wide) using each type's guaranteed range; float types map to None
CASTS = {
    "int8_t": (-2**7, 2**7 - 1, False), "uint8_t": (0, 2**8 - 1, False),
    "int16_t": (-2**15, 2**15 - 1, False), "uint16_t": (0, 2**16 - 1, False),
    "int32_t": (-2**31, 2**31 - 1, False), "uint32_t": (0, 2**32 - 1, False),
    "int64_t": (-2**63, 2**63 - 1, True), "uint64_t": (0, 2**64 - 1, True),
    "int": (-32767, 32767, False), "signed int": (-32767, 32767, False),
    "unsigned": (0, 65535, False), "unsigned int": (0, 65535, False),
    "long": (-2**31 + 1, 2**31 - 1, False), "unsigned long": (0, 2**32 - 1, False),
    "long long": (-2**63 + 1, 2**63 - 1, True), "unsigned long long": (0, 2**64 - 1, True),
    "float": None, "double": None,
}
TOKEN = re.compile(r"\s*(?:(?P<num>0[xX][0-9a-fA-F]+|(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?)(?P<suf>[A-Za-z]*)"
                   r"|(?P<id>[A-Za-z_]\w*)|(?P<op><<|>>|[-+*/%()]))")
INT_SUFFIX = {"", "u", "l", "ul", "lu", "ll", "ull", "llu"}


class Unsupported(Exception):
    pass


class Num:
    def __init__(self, v, flt=False, uns=False, wide=False):
        self.v, self.flt, self.uns, self.wide = Fraction(v), flt, uns, wide


def tokenize(text):
    out, pos, text = [], 0, text.strip()
    while pos < len(text):
        m = TOKEN.match(text, pos)
        if not m or m.end() == pos:
            raise Unsupported(f"unsupported character {text[pos:].strip()[:1]!r}")
        pos = m.end()
        if m.group("num"):
            out.append(("num", m.group("num"), m.group("suf").lower()))
        elif m.group("id"):
            out.append(("id", m.group("id"), None))
        else:
            out.append(("op", m.group("op"), None))
    return out


def literal(text, suf):
    is_float = not text.lower().startswith("0x") and any(c in text for c in ".eE")
    if is_float:
        if suf not in ("", "f", "l"):
            raise Unsupported(f"unsupported suffix {suf!r}")
        return Num(Fraction(text), flt=True)
    if suf not in INT_SUFFIX:
        raise Unsupported(f"unsupported suffix {suf!r}")
    if text.lower().startswith("0x"):
        v = int(text, 16)
    elif len(text) > 1 and text.startswith("0"):
        if not set(text) <= set("01234567"):
            raise Unsupported(f"bad octal literal {text}")
        v = int(text, 8)
    else:
        v = int(text)
    return Num(v, uns="u" in suf, wide="ll" in suf)


def checked(n):
    """Refuse integer results whose value depends on the target's integer width."""
    if n.flt:
        return n
    if n.uns and n.v < 0:
        raise Unsupported("unsigned result below 0 (wraparound is not modeled)")
    hi = (2**64 - 1 if n.uns else 2**63 - 1) if n.wide else (2**32 - 1 if n.uns else 2**31 - 1)
    if not -hi - 1 <= n.v <= hi:
        raise Unsupported(f"integer {n.v} may overflow on the target (needs an LL suffix or a 64-bit cast)")
    return n


def binary(op, a, b):
    flt, uns, wide = a.flt or b.flt, (a.uns or b.uns) and not (a.flt or b.flt), a.wide or b.wide
    if op in ("%", "<<", ">>") and flt:
        raise Unsupported(f"{op} on a floating value")
    if op == "+":
        v = a.v + b.v
    elif op == "-":
        v = a.v - b.v
    elif op == "*":
        v = a.v * b.v
    elif op in ("/", "%"):
        if b.v == 0:
            raise Unsupported("division by zero")
        if flt:
            v = a.v / b.v
        else:
            q = abs(a.v) // abs(b.v) * (1 if (a.v >= 0) == (b.v >= 0) else -1)
            v = q if op == "/" else a.v - q * b.v
    else:
        if a.v < 0 or not 0 <= b.v < 64:
            raise Unsupported(f"shift of a negative value or by {b.v}")
        v = a.v * 2**int(b.v) if op == "<<" else a.v // 2**int(b.v)
    return checked(Num(v, flt, uns, wide))


def evaluate(text):
    """Evaluate a restricted C constant expression; return a Num or raise Unsupported."""
    toks, pos = tokenize(text), [0]
    if not toks:
        raise Unsupported("empty expression")

    def peek(k=0):
        return toks[pos[0] + k] if pos[0] + k < len(toks) else (None, None, None)

    def take(kind=None, val=None):
        t = peek()
        if t[0] is None or (kind and t[0] != kind) or (val and t[1] != val):
            raise Unsupported(f"expected {val or kind}, got {t[1]!r}")
        pos[0] += 1
        return t

    def level(ops, nxt):
        def parse():
            left = nxt()
            while peek()[0] == "op" and peek()[1] in ops:
                left = binary(take()[1], left, nxt())
            return left
        return parse

    def unary():
        t = peek()
        if t[0] == "op" and t[1] in "+-":
            take()
            n = unary()
            return checked(Num(-n.v if t[1] == "-" else n.v, n.flt, n.uns, n.wide))
        if t[0] == "op" and t[1] == "(" and peek(1)[0] == "id":
            k = 1
            while peek(k)[0] == "id":
                k += 1
            name = " ".join(peek(i)[1] for i in range(1, k))
            if peek(k)[1] != ")":
                raise Unsupported(f"unsupported identifier {peek(1)[1]!r}")
            if name not in CASTS:
                raise Unsupported(f"unsupported cast or identifier {name!r}")
            pos[0] += k + 1
            n, rng = unary(), CASTS[name]
            if rng is None:
                return Num(n.v, flt=True)
            v = Fraction(int(n.v)) if n.flt else n.v  # float to integer truncates toward zero
            if not rng[0] <= v <= rng[1]:
                raise Unsupported(f"{v} does not fit the guaranteed range of {name}")
            return Num(v, uns=rng[0] == 0, wide=rng[2])
        return primary()

    def primary():
        t = take()
        if t[0] == "num":
            return checked(literal(t[1], t[2]))
        if t == ("op", "(", None):
            n = expr()
            take("op", ")")
            return n
        raise Unsupported(f"unsupported token {t[1]!r}")

    expr = level(("<<", ">>"), level(("+", "-"), level(("*", "/", "%"), unary)))
    n = expr()
    if pos[0] != len(toks):
        raise Unsupported(f"unexpected {peek()[1]!r}")
    return n


def strip_comments(text):
    """Remove /* */ and // comments, keeping line breaks so line numbers stay right."""
    text = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def define_value(text, symbol):
    """Strategy define. Return (value text, line) or raise Unsupported."""
    lines = strip_comments(text).split("\n")
    logical, i = [], 0
    while i < len(lines):  # join backslash continuations
        start, cur = i + 1, lines[i]
        while cur.endswith("\\") and i + 1 < len(lines):
            i += 1
            cur = cur[:-1] + " " + lines[i]
        logical.append((start, cur))
        i += 1
    directives = [(n, m.group(1), m.group(2).strip()) for n, l in logical
                  for m in [re.match(r"\s*#\s*(\w+)(.*)$", l)] if m]
    guard = None
    if len(directives) >= 2 and directives[0][1] == "ifndef" and directives[1][1] == "define" \
            and directives[1][2] == directives[0][2]:
        guard = directives[0][0]
    depth, found, undef = 0, [], False
    for n, d, rest in directives:
        if d in ("if", "ifdef", "ifndef"):
            depth += 0 if n == guard else 1
        elif d == "endif":
            depth = max(depth - 1, 0)
        elif d == "define":
            m = re.match(r"(\w+)(\(?)(.*)$", rest)
            if m and m.group(1) == symbol:
                found.append((n, m.group(2), m.group(3).strip(), depth))
        elif d == "undef" and rest.split()[:1] == [symbol]:
            undef = True
    if not found:
        raise LookupError(f"no #define {symbol}")
    if len(found) > 1 or undef:
        where = ", ".join(str(f[0]) for f in found)
        raise Unsupported(f"{symbol} defined {len(found)} times (lines {where}){' and #undef' if undef else ''}; use strategy cpp")
    n, paren, body, d = found[0]
    if paren:
        raise Unsupported(f"{symbol} is a function-like macro")
    if d:
        raise Unsupported(f"#define {symbol} (line {n}) is inside conditional compilation; use strategy cpp with variants")
    return body, n


def cpp_value(cpp, path, root, side, defines, timeout):
    """Strategy cpp. Return expansion text, or raise LookupError (UNVERIFIED) / OSError (BLOCKED)."""
    sym = side["symbol"]
    with tempfile.TemporaryDirectory(prefix="specflow-cpp-") as tmp:
        probe = Path(tmp) / "probe.c"
        probe.write_text(f'#include "{path.resolve().as_posix()}"\nspecflow_probe_begin {sym} specflow_probe_end\n',
                         encoding="utf-8")
        argv = [cpp, "-E", "-P"] + [f"-I{(root / inc).resolve()}" for inc in side.get("include", [])] \
            + [f"-D{k}={v}" for k, v in defines.items()] + [str(probe)]
        try:
            p = subprocess.run(argv, cwd=tmp, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=timeout)
        except subprocess.TimeoutExpired:
            raise OSError(f"preprocessor timed out after {timeout}s")
        except OSError as err:
            raise OSError(f"cannot run preprocessor {cpp}: {err}")
    if p.returncode != 0:
        err = next((l for l in p.stderr.splitlines() if "#error" in l or "error:" in l), p.stderr.strip()[:200])
        if "#error" in err:
            raise LookupError(f"preprocessor stopped: {err.strip()}")
        raise OSError(f"preprocessor failed (exit {p.returncode}): {err.strip()}")
    m = re.findall(r"specflow_probe_begin(.*?)specflow_probe_end", p.stdout, re.S)
    if not m:
        raise OSError("preprocessor output has no probe line")
    body = m[-1].strip()
    if body == sym:
        raise LookupError(f"{sym} is not defined for this variant")
    return body


def fmt(v):
    """Exact Fraction as a decimal string when it terminates, else n/d."""
    d = v.denominator
    for p in (2, 5):
        while d % p == 0:
            d //= p
    if d != 1:
        return f"{v.numerator}/{v.denominator}"
    k, n = 0, abs(v)
    while n.denominator != 1:
        n, k = n * 10, k + 1
    digits = str(n.numerator).rjust(k + 1, "0")
    return ("-" if v < 0 else "") + (digits[:-k] + "." + digits[-k:] if k else digits)


def find_file(roots, rel):
    hits = [r for r in roots if (r / rel).is_file() and r.resolve() in (r / rel).resolve().parents]
    if len(hits) != 1:
        raise LookupError(f"{rel} {'not found under --code' if not hits else 'found under more than one --code root'}")
    return hits[0], hits[0] / rel


def read_side(side, roots, cpp, defines, timeout, cache):
    """Return (Num, evidence dict) or raise LookupError/Unsupported (UNVERIFIED) or OSError (BLOCKED)."""
    if "value" in side:
        if not str(side.get("source", "")).strip():
            raise LookupError("a literal side needs source")
        n = evaluate(str(side["value"]))
        return n, {"source": f"stated in the value file ({side['source']})", "raw": str(side["value"])}
    strategy = side.get("strategy", "define")
    if not roots:
        raise OSError("no --code given")
    root, path = find_file(roots, side["file"])
    if strategy == "define":
        key = (str(path), side["symbol"])
        if key not in cache:
            try:
                cache[key] = define_value(path.read_text(encoding="utf-8", errors="replace"), side["symbol"])
            except (LookupError, Unsupported) as err:
                cache[key] = err
        if isinstance(cache[key], Exception):
            raise cache[key]
        body, line = cache[key]
        return evaluate(body), {"source": f"{side['file']}:{line} (define)", "raw": body}
    if not cpp:
        raise OSError("strategy cpp needs --cpp <preprocessor>")
    body = cpp_value(cpp, path, root, side, defines, timeout)
    flags = " ".join(f"-D{k}={v}" for k, v in defines.items())
    return evaluate(body), {"source": f"{side['file']} (cpp {flags})".replace(" )", ")"), "raw": body}


def check_contract(data, table_ids, name):
    """Return problem strings for a malformed value file."""
    if not isinstance(data, dict) or data.get("format") != 1 or not isinstance(data.get("couplings"), list):
        return [f"{name}: needs {{\"format\": 1, \"couplings\": [...]}}"]
    out = []
    variants = data.get("variants", {})
    if not isinstance(variants, dict) or not all(isinstance(v, dict) and isinstance(v.get("defines", {}), dict)
                                                for v in variants.values()):
        out.append(f"{name}: variants must map a name to {{\"defines\": {{...}}}}")
    for c in data["couplings"]:
        cid = str(c.get("id", "?")) if isinstance(c, dict) else "?"
        where = f"{name} coupling {cid}"
        if not isinstance(c, dict):
            out.append(f"{where}: entry is not an object")
            continue
        if cid not in table_ids:
            out.append(f"{where}: no row {cid} in the couplings table")
        check = c.get("check", "value")
        if check == "manual":
            if not str(c.get("how", "")).strip():
                out.append(f"{where}: a manual check needs how")
            continue
        if check != "value":
            out.append(f"{where}: check must be value or manual")
            continue
        if c.get("relation", "==") not in RELATIONS:
            out.append(f"{where}: relation must be one of {', '.join(RELATIONS)}")
        if "variants" in c and not (isinstance(c["variants"], list) and c["variants"]):
            out.append(f"{where}: variants must be a non-empty list")
        for s in ("a", "b"):
            side = c.get(s)
            if not isinstance(side, dict):
                out.append(f"{where}: side {s} missing")
            elif "value" not in side and not (side.get("file") and side.get("symbol")):
                out.append(f"{where}: side {s} needs file and symbol, or value and source")
            elif side.get("strategy", "define") not in ("define", "cpp"):
                out.append(f"{where}: side {s} strategy must be define or cpp")
    return out


def compare(c, va, vb):
    """Return (status, reason, normalized A, normalized B)."""
    ua, ub = c["a"].get("unit"), c["b"].get("unit")
    for s, u in (("A", ua), ("B", ub)):
        if not u:
            return "UNVERIFIED", f"side {s} has no unit", None, None
    if ua == ub:
        dim, base, fa, fb = ua, ua, Fraction(1), Fraction(1)
    elif ua in UNITS and ub in UNITS and UNITS[ua][0] == UNITS[ub][0]:
        dim, base, fa, fb = UNITS[ua][0], BASE[UNITS[ua][0]], UNITS[ua][1], UNITS[ub][1]
    else:
        unknown = [u for u in (ua, ub) if u not in UNITS]
        why = f"unknown unit {unknown[0]}" if unknown else f"units {ua} and {ub} measure different things"
        return "UNVERIFIED", why, None, None
    na, nb = va.v * fa, vb.v * fb
    rel = c.get("relation", "==")
    ok = RELATIONS[rel](na, nb)
    text = f"{fmt(na)} {base} {rel} {fmt(nb)} {base}"
    return ("PASS", text, na, nb) if ok else ("FAIL", f"{text} is false", na, nb)


def check(data, roots, cpp, timeout=60):
    """Return result dicts, one per (coupling, variant); manual couplings give one MANUAL result."""
    declared = data.get("variants", {})
    results, cache = [], {}
    for c in data["couplings"]:
        cid = str(c["id"])
        if c.get("check", "value") == "manual":
            results.append({"id": cid, "variant": None, "status": "MANUAL",
                            "reason": f"{c.get('kind', 'manual')}: {c['how']}"})
            continue
        uses_cpp = any(c[s].get("strategy") == "cpp" for s in ("a", "b"))
        names = c.get("variants") or (list(declared) if uses_cpp and declared else ["default"])
        for vname in names:
            r = {"id": cid, "variant": vname, "relation": c.get("relation", "==")}
            results.append(r)
            if vname != "default" and vname not in declared:
                r.update(status="UNVERIFIED", reason=f"variant {vname} is not declared in variants")
                continue
            defines = declared.get(vname, {}).get("defines", {})
            sides = {}
            for s in ("a", "b"):
                side = c[s]
                info = {"symbol": side.get("symbol"), "unit": side.get("unit"),
                        "strategy": "literal" if "value" in side else side.get("strategy", "define")}
                try:
                    n, ev = read_side(side, roots, cpp, defines, timeout, cache)
                    info.update(ev, value=fmt(n.v))
                    sides[s] = (n, info)
                except OSError as err:
                    info.update(error=str(err), blocked=True)
                except (LookupError, Unsupported) as err:
                    info.update(error=str(err))
                r[s] = info
            errors = [(s.upper(), r[s]) for s in ("a", "b") if "error" in r[s]]
            if errors:
                blocked = any(i.get("blocked") for _, i in errors)
                r.update(status="BLOCKED" if blocked else "UNVERIFIED",
                         reason="; ".join(f"side {s}: {i['error']}" for s, i in errors))
                continue
            status, reason, na, nb = compare(c, sides["a"][0], sides["b"][0])
            r.update(status=status, reason=reason)
            if na is not None:
                r["a"]["normalized"], r["b"]["normalized"] = fmt(na), fmt(nb)
    return results


def line(r):
    """One human-readable line for a result."""
    head = f"value {r['id']}" + (f" [{r['variant']}]" if r.get("variant") not in (None, "default") else "")
    if r["status"] == "MANUAL":
        return f"{head}: MANUAL {r['reason']}"
    sides = []
    for s in ("a", "b"):
        i = r.get(s)
        if i and "value" in i:
            label = i.get("symbol") or "literal"
            sides.append(f"{s.upper()} {label} = {i['raw']} -> {i['value']} {i['unit'] or '(no unit)'} @ {i['source']}")
    tail = f" | {' | '.join(sides)}" if sides else ""
    return f"{head}: {r['status']} {r['reason']}{tail}"
