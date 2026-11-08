"""Runtime JIT sites where T32 needs a register that A32 did not, while the only scratch register (ip) is taken.

2026-11-08 device crash (RT 8.1, Telegram Web A/K, Discord: chrome.dll+0x994a, a CHECK trap in Assembler::AddrMode1,
assembler-arm.cc:1141): T32 has no register-shifted-register operand for orr/add/... (only for mov), so AddrMode1
shifts into rd first - or, when rd == rn, into a scratch register. The register versions of LslPair / LsrPair /
AsrPair hold ip as `scratch` (= 32 - shift) and then emit orr(dst, dst, Operand(src, LSL/LSR, scratch)): rd == rn,
the second Acquire() finds the list empty and RegListBase::first() CHECK-fails. TurboFan (kArm{Lsl,Lsr,Asr}Pair with a
register shift) and Liftoff (emit_i64_shl/shr/sar, every wasm i64 shift by a variable amount) reach them at runtime;
the 10-27 mksnapshot census (rule 990) only saw builtins, which never call them.

pair-shift-*: shift into the scratch register itself (it is dead after the orr), then orr the plain register. Same
result for every shift, shift 0 included (scratch = 32 -> a register shift by 32 gives 0 in A32 and T32 alike); the
sources stay intact (the existing DCHECKs keep dst_high/dst_low off src_low/src_high/shift).

liftoff-store-tagged-barrier: LiftoffAssembler::StoreTaggedPointer takes ip for `offset + imm` when the offset
register is still live, and kept it to the end of the function: the write barrier's CheckPageFlag then took a second
scratch (CHECK), as do the C call of --verify-write-barriers. Same code in the A32 tree (upstream latent: wasm-gc
array.set of a reference with a live index register). ip is now held only for the store; the slot offset is formed
again (offset_reg is live, untouched) just before CallRecordWriteStubSaveRegisters, which reads it in
MoveObjectAndSlot before anything else touches ip (stm, add, mov, then Call(Address), which writes ip by name).
"""
from t2run import Lit

MA = "v8/src/codegen/arm/macro-assembler-arm.cc"
LO = "v8/src/wasm/baseline/arm/liftoff-assembler-arm-inl.h"

RULES = [
    Lit("pair-shift-lsl-no-second-scratch", MA, """  lsl(dst_high, src_high, Operand(shift));
  orr(dst_high, dst_high, Operand(src_low, LSR, scratch));
  lsl(dst_low, src_low, Operand(shift));""", """  lsl(dst_high, src_high, Operand(shift));
  // T32 has no register-shifted-register orr; its emulation would need a
  // second scratch register while this one is held: shift into it first.
  lsr(scratch, src_low, Operand(scratch));
  orr(dst_high, dst_high, Operand(scratch));
  lsl(dst_low, src_low, Operand(shift));""", expect=1),
    Lit("pair-shift-lsr-asr-no-second-scratch", MA, """  orr(dst_low, dst_low, Operand(src_high, LSL, scratch));""",
        """  // T32 has no register-shifted-register orr; its emulation would need a
  // second scratch register while this one is held: shift into it first.
  lsl(scratch, src_high, Operand(scratch));
  orr(dst_low, dst_low, Operand(scratch));""", expect=2),
    Lit("liftoff-store-tagged-barrier-store", LO, """  static_assert(kTaggedSize == kInt32Size);
  UseScratchRegisterScope temps{this};
  Register actual_offset_reg = offset_reg;
  if (offset_reg != no_reg && offset_imm != 0) {
    if (cache_state()->is_used(LiftoffRegister(offset_reg))) {
      // The code below only needs a scratch register if the {MemOperand} given
      // to {str} has an offset outside the uint12 range. After doing the
      // addition below we will not pass an immediate offset to {str} though, so
      // we can use the scratch register here.
      actual_offset_reg = temps.Acquire();
    }
    add(actual_offset_reg, offset_reg, Operand(offset_imm));
  }
  MemOperand dst_op = actual_offset_reg == no_reg
                          ? MemOperand(dst_addr, offset_imm)
                          : MemOperand(dst_addr, actual_offset_reg);

  if (trapping_store_pc) *trapping_store_pc = pc_offset();

  str(src, dst_op);""", """  static_assert(kTaggedSize == kInt32Size);
  Register actual_offset_reg = offset_reg;
  // T32: ip is held for the store only. The write barrier below needs it free
  // (CheckPageFlag and C calls take a scratch register of their own), so the
  // slot offset is formed again there when it lived in ip.
  bool offset_in_scratch = false;
  {
    UseScratchRegisterScope temps{this};
    if (offset_reg != no_reg && offset_imm != 0) {
      if (cache_state()->is_used(LiftoffRegister(offset_reg))) {
        // The code below only needs a scratch register if the {MemOperand}
        // given to {str} has an offset outside the uint12 range. After doing
        // the addition below we will not pass an immediate offset to {str}
        // though, so we can use the scratch register here.
        actual_offset_reg = temps.Acquire();
        offset_in_scratch = true;
      }
      add(actual_offset_reg, offset_reg, Operand(offset_imm));
    }
    MemOperand dst_op = actual_offset_reg == no_reg
                            ? MemOperand(dst_addr, offset_imm)
                            : MemOperand(dst_addr, actual_offset_reg);

    if (trapping_store_pc) *trapping_store_pc = pc_offset();

    str(src, dst_op);
  }""", expect=1, code_only=False),
    Lit("liftoff-store-tagged-barrier-call", LO, """  CallRecordWriteStubSaveRegisters(
      dst_addr,
      actual_offset_reg == no_reg ? Operand(offset_imm)
                                  : Operand(actual_offset_reg),
      SaveFPRegsMode::kSave, StubCallMode::kCallWasmRuntimeStub);
  bind(&exit);""", """  if (offset_in_scratch) {
    UseScratchRegisterScope temps{this};
    Register slot_offset = temps.Acquire();
    add(slot_offset, offset_reg, Operand(offset_imm));
    CallRecordWriteStubSaveRegisters(dst_addr, Operand(slot_offset),
                                     SaveFPRegsMode::kSave,
                                     StubCallMode::kCallWasmRuntimeStub);
  } else {
    CallRecordWriteStubSaveRegisters(
        dst_addr,
        actual_offset_reg == no_reg ? Operand(offset_imm)
                                    : Operand(actual_offset_reg),
        SaveFPRegsMode::kSave, StubCallMode::kCallWasmRuntimeStub);
  }
  bind(&exit);""", expect=1, within=r"LiftoffAssembler::StoreTaggedPointer\b"),
]
