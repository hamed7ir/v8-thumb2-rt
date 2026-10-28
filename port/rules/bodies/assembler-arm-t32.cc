// T32 bodies for v8/src/codegen/arm/assembler-arm.cc (rules\030_t32_core.py, FuncSet "asm-core"). Each definition
// replaces the definition with the same header; the bit layouts come from t32-encodings-arm.h (tested against
// llvm-mc by tests\t32enc). Comments outside the functions are not copied into the tree.

bool Assembler::IsLdrRegisterImmediate(Instr instr) {
  // ldr.w rt, [rn, #imm12] or ldr.w rt, [pc, #+-imm12]: both keep U at bit 23
  // and the offset at bits 11:0.
  uint32_t bits = static_cast<uint32_t>(instr);
  return (bits & 0xFFF00000) == 0xF8D00000 || t32::IsLdrLiteral(bits);
}

int Assembler::GetLdrRegisterImmediateOffset(Instr instr) {
  DCHECK(IsLdrRegisterImmediate(instr));
  return t32::LdrLiteralOffset(static_cast<uint32_t>(instr));
}

Instr Assembler::SetLdrRegisterImmediateOffset(Instr instr, int offset) {
  DCHECK(t32::IsLdrLiteral(static_cast<uint32_t>(instr)));
  DCHECK(is_uint12(std::abs(offset)));
  return static_cast<Instr>(
      t32::SetLdrLiteralOffset(static_cast<uint32_t>(instr), offset));
}

bool Assembler::IsLdrPcImmediateOffset(Instr instr) {
  // Check the instruction is indeed a
  // ldr.w <Rt>, [pc, #+-offset_12].
  return t32::IsLdrLiteral(static_cast<uint32_t>(instr));
}

bool Assembler::IsVldrDPcImmediateOffset(Instr instr) {
  // Check the instruction is indeed a
  // vldr<cond> <Dd>, [pc +/- offset_10] (VFP words are the same in T32).
  return (instr & (15 * B24 | 3 * B20 | 15 * B16 | 15 * B8)) ==
         (13 * B24 | L | pc.code() * B16 | 11 * B8);
}

bool Assembler::IsBOrBlPcImmediateOffset(Instr instr) {
  // b.w, b<c>.w or bl.
  return t32::IsBranch(static_cast<uint32_t>(instr));
}

int Assembler::target_at(int pos) {
  Instr instr = instr_at(pos);
  if (is_uint24(instr)) {
    // Emitted link to a label, not part of a branch.
    return instr;
  }
  DCHECK(t32::IsBranch(static_cast<uint32_t>(instr)));  // b, b<c> or bl
  return pos + Instruction::kPcLoadDelta +
         t32::BranchOffset(static_cast<uint32_t>(instr));
}

void Assembler::target_at_put(int pos, int target_pos) {
  Instr instr = instr_at(pos);
  if (is_uint24(instr)) {
    DCHECK(target_pos == pos || target_pos >= 0);
    // Emitted link to a label, not part of a branch. Load the position of the
    // label relative to the generated code object pointer in a register: the
    // link and the nop after it (which carries the destination register, see
    // mov_label_offset) become movw (+ movt).
    Register dst =
        Register::from_code(Instruction::RmValue(instr_at(pos + kInstrSize)));
    DCHECK(IsNop(instr_at(pos + kInstrSize), dst.code()));
    // + kThumbEntryBit: the address built from this offset is a Thumb address.
    uint32_t target24 = target_pos + (InstructionStream::kHeaderSize -
                                      kHeapObjectTag + kThumbEntryBit);
    CHECK(is_uint24(target24));
    uint16_t target16_0 = target24 & kImm16Mask;
    uint16_t target16_1 = target24 >> 16;
    if (target16_1 == 0) {
      PatchingAssembler patcher(
          options(), reinterpret_cast<uint8_t*>(buffer_start_ + pos), 1);
      CpuFeatureScope scope(&patcher, ARMv7);
      patcher.movw(dst, target16_0);
    } else {
      PatchingAssembler patcher(
          options(), reinterpret_cast<uint8_t*>(buffer_start_ + pos), 2);
      CpuFeatureScope scope(&patcher, ARMv7);
      patcher.movw(dst, target16_0);
      patcher.movt(dst, target16_1);
    }
    return;
  }
  DCHECK(t32::IsBranch(static_cast<uint32_t>(instr)));  // b, b<c> or bl
  instr_at_put(pos, static_cast<Instr>(t32::SetBranchOffset(
                        static_cast<uint32_t>(instr),
                        target_pos - (pos + Instruction::kPcLoadDelta))));
}

void Assembler::print(const Label* L) {
  if (L->is_unused()) {
    PrintF("unused label\n");
  } else if (L->is_bound()) {
    PrintF("bound label to %d\n", L->pos());
  } else if (L->is_linked()) {
    Label l;
    l.link_to(L->pos());
    PrintF("unbound label");
    while (l.is_linked()) {
      PrintF("@ %d ", l.pos());
      uint32_t instr = static_cast<uint32_t>(instr_at(l.pos()));
      if ((instr & ~kImm24Mask) == 0) {
        PrintF("value\n");
      } else {
        DCHECK(t32::IsBranch(instr));  // b, b<c> or bl
        static const char* const kConditions[] = {
            "eq", "ne", "hs", "lo", "mi", "pl", "vs",
            "vc", "hi", "ls", "ge", "lt", "gt", "le"};
        if (t32::IsBranchAndLink(instr)) {
          PrintF("bl\n");
        } else if (instr & 0x1000) {
          PrintF("b\n");
        } else {
          PrintF("b%s\n", kConditions[instr >> 22 & 0xF]);
        }
      }
      next(&l);
    }
  } else {
    PrintF("label in inconsistent state (pos = %d)\n", L->pos_);
  }
}

