"""t2run - pattern runner for the Venetium Thumb-2 V8 port (D:\\cr\\rt8\\src).

Finds and replaces A32 patterns across the whole tree in one pass, so nobody has to open files one by one.
Rules live in F:\\cr\\thumb2\\rules\\NNN_*.py (applied in file-name order, then list order); each module defines
RULES = [ ... ] built from the rule kinds below. Every replace rule states how many sites it expects; a count that
differs from the expectation is reported with the exact lines and nothing is written (all-or-nothing per run).

Rule kinds (import them in a rule file with `from t2run import *`):
  Lit(id, files, find, replace, expect)          literal text (whitespace runs in `find` match any whitespace)
  Re(id, files, find, replace, expect, done=)    regex (MULTILINE); replace = template with \\1 or callable(m) -> str
  Func(id, files, sig, new, expect=1)            replace a whole function definition found by its signature prefix
                                                 (`new` = text or "@bodies/name.cc"); new="" deletes it
  Flag(id, files, find, classify=)               census only: report every site (optionally grouped by classify(m))
Common options: desc, code_only=True (comments/strings are masked before matching), region=None|"arm" (only lines
inside `#if ... V8_TARGET_ARCH_ARM` branches - for generic files), oracle=[("asm text", 0xCANONICAL), ...]
(checked with tools\\t32oracle.py before the rule may apply), map_ref="area/item" (link to map\\fulljit-map.json).

Modes:
  census [ids]           run every rule's find side (+ all Flags); report counts, files and lines
  scan [ids]             dry run of replace rules: pending / applied / MISMATCH per rule
  diff [ids]             write the unified diff the pending rules would make
  apply [ids]            apply pending rules (pristine copy kept first; journal in runs\\journal.jsonl)
  oracle [ids]           check every oracle claim against llvm-mc
  grep REGEX [--files G] ad-hoc search over the scope (code-only, optional --region arm)
  show REF...            print source lines: path:line or path:a-b (basename is enough when unique)
  mapcheck               print the first source line of every file:line reference in map\\fulljit-map.json
  delta                  cumulative diff of every touched file vs the pristine release tree (F:\\cr\\src)
  restore PATH...        put a file back to its pristine content
Reports go to F:\\cr\\thumb2\\runs\\<mode>-<time>.md (stdout gets the summary only).
"""
import argparse, bisect, difflib, glob, hashlib, importlib.util, json, os, re, shutil, sys, time

ROOT = r"D:\cr\rt8\src"
PRISTINE_SRC = r"F:\cr\src"
WORK = r"F:\cr\thumb2"
RULES_DIR = os.path.join(WORK, "rules")
RUNS_DIR = os.path.join(WORK, "runs")
PRISTINE_DIR = os.path.join(WORK, "pristine")
MAP_JSON = os.path.join(WORK, "map", "fulljit-map.json")

# Every ARM32-specific V8 file (arm64 excluded). Rules may name their own globs instead.
ARM = ["v8/src/**/arm/**/*.*", "v8/src/**/*-arm.*", "v8/src/**/*-arm-inl.h", "v8/src/**/*_arm.*"]
ARM_TESTS = ["v8/test/**/arm/**/*.cc", "v8/test/**/*-arm.cc", "v8/test/**/*-arm.h", "v8/test/**/*-arm-unittest.cc"]
# Generic V8 sources: use with region="arm" to reach the `#if V8_TARGET_ARCH_ARM` branches inside them.
GENERIC = ["v8/src/**/*.cc", "v8/src/**/*.h"]

CONDS = r"(?:eq|ne|cs|cc|hs|lo|mi|pl|vs|vc|hi|ls|ge|lt|gt|le)"


# ----------------------------------------------------------------------------------------------------------- rules
class Rule:
    kind = "?"

    def __init__(self, id, files, find=None, replace=None, expect=None, desc="", code_only=True, region=None,
                 oracle=(), map_ref="", done=None, classify=None, flags=re.MULTILINE, within=None):
        self.id, self.files, self.desc = id, ([files] if isinstance(files, str) else list(files)), desc
        self.find_src, self.replace, self.expect = find, replace, expect
        self.code_only, self.region, self.oracle, self.map_ref = code_only, region, list(oracle), map_ref
        self.done_src, self.classify, self.flags = done, classify, flags
        self.within = within  # regex searched in "<qualified name> <signature>" of the enclosing function
        self.module = "?"

    def regex(self):
        return re.compile(self.find_src, self.flags)

    def done_regex(self):
        return re.compile(self.done_src, self.flags) if self.done_src else None

    def matches(self, src):
        """[(start, end, match_on_original_text)] for this rule in src (masked + region filtered)."""
        rx = self.regex()
        hay = src.code if self.code_only else src.text
        out = []
        for m in rx.finditer(hay):
            if self.region == "arm" and not src.in_arm(m.start()):
                continue
            if self.within is not None:
                f = src.function_at(m.start())
                if f is None or not re.search(self.within, f"{f[2]} {f[3]}"):
                    continue
            orig = rx.match(src.text, m.start()) if self.code_only else m
            out.append((m.start(), m.end(), orig if orig is not None and orig.end() == m.end() else m))
        return out

    def replacement(self, m):
        if callable(self.replace):
            return self.replace(m)
        return m.expand(self.replace)


