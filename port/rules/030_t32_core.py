"""T32 assembler core: integer encoders, branches, label links, predicates and patchers.

All bit layouts live in the new src/codegen/arm/t32-encodings-arm.h (tested: tests\\t32enc, 12,572 encodings vs
llvm-mc + decoder round trips). The Assembler bodies in bodies\\assembler-arm-t32.cc only add T32's legality rules
(sp/pc positions, scratch registers, IT placement, 16-bit units) and keep V8's API unchanged.
"""
from t2run import FuncSet, Insert, Lit, NewFile, Re

ASM = "v8/src/codegen/arm/assembler-arm.cc"
ASM_H = "v8/src/codegen/arm/assembler-arm.h"
ASM_INL = "v8/src/codegen/arm/assembler-arm-inl.h"
CONST_H = "v8/src/codegen/arm/constants-arm.h"

HELPERS = """// The condition's number (0 = eq .. 14 = al), as the t32:: encoders take it.
uint32_t CondNumber(Condition cond) {
  return static_cast<uint32_t>(cond) >> 28;
}

// T32 op field of an A32 data-processing opcode (the Opcode API value).
uint32_t T32Op(uint32_t opcode) {
  switch (opcode) {
    case AND:
    case TST:
      return t32::kAnd;
    case BIC:
      return t32::kBic;
    case ORR:
    case MOV:
      return t32::kOrr;
    case MVN:
      return t32::kOrn;
    case EOR:
    case TEQ:
      return t32::kEor;
    case ADD:
    case CMN:
      return t32::kAdd;
    case ADC:
      return t32::kAdc;
    case SBC:
      return t32::kSbc;
    case SUB:
    case CMP:
      return t32::kSub;
    case RSB:
      return t32::kRsb;
  }
  UNREACHABLE();  // rsc has no T32 form
}

// T32 op and placed modified immediate for <opcode> #imm32. When imm32 is
// not a modified immediate the complementary operation is tried (mov/mvn,
// and/bic, orr/orn with ~imm32, add/sub and cmp/cmn with -imm32).
bool T32ImmediateOp(uint32_t opcode, uint32_t imm32, uint32_t* op,
                    uint32_t* placed) {
  *op = T32Op(opcode);
  if (t32::ModifiedImmediate(imm32, placed)) return true;
  switch (opcode) {
    case MOV:
    case ORR:
      *op = t32::kOrn;
      return t32::ModifiedImmediate(~imm32, placed);
    case MVN:
      *op = t32::kOrr;
      return t32::ModifiedImmediate(~imm32, placed);
    case AND:
      *op = t32::kBic;
      return t32::ModifiedImmediate(~imm32, placed);
    case BIC:
      *op = t32::kAnd;
      return t32::ModifiedImmediate(~imm32, placed);
    case ADD:
    case CMN:
      *op = t32::kSub;
      return t32::ModifiedImmediate(0u - imm32, placed);
    case SUB:
    case CMP:
      *op = t32::kAdd;
      return t32::ModifiedImmediate(0u - imm32, placed);
  }
  return false;
}"""

