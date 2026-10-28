"""NEON + VFP encoders -> T32 by constant rewrites (no new encoders needed).

Rules proven by tools\\neon_rule_check.py (104/104 instructions, llvm-mc A32 vs T32):
  Advanced SIMD data processing  A32 1111 001U ...  ->  T32 111U 1111 ...   (base constant + U bit 24 -> 28)
  Advanced SIMD load/store       A32 1111 0100 ...  ->  T32 1111 1001 ...
  VFP / coprocessor              same word with cond = AL; a predicated one becomes it(cond) + the AL word.
Also: the IT unit (it()), data words that went through emit(), kPcLoadDelta, branch range.
"""
from t2run import Lit, Re, Insert

ASM = "v8/src/codegen/arm/assembler-arm.cc"
ASM_H = "v8/src/codegen/arm/assembler-arm.h"
CONST_H = "v8/src/codegen/arm/constants-arm.h"
NEON_FN = r"^(?:EncodeNeon\w+|Assembler::v\w+|WriteVmovIntImmEncoding) "
U_BIT_FN = r"^(?:EncodeNeon(?:DataTypeBinOp|ShiftRegisterOp|PairwiseOp)|Assembler::(?:vmovl|vmlal|vmull)) "
OP_ENC_FN = r"^EncodeNeon(?:BinaryBitwiseOp|BinOp|DataTypeBinOp|ShiftOp) "
VFP_FN = r"^Assembler::(?:v\w+|cdp|mcr|mrc|ldc) "

IT_DECL = """  // Thumb-2: predicate the next instruction on cond. For cond != al this emits
  // one 4-byte unit, a 16-bit NOP then IT<cond>, so the predicated instruction
  // that follows stays 4-byte aligned; for al it emits nothing.
  void it(Condition cond);"""

IT_DEF = """// Thumb-2 predication: [nop16][it<cond>] as one 4-byte unit (canonical
// 0xBF00'BFc8, c = cond), then the predicated instruction.
void Assembler::it(Condition cond) {
  if (cond == al) return;
  // Keep the constant pool out from between the IT and its instruction.
  BlockConstPoolFor(2);
  emit(0xBF00BF08 | (static_cast<uint32_t>(cond) >> 28) << 4);
}"""

