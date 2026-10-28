"""Disassemble one builtin from an embedded.S (mksnapshot --embedded_src output) as Thumb-2 with llvm-mc.
usage: python disasm_builtin.py <embedded.S> <BuiltinName> [--a32]
The data directives are little-endian: .octa/.quad/.long values are split into bytes low byte first, .byte as is.
Trailing 0xcc padding (the blob's filler between builtins) is dropped."""
import re, subprocess, sys

sys.stdout.reconfigure(errors="replace")
MC = r"F:\cr\rt21-dev\bin\llvm-mc.exe"
path, name = sys.argv[1], sys.argv[2]
triple = "armv7-pc-windows-msvc" if "--a32" in sys.argv else "thumbv7-pc-windows-msvc"
lines = open(path, encoding="utf-8").read().splitlines()
start = next(i for i, l in enumerate(lines) if l.strip() == f"Builtins_{name}:")
data = bytearray()
for l in lines[start + 1:]:
    s = l.strip()
    if s.endswith(":") and s.startswith("Builtins_"):
        break
    m = re.match(r"\.(octa|quad|long|byte)\s+(.*)", s)
    if not m:
        continue
    width = {"octa": 16, "quad": 8, "long": 4, "byte": 1}[m.group(1)]
    for v in m.group(2).split(","):
        data += int(v.strip(), 0).to_bytes(width, "little")
while data and data[-1] == 0xCC:
    data.pop()
hexs = " ".join(f"0x{b:02x}" for b in data)
r = subprocess.run([MC, "--disassemble", f"-triple={triple}", "-show-encoding"], input=hexs, capture_output=True,
                   text=True)
print(f"; {name}: {len(data)} bytes, {triple}")
print(r.stdout)
if r.stderr.strip():
    print("STDERR:", r.stderr[:2000])
