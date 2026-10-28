"""Self-test for t2run.mask(): comments / strings / char literals / raw strings blanked, lengths and newlines kept."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import t2run

# (source, expected masked text with whitespace collapsed)
CASES = [
    ('int a = 1\'000; // c "x"\nb;', "int a = 1'000; b;"),
    ("char c = '\\''; d;", "char c = ; d;"),
    ('s = "a\\"b" + x;', "s = + x;"),
    ("p /* m\nl */ q", "p q"),
    ('R"x(raw ) " )x" end', "end"),
    ('f("//not comment"); g;', "f( ); g;"),
    ("pc_offset() // tail", "pc_offset()"),
    ("x = y; /* a */ z = '/'; // b", "x = y; z = ;"),
]
bad = 0
for src, want in CASES:
    got = t2run.mask(src)
    same_shape = len(got) == len(src) and [i for i, c in enumerate(got) if c == "\n"] == \
        [i for i, c in enumerate(src) if c == "\n"]
    ok = same_shape and " ".join(got.split()) == want
    bad += not ok
    print("ok " if ok else "BAD", repr(src), "->", repr(got), "" if ok else f"want {want!r} shape={same_shape}")
print("FAIL" if bad else "ALL OK")
sys.exit(1 if bad else 0)