LOAD_STORE = """// Single T32 loads/stores (ldr, str, ldrb, strb, ldrh, strh, ldrsb, ldrsh).
// t3 selects the instruction (t32::kLdr ...). Forms: imm12 upwards, imm8
// downwards or with writeback, register with lsl #0-3. Everything else goes
// through a scratch register or an add/sub of the base.
void Assembler::LoadStoreT32(uint32_t t3, Register rt, const MemOperand& x,
                             Condition cond) {
  Register rn = x.rn_;
  DCHECK(rn.is_valid() && rn != pc);
  DCHECK(t3 == t32::kLdr || rt != pc);
  bool is_load = t32::IsLoad(t3);
  int am = x.am_;
  bool pre = (am & P) != 0;
  bool writeback = !pre || (am & W) != 0;
  bool up = (am & U) != 0;
  UseScratchRegisterScope temps(this);
  if (!x.rm_.is_valid()) {
    int32_t offset = up ? x.offset_ : -x.offset_;
    if (!writeback && offset >= 0 && offset <= 4095) {
      it(cond);
      emit(t32::LoadStoreImm12(t3, rn.code(), rt.code(), offset));
      return;
    }
    if (offset >= -255 && offset <= 255 && (writeback || offset < 0)) {
      it(cond);
      emit(t32::LoadStoreImm8(t3, rn.code(), rt.code(), pre, offset >= 0,
                              writeback, offset >= 0 ? offset : -offset));
      return;
    }
    // Immediate offset cannot be encoded. A load into its own base only moves
    // the base. A store with no scratch register left (gap moves hold ip)
    // moves its base onto the address and back.
    if (!writeback && is_load && rt == rn && rt != sp) {
      add(rn, rn, Operand(offset), LeaveCC, cond);
      LoadStoreT32(t3, rt, MemOperand(rn), cond);
      return;
    }
    if (!writeback && !is_load && rt != rn && !temps.CanAcquire()) {
      add(rn, rn, Operand(offset), LeaveCC, cond);
      LoadStoreT32(t3, rt, MemOperand(rn), cond);
      sub(rn, rn, Operand(offset), LeaveCC, cond);
      return;
    }
    // Otherwise load the offset into a scratch register (the target of a
    // load without writeback, if free) and use it as a register offset.
    Register scratch =
        (is_load && !writeback && rt != rn && rt != pc && rt != sp)
            ? rt
            : temps.Acquire();
    mov(scratch, Operand(x.offset_), LeaveCC, cond);
    LoadStoreT32(t3, rt, MemOperand(rn, scratch, static_cast<AddrMode>(am)),
                 cond);
    return;
  }
  Register rm = x.rm_;
  DCHECK(rm != pc && rm != sp);
  if (!writeback && up && x.shift_op_ == LSL && x.shift_imm_ <= 3) {
    it(cond);
    emit(t32::LoadStoreRegister(t3, rn.code(), rt.code(), rm.code(),
                                x.shift_imm_));
    return;
  }
  // No negative, writeback or shifted (other than lsl #0-3) register offsets
  // in T32: form the address, or the updated base, with an add/sub.
  Operand index(rm, x.shift_op_, x.shift_imm_);
  if (!writeback) {
    Register address =
        (is_load && rt != rn && rt != rm && rt != pc && rt != sp)
            ? rt
            : temps.Acquire();
    if (up) {
      add(address, rn, index, LeaveCC, cond);
    } else {
      sub(address, rn, index, LeaveCC, cond);
    }
    LoadStoreT32(t3, rt, MemOperand(address), cond);
  } else if (pre) {
    if (up) {
      add(rn, rn, index, LeaveCC, cond);
    } else {
      sub(rn, rn, index, LeaveCC, cond);
    }
    LoadStoreT32(t3, rt, MemOperand(rn), cond);
  } else {
    LoadStoreT32(t3, rt, MemOperand(rn), cond);
    if (up) {
      add(rn, rn, index, LeaveCC, cond);
    } else {
      sub(rn, rn, index, LeaveCC, cond);
    }
  }
}"""

