"""Prints the normalised header (as FuncSet matches it) of every function in a file whose name matches a regex.
Usage: python sigs.py <rel path under D:\\cr\\rt8\\src> <name regex>"""
import re, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import t2run

sys.stdout.reconfigure(errors="replace")
src = t2run.source(sys.argv[1])
rx = re.compile(sys.argv[2])
for s, e, name, sig in src.functions():
    if rx.search(name):
        print(f"{src.line_of(s)}\t{sig}")