int Operand::InstructionsRequired(const Assembler* assembler,
                                  Instr instr) const {
  DCHECK_NOT_NULL(assembler);
  if (rm_.is_valid()) {
    // A register-shifted register operand is shifted into a scratch register
    // first, except by mov.
    return IsRegisterShiftedRegister() && (instr & kOpCodeMask) != MOV ? 2 : 1;
  }
  uint32_t op, placed;
  if (MustOutputRelocInfo(assembler) ||
      !T32ImmediateOp(instr & kOpCodeMask, static_cast<uint32_t>(immediate()),
                      &op, &placed)) {
    if ((instr & ~kCondMask) == MOV && !MustOutputRelocInfo(assembler) &&
        is_uint16(immediate())) {
      return 1;  // movw
    }
    // The immediate operand cannot be encoded as a modified immediate, or use
    // of constant pool is required. First account for the instructions
    // required for the constant pool or immediate load.
    int instructions;
    if (UseMovImmediateLoad(*this, assembler)) {
      DCHECK(CpuFeatures::IsSupported(ARMv7));
      // A movw / movt immediate load.
      instructions = 2;
    } else {
      // A small constant pool load.
      instructions = 1;
    }
    if ((instr & ~kCondMask) != MOV) {  // mov, S not set
      // For a mov or mvn instruction which doesn't set the condition
      // code, the constant pool or immediate load is enough, otherwise we need
      // to account for the actual instruction being requested.
      instructions += 1;
    }
    return instructions;
  }
  return 1;
}

void Assembler::Move32BitImmediate(Register rd, const Operand& x,
                                   Condition cond) {
  if (UseMovImmediateLoad(x, this)) {
    CpuFeatureScope scope(this, ARMv7);
    // UseMovImmediateLoad should return false when we need to output
    // relocation info, since we prefer the constant pool for values that
    // can be patched.
    DCHECK(!x.MustOutputRelocInfo(this));
    UseScratchRegisterScope temps(this);
    // Reuse the destination register as a scratch if possible.
    Register target = rd != pc && rd != sp ? rd : temps.Acquire();
    uint32_t imm32 = static_cast<uint32_t>(x.immediate());
    movw(target, imm32 & 0xFFFF, cond);
    movt(target, imm32 >> 16, cond);
    if (target.code() != rd.code()) {
      mov(rd, target, LeaveCC, cond);
    }
  } else {
    // The pool entry (and a heap number request) record the position of the
    // load itself, so a predicated load emits its IT unit first.
    it(cond);
    int32_t immediate;
    if (x.IsHeapNumberRequest()) {
      RequestHeapNumber(x.heap_number_request());
      immediate = 0;
    } else {
      immediate = x.immediate();
    }
    ConstantPoolAddEntry(pc_offset(), x.rmode_, immediate);
    ldr_pcrel(rd, 0, al);
  }
}