def _ws_pattern(text):
    """Literal text where every whitespace run matches any whitespace (\\s+), everything else escaped."""
    parts = re.split(r"(\s+)", text.strip())
    return "".join(r"\s+" if p.isspace() else re.escape(p) for p in parts if p)


class Lit(Rule):
    kind = "lit"

    def __init__(self, id, files, find, replace, expect, **kw):
        super().__init__(id, files, _ws_pattern(find), replace, expect, **kw)
        # The match starts at find's first non-blank character (and ends at its last), so the replacement drops
        # the same outer whitespace; the indentation already in the file stays.
        if find[:1].isspace():
            replace = replace.lstrip()
        if find[-1:].isspace():
            replace = replace.rstrip()
        self.lit_find, self.lit_replace = find, replace
        if "done" not in kw and replace.strip():
            self.done_src = _ws_pattern(replace)

    def replacement(self, m):
        return self.lit_replace


class Re(Rule):
    kind = "re"


class Flag(Rule):
    kind = "flag"

    def __init__(self, id, files, find, **kw):
        super().__init__(id, files, find, None, None, **kw)


class Func(Rule):
    """Whole function definition (signature prefix -> matching closing brace) replaced by `new`."""
    kind = "func"

    def __init__(self, id, files, sig, new, expect=1, **kw):
        super().__init__(id, files, _ws_pattern(sig), None, expect, **kw)
        self.sig, self.new = sig, new

    def new_text(self):
        if self.new.startswith("@"):
            with open(os.path.join(RULES_DIR, self.new[1:]), encoding="utf-8", newline="") as f:
                return f.read().replace("\r\n", "\n").strip("\n")
        return self.new.strip("\n")

    def matches(self, src):
        out = []
        for s, e, m in super().matches(src):
            # A definition: the parameter list closes, then (qualifiers / ctor init list) and a '{' before any ';'.
            depth, i, code = 0, s, src.code
            while i < len(code):
                c = code[i]
                if c == "(":
                    depth += 1
                elif c == ")":
                    depth -= 1
                    if depth == 0:
                        break
                i += 1
            j = i + 1
            while j < len(code) and code[j] not in "{;":
                j += 1
            if j >= len(code) or code[j] == ";":
                continue  # a declaration, not the definition
            depth, k = 0, j
            while k < len(code):
                if code[k] == "{":
                    depth += 1
                elif code[k] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                k += 1
            end = k + 1
            if self.new_text() and _norm(src.text[s:end]) == _norm(self.new_text()):
                continue  # already the new definition
            if not self.new_text() and end < len(code) and src.text[end] == "\n":
                end += 1  # deleting: take the definition's line break too
                if end < len(code) and src.text[end] == "\n" and (s == 0 or src.text[s - 1] == "\n"):
                    end += 1  # and one blank separator line
            out.append((s, end, m))
        return out

    def replacement(self, m):
        return self.new_text()

    def is_done(self, src):
        new = self.new_text()
        if not new:
            return None  # a deletion has no positive marker; zero matches is all we can say
        return _norm(src.text).count(_norm(new))


def _norm(t):
    return " ".join(t.split())


class _Hit:
    def __init__(self, text):
        self.text = text


