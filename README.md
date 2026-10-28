# v8-thumb2-rt

v8's arm32 jit ported from arm (a32) code to thumb-2 only, so v8 runs with its jit on windows rt 8.0 / 8.1.

windows rt 8.x only runs thumb-2 code. v8 makes a32 code, and even its builtins are a32, so no chromium for arm32 could
run there, not even jitless. this is the v8 part of the thumb-2 build of
[venetium](https://github.com/hamed7ir/venetium) (chromium 150 for surface rt / surface 2).

## status

- all tiers work: ignition, sparkplug, maglev, turbofan, liftoff, regexp, wasm (stack switching and jspi too)
- all 2,439 builtins are thumb-2 (every one disassembles clean as thumb-2)
- on my surface rt (tegra 3, windows rt 8.1): 698 of the 700 mjsunit runs of my device kit passed, and the 2 failures
  are fixed now (re-run: 21/21). all 25 js perf suites run. the browser does speedometer 3.1 at 1.18 with the sandbox on
- not tested yet: windows rt 8.0 on a device (i checked against 8.0's system dlls offline) and windows 10 15035

## what's here

- `patches/v8-thumb2.patch` - the whole port, for v8 15.0.245.28 (chromium 150.0.7871.226).
  from the v8 root: `git -c core.autocrlf=false apply v8-thumb2.patch` (from chromium src: add `--directory=v8`)
- `port/` - how it was made. i didn't edit v8 by hand: `port/tools/t2run.py` applies the rules in `port/rules`, every
  pattern with an exact count, every encoding checked against llvm-mc. `port/DESIGN.md` has the decisions.
- `tools/hangprobe` - small arm32 tool i used on the tablet: catches a hang or an illegal instruction and writes the
  registers, the code bytes and a dump.

the chromium side (windows 8 api gaps, pe subsystem 6.02) is venetium patch 0064, not here.

## how it works, short

- only 32-bit thumb-2 instructions, so v8's 4-byte instruction slot stays (16-bit ones only in pairs)
- conditional instructions get an `it` block; branch ranges are thumb-2 ones (+-1 mb conditional, +-16 mb)
- every code address and pc has the thumb bit (bit 0) set
- the pc reads +4 instead of +8, and the root register bias is 256 (thumb-2 load/store offsets are smaller)

## license

the patch changes v8, which is bsd 3-clause (v8's own license). my rules and tools here are gplv3, like venetium.