void Assembler::AddrMode1(Instr instr, Register rd, Register rn,
                          const Operand& x) {
  CheckBuffer();
  uint32_t opcode = instr & kOpCodeMask;
  bool set_flags = (instr & S) != 0;
  Condition cond = Instruction::ConditionField(instr);
  DCHECK((opcode == ADC) || (opcode == ADD) || (opcode == AND) ||
         (opcode == BIC) || (opcode == EOR) || (opcode == ORR) ||
         (opcode == RSB) || (opcode == SBC) || (opcode == SUB) ||
         (opcode == CMN) || (opcode == CMP) || (opcode == TEQ) ||
         (opcode == TST) || (opcode == MOV) || (opcode == MVN));
  // For comparison instructions, rd is not defined. A CHECK, not a DCHECK: in
  // T32 an invalid rd (no_reg, code -1) would silently encode as pc.
  CHECK(rd.is_valid() || (opcode == CMN) || (opcode == CMP) ||
        (opcode == TEQ) || (opcode == TST));
  // For move instructions, rn is not defined.
  DCHECK(rn.is_valid() || (opcode == MOV) || (opcode == MVN));
  DCHECK(rd.is_valid() || rn.is_valid());
  DCHECK_EQ(instr & ~(kCondMask | kOpCodeMask | S), 0);
  bool is_move = (opcode == MOV) || (opcode == MVN);
  bool shifted_register = x.IsRegisterShiftedRegister();
  Register rm = x.rm_;
  UseScratchRegisterScope temps(this);

  // Writes to pc: mov pc, rm and add pc, pc, rm << n (jump tables) are 16-bit
  // instructions sharing a 4-byte unit with an IT or a nop; mov pc, #imm is a
  // literal load into pc; any other result goes through a scratch register.
  if (rd == pc) {
    DCHECK(!set_flags);
    if (opcode == MOV && x.IsImmediate()) {
      Move32BitImmediate(pc, x, cond);
      return;
    }
    if (opcode == MOV && !shifted_register && x.shift_imm_ == 0) {
      emit(t32::Unit(t32::Prefix16(CondNumber(cond)), t32::MovPc(rm.code())));
      return;
    }
    if (opcode == ADD && rn == pc && rm.is_valid() && !shifted_register) {
      DCHECK_EQ(cond, al);
      Register index = rm;
      if (x.shift_imm_ != 0) {
        index = temps.Acquire();
        mov(index, Operand(rm, x.shift_op_, x.shift_imm_));
      }
      // pc reads as this unit + 4: the slot right after it.
      BlockConstPoolFor(1);
      emit(t32::Unit(t32::AddPc(index.code()), t32::kNop16));
      return;
    }
    DCHECK_NE(rn, pc);
    Register target = temps.Acquire();
    AddrMode1(instr, target, rn, x);
    mov(pc, Operand(target), LeaveCC, cond);
    return;
  }

  // Reads of pc (its address + 4; every instruction V8 emits is 4-byte
  // aligned, so that is also the adr base) exist as adr (addw/subw rd, pc,
  // #imm12) and as the 16-bit add rdn, pc (rd = rd + pc, GetLabelAddress).
  // Any other read of pc goes through an adr into a scratch.
  if (rn == pc || rm == pc) {
    DCHECK(!set_flags);
    DCHECK_EQ(cond, al);
    if (opcode == ADD && !shifted_register && x.shift_op_ == LSL &&
        x.shift_imm_ == 0 && rd != sp && rd != pc &&
        ((rn == pc && rm == rd) || (rm == pc && rn == rd))) {
      emit(t32::Unit(t32::AddRegPc(rd.code()), t32::kNop16));
      BlockConstPoolFor(1);
      return;
    }
    if (rn == pc && x.IsImmediate() && !x.MustOutputRelocInfo(this) &&
        (opcode == ADD || opcode == SUB)) {
      int32_t offset = opcode == ADD ? x.immediate() : -x.immediate();
      if (offset >= -4095 && offset <= 4095) {
        emit(t32::Adr(rd.code(), offset));
        // Block constant pool emission for one instruction after reading pc.
        BlockConstPoolFor(1);
        return;
      }
    }
    Register pc_value = (rd.is_valid() && rd != rn && rd != rm && rd != sp &&
                         !(shifted_register && rd == x.rs_))
                            ? rd
                            : temps.Acquire();
    emit(t32::Adr(pc_value.code(), 0));
    BlockConstPoolFor(1);
    Operand y = rm != pc ? x
                : shifted_register
                    ? Operand(pc_value, x.shift_op_, x.rs_)
                    : Operand(pc_value, x.shift_op_, x.shift_imm_);
    AddrMode1(instr, rd, rn == pc ? pc_value : rn, y);
    return;
  }

  // sp: T32 reads it as rn only in add/sub/cmp/cmn, writes it only from
  // add/sub with rn == sp and from mov (register), and reads it as rm only in
  // mov (register). Every other use goes through a scratch register. An
  // illegal write is handled first: the result register then also takes the
  // copy of sp an illegal read needs (and sp, sp, #-8 = mov ip, sp;
  // bic ip, ip, #7; mov sp, ip - one scratch register).
  if (rd == sp) {
    bool legal =
        opcode == MOV
            ? !set_flags && rm.is_valid() && !shifted_register &&
                  x.shift_op_ == LSL && x.shift_imm_ == 0
            : (opcode == ADD || opcode == SUB) && rn == sp &&
                  (x.IsImmediate() || (!shifted_register &&
                                       x.shift_op_ == LSL &&
                                       x.shift_imm_ <= 3));
    if (!legal) {
      Register result = temps.Acquire();
      AddrMode1(instr, result, rn, x);
      mov(sp, Operand(result), LeaveCC, cond);
      return;
    }
  }
  if (rm == sp || (shifted_register && x.rs_ == sp)) {
    bool plain = !shifted_register && x.shift_op_ == LSL && x.shift_imm_ == 0;
    if (opcode == ADD && plain && rn.is_valid() && rn != sp) {
      AddrMode1(instr, rd, sp, Operand(rn));  // add rd, sp, rn
      return;
    }
    if (!(opcode == MOV && plain && !set_flags && rd != sp)) {
      Register sp_copy =
          (rd.is_valid() && rd != rn && rd != sp &&
           !(shifted_register && (rd == rm || rd == x.rs_)))
              ? rd
              : temps.Acquire();
      mov(sp_copy, Operand(sp), LeaveCC, cond);
      Operand y =
          shifted_register
              ? Operand(rm == sp ? sp_copy : rm, x.shift_op_,
                        x.rs_ == sp ? sp_copy : x.rs_)
              : Operand(sp_copy, x.shift_op_, x.shift_imm_);
      AddrMode1(instr, rd, rn, y);
      return;
    }
  }
  if (rn == sp && !(opcode == ADD || opcode == SUB || opcode == CMP ||
                    opcode == CMN)) {
    Register sp_copy = (rd.is_valid() && rd != sp && rd != rm &&
                        !(shifted_register && rd == x.rs_))
                           ? rd
                           : temps.Acquire();
    mov(sp_copy, Operand(sp), LeaveCC, cond);
    AddrMode1(instr, rd, sp_copy, x);
    return;
  }

  uint32_t rn_field = is_move ? t32::kNoReg : rn.code();
  uint32_t rd_field = rd.is_valid() ? rd.code() : t32::kNoReg;
  if (x.IsImmediate()) {
    uint32_t imm = static_cast<uint32_t>(x.immediate());
    uint32_t op, placed;
    if (x.MustOutputRelocInfo(this) ||
        !T32ImmediateOp(opcode, imm, &op, &placed)) {
      if ((opcode == MOV) && !set_flags) {
        // Generate a movw, a movw/movt pair or a load from the constant pool
        // only for a MOV instruction which does not set the flags.
        DCHECK(!rn.is_valid());
        if (!x.MustOutputRelocInfo(this) && imm <= 0xFFFF && rd != sp) {
          movw(rd, imm, cond);
        } else {
          Move32BitImmediate(rd, x, cond);
        }
      } else if ((opcode == ADD || opcode == SUB) && !set_flags &&
                 (rd == rn) && !temps.CanAcquire()) {
        // Split the operation into a sequence of additions if we cannot use a
        // scratch register: the top 8 bits of what is left are always a
        // modified immediate.
        uint32_t rest = imm;
        while (!T32ImmediateOp(opcode, rest, &op, &placed)) {
          int shift = 24 - base::bits::CountLeadingZeros32(rest);
          uint32_t chunk = rest & (0xFFu << (shift > 0 ? shift : 0));
          AddrMode1(instr, rd, rn, Operand(static_cast<int32_t>(chunk)));
          rest &= ~chunk;
        }
        AddrMode1(instr, rd, rn, Operand(static_cast<int32_t>(rest)));
      } else {
        // The immediate operand cannot be encoded as a modified immediate, so
        // load it first to a scratch register and change the original
        // instruction to use it. Reuse the destination register if possible.
        Register scratch =
            (rd.is_valid() && rd != rn && rd != pc && rd != sp)
                ? rd
                : temps.Acquire();
        mov(scratch, x, LeaveCC, cond);
        AddrMode1(instr, rd, rn, Operand(scratch));
      }
      return;
    }
    it(cond);
    emit(t32::DPImmediate(op, set_flags, rn_field, rd_field, placed));
    return;
  }

  if (shifted_register) {
    // It is unpredictable to use the PC in this case.
    DCHECK(rm != pc && x.rs_ != pc);
    if (opcode == MOV) {
      it(cond);
      emit(t32::ShiftRegister(x.shift_op_ >> 5, set_flags, rd.code(),
                              rm.code(), x.rs_.code()));
      return;
    }
    // No T32 form: shift into a scratch register (the destination when that
    // is free), then use the register form.
    Register shifted = (rd.is_valid() && rd != rn) ? rd : temps.Acquire();
    mov(shifted, Operand(rm, x.shift_op_, x.rs_), LeaveCC, cond);
    AddrMode1(instr, rd, rn, Operand(shifted));
    return;
  }

  it(cond);
  emit(t32::DPShifted(T32Op(opcode), set_flags, rn_field, rd_field, rm.code(),
                      x.shift_op_ >> 5, x.shift_imm_));
}

