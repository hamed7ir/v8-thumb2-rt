"""Checks the NEON mapping rule of DESIGN.md against llvm-mc: for every instruction below, the T32 encoding must equal
the rule applied to the A32 encoding:  A32 1111 001U xxxx... -> T32 111U 1111 xxxx...  and  A32 1111 0100 -> T32 1111 1001.
Also checks that VFP/coprocessor words are identical (cond = AL). Usage: python neon_rule_check.py"""
import re, subprocess, sys, tempfile, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import t32oracle

A32_TRIPLE = "armv7-none-eabi"

NEON = [
    "vand q0, q1, q2", "vbic q0, q1, q2", "vbsl q0, q1, q2", "veor d0, d1, d2", "vorr q0, q1, q2", "vorn q0, q1, q2",
    "vadd.f32 q0, q1, q2", "vsub.f32 q0, q1, q2", "vmul.f32 q0, q1, q2", "vmin.f32 q0, q1, q2", "vmax.f32 q0, q1, q2",
    "vrecps.f32 q0, q1, q2", "vrsqrts.f32 q0, q1, q2", "vceq.f32 q0, q1, q2", "vcge.f32 q0, q1, q2",
    "vcgt.f32 q0, q1, q2", "vqadd.s8 q0, q1, q2", "vqadd.u16 q0, q1, q2", "vqsub.u32 q0, q1, q2",
    "vmin.u8 q0, q1, q2", "vmax.s16 q0, q1, q2", "vcge.u32 q0, q1, q2", "vcgt.s8 q0, q1, q2", "vrhadd.u8 q0, q1, q2",
    "vqrdmulh.s16 q0, q1, q2", "vadd.i32 q0, q1, q2", "vsub.i8 q0, q1, q2", "vmul.i16 q0, q1, q2",
    "vtst.i32 q0, q1, q2", "vceq.i8 q0, q1, q2", "vshl.s32 q0, q1, q2", "vshl.u8 q0, q1, q2",
    "vshl.i32 q0, q1, #3", "vshr.s16 d0, d1, #5", "vshr.u32 q0, q1, #7", "vsli.32 d0, d1, #4", "vsri.16 d0, d1, #2",
    "vsra.u8 d0, d1, #1", "vsra.s32 d0, d1, #9", "vpadd.i8 d0, d1, d2", "vpmin.u16 d0, d1, d2", "vpmax.s32 d0, d1, d2",
    "vpadd.f32 d0, d1, d2", "vext.8 q0, q1, q2, #3", "vmvn q0, q1", "vswp d0, d1", "vswp q0, q1",
    "vabs.f32 q0, q1", "vabs.s8 q0, q1", "vneg.f32 q0, q1", "vneg.s16 q0, q1", "vrecpe.f32 q0, q1",
    "vrsqrte.f32 q0, q1", "vceq.i32 q0, q1, #0", "vclt.s8 q0, q1, #0", "vzip.8 d0, d1", "vzip.16 q0, q1",
    "vuzp.8 d0, d1", "vuzp.32 q0, q1", "vrev16.8 q0, q1", "vrev32.16 q0, q1", "vrev64.32 q0, q1", "vtrn.8 d0, d1",
    "vtrn.32 q0, q1", "vpadal.s8 q0, q1", "vpadal.u16 q0, q1", "vpaddl.u32 q0, q1", "vcnt.8 q0, q1",
    "vcvt.f32.s32 q0, q1", "vcvt.u32.f32 q0, q1", "vdup.8 d0, d1[3]", "vdup.32 q0, d1[1]", "vqmovn.s32 d0, q1",
    "vqmovn.u16 d0, q1", "vqmovun.s32 d0, q1", "vmovl.u8 q0, d1", "vmovl.s16 q0, d1", "vmlal.u32 q0, d1, d2",
    "vmull.s8 q0, d1, d2", "vmull.u16 q0, d1, d2", "vtbl.8 d0, {d1, d2}, d3", "vtbx.8 d0, {d1}, d2",
    "vmov.i8 d0, #0xff", "vmov.i8 q0, #0x7f", "vmov.i32 q0, #0x80", "vld1.8 {d0}, [r1]", "vld1.32 {d0, d1}, [r1]!",
    "vld1.16 {d0[2]}, [r1]", "vld1.32 {d0[]}, [r1]", "vst1.8 {d0}, [r1], r2", "vst1.32 {d0[1]}, [r1]",
]
VFP = ["vadd.f64 d0, d1, d2", "vldr d0, [r1, #-8]", "vstmdb sp!, {d8-d15}", "vmrs APSR_nzcv, fpscr",
       "vmov r0, r1, d2", "vcvt.s32.f64 s0, d1", "vmov.32 d0[1], r0", "vmov.u8 r0, d1[3]", "vdup.32 q0, r1",
       "vsqrt.f64 d0, d1", "vmov.f64 d0, #1.0", "vcmp.f64 d0, #0", "vmov s0, r1"]


def a32(lines):
    with tempfile.NamedTemporaryFile("w", suffix=".s", delete=False, encoding="ascii") as f:
        f.write("\n".join(lines) + "\n")
        path = f.name
    try:
        r = subprocess.run([t32oracle.LLVM_MC, "-triple=" + A32_TRIPLE, "-mattr=+v7,+vfp3,+d32,+neon",
                            "-show-encoding", path], capture_output=True, text=True)
    finally:
        os.unlink(path)
    if "error" in r.stderr:
        print(r.stderr)
    out = []
    for m in re.finditer(r"encoding: \[([^\]]*)\]", r.stdout):
        bs = [int(x, 16) for x in m.group(1).split(",")]
        out.append(bs[0] | bs[1] << 8 | bs[2] << 16 | bs[3] << 24)
    return out


def rule(a):
    top = a >> 24
    if top in (0xF2, 0xF3):
        return 0xEF000000 | ((top & 1) << 28) | (a & 0x00FFFFFF)
    if top == 0xF4:
        return 0xF9000000 | (a & 0x00FFFFFF)
    if top >> 4 == 0xE:  # VFP / coprocessor with cond = AL: identical
        return a
    return None


bad = 0
for group in (NEON, VFP):
    av = a32(group)
    tv = [c for _, _, c, _ in t32oracle.encode(group)]
    assert len(av) == len(group) == len(tv), (len(av), len(group), len(tv))
    for text, a, t in zip(group, av, tv):
        want = rule(a)
        ok = want == t
        bad += not ok
        print(f"{'ok ' if ok else 'BAD'} {text:30} A32 {a:08X} -> rule {want if want is None else format(want, '08X')}"
              f"  T32 {t:08X}")
print(f"{len(NEON) + len(VFP) - bad}/{len(NEON) + len(VFP)} match the rule" + ("" if not bad else "  <-- FAIL"))
sys.exit(1 if bad else 0)
