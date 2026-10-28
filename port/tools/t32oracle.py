"""T32 encoding oracle for the V8 Thumb-2 port: assembles Thumb-2 instructions with rt2.1's llvm-mc and returns their
encodings, so every encoder in V8's assembler can be checked bit-for-bit.

Encodings are returned in the ARM ARM's CANONICAL form: a 32-bit instruction as (hw1 << 16) | hw2 (e.g. mov.w r0,#1 ->
0xF04F0001); in memory the first halfword hw1 sits at the lower address, so a little-endian 32-bit read gives
(hw2 << 16) | hw1. 16-bit instructions are returned as their halfword. Usage:
    import t32oracle; t32oracle.encode(["mov.w r0, #1", "it eq", "moveq r0, #1"]) -> [(text, [halfwords], canonical|None, error)]
    python -I t32oracle.py "mov.w r0, #1" "vadd.f64 d0, d1, d2"
"""
import re, subprocess, sys, tempfile, os

LLVM_MC = r"F:\cr\rt21-dev\bin\llvm-mc.exe"
TRIPLE = "thumbv7-pc-windows-msvc"
MATTR = "+v7,+thumb2,+vfp3,+d32,+neon,+hwdiv,+hwdiv-arm"


def encode(lines):
    """Assemble each line separately-but-batched; returns [(text, halfwords, canonical32_or_None, error_or_None)]."""
    with tempfile.NamedTemporaryFile("w", suffix=".s", delete=False, encoding="ascii") as f:
        f.write("\n".join(lines) + "\n")
        path = f.name
    try:
        r = subprocess.run([LLVM_MC, "-triple=" + TRIPLE, "-mattr=" + MATTR, "-show-encoding", path],
                           capture_output=True, text=True)
    finally:
        os.unlink(path)
    errors = {}
    for m in re.finditer(r":(\d+):\d+: error: (.*)", r.stderr):
        errors.setdefault(int(m.group(1)), m.group(2))
    encs = [m for m in re.finditer(r"encoding: \[([^\]]*)\]", r.stdout)]
    out, ei = [], 0
    for i, text in enumerate(lines, 1):
        if i in errors or not text.strip() or text.strip().startswith(("@", ".")):
            out.append((text, None, None, errors.get(i)))
            continue
        bs = [int(x, 16) for x in encs[ei].group(1).split(",")]
        ei += 1
        hws = [bs[k] | (bs[k + 1] << 8) for k in range(0, len(bs), 2)]
        canon = (hws[0] << 16) | hws[1] if len(hws) == 2 else None
        out.append((text, hws, canon, None))
    return out


if __name__ == "__main__":
    for text, hws, canon, err in encode(sys.argv[1:]):
        if err:
            print(f"{text:40} ERROR {err}")
        else:
            print(f"{text:40} {'0x%08X' % canon if canon is not None else '0x%04X (16-bit)' % hws[0]}")