void Assembler::AddrMode2(Instr instr, Register rd, const MemOperand& x) {
  DCHECK((instr & ~(kCondMask | B | L)) == B26);
  // This method does not handle pc-relative addresses. ldr_pcrel() should be
  // used instead.
  DCHECK(x.rn_ != pc);
  bool is_load = (instr & L) != 0;
  uint32_t t3 = (instr & B) != 0 ? (is_load ? t32::kLdrb : t32::kStrb)
                                 : (is_load ? t32::kLdr : t32::kStr);
  LoadStoreT32(t3, rd, x, Instruction::ConditionField(instr));
}

void Assembler::AddrMode3(Instr instr, Register rd, const MemOperand& x) {
  DCHECK((instr & ~(kCondMask | L | S6 | H)) == (B4 | B7));
  DCHECK(x.rn_.is_valid());
  // This method does not handle pc-relative addresses. ldr_pcrel() should be
  // used instead.
  DCHECK(x.rn_ != pc);
  Condition cond = Instruction::ConditionField(instr);
  bool is_load = (instr & L) != 0;
  bool is_signed = (instr & S6) != 0;
  bool is_half = (instr & H) != 0;
  if (is_load || !is_signed) {
    // ldrh, strh, ldrsb, ldrsh.
    uint32_t t3 = is_signed ? (is_half ? t32::kLdrsh : t32::kLdrsb)
                            : (is_load ? t32::kLdrh : t32::kStrh);
    LoadStoreT32(t3, rd, x, cond);
    return;
  }
  // ldrd (S6) / strd (S6 | H): T32 offsets are imm8 * 4 with P/U/W.
  bool is_ldrd = !is_half;
  Register rd2 = Register::from_code(rd.code() + 1);
  DCHECK(!x.rm_.is_valid());
  int am = x.am_;
  bool pre = (am & P) != 0;
  bool writeback = !pre || (am & W) != 0;
  int32_t offset = (am & U) != 0 ? x.offset_ : -x.offset_;
  if ((offset & 3) == 0 && offset >= -1020 && offset <= 1020) {
    it(cond);
    emit(t32::LoadStoreDual(is_ldrd, x.rn_.code(), rd.code(), rd2.code(), pre,
                            offset >= 0, writeback,
                            (offset >= 0 ? offset : -offset) >> 2));
    return;
  }
  // The offset does not fit: form the address, or the updated base, first.
  if (!writeback) {
    UseScratchRegisterScope temps(this);
    Register address = temps.Acquire();
    add(address, x.rn_, Operand(offset), LeaveCC, cond);
    AddrMode3(instr, rd, MemOperand(address));
  } else if (pre) {
    add(x.rn_, x.rn_, Operand(offset), LeaveCC, cond);
    AddrMode3(instr, rd, MemOperand(x.rn_));
  } else {
    AddrMode3(instr, rd, MemOperand(x.rn_));
    add(x.rn_, x.rn_, Operand(offset), LeaveCC, cond);
  }
}

void Assembler::AddrMode4(Instr instr, Register rn, RegList rl) {
  DCHECK((instr & ~(kCondMask | P | U | W | L)) == B27);
  DCHECK(!rl.is_empty());
  DCHECK(rn != pc);
  Condition cond = Instruction::ConditionField(instr);
  bool is_load = (instr & L) != 0;
  bool before = (instr & P) != 0;
  bool up = (instr & U) != 0;
  bool writeback = (instr & W) != 0;
  uint32_t bits = rl.bits();
  int count = base::bits::CountPopulation(bits);
  DCHECK(!(writeback && rl.has(rn)));
  // T32 ldm/stm: ia or db, two or more registers, never sp, no pc in stores
  // and not pc together with lr in loads.
  if (up != before && count >= 2 && !rl.has(sp) &&
      !(is_load ? (rl.has(pc) && rl.has(lr)) : rl.has(pc))) {
    it(cond);
    emit(t32::LoadStoreMultiple(is_load, up, writeback, rn.code(), bits));
    return;
  }
  // Otherwise single loads/stores with the same memory layout: registers in
  // ascending order at ascending addresses.
  if (count == 1 && writeback) {
    Register r = Register::from_code(base::bits::CountTrailingZeros(bits));
    AddrMode am = up ? (before ? PreIndex : PostIndex)
                     : (before ? NegPreIndex : NegPostIndex);
    if (is_load) {
      ldr(r, MemOperand(rn, kPointerSize, am), cond);
    } else {
      str(r, MemOperand(rn, kPointerSize, am), cond);
    }
    return;
  }
  DCHECK(!(is_load && rl.has(pc) && rl.has(rn)));
  int size = count * kPointerSize;
  // Offset of the lowest register's slot from rn.
  int lowest = up ? (before ? kPointerSize : 0)
                  : (before ? -size : kPointerSize - size);
  if (writeback && !up) {
    // Decrementing: move the base first so every access is at offset >= 0.
    sub(rn, rn, Operand(size), LeaveCC, cond);
    lowest += size;
  }
  int offset = lowest;
  int rn_offset = 0;
  int pc_offset_in_block = 0;
  for (int code = 0; code < kNumRegisters; code++) {
    if ((bits & (1u << code)) == 0) continue;
    Register r = Register::from_code(code);
    if (is_load && r == pc) {
      pc_offset_in_block = offset;  // loaded last: it is a branch
    } else if (is_load && r == rn) {
      rn_offset = offset;  // loaded after the others that use rn
    } else if (is_load) {
      ldr(r, MemOperand(rn, offset), cond);
    } else {
      str(r, MemOperand(rn, offset), cond);
    }
    offset += kPointerSize;
  }
  if (is_load && rl.has(rn)) ldr(rn, MemOperand(rn, rn_offset), cond);
  if (writeback && up) {
    if (is_load && rl.has(pc)) {
      // pc is the highest slot: advance to it, then load it post-indexed so
      // the base ends at rn + size.
      add(rn, rn, Operand(pc_offset_in_block), LeaveCC, cond);
      ldr(pc, MemOperand(rn, size - pc_offset_in_block, PostIndex), cond);
    } else {
      add(rn, rn, Operand(size), LeaveCC, cond);
    }
  } else if (is_load && rl.has(pc)) {
    ldr(pc, MemOperand(rn, pc_offset_in_block), cond);
  }
}

