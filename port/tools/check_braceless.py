"""For rules that turn one statement into two (e.g. `it(cond); emit(...)`), list every site whose previous code line
is a brace-less if/else/for/while header, where the split would change control flow.
Usage: python check_braceless.py RULE_ID [RULE_ID...]"""
import re, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import t2run

HEADER = re.compile(r"(?:\b(?:if|for|while)\s*\(.*\)|\belse)\s*$")
rules = {r.id: r for r in t2run.load_rules()}
bad = 0
for rid in sys.argv[1:]:
    r = rules[rid]
    n = 0
    for rel in t2run.files_for(r.files):
        src = t2run.source(rel)
        for s, e, m in r.matches(src):
            n += 1
            ln = src.line_of(s)
            k = ln - 1
            while k > 0 and not src.code.split("\n")[k - 1].strip():
                k -= 1
            prev = src.code.split("\n")[k - 1].rstrip() if k > 0 else ""
            # a header line ending in ')' or 'else' with no '{' / ';' after it means a brace-less body follows
            if (HEADER.search(prev) or prev.endswith((")", "else"))) and not prev.endswith(("{", ";")):
                bad += 1
                print(f"BRACELESS {rid} {rel}:{ln}: prev `{prev.strip()}`")
    print(f"{rid}: {n} site(s) checked")
print("ALL SITES SAFE" if not bad else f"{bad} UNSAFE SITE(S)")
sys.exit(1 if bad else 0)