class FuncSet(Rule):
    """Many function definitions at once: every definition in rules\\<body> replaces the target definition with the
    same (whitespace-normalised) header; every header listed in `delete` is removed. Expect = bodies + deletes."""
    kind = "funcset"

    def __init__(self, id, files, body, delete=(), **kw):
        super().__init__(id, files, r"(?!)", None, None, **kw)
        self.body, self.delete = body, [_norm(d) for d in delete]
        b = self.bodies()
        self.expect = len(b) + len(self.delete)

    def bodies(self):
        with open(os.path.join(RULES_DIR, self.body), encoding="utf-8", newline="") as f:
            text = f.read().replace("\r\n", "\n")
        out = {}
        for s, e, name, sig in find_functions(mask(text)):
            if sig in out:
                raise SystemExit(f"{self.body}: two bodies with the same header: {sig}")
            out[sig] = text[s:e]
        return out

    def matches(self, src):
        bodies, out = self.bodies(), []
        for s, e, name, sig in src.functions():
            if sig in bodies and _norm(src.text[s:e]) != _norm(bodies[sig]):
                out.append((s, e, _Hit(bodies[sig])))
            elif sig in self.delete:
                end = e
                while end < len(src.text) and src.text[end] == "\n" and end - e < 2:
                    end += 1  # the definition's line break and one blank separator line
                out.append((s, end, _Hit("")))
        return out

    def replacement(self, m):
        return m.text

    def missing(self, srcs):
        """Bodies whose header exists in none of the target files (a typo or a changed signature)."""
        have = {sig for src in srcs for _, _, _, sig in src.functions()}
        return [sig for sig in self.bodies() if sig not in have]

    def is_done(self, src):
        bodies = self.bodies()
        done = sum(1 for s, e, _, sig in src.functions() if sig in bodies and
                   _norm(src.text[s:e]) == _norm(bodies[sig]))
        left = sum(1 for _, _, _, sig in src.functions() if sig in self.delete)
        return done if not left else 0


class NewFile(Rule):
    """A file the port adds: written from rules\\<body> (CRLF, like the tree). Pristine = absent."""
    kind = "newfile"

    def __init__(self, id, path, body, **kw):
        super().__init__(id, [path], r"(?!)", None, 1, **kw)
        self.path, self.body = path, body

    def text(self):
        with open(os.path.join(RULES_DIR, self.body), encoding="utf-8", newline="") as f:
            return f.read().replace("\r\n", "\n")


class Insert(Rule):
    """New code: `text` inserted before/after each anchor match (regex), unless the text is already present."""
    kind = "insert"

    def __init__(self, id, files, anchor, text, where="before", expect=1, blank=True, **kw):
        super().__init__(id, files, anchor, None, expect, **kw)
        self.text, self.where, self.blank = text.strip("\n"), where, blank

    def matches(self, src):
        if _norm(self.text) in _norm(src.text):
            return []
        out = []
        for s, e, m in super().matches(src):
            if self.where == "before":
                ls = src.text.rfind("\n", 0, s) + 1  # insert at the start of the anchor's line
                out.append((ls, ls, m))
            else:
                le = src.text.find("\n", e)
                le = len(src.text) if le < 0 else le + 1  # after the anchor's line
                out.append((le, le, m))
        return out

    def replacement(self, m):
        sep = "\n" if self.blank else ""
        return self.text + "\n" + sep if self.where == "before" else sep + self.text + "\n"

    def is_done(self, src):
        return 1 if _norm(self.text) in _norm(src.text) else 0


# ----------------------------------------------------------------------------------------------------------- sources
class Source:
    def __init__(self, rel, raw=None):
        self.rel = rel.replace("\\", "/")
        self.path = os.path.join(ROOT, self.rel)
        if raw is None:
            with open(self.path, encoding="utf-8", newline="") as f:
                raw = f.read()
        crlf, lf = raw.count("\r\n"), raw.count("\n")
        self.eol = "\r\n" if crlf and crlf == lf else "\n"
        self.mixed = bool(crlf) and crlf != lf
        self.text = raw.replace("\r\n", "\n") if self.eol == "\r\n" else raw
        self.orig = self.text
        self.applied = []
        self._refresh()

    def _refresh(self):
        self.code = mask(self.text)
        self.line_starts = [0] + [m.end() for m in re.finditer("\n", self.text)]
        self._arm = None
        self._funcs = None

    def functions(self):
        """[(start, end, qualified_name, signature)] of every function definition (namespace/class level)."""
        if self._funcs is None:
            self._funcs = find_functions(self.code)
        return self._funcs

    def function_at(self, pos):
        best = None
        for f in self.functions():
            if f[0] <= pos < f[1] and (best is None or f[0] >= best[0]):
                best = f
        return best

    def set_text(self, t):
        self.text = t
        self._refresh()

    def line_of(self, pos):
        return bisect.bisect_right(self.line_starts, pos)

    def line_text(self, ln):
        s = self.line_starts[ln - 1]
        e = self.text.find("\n", s)
        return self.text[s:] if e < 0 else self.text[s:e]

    def in_arm(self, pos):
        if self._arm is None:
            self._arm = arm_lines(self.code)
        return self._arm[self.line_of(pos) - 1]

    def save(self):
        if self.mixed:
            raise SystemExit(f"refusing to write {self.rel}: mixed line endings")
        keep_pristine(self.rel)
        data = self.text.replace("\n", "\r\n") if self.eol == "\r\n" else self.text
        with open(self.path, "w", encoding="utf-8", newline="") as f:
            f.write(data)