void Assembler::AddrMode5(Instr instr, CRegister crd, const MemOperand& x) {
  // Unindexed addressing is not encoded by this function.
  DCHECK_EQ((B27 | B26),
            (instr & ~(kCondMask | kCoprocessorMask | P | U | N | W | L)));
  DCHECK(x.rn_.is_valid() && !x.rm_.is_valid());
  int am = x.am_;
  int offset_8 = x.offset_;
  DCHECK_EQ(offset_8 & 3, 0);  // offset must be an aligned word offset
  offset_8 >>= 2;
  if (offset_8 < 0) {
    offset_8 = -offset_8;
    am ^= U;
  }
  DCHECK(is_uint8(offset_8));  // unsigned word offset must fit in a byte
  DCHECK((am & (P | W)) == P || x.rn_ != pc);  // no pc base with writeback

  // Post-indexed addressing requires W == 1; different than in AddrMode2/3.
  if ((am & P) == 0) am |= W;

  DCHECK_GE(offset_8, 0);  // no masking needed
  // Coprocessor words are the same in T32 with cond = AL.
  it(Instruction::ConditionField(instr));
  emit((instr & ~kCondMask) | al | am | x.rn_.code() * B16 |
       crd.code() * B12 | offset_8);
}

void Assembler::b(int branch_offset, Condition cond, RelocInfo::Mode rmode) {
  // A conditional jump to a code target needs B.W's reach (+-16 MiB): IT +
  // B.W, with the reloc info on the branch itself. Every other conditional
  // branch is B<c>.W (+-1 MiB).
  bool predicated = cond != al && !RelocInfo::IsNoInfo(rmode);
  if (predicated) it(cond);
  if (!RelocInfo::IsNoInfo(rmode)) RecordRelocInfo(rmode);
  DCHECK_EQ(branch_offset & 1, 0);

  // Block the emission of the constant pool before the next instruction.
  // Otherwise the passed-in branch offset would be off.
  BlockConstPoolFor(1);

  emit(t32::Branch(predicated ? t32::kAl : CondNumber(cond), false,
                   branch_offset));

  if (cond == al) {
    // Dead code is a good location to emit the constant pool.
    CheckConstPool(false, false);
  }
}

void Assembler::bl(int branch_offset, Condition cond, RelocInfo::Mode rmode) {
  // BL has no condition field: a predicated call is IT + BL, with the reloc
  // info on the BL itself.
  it(cond);
  if (!RelocInfo::IsNoInfo(rmode)) RecordRelocInfo(rmode);
  DCHECK_EQ(branch_offset & 1, 0);

  // Block the emission of the constant pool before the next instruction.
  // Otherwise the passed-in branch offset would be off.
  BlockConstPoolFor(1);

  emit(t32::Branch(t32::kAl, true, branch_offset));
}

void Assembler::blx(Register target, Condition cond) {
  DCHECK(target != pc);
  // 16-bit only: [nop16 or IT][blx] fill one unit, so the return address is
  // the next 4-byte unit.
  emit(t32::Unit(t32::Prefix16(CondNumber(cond)), t32::Blx(target.code())));
}

void Assembler::bx(Register target, Condition cond) {
  DCHECK(target != pc);  // use of pc is actually allowed, but discouraged
  // 16-bit only: [nop16 or IT][bx] fill one unit.
  emit(t32::Unit(t32::Prefix16(CondNumber(cond)), t32::Bx(target.code())));
}

void Assembler::bl(Label* L, Condition cond) {
  CheckBuffer();
  // The label links to the BL, so its IT unit goes first.
  it(cond);
  bl(branch_offset(L), al);
}

void Assembler::mov_label_offset(Register dst, Label* label) {
  if (label->is_bound()) {
    // + kThumbEntryBit: the address built from this offset is a Thumb address.
    mov(dst, Operand(label->pos() + (InstructionStream::kHeaderSize -
                                     kHeapObjectTag + kThumbEntryBit)));
  } else {
    // Emit the link to the label in the code stream followed by a nop that
    // carries the destination register. When the label is bound, target_at
    // extracts the link and target_at_put patches both with movw (+ movt),
    // loading the position of the label from the beginning of the code.
    // If the label is not linked, then start a new link chain by linking it to
    // itself, emitting pc_offset().
    int link = label->is_linked() ? label->pos() : pc_offset();
    label->link_to(pc_offset());
    CHECK(is_uint24(link));
    BlockConstPoolScope block_const_pool(this);
    emit(link);
    nop(dst.code());
  }
}

void Assembler::movw(Register reg, uint32_t immediate, Condition cond) {
  DCHECK(IsEnabled(ARMv7));
  DCHECK(reg != sp && reg != pc);
  it(cond);
  emit(t32::Movw(reg.code(), immediate));
}

void Assembler::movt(Register reg, uint32_t immediate, Condition cond) {
  DCHECK(IsEnabled(ARMv7));
  DCHECK(reg != sp && reg != pc);
  it(cond);
  emit(t32::Movt(reg.code(), immediate));
}

void Assembler::mla(Register dst, Register src1, Register src2, Register srcA,
                    SBit s, Condition cond) {
  DCHECK(dst != pc && src1 != pc && src2 != pc && srcA != pc);
  it(cond);
  emit(t32::Reg4(t32::kMla, src1.code(), srcA.code(), dst.code(),
                 src2.code()));
  // T32 multiplies never set flags; tst sets N and Z from the result and
  // leaves C and V alone, as mlas does.
  if (s == SetCC) tst(dst, Operand(dst), cond);
}

void Assembler::mls(Register dst, Register src1, Register src2, Register srcA,
                    Condition cond) {
  DCHECK(dst != pc && src1 != pc && src2 != pc && srcA != pc);
  DCHECK(IsEnabled(ARMv7));
  it(cond);
  emit(t32::Reg4(t32::kMls, src1.code(), srcA.code(), dst.code(),
                 src2.code()));
}

