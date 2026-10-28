"""Disassemble a raw code dump (hangprobe pc-*.bin / lr-*.bin) as Thumb-2 with llvm-mc, with addresses.
usage: python disasm_bin.py <file.bin> <base_address_hex> [mark_address_hex]"""
import re, subprocess, sys

sys.stdout.reconfigure(errors="replace")
MC = r"F:\cr\rt21-dev\bin\llvm-mc.exe"
data = open(sys.argv[1], "rb").read()
base = int(sys.argv[2], 16)
mark = int(sys.argv[3], 16) & ~1 if len(sys.argv) > 3 else None
hexs = " ".join(f"0x{b:02x}" for b in data)
r = subprocess.run([MC, "--disassemble", "-triple=thumbv7-pc-windows-msvc", "-show-encoding"], input=hexs,
                   capture_output=True, text=True)
addr = base
for line in r.stdout.splitlines():
    m = re.match(r"\s+(\S.*?)\s*@ encoding: \[(.*)\]", line)
    if not m:
        continue
    n = len(m.group(2).split(","))
    flag = "  <== pc" if mark is not None and addr == mark else ""
    print(f"{addr:08X}  {m.group(1):40} [{m.group(2)}]{flag}")
    addr += n
if r.stderr.strip():
    print("STDERR:", r.stderr[:800])