_TOK = re.compile(r'//[^\n]*|/\*.*?\*/|(?<!\w)R"([^(\s"]{0,16})\(.*?\)\1"|"(?:\\.|[^"\\\n])*"|'
                  r"(?<![0-9A-Za-z_])'(?:\\.|[^'\\\n])*'", re.S)
_NOT_NL = re.compile(r"[^\n]")


def mask(t):
    """Same-length copy of C++ text with comments and string/char literals blanked (newlines kept)."""
    return _TOK.sub(lambda m: _NOT_NL.sub(" ", m.group(0)), t)


_SCOPE_KW = re.compile(r"^\s*(?:template\s*<[^{]*>\s*)?(?:namespace|class|struct|union|enum|extern\s+\"C\")\b")
_CTRL_KW = re.compile(r"^\s*(?:if|for|while|switch|do|else|catch|try|return)\b")
_PPLINE = re.compile(r"^[ \t]*#.*$", re.M)


_PP_DIRECTIVE = re.compile(r"^[ \t]*#(?:[^\n]*\\\n)*[^\n]*", re.M)


def find_functions(code):
    """Function definitions at namespace/class level in masked code -> [(start, end, name, signature)].
    The signature is the text between the previous ; { } and the body's '{' (preprocessor lines removed)."""
    # Preprocessor directives (with their continuation lines, e.g. macro bodies with braces) are not code here.
    code = _PP_DIRECTIVE.sub(lambda m: _NOT_NL.sub(" ", m.group(0)), code)
    out, stack, last = [], [], 0  # stack: (kind, name)
    i, n = 0, len(code)
    while i < n:
        c = code[i]
        if c == "{":
            header = _PPLINE.sub(" ", code[last:i])
            parent_ok = all(k in ("scope",) for k, _ in stack)
            kind, name = "block", ""
            if _SCOPE_KW.match(header):
                kind = "scope"
                m = re.search(r"(?:class|struct|union)\s+(?:V8_EXPORT_PRIVATE\s+|V8_NODISCARD\s+)?(\w+)", header)
                name = m.group(1) if m else ""
            elif parent_ok and "(" in header and not _CTRL_KW.match(header) and "=" not in header.split("(")[0]:
                m = re.search(r"((?:\w+::)*~?\w+|operator\s*\S+)\s*\(", header)
                if m:
                    kind = "fn"
                    cls = "::".join(nm for k, nm in stack if k == "scope" and nm)
                    name = m.group(1) if "::" in m.group(1) or not cls else f"{cls}::{m.group(1)}"
                    stack.append((kind, name))
                    sig_start = i - len(code[last:i].lstrip()) if code[last:i].strip() else i
                    depth, j = 1, i + 1
                    while j < n and depth:
                        if code[j] == "{":
                            depth += 1
                        elif code[j] == "}":
                            depth -= 1
                        j += 1
                    out.append((sig_start, j, name, " ".join(header.split())))
                    stack.pop()
                    i, last = j, j
                    continue
            stack.append((kind, name))
            last = i + 1
        elif c == "}":
            if stack:
                stack.pop()
            last = i + 1
        elif c == ";":
            last = i + 1
        i += 1
    return out


_PP = re.compile(r"^[ \t]*#[ \t]*(if|ifdef|ifndef|elif|else|endif)\b(.*)$")
_NEG = re.compile(r"!\s*(?:defined\s*\(\s*\w+\s*\)|\w+)")
_ARMCOND = re.compile(r"\bV8_TARGET_ARCH_ARM\b(?!64)")


def _is_arm(cond):
    return bool(_ARMCOND.search(_NEG.sub(" ", cond)))


def arm_lines(code):
    """Per line: True when inside a preprocessor branch whose condition names V8_TARGET_ARCH_ARM positively."""
    res, stack = [], []  # stack of [this_branch_is_arm]
    for line in code.split("\n"):
        m = _PP.match(line)
        if m:
            d, cond = m.group(1), m.group(2)
            if d in ("if", "ifdef", "ifndef"):
                stack.append(d != "ifndef" and _is_arm(cond))
            elif d == "elif" and stack:
                stack[-1] = _is_arm(cond)
            elif d == "else" and stack:
                stack[-1] = False
            elif d == "endif" and stack:
                stack.pop()
        res.append(any(stack))
    return res


_cache = {}


def files_for(globs):
    out = []
    for g in globs:
        if g not in _cache:
            hits = glob.glob(os.path.join(ROOT, g), recursive=True)
            _cache[g] = sorted({os.path.relpath(h, ROOT).replace("\\", "/") for h in hits
                                if os.path.isfile(h) and "arm64" not in h.replace("\\", "/")
                                and h.endswith((".cc", ".h", ".S", ".inc", ".gn", ".gni"))})
        out += _cache[g]
    return sorted(set(out))


