"""Group every emit(...) in assembler-arm.cc by its encoding family (first terms of the argument) and by the
enclosing function, so encoder rules can be written per family. Usage: python emit_shapes.py [--sites]"""
import re, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import t2run

src = t2run.source("v8/src/codegen/arm/assembler-arm.cc")
code = src.code
func_rx = re.compile(r"^(?:[\w:<>,*& ]+?\s)?(?P<name>(?:\w+::)+~?\w+)\s*\([^;{]*\)\s*(?:const\s*)?\{", re.M)
funcs = [(m.start(), m.group("name")) for m in func_rx.finditer(code)]


def enclosing(pos):
    name = "?"
    for s, n in funcs:
        if s > pos:
            break
        name = n
    return name


def arg_of(pos):
    i = code.index("(", pos)
    depth, j = 0, i
    while j < len(code):
        if code[j] == "(":
            depth += 1
        elif code[j] == ")":
            depth -= 1
            if depth == 0:
                break
        j += 1
    return " ".join(code[i + 1:j].split())


fam = {}
for m in re.finditer(r"\bemit\s*\(", code):
    a = arg_of(m.start())
    first = re.split(r"\s*\|\s*", a)
    head = first[0]
    if re.match(r"^(cond|static_cast<Instr>\(cond\))$", head):
        k = "cond | " + (first[1] if len(first) > 1 else "")
    elif head in ("kSpecialCondition",) or head.startswith(("0xF", "0x1E", "0x1F")):
        k = "uncond " + head
    elif re.match(r"^\w+\(", head):
        k = "call " + head.split("(")[0]
    else:
        k = "other " + head[:30]
    fam.setdefault(k, []).append((src.line_of(m.start()), enclosing(m.start()), a))

for k, v in sorted(fam.items(), key=lambda x: -len(x[1])):
    print(f"{len(v):4}  {k}")
    if "--sites" in sys.argv:
        for ln, fn, a in v:
            print(f"        {ln:5} {fn:40} {a[:110]}")