void Assembler::sdiv(Register dst, Register src1, Register src2,
                     Condition cond) {
  DCHECK(dst != pc && src1 != pc && src2 != pc);
  DCHECK(IsEnabled(SUDIV));
  it(cond);
  emit(t32::Reg4(t32::kSdiv, src1.code(), 0, dst.code(), src2.code()));
}

void Assembler::udiv(Register dst, Register src1, Register src2,
                     Condition cond) {
  DCHECK(dst != pc && src1 != pc && src2 != pc);
  DCHECK(IsEnabled(SUDIV));
  it(cond);
  emit(t32::Reg4(t32::kUdiv, src1.code(), 0, dst.code(), src2.code()));
}

void Assembler::mul(Register dst, Register src1, Register src2, SBit s,
                    Condition cond) {
  DCHECK(dst != pc && src1 != pc && src2 != pc);
  it(cond);
  emit(t32::Reg4(t32::kMul, src1.code(), 0, dst.code(), src2.code()));
  // T32 multiplies never set flags; tst sets N and Z from the result and
  // leaves C and V alone, as muls does.
  if (s == SetCC) tst(dst, Operand(dst), cond);
}

void Assembler::smmla(Register dst, Register src1, Register src2, Register srcA,
                      Condition cond) {
  DCHECK(dst != pc && src1 != pc && src2 != pc && srcA != pc);
  it(cond);
  emit(t32::Reg4(t32::kSmmla, src1.code(), srcA.code(), dst.code(),
                 src2.code()));
}

void Assembler::smmul(Register dst, Register src1, Register src2,
                      Condition cond) {
  DCHECK(dst != pc && src1 != pc && src2 != pc);
  it(cond);
  emit(t32::Reg4(t32::kSmmul, src1.code(), 0, dst.code(), src2.code()));
}

void Assembler::smlal(Register dstL, Register dstH, Register src1,
                      Register src2, SBit s, Condition cond) {
  DCHECK(dstL != pc && dstH != pc && src1 != pc && src2 != pc);
  DCHECK(dstL != dstH);
  DCHECK_EQ(s, LeaveCC);  // T32 long multiplies never set flags
  it(cond);
  emit(t32::Reg4(t32::kSmlal, src1.code(), dstL.code(), dstH.code(),
                 src2.code()));
}

void Assembler::smull(Register dstL, Register dstH, Register src1,
                      Register src2, SBit s, Condition cond) {
  DCHECK(dstL != pc && dstH != pc && src1 != pc && src2 != pc);
  DCHECK(dstL != dstH);
  DCHECK_EQ(s, LeaveCC);  // T32 long multiplies never set flags
  it(cond);
  emit(t32::Reg4(t32::kSmull, src1.code(), dstL.code(), dstH.code(),
                 src2.code()));
}

void Assembler::umlal(Register dstL, Register dstH, Register src1,
                      Register src2, SBit s, Condition cond) {
  DCHECK(dstL != pc && dstH != pc && src1 != pc && src2 != pc);
  DCHECK(dstL != dstH);
  DCHECK_EQ(s, LeaveCC);  // T32 long multiplies never set flags
  it(cond);
  emit(t32::Reg4(t32::kUmlal, src1.code(), dstL.code(), dstH.code(),
                 src2.code()));
}

void Assembler::umull(Register dstL, Register dstH, Register src1,
                      Register src2, SBit s, Condition cond) {
  DCHECK(dstL != pc && dstH != pc && src1 != pc && src2 != pc);
  DCHECK(dstL != dstH);
  DCHECK_EQ(s, LeaveCC);  // T32 long multiplies never set flags
  it(cond);
  emit(t32::Reg4(t32::kUmull, src1.code(), dstL.code(), dstH.code(),
                 src2.code()));
}

void Assembler::clz(Register dst, Register src, Condition cond) {
  DCHECK(dst != pc && src != pc);
  it(cond);
  emit(t32::Clz(dst.code(), src.code()));
}

void Assembler::usat(Register dst, int satpos, const Operand& src,
                     Condition cond) {
  DCHECK(dst != pc && src.rm_ != pc);
  DCHECK((satpos >= 0) && (satpos <= 31));
  DCHECK(src.IsImmediateShiftedRegister());
  DCHECK((src.shift_op_ == ASR) || (src.shift_op_ == LSL));
  // asr #0 would be usat16.
  DCHECK(src.shift_op_ == LSL || src.shift_imm_ != 0);
  it(cond);
  emit(t32::Usat(dst.code(), satpos, src.rm_.code(), src.shift_op_ == ASR,
                 src.shift_imm_));
}

void Assembler::ubfx(Register dst, Register src, int lsb, int width,
                     Condition cond) {
  DCHECK(IsEnabled(ARMv7));
  DCHECK(dst != pc && src != pc);
  DCHECK((lsb >= 0) && (lsb <= 31));
  DCHECK((width >= 1) && (width <= (32 - lsb)));
  it(cond);
  emit(t32::Bfx(false, dst.code(), src.code(), lsb, width));
}

void Assembler::sbfx(Register dst, Register src, int lsb, int width,
                     Condition cond) {
  DCHECK(IsEnabled(ARMv7));
  DCHECK(dst != pc && src != pc);
  DCHECK((lsb >= 0) && (lsb <= 31));
  DCHECK((width >= 1) && (width <= (32 - lsb)));
  it(cond);
  emit(t32::Bfx(true, dst.code(), src.code(), lsb, width));
}

void Assembler::bfc(Register dst, int lsb, int width, Condition cond) {
  DCHECK(IsEnabled(ARMv7));
  DCHECK(dst != pc);
  DCHECK((lsb >= 0) && (lsb <= 31));
  DCHECK((width >= 1) && (width <= (32 - lsb)));
  it(cond);
  emit(t32::Bfi(dst.code(), t32::kNoReg, lsb, width));
}

void Assembler::bfi(Register dst, Register src, int lsb, int width,
                    Condition cond) {
  DCHECK(IsEnabled(ARMv7));
  DCHECK(dst != pc && src != pc);
  DCHECK((lsb >= 0) && (lsb <= 31));
  DCHECK((width >= 1) && (width <= (32 - lsb)));
  it(cond);
  emit(t32::Bfi(dst.code(), src.code(), lsb, width));
}