_sources = {}
_new_files = {}  # rel -> (current text or None, new text), filled by NewFile rules
_replay = False  # set by the replay mode: files the port created start absent


def source(rel):
    if rel not in _sources:
        _sources[rel] = Source(rel)
    return _sources[rel]


def keep_pristine(rel):
    dst = os.path.join(PRISTINE_DIR, rel)
    if not os.path.exists(dst):
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        org = os.path.join(PRISTINE_SRC, rel)
        shutil.copyfile(org if os.path.exists(org) else os.path.join(ROOT, rel), dst)


# ----------------------------------------------------------------------------------------------------------- loading
def load_rules(only=None):
    rules = []
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    sys.modules.setdefault("t2run", sys.modules[__name__])
    for fn in sorted(os.listdir(RULES_DIR)):
        if not fn.endswith(".py") or fn.startswith("_"):
            continue
        spec = importlib.util.spec_from_file_location("rules_" + fn[:-3], os.path.join(RULES_DIR, fn))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        for r in getattr(mod, "RULES", []):
            r.module = fn
            rules.append(r)
    ids = [r.id for r in rules]
    dup = {i for i in ids if ids.count(i) > 1}
    if dup:
        raise SystemExit(f"duplicate rule ids: {sorted(dup)}")
    if only:
        pats = [re.compile(re.escape(o).replace(r"\*", ".*") + "$") for o in only]
        rules = [r for r in rules if any(p.match(r.id) or r.module.startswith(o) for p, o in zip(pats, only))]
        if not rules:
            raise SystemExit(f"no rule matches {only} (ids exact with * wildcards, or a rule-file name prefix)")
    return rules