DELETE_ASM = [
    "Condition Assembler::GetCondition(Instr instr)",
    "bool Assembler::IsVldrDRegisterImmediate(Instr instr)",
    "int Assembler::GetVldrDRegisterImmediateOffset(Instr instr)",
    "Instr Assembler::SetVldrDRegisterImmediateOffset(Instr instr, int offset)",
    "bool Assembler::IsStrRegisterImmediate(Instr instr)",
    "Instr Assembler::SetStrRegisterImmediateOffset(Instr instr, int offset)",
    "bool Assembler::IsAddRegisterImmediate(Instr instr)",
    "Instr Assembler::SetAddRegisterImmediateOffset(Instr instr, int offset)",
    "Register Assembler::GetRd(Instr instr)",
    "Register Assembler::GetRn(Instr instr)",
    "Register Assembler::GetRm(Instr instr)",
    "bool Assembler::IsPush(Instr instr)",
    "bool Assembler::IsPop(Instr instr)",
    "bool Assembler::IsStrRegFpOffset(Instr instr)",
    "bool Assembler::IsLdrRegFpOffset(Instr instr)",
    "bool Assembler::IsStrRegFpNegOffset(Instr instr)",
    "bool Assembler::IsLdrRegFpNegOffset(Instr instr)",
    "bool Assembler::IsBlxReg(Instr instr)",
    "bool Assembler::IsBlxIp(Instr instr)",
    "bool Assembler::IsTstImmediate(Instr instr)",
    "bool Assembler::IsCmpRegister(Instr instr)",
    "bool Assembler::IsCmpImmediate(Instr instr)",
    "Register Assembler::GetCmpImmediateRegister(Instr instr)",
    "int Assembler::GetCmpImmediateRawImmediate(Instr instr)",
    "bool FitsShifter(uint32_t imm32, uint32_t* rotate_imm, uint32_t* immed_8, Instr* instr)",
    "bool Assembler::AddrMode1TryEncodeOperand(Instr* instr, const Operand& x)",
    "void Assembler::blx(int branch_offset)",
    "void Assembler::blx(Label* L)",
    "void Assembler::rsc(Register dst, Register src1, const Operand& src2, SBit s, Condition cond)",
    "void Assembler::cmp_raw_immediate(Register src, int raw_immediate, Condition cond)",
    "Instr Assembler::GetMovTPattern()",
    "Instr Assembler::GetMovWPattern()",
    "int Assembler::DecodeShiftImm(Instr instr)",
    "Instr Assembler::PatchShiftImm(Instr instr, int immed)",
    "bool Assembler::IsMovImmed(Instr instr)",
    "bool Assembler::IsOrrImmed(Instr instr)",
]

# Declarations of the deleted members (one line each in assembler-arm.h).
DECL_LINES = [
    "static Condition GetCondition(Instr instr);",
    "static bool IsVldrDRegisterImmediate(Instr instr);",
    "static int GetVldrDRegisterImmediateOffset(Instr instr);",
    "static Instr SetVldrDRegisterImmediateOffset(Instr instr, int offset);",
    "static bool IsStrRegisterImmediate(Instr instr);",
    "static Instr SetStrRegisterImmediateOffset(Instr instr, int offset);",
    "static bool IsAddRegisterImmediate(Instr instr);",
    "static Instr SetAddRegisterImmediateOffset(Instr instr, int offset);",
    "static Register GetRd(Instr instr);",
    "static Register GetRn(Instr instr);",
    "static Register GetRm(Instr instr);",
    "static bool IsPush(Instr instr);",
    "static bool IsPop(Instr instr);",
    "static bool IsStrRegFpOffset(Instr instr);",
    "static bool IsLdrRegFpOffset(Instr instr);",
    "static bool IsStrRegFpNegOffset(Instr instr);",
    "static bool IsLdrRegFpNegOffset(Instr instr);",
    "static bool IsBlxReg(Instr instr);",
    "static bool IsBlxIp(Instr instr);",
    "static bool IsTstImmediate(Instr instr);",
    "static bool IsCmpRegister(Instr instr);",
    "static bool IsCmpImmediate(Instr instr);",
    "static Register GetCmpImmediateRegister(Instr instr);",
    "static int GetCmpImmediateRawImmediate(Instr instr);",
    "static bool IsMovImmed(Instr instr);",
    "static bool IsOrrImmed(Instr instr);",
    "static Instr GetMovTPattern();",
    "static Instr GetMovWPattern();",
    "static int DecodeShiftImm(Instr instr);",
    "static Instr PatchShiftImm(Instr instr, int immed);",
]

