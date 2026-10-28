"""Whole-blob sanity scan: disassemble EVERY builtin of an embedded.S as Thumb-2 (llvm-mc) and count the bytes llvm-mc
cannot decode per builtin. Leftover A32 code shows up as large counts; constant-pool data words and jump-table entries
explain small ones (each pool starts with the udf.w #length marker, so pool sizes are known and reported separately).
usage: python blob_scan.py <embedded.S> [top N]"""
import collections, re, subprocess, sys

sys.stdout.reconfigure(errors="replace")
MC = r"F:\cr\rt21-dev\bin\llvm-mc.exe"
path = sys.argv[1]
top = int(sys.argv[2]) if len(sys.argv) > 2 else 25
builtins, cur, data = [], None, bytearray()
for l in open(path, encoding="utf-8"):
    s = l.strip()
    if s.startswith("Builtins_") and s.endswith(":"):
        if cur:
            builtins.append((cur, bytes(data)))
        cur, data = s[len("Builtins_"):-1], bytearray()
        continue
    m = re.match(r"\.(octa|quad|long|byte)\s+(.*)", s)
    if cur and m:
        width = {"octa": 16, "quad": 8, "long": 4, "byte": 1}[m.group(1)]
        for v in m.group(2).split(","):
            data += int(v.strip(), 0).to_bytes(width, "little")
if cur:
    builtins.append((cur, bytes(data)))

bad, total, pools = collections.Counter(), 0, collections.Counter()
for name, code in builtins:
    code = code.rstrip(b"\xcc")
    total += len(code)
    hexs = " ".join(f"0x{b:02x}" for b in code)
    r = subprocess.run([MC, "--disassemble", "-triple=thumbv7-pc-windows-msvc"], input=hexs, capture_output=True,
                       text=True)
    bad[name] = r.stderr.count("invalid instruction encoding")
    pools[name] = len(re.findall(r"udf\.w\s+#", r.stdout))
nb = sum(1 for n in bad if bad[n])
print(f"{len(builtins)} builtins, {total} code bytes; builtins with undecodable words: {nb}; "
      f"undecodable words total: {sum(bad.values())}; pool markers seen: {sum(pools.values())}")
for name, n in bad.most_common(top):
    if n:
        print(f"{n:6}  {name}  (pool markers {pools[name]})")
