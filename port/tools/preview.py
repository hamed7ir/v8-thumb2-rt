"""Applies rules in memory (nothing is written) and prints lines of the result.
Usage: python preview.py <rule ids/prefixes,comma separated> <rel path> <regex> [context lines]
Prints `context` lines after every line matching regex in the would-be result."""
import re, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import t2run

sys.stdout.reconfigure(errors="replace")
only = sys.argv[1].split(",")
rel, rx = sys.argv[2], re.compile(sys.argv[3])
ctx = int(sys.argv[4]) if len(sys.argv) > 4 else 12
rules = t2run.load_rules(only)
for r in rules:
    if isinstance(r, (t2run.Flag, t2run.NewFile)):
        continue
    state, total, per = t2run.evaluate(r)
    if state == "pending":
        t2run.apply_rule(r, per)
lines = t2run.source(rel).text.split("\n")
for i, l in enumerate(lines):
    if rx.search(l):
        print(f"---- line {i + 1}")
        print("\n".join(lines[i:i + ctx]))
