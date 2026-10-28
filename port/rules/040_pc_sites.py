"""Call sites whose A32 idiom depends on how pc reads or writes (pc reads as instr + 8 in A32, + 4 in T32; T32
writes pc only through bx/blx/ldr/pop/16-bit mov and add). Census groups pc-dest / pc-src; everything else in
those groups already works through the T32 encoders (mov pc, lr; ldr pc, [sp]; vmrs APSR_nzcv uses pc as Rt;
GetLabelAddress / ComputeCodeStartAddress via adr; regexp computed jumps via scratch + mov pc).
"""
from t2run import Lit

MASM = "v8/src/codegen/arm/macro-assembler-arm.cc"
CODEGEN = "v8/src/compiler/backend/arm/code-generator-arm.cc"

RULES = [
    # Jump tables. A32: add<lo> pc, pc, value lsl #2 ; b default ; table (pc = add + 8 = table). T32 cannot predicate
    # the 16-bit add pc, so the default branch goes first: b<hs> default ; [lsl] ; add pc, idx (pc = unit + 4 =
    # table) ; table. The table entries stay b.w <label> (4 bytes each).
    Lit("switch-table", MASM, """  // This {cmp} might still emit a constant pool entry.
  cmp(value, Operand(num_labels));
  // Ensure to emit the constant pool first if necessary.
  CheckConstPool(true, true);
  BlockConstPoolFor(num_labels + 2);
  add(pc, pc, Operand(value, LSL, 2), LeaveCC, lo);
  b(&fallthrough);
  for (int i = 0; i < num_labels; ++i) {""", """  // This {cmp} might still emit a constant pool entry.
  cmp(value, Operand(num_labels));
  // Ensure to emit the constant pool first if necessary.
  CheckConstPool(true, true);
  BlockConstPoolFor(num_labels + 3);
  b(&fallthrough, hs);
  // pc reads as the unit after the add pc: the first table entry. The shifted
  // index needs a register: the caller's scratch, else a scratch register of
  // our own (Sparkplug passes no_reg - A32's add pc, pc, value, lsl #2 needed
  // none; no_reg would encode as pc here and make the jump target itself).
  {
    UseScratchRegisterScope temps(this);
    Register index = scratch.is_valid() ? scratch : temps.Acquire();
    lsl(index, value, Operand(2));
    add(pc, pc, Operand(index));
  }
  for (int i = 0; i < num_labels; ++i) {""", expect=1, code_only=False, desc="MacroAssembler::Switch"),
    Lit("codegen-table-switch", CODEGEN, """  // This {cmp} might still emit a constant pool entry.
  __ cmp(input, Operand(case_count));
  // Ensure to emit the constant pool first if necessary.
  __ CheckConstPool(true, true);
  __ BlockConstPoolFor(case_count + 2);
  __ add(pc, pc, Operand(input, LSL, 2), LeaveCC, lo);
  __ b(GetLabel(i.InputRpo(1)));""", """  // This {cmp} might still emit a constant pool entry.
  __ cmp(input, Operand(case_count));
  // Ensure to emit the constant pool first if necessary.
  __ CheckConstPool(true, true);
  __ BlockConstPoolFor(case_count + 3);
  __ b(GetLabel(i.InputRpo(1)), hs);
  // ip = input << 2, then add pc, ip: pc reads as the first table entry.
  __ add(pc, pc, Operand(input, LSL, 2));""", expect=1, code_only=False,
        desc="CodeGenerator::AssembleArchTableSwitch"),
    # StoreReturnAddressAndCall: adr lr, #8 = this + 4 + 8 = after adr, str and the [nop|blx] unit.
    Lit("store-return-address", MASM, """  // Compute the return address in lr to return to after the jump below. The pc
  // is already at '+ 8' from the current instruction; but return is after three
  // instructions, so add another 4 to pc to get the return address.
  Assembler::BlockConstPoolScope block_const_pool(this);
  add(lr, pc, Operand(4));""", """  // Compute the return address in lr to return to after the jump below. The pc
  // reads as the current instruction + 4 (T32); the return is after three
  // 4-byte units (adr, str, [nop | blx]), so add another 8.
  Assembler::BlockConstPoolScope block_const_pool(this);
  add(lr, pc, Operand(2 * kInstrSize + kThumbEntryBit));""", expect=1, code_only=False),
]