INSTRUCTION_BRANCH = """  // Fields used in Branch instructions
  inline int LinkValue() const { return Bit(24); }
  inline int SImmed24Value() const {
    return signed_bitextract_32(23, 0, InstructionBits());
  }

  // T32 b.w, b<c>.w and bl (see t32-encodings-arm.h).
  bool IsBranch() {
    return t32::IsBranch(static_cast<uint32_t>(InstructionBits()));
  }

  int GetBranchOffset() {
    DCHECK(IsBranch());
    return t32::BranchOffset(static_cast<uint32_t>(InstructionBits()));
  }

  inline void SetBranchOffset(int32_t branch_offset,
                              WritableJitAllocation* jit_allocation) {
    DCHECK(IsBranch());
    SetInstructionBits(static_cast<Instr>(t32::SetBranchOffset(
                           static_cast<uint32_t>(InstructionBits()),
                           branch_offset)),
                       jit_allocation);
  }"""

INSTRUCTION_BRANCH_A32 = """  // Fields used in Branch instructions
  inline int LinkValue() const { return Bit(24); }
  inline int SImmed24Value() const {
    return signed_bitextract_32(23, 0, InstructionBits());
  }

  bool IsBranch() { return Bit(27) == 1 && Bit(25) == 1; }

  int GetBranchOffset() {
    DCHECK(IsBranch());
    return SImmed24Value() * kInstrSize;
  }

  inline void SetBranchOffset(int32_t branch_offset,
                              WritableJitAllocation* jit_allocation) {
    DCHECK(IsBranch());
    DCHECK_EQ(branch_offset % kInstrSize, 0);
    int32_t new_imm24 = branch_offset / kInstrSize;
    CHECK(is_int24(new_imm24));
    SetInstructionBits(
        (InstructionBits() & ~(kImm24Mask)) | (new_imm24 & kImm24Mask),
        jit_allocation);
  }"""