RULES = [
    # ---- NEON data processing: base constants (F2 0/8, F3 0/8 -> EF 0/8, FF 0/8)
    Lit("neon-base-f2-0", ASM, "0x1E4U * B23", "0x1DEU * B23", expect=5, within=NEON_FN,
        oracle=[("vand q0, q1, q2", 0xEF020154), ("vadd.f32 q0, q1, q2", 0xEF020D44)]),
    Lit("neon-base-f2-8", ASM, "0x1E5U * B23", "0x1DFU * B23", expect=2, within=NEON_FN,
        oracle=[("vext.8 q0, q1, q2, #3", 0xEFB20344), ("vshr.s16 d0, d1, #5", 0xEF9B0011)]),
    Lit("neon-base-f3-0", ASM, "0x1E6U * B23", "0x1FEU * B23", expect=1, within=NEON_FN,
        oracle=[("vpadd.f32 d0, d1, d2", 0xFF010D02)]),
    Lit("neon-base-f3-8", ASM, "0x1E7U * B23", "0x1FFU * B23", expect=5, within=NEON_FN,
        oracle=[("vmvn q0, q1", 0xFFB005C2), ("vtbl.8 d0, {d1, d2}, d3", 0xFFB10903)]),
    # ---- NEON U bit (A32 bit 24 -> T32 bit 28)
    Re("neon-u-bit", ASM, r"\b([uU]) \* B24\b", r"\1 * B28", expect=6, within=U_BIT_FN, done=r"\b[uU] \* B28\b",
       oracle=[("vqadd.u16 q0, q1, q2", 0xFF120054), ("vmull.u16 q0, d1, d2", 0xFF910C02)]),
    Re("neon-op-bit24", ASM, r"(?<!\* )\bB24\b", "B28", expect=14, within=OP_ENC_FN,
       done=r"op_encoding (?:\|)?= B28\b",
       oracle=[("vbsl q0, q1, q2", 0xFF120154), ("vmul.f32 q0, q1, q2", 0xFF020D54),
               ("vsra.u8 d0, d1, #1", 0xFF8F0111)]),
    # vmovl / vmlal / vmull: 0xF2 base with U variable (U already moved by neon-u-bit)
    Lit("neon-base-f2-u", ASM, "0xFU * B28 | B25 |", "0xEU * B28 | 0xF * B24 |", expect=3, within=NEON_FN,
        oracle=[("vmovl.u8 q0, d1", 0xFF880A11), ("vmull.s8 q0, d1, d2", 0xEF810C02)]),
    # vmov.i8/i32 (modified immediate): base F2 8 -> EF 8, the 'a' (i) bit 24 -> 28
    Lit("neon-vmov-imm-base", ASM, "kSpecialCondition | 0x05 * B23", "0x1DFU * B23", expect=2, within=NEON_FN,
        oracle=[("vmov.i8 d0, #0xff", 0xFF870E1F), ("vmov.i8 q0, #0x7f", 0xEF870E5F)]),
    Lit("neon-vmov-imm-a", ASM, "((imm & 0x80) << (24 - 7))", "((imm & 0x80) << (28 - 7))", expect=1,
        within=NEON_FN),
    # ---- NEON load/store: F4 -> F9
    Lit("neon-ldst", ASM, "0xFU * B28 | 4 * B24", "0xFU * B28 | 9 * B24", expect=4, within=NEON_FN,
        oracle=[("vld1.8 {d0}, [r1]", 0xF921070F), ("vld1.32 {d0[]}, [r1]", 0xF9A10C8F)]),
    Lit("neon-ldst-lane", ASM, "0xFU * B28 | 9 * B23", "0xFU * B28 | 0x13 * B23", expect=1, within=NEON_FN,
        oracle=[("vst1.32 {d0[1]}, [r1]", 0xF981088F)]),
    # ---- IT unit
    Insert("it-decl", ASM_H, r"^  void nop\(int type = 0\);", IT_DECL, where="before"),
    Insert("it-def", ASM, r"^// Pseudo instructions\.", IT_DEF, where="before", code_only=False,
           oracle=[("nop", 0xBF00), ("it eq ; nopeq", 0xBF08), ("it ne ; nopne", 0xBF18), ("it hi ; nophi", 0xBF88),
                   ("it lt ; noplt", 0xBFB8), ("it eq ; vaddeq.f64 d0, d1, d2", 0xBF08)]),
    # ---- VFP / coprocessor: predicated word -> it(cond) + AL word
    Re("vfp-pred", ASM, r"^([ \t]*)emit\(cond \|", r"\1it(cond);\n\1emit(al |", expect=49, within=VFP_FN,
       done=r"it\(cond\);\s*emit\(al \|",
       oracle=[("vadd.f64 d0, d1, d2", 0xEE310B02), ("vldr d0, [r1, #-8]", 0xED110B02),
               ("vmrs APSR_nzcv, fpscr", 0xEEF1FA10)]),
    Re("vfp-vcvt-pred", ASM, r"^([ \t]*)emit\(EncodeVCVT\(([^;]*?),\s*cond\)\);",
       r"\1it(cond);\n\1emit(EncodeVCVT(\2, al));", expect=10, done=r"emit\(EncodeVCVT\([^;]*?, al\)\);",
       oracle=[("vcvt.s32.f64 s0, d1", 0xEEBD0BC1)]),
    # ---- data words that went through emit() (never swapped)
    Lit("pool-entry-data", ASM, "emit(entry.value());", "dd(entry.value());", expect=1,
        desc="constant pool entries are data"),
    Lit("patching-emit-data", ASM, "void PatchingAssembler::Emit(Address addr) { emit(static_cast<Instr>(addr)); }",
        "void PatchingAssembler::Emit(Address addr) { dd(static_cast<uint32_t>(addr)); }", expect=1,
        desc="PatchingAssembler::Emit writes an address (data)"),
    # ---- constants
    Lit("pc-load-delta-4", CONST_H, "static constexpr int kPcLoadDelta = 8;", "static constexpr int kPcLoadDelta = 4;",
        expect=1, desc="T32 reads pc as instr + 4 (literal base Align(pc, 4))"),
    Lit("pcrel-range", CONST_H, "constexpr size_t kMaxPCRelativeCodeRangeInMB = 32;",
        "constexpr size_t kMaxPCRelativeCodeRangeInMB = 16;", expect=1, desc="B.W / BL reach +-16 MiB"),
]
