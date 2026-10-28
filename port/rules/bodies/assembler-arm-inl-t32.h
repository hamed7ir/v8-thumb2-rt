// T32 bodies for v8/src/codegen/arm/assembler-arm-inl.h (rules\030_t32_core.py, FuncSet "inl-patchers").

Address Assembler::target_address_at(Address pc, Address constant_pool) {
  if (is_constant_pool_load(pc)) {
    // This is a constant pool lookup. Return the value in the constant pool.
    return Memory<Address>(constant_pool_entry_address(pc, constant_pool));
  } else if (IsMovW(instr_at(pc))) {
    // This is an movw / movt immediate load. Return the immediate.
    DCHECK(IsMovW(instr_at(pc)) && IsMovT(instr_at(pc + kInstrSize)));
    Instruction* movw_instr = Instruction::At(pc);
    Instruction* movt_instr = Instruction::At(pc + kInstrSize);
    return static_cast<Address>((movt_instr->ImmedMovwMovtValue() << 16) |
                                movw_instr->ImmedMovwMovtValue());
  } else {
    Instruction* branch = Instruction::At(pc);
    int32_t delta = branch->GetBranchOffset();
    return pc + delta + Instruction::kPcLoadDelta;
  }
}

void Assembler::set_target_address_at(Address pc, Address constant_pool,
                                      Address target,
                                      WritableJitAllocation* jit_allocation,
                                      ICacheFlushMode icache_flush_mode) {
  if (is_constant_pool_load(pc)) {
    // This is a constant pool lookup. Update the entry in the constant pool.
    if (jit_allocation) {
      jit_allocation->WriteValue<Address>(
          constant_pool_entry_address(pc, constant_pool), target);
    } else {
      Memory<Address>(constant_pool_entry_address(pc, constant_pool)) = target;
    }
    // Intuitively, we would think it is necessary to always flush the
    // instruction cache after patching a target address in the code as follows:
    //   FlushInstructionCache(pc, sizeof(target));
    // However, on ARM, no instruction is actually patched in the case
    // of embedded constants of the form:
    // ldr   ip, [pp, #...]
    // since the instruction accessing this address in the constant pool remains
    // unchanged.
  } else if (IsMovW(instr_at(pc))) {
    // This is an movw / movt immediate load. Patch the immediate embedded in
    // the instructions (instruction words in memory order, see instr_at).
    DCHECK(IsMovW(instr_at(pc)));
    DCHECK(IsMovT(instr_at(pc + kInstrSize)));
    uint32_t immediate = static_cast<uint32_t>(target);
    Instr movw = PatchMovwImmediate(instr_at(pc), immediate & 0xFFFF);
    Instr movt = PatchMovwImmediate(instr_at(pc + kInstrSize), immediate >> 16);
    if (jit_allocation) {
      jit_allocation->WriteValue(pc, T32SwapHalfwords(movw));
      jit_allocation->WriteValue(pc + kInstrSize, T32SwapHalfwords(movt));
    } else {
      instr_at_put(pc, movw);
      instr_at_put(pc + kInstrSize, movt);
    }
    DCHECK(IsMovW(instr_at(pc)));
    DCHECK(IsMovT(instr_at(pc + kInstrSize)));
    if (icache_flush_mode != SKIP_ICACHE_FLUSH) {
      FlushInstructionCache(pc, 2 * kInstrSize);
    }
  } else {
    // Branch offsets are even: a Thumb entry address (bit 0 set) is
    // reached by the offset to its first byte.
    intptr_t branch_offset =
        (target & ~static_cast<Address>(1)) - pc - Instruction::kPcLoadDelta;
    Instruction* branch = Instruction::At(pc);
    branch->SetBranchOffset(branch_offset, jit_allocation);
    if (icache_flush_mode != SKIP_ICACHE_FLUSH) {
      FlushInstructionCache(pc, kInstrSize);
    }
  }
}
