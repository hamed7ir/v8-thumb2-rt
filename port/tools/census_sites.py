"""Summarise a snap_census cdb log: for every T2DIAG-HIT stack, the first frame outside Acquire/AddrMode*/LoadStore
(the caller that asked for the instruction) and the assembler frame below it; count distinct sites."""
import collections, re, sys

sys.stdout.reconfigure(errors="replace")
text = open(sys.argv[1], encoding="utf-8", errors="replace").read()
blocks = text.split("T2DIAG-HIT")[1:]
FRAME = re.compile(r"^\s*[0-9a-f]{2} .*?mksnapshot_exe!(\S+?)\+0x[0-9a-f]+ \[(?:o:\\)?([^\]]+?) @ (\d+)\]", re.M)
LOW = ("T2DiagScratchExhausted", "UseScratchRegisterScope", "RegListBase", "Assembler::AddrMode", "Assembler::LoadStore",
       "Assembler::Move32BitImmediate")
sites = collections.Counter()
for b in blocks:
    frames = FRAME.findall(b.split("T2DIAG-STOP")[0])
    asm = next((f"{n} {p}:{l}" for n, p, l in frames if n.startswith("v8::internal::Assembler::") and
                not n.startswith(("v8::internal::Assembler::AddrMode", "v8::internal::Assembler::LoadStore"))), "?")
    low = next((f"{n.split('::')[-1]}:{l}" for n, p, l in frames if any(k in n for k in LOW[3:])), "?")
    caller = next((f"{n} {p}:{l}" for n, p, l in frames if not any(k in n for k in LOW) and
                   not n.startswith("v8::internal::Assembler::")), "?")
    sites[(caller, asm, low)] += 1
print(f"{len(blocks)} hits, {len(sites)} distinct sites")
for (caller, asm, low), n in sites.most_common():
    print(f"{n:5}  {caller}\n         via {asm}  [{low}]")