void Assembler::pkhbt(Register dst, Register src1, const Operand& src2,
                      Condition cond) {
  DCHECK(dst != pc);
  DCHECK(src1 != pc);
  DCHECK(src2.IsImmediateShiftedRegister());
  DCHECK(src2.rm() != pc);
  DCHECK((src2.shift_imm_ >= 0) && (src2.shift_imm_ <= 31));
  DCHECK(src2.shift_op() == LSL);
  it(cond);
  emit(t32::Pkh(false, dst.code(), src1.code(), src2.rm().code(),
                src2.shift_imm_));
}

void Assembler::pkhtb(Register dst, Register src1, const Operand& src2,
                      Condition cond) {
  DCHECK(dst != pc);
  DCHECK(src1 != pc);
  DCHECK(src2.IsImmediateShiftedRegister());
  DCHECK(src2.rm() != pc);
  DCHECK((src2.shift_imm_ >= 1) && (src2.shift_imm_ <= 32));
  DCHECK(src2.shift_op() == ASR);
  int asr = (src2.shift_imm_ == 32) ? 0 : src2.shift_imm_;
  it(cond);
  emit(t32::Pkh(true, dst.code(), src1.code(), src2.rm().code(), asr));
}

void Assembler::sxtb(Register dst, Register src, int rotate, Condition cond) {
  DCHECK(dst != pc);
  DCHECK(src != pc);
  DCHECK(rotate == 0 || rotate == 8 || rotate == 16 || rotate == 24);
  it(cond);
  emit(t32::Extend(t32::kSxtab, t32::kNoReg, dst.code(), rotate, src.code()));
}

void Assembler::sxtab(Register dst, Register src1, Register src2, int rotate,
                      Condition cond) {
  DCHECK(dst != pc);
  DCHECK(src1 != pc);
  DCHECK(src2 != pc);
  DCHECK(rotate == 0 || rotate == 8 || rotate == 16 || rotate == 24);
  it(cond);
  emit(t32::Extend(t32::kSxtab, src1.code(), dst.code(), rotate, src2.code()));
}

void Assembler::sxth(Register dst, Register src, int rotate, Condition cond) {
  DCHECK(dst != pc);
  DCHECK(src != pc);
  DCHECK(rotate == 0 || rotate == 8 || rotate == 16 || rotate == 24);
  it(cond);
  emit(t32::Extend(t32::kSxtah, t32::kNoReg, dst.code(), rotate, src.code()));
}

void Assembler::sxtah(Register dst, Register src1, Register src2, int rotate,
                      Condition cond) {
  DCHECK(dst != pc);
  DCHECK(src1 != pc);
  DCHECK(src2 != pc);
  DCHECK(rotate == 0 || rotate == 8 || rotate == 16 || rotate == 24);
  it(cond);
  emit(t32::Extend(t32::kSxtah, src1.code(), dst.code(), rotate, src2.code()));
}

void Assembler::uxtb(Register dst, Register src, int rotate, Condition cond) {
  DCHECK(dst != pc);
  DCHECK(src != pc);
  DCHECK(rotate == 0 || rotate == 8 || rotate == 16 || rotate == 24);
  it(cond);
  emit(t32::Extend(t32::kUxtab, t32::kNoReg, dst.code(), rotate, src.code()));
}

void Assembler::uxtab(Register dst, Register src1, Register src2, int rotate,
                      Condition cond) {
  DCHECK(dst != pc);
  DCHECK(src1 != pc);
  DCHECK(src2 != pc);
  DCHECK(rotate == 0 || rotate == 8 || rotate == 16 || rotate == 24);
  it(cond);
  emit(t32::Extend(t32::kUxtab, src1.code(), dst.code(), rotate, src2.code()));
}

void Assembler::uxtb16(Register dst, Register src, int rotate, Condition cond) {
  DCHECK(dst != pc);
  DCHECK(src != pc);
  DCHECK(rotate == 0 || rotate == 8 || rotate == 16 || rotate == 24);
  it(cond);
  emit(t32::Extend(t32::kUxtab16, t32::kNoReg, dst.code(), rotate,
                   src.code()));
}

void Assembler::uxth(Register dst, Register src, int rotate, Condition cond) {
  DCHECK(dst != pc);
  DCHECK(src != pc);
  DCHECK(rotate == 0 || rotate == 8 || rotate == 16 || rotate == 24);
  it(cond);
  emit(t32::Extend(t32::kUxtah, t32::kNoReg, dst.code(), rotate, src.code()));
}

void Assembler::uxtah(Register dst, Register src1, Register src2, int rotate,
                      Condition cond) {
  DCHECK(dst != pc);
  DCHECK(src1 != pc);
  DCHECK(src2 != pc);
  DCHECK(rotate == 0 || rotate == 8 || rotate == 16 || rotate == 24);
  it(cond);
  emit(t32::Extend(t32::kUxtah, src1.code(), dst.code(), rotate, src2.code()));
}

void Assembler::rbit(Register dst, Register src, Condition cond) {
  DCHECK(IsEnabled(ARMv7));
  DCHECK(dst != pc);
  DCHECK(src != pc);
  it(cond);
  emit(t32::Rbit(dst.code(), src.code()));
}

void Assembler::rev(Register dst, Register src, Condition cond) {
  DCHECK(dst != pc);
  DCHECK(src != pc);
  it(cond);
  emit(t32::Rev(dst.code(), src.code()));
}

void Assembler::mrs(Register dst, SRegister s, Condition cond) {
  DCHECK(dst != pc);
  it(cond);
  emit(t32::Mrs(dst.code(), s == SPSR));
}

void Assembler::msr(SRegisterFieldMask fields, const Operand& src,
                    Condition cond) {
  DCHECK_NE(fields & 0x000F0000, 0);  // At least one field must be set.
  DCHECK(((fields & 0xFFF0FFFF) == CPSR) || ((fields & 0xFFF0FFFF) == SPSR));
  if (!src.IsRegister()) {
    // T32 msr takes only a register: load the immediate first.
    DCHECK(src.IsImmediate());
    UseScratchRegisterScope temps(this);
    Register scratch = temps.Acquire();
    Move32BitImmediate(scratch, src, cond);
    msr(fields, Operand(scratch), cond);
    return;
  }
  it(cond);
  emit(t32::Msr((fields & 0xFFF0FFFF) == SPSR, (fields >> 16) & 0xF,
                src.rm_.code()));
}