RULES = [
    NewFile("t32-encodings-header", "v8/src/codegen/arm/t32-encodings-arm.h", "bodies/t32-encodings-arm.h",
            desc="all T32 bit layouts (tested by tests\\t32enc)"),
    Insert("t32-include", CONST_H, r'^#include "src/common/code-memory-access\.h"',
           '#include "src/codegen/arm/t32-encodings-arm.h"', where="before", blank=False, code_only=False),
    # Constant pool marker: udf.w #length.
    Lit("pool-marker", CONST_H, """// Constant pool marker.
// Use UDF, the permanently undefined instruction.
const int kConstantPoolMarkerMask = 0xfff000f0;
const int kConstantPoolMarker = 0xe7f000f0;
const int kConstantPoolLengthMaxMask = 0xffff;
inline int EncodeConstantPoolLength(int length) {
  DCHECK((length & kConstantPoolLengthMaxMask) == length);
  return ((length & 0xfff0) << 4) | (length & 0xf);
}
inline int DecodeConstantPoolLength(int instr) {
  DCHECK_EQ(instr & kConstantPoolMarkerMask, kConstantPoolMarker);
  return ((instr >> 4) & 0xfff0) | (instr & 0xf);
}""", """// Constant pool marker.
// Use udf.w #length, the permanently undefined T32 instruction.
const int kConstantPoolMarkerMask = 0xfff0f000;
const int kConstantPoolMarker = 0xf7f0a000;
const int kConstantPoolLengthMaxMask = 0xffff;
inline int EncodeConstantPoolLength(int length) {
  DCHECK((length & kConstantPoolLengthMaxMask) == length);
  return ((length & 0xf000) << 4) | (length & 0xfff);
}
inline int DecodeConstantPoolLength(int instr) {
  DCHECK_EQ(instr & kConstantPoolMarkerMask, kConstantPoolMarker);
  return ((instr >> 4) & 0xf000) | (instr & 0xfff);
}""", expect=1, code_only=False, oracle=[("udf.w #0x1234", 0xF7F1A234)]),
    Lit("instruction-movw-value", CONST_H, """  inline int ImmedMovwMovtValue() const {
    return Immed4Value() << 12 | Offset12Value();
  }""", """  inline int ImmedMovwMovtValue() const {
    return static_cast<int>(
        t32::DecodeImm16(static_cast<uint32_t>(InstructionBits())));
  }""", expect=1),
    Lit("instruction-branch", CONST_H, INSTRUCTION_BRANCH_A32, INSTRUCTION_BRANCH, expect=1, code_only=False),
    # Thumb-2 implies ARMv7 (Windows RT also requires VFPv3-D32 and NEON).
    Re("armv7-baseline", ASM, r"^  dcache_line_size_ = 64;\n(?!  // Thumb-2)",
       "  dcache_line_size_ = 64;\n"
       "  // Thumb-2 V8 (Windows RT): ARMv7 with VFPv3-D32 and NEON is the baseline,\n"
       "  // also for the snapshot; there are no ARMv6 encodings to fall back to.\n"
       "  supported_ |= kArmv7;\n", expect=1, code_only=False, done=r"supported_ \|= kArmv7;"),
    # Dead A32 instruction-pattern constants (their only users are deleted below).
    Re("a32-pattern-constants", ASM,
       r"^// str\(r, MemOperand\(sp, 4, NegPreIndex\), al\) instruction \(aka push\(r\)\)\n.*?"
       r"^const Instr kLdrStrInstrTypeMask = 0xFFFF0000;\n\n",
       "", expect=1, code_only=False, flags=__import__("re").M | __import__("re").S,
       done=r"^Assembler::Assembler\(const AssemblerOptions& options,"),
    # The helpers take the place of FitsShifter's comment (FitsShifter itself is deleted by asm-core).
    Re("t32-helpers", ASM,
       r"^// Low-level code emission routines depending on the addressing mode\.\n"
       r"// If this returns true then you have to use the rotate_imm and immed_8\n"
       r"// that it returns, because it may have already changed the instruction\n"
       r"// to match them!\n",
       HELPERS.replace("\\", "\\\\") + "\n\n", expect=1, code_only=False,
       done=r"uint32_t CondNumber\(Condition cond\)"),
    Insert("load-store-t32", ASM, r"^void Assembler::AddrMode2\(Instr instr, Register rd, const MemOperand& x\)",
           LOAD_STORE, where="before"),
    Re("addrmode1-try-decl", ASM_H,
       r"^  // Attempt to encode operand \|x\| for instruction \|instr\|.*?"
       r"bool AddrMode1TryEncodeOperand\(Instr\* instr, const Operand& x\);\n\n",
       "", expect=1, code_only=False, flags=__import__("re").M | __import__("re").S,
       desc="drop AddrMode1TryEncodeOperand and its comment"),
    Insert("load-store-t32-decl", ASM_H,
           r"^  void AddrMode2\(Instr instr, Register rd, const MemOperand& x\);",
           "  // Single T32 loads and stores (ldr/str/ldrb/strb/ldrh/strh/ldrsb/ldrsh).\n"
           "  void LoadStoreT32(uint32_t t3, Register rt, const MemOperand& x,\n"
           "                    Condition cond);", where="before", blank=False),
    FuncSet("asm-core", ASM, "bodies/assembler-arm-t32.cc", delete=DELETE_ASM,
            desc="T32 encoders/predicates/patchers; dead A32 predicates deleted"),
    FuncSet("inl-patchers", ASM_INL, "bodies/assembler-arm-inl-t32.h",
            desc="target_address_at / set_target_address_at: T32 movw/movt + branches, ARMv6 path gone"),
] + [
    Re(f"decl-{i:02d}", ASM_H, r"^[ \t]*" + __import__("re").escape(d) + r"[ \t]*\n", "", expect=1,
       done=None, desc=f"drop declaration: {d}")
    for i, d in enumerate(DECL_LINES)
] + [
    Re("decl-blx-int", ASM_H, r"^  void blx\(int branch_offset\);[^\n]*\n", "", expect=1),
    Re("decl-blx-label", ASM_H, r"^  void blx\(Label\* L\);[^\n]*\n", "", expect=1),
    Re("decl-rsc", ASM_H, r"^  void rsc\(Register dst, Register src1, const Operand& src2, SBit s = LeaveCC,\n"
       r"[^\n]*\n\n", "", expect=1),
    Re("decl-cmp-raw", ASM_H,
       r"^  void cmp_raw_immediate\(Register src1, int raw_immediate, Condition cond = al\);\n\n", "", expect=1),
]