# ----------------------------------------------------------------------------------------------------------- engine
class Report:
    def __init__(self, mode):
        os.makedirs(RUNS_DIR, exist_ok=True)
        self.path = os.path.join(RUNS_DIR, f"{mode}-{time.strftime('%Y%m%d-%H%M%S')}.md")
        self.lines = []

    def __call__(self, s=""):
        self.lines.append(s)

    def write(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("\n".join(self.lines) + "\n")
        return self.path


def site_lines(src, hits, limit=None):
    seen, out = set(), []
    for s, e, m in hits:
        ln = src.line_of(s)
        if ln in seen:
            continue
        seen.add(ln)
        out.append(f"  {src.rel}:{ln}: {src.line_text(ln).strip()}")
    return out[:limit] if limit else out


def evaluate(rule):
    """-> (state, total, per_file {rel: hits}) on the current in-memory sources."""
    per = {}
    for rel in files_for(rule.files):
        hits = rule.matches(source(rel))
        if hits:
            per[rel] = hits
    total = sum(len(h) for h in per.values())
    if isinstance(rule, Flag):
        return "flag", total, per
    if isinstance(rule, FuncSet):
        # Bodies can be added after a first apply: pending = everything that still differs, unless a body has no
        # target at all (a typo or a changed signature), which is a mismatch.
        if rule.missing([source(rel) for rel in files_for(rule.files)]):
            return "MISMATCH", total, per
        rule.expect = total if total else rule.expect
    if rule.expect is not None and total == rule.expect:
        return "pending", total, per
    if total == 0:
        done = 0
        for rel in files_for(rule.files):
            src = source(rel)
            if hasattr(rule, "is_done"):
                d = rule.is_done(src)
                done = None if d is None else done + d
            elif rule.done_regex() is not None:
                done += len(rule.done_regex().findall(src.text))
            else:
                done = None
            if done is None:
                break
        if done is None:
            return "applied?", 0, per
        return ("applied" if done >= 1 else "MISSING"), 0, per
    return "MISMATCH", total, per


def apply_rule(rule, per):
    for rel, hits in per.items():
        src = source(rel)
        t = src.text
        for s, e, m in sorted(hits, key=lambda h: h[0], reverse=True):
            t = t[:s] + rule.replacement(m) + t[e:]
        src.set_text(t)
        src.applied.append(rule.id)


def check_oracle(rules, rep):
    import importlib
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    t32 = importlib.import_module("t32oracle")
    claims = [(r, a, v) for r in rules for a, v in r.oracle]
    if not claims:
        return set()
    # A claim may carry follow-on instructions ("it ne ; nopne"); the value is checked against the first one.
    lines, first, groups = [], [], []
    for _, a, _ in claims:
        parts = [p.strip() for p in a.split(" ; ")]
        first.append(len(lines))
        groups.append((len(lines), len(lines) + len(parts)))
        lines += parts
    enc = t32.encode(lines)
    res = []
    for (lo, hi) in groups:
        err = next((e[3] for e in enc[lo:hi] if e[3]), None)
        res.append((None, enc[lo][1], enc[lo][2], err))
    bad = set()
    for (r, a, v), (_, hws, canon, err) in zip(claims, res):
        got = canon if canon is not None else (hws[0] if hws else None)
        ok = err is None and got == v
        if not ok:
            bad.add(r.id)
        rep(f"  {'ok ' if ok else 'BAD'} {r.id:28} {a:36} claim 0x{v:08X} oracle "
            + (f"ERROR {err}" if err else f"0x{got:08X}"))
    return bad


def run_rules(mode, rules, rep):
    bad_oracle = set()
    if mode in ("apply", "diff", "scan"):
        rep("## oracle claims")
        bad_oracle = check_oracle([r for r in rules if not isinstance(r, Flag)], rep)
    rep(f"## rules ({mode})")
    summary, ok = [], True
    for r in rules:
        if mode != "census" and isinstance(r, Flag):
            continue
        if isinstance(r, NewFile):
            path = os.path.join(ROOT, r.path)
            ours = os.path.exists(os.path.join(PRISTINE_DIR, r.path + ".absent"))
            cur = open(path, encoding="utf-8", newline="").read().replace("\r\n", "\n") if os.path.exists(path) else None
            if _replay and ours:
                cur = None  # replay starts from pristine, where the file does not exist
            state = "applied" if cur == r.text() else ("pending" if cur is None or ours else "MISMATCH")
            if r.id in bad_oracle:
                state = "ORACLE-FAIL"
            summary.append(f"{state:12} {r.id:34} {'new':>5}  [{r.module}] {r.desc or r.path}")
            rep(f"### {r.id} - {state} - new file {r.path}  [{r.module}]")
            if state in ("MISMATCH", "ORACLE-FAIL"):
                ok = False
            if state == "pending" and mode in ("apply", "diff", "scan"):
                _new_files[r.path] = (cur, r.text())
            continue
        state, total, per = evaluate(r)
        if r.id in bad_oracle:
            state = "ORACLE-FAIL"
        exp = "" if r.expect is None else f" expect {r.expect}"
        summary.append(f"{state:12} {r.id:34} {total:5}{exp}  [{r.module}] {r.desc}")
        rep(f"### {r.id} - {state} - {total} site(s){exp}  [{r.module}]")
        if isinstance(r, FuncSet):
            miss = r.missing([source(rel) for rel in files_for(r.files)])
            for sig in miss:
                rep(f"NO TARGET for body: {sig}")
            if miss:
                summary[-1] += f"  ({len(miss)} body header(s) match nothing - see report)"
        if r.desc:
            rep(f"{r.desc}")
        if r.map_ref:
            rep(f"map: {r.map_ref}")
        groups = {}
        for rel, hits in per.items():
            src = source(rel)
            if r.classify:
                for h in hits:
                    groups.setdefault(r.classify(h[2]), []).append((src, h))
            rep(f"- {rel}: {len(hits)}")
        if r.classify:
            rep("groups: " + ", ".join(f"{k}={len(v)}" for k, v in sorted(groups.items(), key=lambda x: -len(x[1]))))
            for k, v in sorted(groups.items(), key=lambda x: -len(x[1])):
                rep(f"#### {r.id} / {k}")
                for src, h in v:
                    rep(site_lines(src, [h])[0])
        else:
            for rel, hits in per.items():
                rep("\n".join(site_lines(source(rel), hits)))
        if state in ("MISMATCH", "MISSING", "ORACLE-FAIL"):
            ok = False
        if state == "pending" and mode in ("apply", "diff", "scan"):  # later rules see earlier ones applied
            apply_rule(r, per)
            after = sum(len(r.matches(source(rel))) for rel in files_for(r.files))
            if after:
                ok = False
                summary[-1] = summary[-1].replace("pending", "NOT-IDEMP", 1)
                rep(f"NOT IDEMPOTENT: {after} site(s) still match after replacement")
    return summary, ok


def changed_sources():
    return [s for s in _sources.values() if s.text != s.orig]


def unified(src_rel, a, b):
    return "".join(difflib.unified_diff(a.splitlines(True), b.splitlines(True), "a/" + src_rel, "b/" + src_rel, n=3))


# ----------------------------------------------------------------------------------------------------------- modes
def resolve_ref(ref):
    """'assembler-arm.cc:12-20' or 'v8/src/x.cc:5' -> (rel, a, b)."""
    m = re.match(r"(.+?):(\d+)(?:-(\d+))?$", ref)
    if not m:
        return None
    name, a, b = m.group(1).strip(), int(m.group(2)), int(m.group(3) or m.group(2))
    cand = [name] if os.path.isfile(os.path.join(ROOT, name)) else []
    if not cand and os.path.isfile(os.path.join(ROOT, "v8", name)):
        cand = ["v8/" + name]
    if not cand:
        cand = [f for f in files_for(GENERIC + ARM + ARM_TESTS) if f.endswith("/" + name.split("/")[-1])
                and f.endswith(name.replace("\\", "/"))]
    return (cand, a, b)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["census", "scan", "diff", "apply", "oracle", "grep", "show", "mapcheck",
                                     "delta", "restore", "funcs", "replay"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--files", action="append", help="globs for grep/funcs (default: ARM files)")
    ap.add_argument("--region", choices=["arm"], help="grep only inside #if V8_TARGET_ARCH_ARM branches")
    ap.add_argument("--within", help="grep/funcs only inside functions whose '<name> <signature>' matches this regex")
    ap.add_argument("--all-text", action="store_true", help="grep comments/strings too")
    ap.add_argument("--max", type=int, default=0, help="grep: stop printing after N lines (0 = all)")
    o = ap.parse_args()
    sys.stdout.reconfigure(errors="replace")
    rep = Report(o.mode)

    if o.mode == "funcs":
        for rel in files_for(o.files or ARM):
            src = source(rel)
            for s, e, name, sig in src.functions():
                if o.within and not re.search(o.within, f"{name} {sig}"):
                    continue
                print(f"{rel}:{src.line_of(s)}-{src.line_of(e - 1)}: {name}  [{sig[:150]}]")
        return

    if o.mode == "grep":
        rx = re.compile(o.args[0], re.MULTILINE)
        globs = o.files or ARM
        n = 0
        for rel in files_for(globs):
            src = source(rel)
            hay = src.text if o.all_text else src.code
            for m in rx.finditer(hay):
                if o.region == "arm" and not src.in_arm(m.start()):
                    continue
                f = src.function_at(m.start()) if o.within else None
                # --within "." only labels matches with their function (never drops one).
                if o.within and o.within != "." and (f is None or not re.search(o.within, f"{f[2]} {f[3]}")):
                    continue
                ln = src.line_of(m.start())
                n += 1
                line = f"{rel}:{ln}: {src.line_text(ln).strip()}" + (f"   <{f[2]}>" if f else "")
                rep(line)
                if not o.max or n <= o.max:
                    print(line)
        print(f"-- {n} match(es); report {rep.write()}")
        return

    if o.mode == "show":
        for ref in o.args:
            cand, a, b = resolve_ref(ref)
            if len(cand) != 1:
                print(f"{ref}: {'not found' if not cand else 'ambiguous ' + str(cand)}")
                continue
            src = source(cand[0])
            for ln in range(a, min(b, len(src.line_starts)) + 1):
                print(f"{cand[0]}:{ln}: {src.line_text(ln)}")
        return

    if o.mode == "mapcheck":
        d = json.load(open(MAP_JSON, encoding="utf-8"))
        refrx = re.compile(r"([\w./-]+\.(?:cc|h|S)):(\d+(?:-\d+)?(?:,\s*\d+(?:-\d+)?)*)")
        for area in d["results"]:
            for it in area["items"]:
                rep(f"### [{area['area']}] {it.get('area')}")
                for fm in refrx.finditer(it.get("site", "")):
                    for part in re.split(r",\s*", fm.group(2)):
                        cand, a, b = resolve_ref(f"{fm.group(1)}:{part}")
                        if len(cand) != 1:
                            rep(f"  {fm.group(1)}:{part}: {'NOT FOUND' if not cand else 'AMBIGUOUS'}")
                            continue
                        src = source(cand[0])
                        if a > len(src.line_starts):
                            rep(f"  {cand[0]}:{a}: PAST EOF")
                            continue
                        rep(f"  {cand[0]}:{part}: {src.line_text(a).strip()}")
        print(f"report {rep.write()}")
        return

    if o.mode == "delta":
        out = []
        for root, _, fs in os.walk(PRISTINE_DIR):
            for fn in fs:
                p = os.path.join(root, fn)
                rel = os.path.relpath(p, PRISTINE_DIR).replace("\\", "/")
                if rel.endswith(".absent"):
                    rel, a = rel[:-len(".absent")], ""
                else:
                    a = open(p, encoding="utf-8", newline="").read().replace("\r\n", "\n")
                b = source(rel).text
                out.append(unified(rel, a, b))
        path = os.path.join(RUNS_DIR, "thumb2-delta.patch")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write("".join(out))
        print(f"{len([x for x in out if x])} file(s) differ from pristine -> {path}")
        return

    if o.mode == "replay":
        # The port must equal pristine + rules: start every touched file from its pristine copy (new files from
        # nothing), apply every rule in order in memory, compare with the tree. Nothing is written.
        global _replay
        _replay = True
        for root, _, fs in os.walk(PRISTINE_DIR):
            for fn in fs:
                rel = os.path.relpath(os.path.join(root, fn), PRISTINE_DIR).replace("\\", "/")
                if rel.endswith(".absent"):
                    continue
                raw = open(os.path.join(root, fn), encoding="utf-8", newline="").read()
                _sources[rel] = Source(rel, raw)
        rules = load_rules(o.args)
        summary, ok = run_rules("scan", rules, rep)
        bad = [ln for ln in summary if not ln.startswith(("pending", "applied"))]
        diffs = []
        for rel, src in sorted(_sources.items()):
            disk = open(os.path.join(ROOT, rel), encoding="utf-8", newline="").read().replace("\r\n", "\n")
            if src.text != disk:
                diffs.append(rel)
                rep(unified(rel, disk, src.text))
        for rel, (cur, new) in _new_files.items():
            disk = open(os.path.join(ROOT, rel), encoding="utf-8", newline="").read().replace("\r\n", "\n")
            if new != disk:
                diffs.append(rel)
        print("\n".join(bad) if bad else f"all {len(summary)} rules applied cleanly from pristine")
        print(f"-- replay: {len(_sources)} file(s) + {len(_new_files)} new; "
              f"{'IDENTICAL to the tree' if not diffs else 'DIFFERS: ' + ', '.join(diffs)}; report {rep.write()}")
        sys.exit(0 if not diffs and not bad else 1)

    if o.mode == "restore":
        for rel in o.args:
            rel = rel.replace("\\", "/")
            p = os.path.join(PRISTINE_DIR, rel)
            if not os.path.exists(p):
                print(f"{rel}: no pristine copy (never written by t2run)")
                continue
            shutil.copyfile(p, os.path.join(ROOT, rel))
            print(f"restored {rel}")
        return

    rules = load_rules(o.args)
    if o.mode == "oracle":
        rep("## oracle claims")
        bad = check_oracle(rules, rep)
        print("\n".join(rep.lines))
        print(f"-- {len(bad)} rule(s) with failing claims; report {rep.write()}")
        return

    summary, ok = run_rules(o.mode, rules, rep)
    changed = changed_sources()
    if o.mode == "diff":
        path = os.path.join(RUNS_DIR, f"diff-{time.strftime('%Y%m%d-%H%M%S')}.patch")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write("".join(unified(s.rel, s.orig, s.text) for s in changed))
            f.write("".join(unified(rel, cur or "", new) for rel, (cur, new) in _new_files.items()))
        rep(f"diff: {path}")
    if o.mode == "apply":
        if not ok:
            rep("NOTHING WRITTEN: fix the MISMATCH / MISSING / ORACLE-FAIL / NOT-IDEMP rules first")
        else:
            with open(os.path.join(RUNS_DIR, "journal.jsonl"), "a", encoding="utf-8") as j:
                for s in changed:
                    before = hashlib.sha256(s.orig.encode()).hexdigest()[:16]
                    s.save()
                    after = hashlib.sha256(s.text.encode()).hexdigest()[:16]
                    j.write(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "file": s.rel,
                                        "rules": s.applied,
                                        "sha_before": before, "sha_after": after}) + "\n")
                for rel, (cur, new) in _new_files.items():
                    path = os.path.join(ROOT, rel)
                    marker = os.path.join(PRISTINE_DIR, rel + ".absent")
                    if cur is None and not os.path.exists(marker):
                        os.makedirs(os.path.dirname(marker), exist_ok=True)
                        open(marker, "w").close()
                    with open(path, "w", encoding="utf-8", newline="") as f:
                        f.write(new.replace("\n", "\r\n"))
                    j.write(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "file": rel, "rules": ["new file"],
                                        "sha_after": hashlib.sha256(new.encode()).hexdigest()[:16]}) + "\n")
            rep(f"WROTE {len(changed) + len(_new_files)} file(s)")
    print("\n".join(summary))
    tail = f"-- {len(summary)} rule(s), {len(changed) + len(_new_files)} file(s) changed in memory"
    if o.mode == "apply":
        tail += "; WRITTEN" if ok else "; NOTHING WRITTEN"
    print(f"{tail}; report {rep.write()}")
    sys.exit(0 if ok or o.mode == "census" else 1)


if __name__ == "__main__":
    main()