void Assembler::ldr_pcrel(Register dst, int imm12, Condition cond) {
  DCHECK(is_uint12(std::abs(imm12)));
  it(cond);
  emit(t32::LoadLiteral(t32::kLdr, dst.code(), imm12));
}

void Assembler::ldrex(Register dst, Register src, Condition cond) {
  DCHECK(dst != pc);
  DCHECK(src != pc);
  it(cond);
  emit(t32::Ldrex(dst.code(), src.code()));
}

void Assembler::strex(Register src1, Register src2, Register dst,
                      Condition cond) {
  DCHECK(dst != pc);
  DCHECK(src1 != pc);
  DCHECK(src2 != pc);
  DCHECK(src1 != dst);
  DCHECK(src1 != src2);
  it(cond);
  emit(t32::Strex(src1.code(), src2.code(), dst.code()));
}

void Assembler::ldrexb(Register dst, Register src, Condition cond) {
  DCHECK(dst != pc);
  DCHECK(src != pc);
  it(cond);
  emit(t32::Ldrexb(dst.code(), src.code()));
}

void Assembler::strexb(Register src1, Register src2, Register dst,
                       Condition cond) {
  DCHECK(dst != pc);
  DCHECK(src1 != pc);
  DCHECK(src2 != pc);
  DCHECK(src1 != dst);
  DCHECK(src1 != src2);
  it(cond);
  emit(t32::Strexb(src1.code(), src2.code(), dst.code()));
}

void Assembler::ldrexh(Register dst, Register src, Condition cond) {
  DCHECK(dst != pc);
  DCHECK(src != pc);
  it(cond);
  emit(t32::Ldrexh(dst.code(), src.code()));
}

void Assembler::strexh(Register src1, Register src2, Register dst,
                       Condition cond) {
  DCHECK(dst != pc);
  DCHECK(src1 != pc);
  DCHECK(src2 != pc);
  DCHECK(src1 != dst);
  DCHECK(src1 != src2);
  it(cond);
  emit(t32::Strexh(src1.code(), src2.code(), dst.code()));
}

void Assembler::ldrexd(Register dst1, Register dst2, Register src,
                       Condition cond) {
  DCHECK(dst1 != lr);  // r14.
  // The pair of destination registers is restricted to being an even-numbered
  // register and the odd-numbered register that immediately follows it.
  DCHECK_EQ(0, dst1.code() % 2);
  DCHECK_EQ(dst1.code() + 1, dst2.code());
  it(cond);
  emit(t32::Ldrexd(dst1.code(), dst2.code(), src.code()));
}

void Assembler::strexd(Register res, Register src1, Register src2, Register dst,
                       Condition cond) {
  DCHECK(src1 != lr);  // r14.
  // The pair of source registers is restricted to being an even-numbered
  // register and the odd-numbered register that immediately follows it.
  DCHECK_EQ(0, src1.code() % 2);
  DCHECK_EQ(src1.code() + 1, src2.code());
  it(cond);
  emit(t32::Strexd(res.code(), src1.code(), src2.code(), dst.code()));
}

void Assembler::pld(const MemOperand& address) {
  DCHECK(address.rm() == no_reg);
  DCHECK(address.am() == Offset);
  int offset = address.offset();
  DCHECK(offset >= -255 && offset <= 4095);
  emit(t32::Pld(address.rn().code(), offset));
}

void Assembler::bkpt(uint32_t imm16) {
  DCHECK(is_uint16(imm16));
  // Windows on ARM's breakpoint is udf #0xfe (16-bit), paired with a nop.
  emit(t32::Unit(t32::kNop16, t32::kWindowsBreakpoint));
}

void Assembler::svc(uint32_t imm24, Condition cond) {
  CHECK(is_uint24(imm24));
  // Only the simulator executes svc (redirections and stops); generated code
  // never makes system calls. T32 svc has an 8-bit immediate, so the code goes
  // into udf.w #imm16: bit 15 = stop (bit 23 of imm24), bits 14:0 = the code.
  DCHECK_LT(imm24 & kStopCodeMask, 0x8000u);
  uint32_t imm16 =
      (imm24 & kStopCode ? 0x8000 : 0) | (imm24 & kStopCodeMask & 0x7FFF);
  it(cond);
  emit(t32::Udf(imm16));
}

void Assembler::dmb(BarrierOption option) {
  // Details available in ARM DDI 0406C.b, A8-378.
  emit(t32::Dmb(option));
}

void Assembler::dsb(BarrierOption option) {
  // Details available in ARM DDI 0406C.b, A8-380.
  emit(t32::Dsb(option));
}

void Assembler::isb(BarrierOption option) {
  // Details available in ARM DDI 0406C.b, A8-389.
  emit(t32::Isb(option));
}

void Assembler::csdb() {
  // Details available in Arm Cache Speculation Side-channels white paper,
  // version 1.1, page 4.
  emit(t32::kCsdb);
}

void Assembler::nop(int type) {
  // mov.w rT, rT is a nop whose register T encodes a marker type (mov.w sp,
  // sp and mov.w pc, pc are unpredictable).
  DCHECK(0 <= type && type <= 14 && type != 13);
  emit(t32::MarkerNop(type));
}

bool Assembler::IsMovT(Instr instr) {
  return t32::IsMovt(static_cast<uint32_t>(instr));
}

bool Assembler::IsMovW(Instr instr) {
  return t32::IsMovw(static_cast<uint32_t>(instr));
}

Instr Assembler::EncodeMovwImmediate(uint32_t immediate) {
  DCHECK_LT(immediate, 0x10000);
  return static_cast<Instr>(t32::Imm16(immediate));
}

bool Assembler::IsNop(Instr instr, int type) {
  DCHECK(0 <= type && type <= 14 && type != 13);
  // Check for mov.w rx, rx where x = type.
  return static_cast<uint32_t>(instr) == t32::MarkerNop(type);
}

bool Assembler::ImmediateFitsAddrMode1Instruction(int32_t imm32) {
  uint32_t placed;
  return t32::ModifiedImmediate(static_cast<uint32_t>(imm32), &placed);
}

bool Assembler::ImmediateFitsAddrMode2Instruction(int32_t imm32) {
  // ldr/str: imm12 upwards, imm8 downwards.
  return imm32 >= -255 && imm32 <= 4095;
}
