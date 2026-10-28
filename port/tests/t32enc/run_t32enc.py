"""Builds t32enc_test.cc against the t32-encodings-arm.h rule body (or the tree copy with --tree), runs it and checks
every line with llvm-mc (tools\\t32oracle.py). Usage: python run_t32enc.py [--tree] [--show N]"""
import os, re, shutil, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "tools"))
import t32oracle

BODY = os.path.join(HERE, "..", "..", "rules", "bodies", "t32-encodings-arm.h")
TREE = r"D:\cr\rt8\src\v8\src\codegen\arm\t32-encodings-arm.h"
VCVARS = r"D:\Program Files\vs22buildtools\VC\Auxiliary\Build\vcvars64.bat"
BUILD = os.path.join(HERE, "build")


def build(header):
    inc = os.path.join(BUILD, "inc", "src", "codegen", "arm")
    os.makedirs(inc, exist_ok=True)
    shutil.copyfile(header, os.path.join(inc, "t32-encodings-arm.h"))
    exe = os.path.join(BUILD, "t32enc_test.exe")
    script = os.path.join(BUILD, "build.cmd")
    with open(script, "w") as f:
        f.write("@echo off\r\n")
        f.write(f'call "{VCVARS}" >nul || exit /b 2\r\n')
        f.write(f'cl /nologo /std:c++17 /W4 /WX /EHsc /O2 /I"{os.path.join(BUILD, "inc")}" '
                f'/I"{os.path.join(HERE, "stub")}" /Fo"{BUILD}\\\\" /Fe"{exe}" '
                f'"{os.path.join(HERE, "t32enc_test.cc")}"\r\n')
    r = subprocess.run(["cmd", "/c", script], capture_output=True, text=True)
    if r.returncode:
        print(r.stdout[-4000:], r.stderr[-2000:])
        raise SystemExit("BUILD FAILED")
    return exe


def main():
    header = TREE if "--tree" in sys.argv else BODY
    show = int(sys.argv[sys.argv.index("--show") + 1]) if "--show" in sys.argv else 25
    exe = build(header)
    r = subprocess.run([exe], capture_output=True, text=True)
    lines = r.stdout.splitlines()
    selfs = [l for l in lines if l.startswith("SELF ")]
    cases = [l.split("\t") for l in lines if not l.startswith("SELF ") and "\t" in l]
    flat, spans = [], []
    for text, _ in cases:
        parts = [p.strip() for p in text.split(" ; ")]
        spans.append((len(flat), len(parts)))
        flat += parts
    enc = t32oracle.encode(flat)
    bad = []
    for (text, want), (lo, n) in zip(cases, spans):
        res = enc[lo:lo + n]
        err = next((e[3] for e in res if e[3]), None)
        if want == "ERR":
            if err is None:
                got = res[0][2] if n == 1 else None
                bad.append((text, "ERR", f"llvm-mc accepts it ({got and hex(got)})"))
            continue
        if err:
            bad.append((text, want, "llvm-mc ERROR " + err))
            continue
        if n == 1:
            got = res[0][2] if res[0][2] is not None else None
            if got is None:
                bad.append((text, want, f"llvm-mc chose a 16-bit form {res[0][1]}"))
                continue
        else:
            hws = [h for e in res for h in e[1]]
            if len(hws) != 2:
                bad.append((text, want, f"unit is not two halfwords: {hws}"))
                continue
            got = hws[0] << 16 | hws[1]
        if got != int(want, 16):
            bad.append((text, want, f"llvm-mc {got:08X}"))
    self_bad = [s for s in selfs if not s.endswith(" ok")]
    print(f"{len(cases)} encodings checked, {len(bad)} mismatch(es); {len(selfs)} self-checks, {len(self_bad)} failed;"
          f" exit {r.returncode}")
    for b in bad[:show]:
        print("  BAD", b)
    for s in self_bad:
        print("  ", s)
    if r.stderr.strip():
        print(r.stderr.strip()[-1500:])
    sys.exit(1 if bad or self_bad or r.returncode else 0)


if __name__ == "__main__":
    main()
