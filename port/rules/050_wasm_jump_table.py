"""wasm JumpTableAssembler (ARM section of a generic file): raw A32 words -> T32 ldr.w literal words, stored in
memory order. Slot sizes are unchanged (lazy compile 16 bytes, jump 8 bytes); the data words stay where they were.
"""
from t2run import Lit

JTA = "v8/src/wasm/jump-table-assembler.cc"

RULES = [
    Lit("wasm-lazy-compile-slot", JTA, """  // Note that below, [pc] points to the instruction after the next.
  const uint32_t inst[kLazyCompileTableSlotSize / 4] = {
      0xe59f4000,  // ldr r4, [pc]
      0xe59ff000,  // ldr pc, [pc]
      0x00000000,  // func_index
      0x00000000,  // target
  };""", """  // T32: pc reads as the instruction + 4, so [pc, #4] is the word after the
  // next instruction. Instruction words are stored in memory order.
  const uint32_t inst[kLazyCompileTableSlotSize / 4] = {
      static_cast<uint32_t>(
          T32SwapHalfwords(t32::LoadLiteral(t32::kLdr, 4, 4))),  // ldr.w r4, [pc, #4]
      static_cast<uint32_t>(
          T32SwapHalfwords(t32::LoadLiteral(t32::kLdr, 15, 4))),  // ldr.w pc, [pc, #4]
      0x00000000,  // func_index
      0x00000000,  // target
  };""", expect=1, code_only=False, region="arm",
        oracle=[("ldr.w r4, [pc, #4]", 0xF8DF4004), ("ldr.w pc, [pc, #4]", 0xF8DFF004)]),
    Lit("wasm-jump-slot", JTA, """  // Load from [pc + kInstrSize] to pc. Note that {pc} points two instructions
  // after the currently executing one.
  const uint32_t inst[kJumpTableSlotSize / kInstrSize] = {
      0xe51ff004,  // ldr pc, [pc, -4]
      0x00000000,  // target
  };""", """  // Load the next word to pc. T32: {pc} points to the next instruction
  // (instruction words are stored in memory order).
  const uint32_t inst[kJumpTableSlotSize / kInstrSize] = {
      static_cast<uint32_t>(
          T32SwapHalfwords(t32::LoadLiteral(t32::kLdr, 15, 0))),  // ldr.w pc, [pc]
      0x00000000,  // target
  };""", expect=1, code_only=False, region="arm",
        oracle=[("ldr.w pc, [pc, #0]", 0xF8DFF000)]),
]
