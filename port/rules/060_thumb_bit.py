"""Interworking, JS side: the ALL-ODD convention (map wf_404efbad-1b2, verified site by site 10-27).

Every code entry point and every pc carries the Thumb bit (kThumbEntryBit, 1 on ARM, 0 elsewhere):
  * Code::instruction_start() and EmbeddedData::InstructionStartOf() are odd (one setter, one accessor), so the
    ~60 consumers that do pc - instruction_start() stay exact (hardware return addresses are odd too);
  * byte accesses to code (copies, icache flush, zap, relocation, disassembly, byte dumps) use the raw (even)
    address: Code::raw_instruction_start(), EmbeddedData::RawInstructionStartOf();
  * RelocInfo stores odd call/jump targets in code and returns the even byte address to the heap/GC;
  * synthesized code addresses (label addresses, exit-frame return address, code start register) are odd.
InstructionStream::instruction_start() stays the even byte address of the on-heap body.
WasmCode is the separate batch 061.
"""
from t2run import Insert, Lit

GLOBALS = "v8/src/common/globals.h"
CODE_H = "v8/src/objects/code.h"
CODE_INL = "v8/src/objects/code-inl.h"
CODE_CC = "v8/src/objects/code.cc"
ED_H = "v8/src/snapshot/embedded/embedded-data.h"
ED_INL = "v8/src/snapshot/embedded/embedded-data-inl.h"
ED_CC = "v8/src/snapshot/embedded/embedded-data.cc"
ISTREAM_INL = "v8/src/objects/instruction-stream-inl.h"
ASM = "v8/src/codegen/arm/assembler-arm.cc"
ASM_INL = "v8/src/codegen/arm/assembler-arm-inl.h"
MASM = "v8/src/codegen/arm/macro-assembler-arm.cc"

THUMB_BIT = """// Venetium Thumb-2 (Windows RT): all ARM32 generated code is T32, so every code
// entry point and every pc carries the Thumb state bit (bit 0). Byte accesses
// to code (copies, icache flushes, relocation, disassembly) use the address
// without it.
#if V8_TARGET_ARCH_ARM
constexpr Address kThumbEntryBit = 1;
#else
constexpr Address kThumbEntryBit = 0;
#endif"""

RULES = [
    Insert("thumb-bit-constant", GLOBALS, r"^constexpr intptr_t kCodeAlignmentMask = kCodeAlignment - 1;",
           THUMB_BIT, where="after"),
    # ---- Code: odd entry, raw byte address
    Lit("code-raw-decl", CODE_H, """  DECL_GETTER(instruction_start, Address)
  DECL_PRIMITIVE_ACCESSORS(instruction_size, int)""", """  DECL_GETTER(instruction_start, Address)
  // instruction_start() without the Thumb bit: the address of the first byte.
  inline Address raw_instruction_start() const;
  DECL_PRIMITIVE_ACCESSORS(instruction_size, int)""", expect=1),
    Lit("code-raw-def", CODE_INL, "Address Code::body_start() const { return instruction_start(); }",
        """Address Code::raw_instruction_start() const {
  return instruction_start() & ~kThumbEntryBit;
}

Address Code::body_start() const { return raw_instruction_start(); }""", expect=1),
    Lit("code-metadata-start", CODE_INL, """    static_assert(InstructionStream::kOnHeapBodyIsContiguous);
    return instruction_start() + instruction_size();""", """    static_assert(InstructionStream::kOnHeapBodyIsContiguous);
    return raw_instruction_start() + instruction_size();""", expect=1),
    Lit("code-set-start-odd", CODE_INL, """void Code::set_instruction_start(IsolateForSandbox isolate, Address value) {
#ifdef V8_ENABLE_SANDBOX""", """void Code::set_instruction_start(IsolateForSandbox isolate, Address value) {
  // Entry points are Thumb addresses (null stays null).
  if (value != kNullAddress) value |= kThumbEntryBit;
#ifdef V8_ENABLE_SANDBOX""", expect=1),
    Lit("code-flush-icache", CODE_CC, "FlushInstructionCache(instruction_start(), instruction_size());",
        "FlushInstructionCache(raw_instruction_start(), instruction_size());", expect=1),
    Lit("code-zap-start", CODE_CC, """    Address start = instruction_start();
    Address end = start + deoptimization_data()->DeoptExitStart().value();""",
        """    Address start = raw_instruction_start();
    Address end = start + deoptimization_data()->DeoptExitStart().value();""", expect=1),
    Lit("code-disassemble", CODE_CC, "DisassembleCodeRange(isolate, os, code, code->instruction_start(),",
        "DisassembleCodeRange(isolate, os, code, code->raw_instruction_start(),", expect=2),
    Lit("code-verify-istream", "v8/src/diagnostics/objects-debug.cc",
        "if (istream->instruction_start() == instruction_start()) {",
        "if (istream->instruction_start() == raw_instruction_start()) {", expect=1),
    Lit("code-verify-istream-eq", "v8/src/diagnostics/objects-debug.cc",
        "CHECK_EQ(istream->instruction_start(), instruction_start());",
        "CHECK_EQ(istream->instruction_start(), raw_instruction_start());", expect=1),
    # ---- byte consumers of Code starts
    Lit("profiling-trampoline-copy", "v8/src/builtins/builtins.cc",
        "desc.buffer = reinterpret_cast<uint8_t*>(code->instruction_start());",
        "desc.buffer = reinterpret_cast<uint8_t*>(code->raw_instruction_start());", expect=1),
    Lit("setup-builtins-flush", "v8/src/builtins/setup-builtins-internal.cc",
        "FlushInstructionCache(code->instruction_start(),",
        "FlushInstructionCache(code->raw_instruction_start(),", expect=1),
    Lit("code-reference-start", "v8/src/codegen/code-reference.cc",
        """  Address instruction_start() const { return code->instruction_start(); }
  Address instruction_end() const { return code->instruction_end(); }""",
        """  // Code bytes: the address without the Thumb bit.
  Address instruction_start() const { return code->raw_instruction_start(); }
  Address instruction_end() const {
    return code->raw_instruction_start() + code->instruction_size();
  }""", expect=1),
    Lit("embedded-copy", ED_CC, "std::memcpy(dst, reinterpret_cast<uint8_t*>(code->instruction_start()),",
        "std::memcpy(dst, reinterpret_cast<uint8_t*>(code->raw_instruction_start()),", expect=1),
    Lit("js-entry-stubs-range", "v8/src/api/api.cc",
        "reinterpret_cast<const void*>(js_entry->instruction_start());",
        "reinterpret_cast<const void*>(js_entry->raw_instruction_start());", expect=1),
    Lit("perf-jit-bytes", "v8/src/diagnostics/perf-jit.cc",
        "uint8_t* code_pointer = reinterpret_cast<uint8_t*>(code->instruction_start());",
        "uint8_t* code_pointer = reinterpret_cast<uint8_t*>(code->raw_instruction_start());", expect=1),
    Lit("low-level-log-bytes", "v8/src/logging/log.cc",
        "LogWriteBytes(reinterpret_cast<const char*>(code->InstructionStart()),",
        "LogWriteBytes(\n      reinterpret_cast<const char*>(code->InstructionStart() & ~kThumbEntryBit),",
        expect=1, desc="AbstractCode bytes (WasmCode::instruction_start is already raw)"),
    Lit("perfetto-bytes", "v8/src/tracing/perfetto-logger.cc",
        "reinterpret_cast<const uint8_t*>(code->instruction_start()),",
        "reinterpret_cast<const uint8_t*>(code->raw_instruction_start()),", expect=4,
        within=r"^(?!.*WasmCode)", desc="Code bytes only (WasmCode::instruction_start is already raw)"),
    # ---- embedded blob: odd entries, raw bytes
    Insert("embedded-raw-decl", ED_H, r"^  inline Address InstructionStartOf\(Builtin builtin\) const;",
           """  // InstructionStartOf() without the Thumb bit: the address of the first byte.
  inline Address RawInstructionStartOf(Builtin builtin) const;""", where="after", blank=False),
    Lit("embedded-start-odd", ED_INL, """  const uint8_t* result = RawCode() + desc.instruction_offset;
  DCHECK_LT(result, code_ + code_size_);
  return reinterpret_cast<Address>(result);
}

Address EmbeddedData::InstructionEndOf(Builtin builtin) const {""",
        """  const uint8_t* result = RawCode() + desc.instruction_offset;
  DCHECK_LT(result, code_ + code_size_);
  return reinterpret_cast<Address>(result) | kThumbEntryBit;
}

Address EmbeddedData::RawInstructionStartOf(Builtin builtin) const {
  return InstructionStartOf(builtin) & ~kThumbEntryBit;
}

Address EmbeddedData::InstructionEndOf(Builtin builtin) const {""", expect=1),
    Lit("embedded-lookup-dchecks", ED_CC, """  DCHECK_LT(address,
            InstructionStartOf(builtin) + PaddedInstructionSizeOf(builtin));
  DCHECK_GE(address, InstructionStartOf(builtin));""", """  DCHECK_LT(address,
            RawInstructionStartOf(builtin) + PaddedInstructionSizeOf(builtin));
  DCHECK_GE(address, RawInstructionStartOf(builtin));""", expect=1),
    Lit("embedded-file-bytes", "v8/src/snapshot/embedded/embedded-file-writer.cc",
        "reinterpret_cast<const uint8_t*>(blob->InstructionStartOf(builtin));",
        "reinterpret_cast<const uint8_t*>(blob->RawInstructionStartOf(builtin));", expect=1),
    Lit("embedded-win-unwind-offsets", "v8/src/snapshot/embedded/platform-embedded-file-writer-win.cc",
        "blob->InstructionStartOf(builtin) -", "blob->RawInstructionStartOf(builtin) -", expect=2,
        desc="PDATA offsets are byte offsets"),
    Lit("reloc-iterator-embedded", "v8/src/codegen/reloc-info.cc",
        ": RelocIterator(embedded_data->InstructionStartOf(code->builtin_id()),",
        ": RelocIterator(embedded_data->RawInstructionStartOf(code->builtin_id()),", expect=1),
    # ---- entry address -> InstructionStream
    Lit("istream-from-target", ISTREAM_INL, """  Tagged<HeapObject> code =
      HeapObject::FromAddress(address - InstructionStream::kHeaderSize);""", """  Tagged<HeapObject> code = HeapObject::FromAddress(
      (address & ~kThumbEntryBit) - InstructionStream::kHeaderSize);""", expect=1),
    Lit("istream-from-entry", ISTREAM_INL, """  Tagged<HeapObject> code =
      HeapObject::FromAddress(code_entry - InstructionStream::kHeaderSize);""", """  Tagged<HeapObject> code = HeapObject::FromAddress(
      (code_entry & ~kThumbEntryBit) - InstructionStream::kHeaderSize);""", expect=1),
    Lit("remembered-set-code-entry", "v8/src/heap/remembered-set.h",
        "base::Memory<Address>(entry_address) = code->instruction_start();",
        "base::Memory<Address>(entry_address) =\n          code->instruction_start() | kThumbEntryBit;", expect=1),
    # ---- RelocInfo (ARM): code targets stored odd in code, returned as byte addresses to the heap
    Lit("reloc-target-address", ASM_INL, """  DCHECK(IsCodeTargetMode(rmode_) || IsWasmCall(rmode_) ||
         IsWasmStubCall(rmode_));
  return Assembler::target_address_at(pc_, constant_pool_);""", """  DCHECK(IsCodeTargetMode(rmode_) || IsWasmCall(rmode_) ||
         IsWasmStubCall(rmode_));
  // Stored with the Thumb bit (a call/jump target); the heap wants the byte
  // address.
  return Assembler::target_address_at(pc_, constant_pool_) & ~kThumbEntryBit;""", expect=1),
    Lit("reloc-off-heap-target", ASM_INL, """  DCHECK(IsOffHeapTarget(rmode_));
  return Assembler::target_address_at(pc_, constant_pool_);""", """  DCHECK(IsOffHeapTarget(rmode_));
  return Assembler::target_address_at(pc_, constant_pool_) & ~kThumbEntryBit;""", expect=1),
    Lit("reloc-set-target", "v8/src/codegen/reloc-info.cc", """  DCHECK(IsCodeTargetMode(rmode_) || IsNearBuiltinEntry(rmode_) ||
         IsWasmCall(rmode_));
  Assembler::set_target_address_at(pc_, constant_pool_, target,
                                   &jit_allocation_, icache_flush_mode);""", """  DCHECK(IsCodeTargetMode(rmode_) || IsNearBuiltinEntry(rmode_) ||
         IsWasmCall(rmode_));
  // A call/jump target: stored with the Thumb bit (branch forms drop it).
  Assembler::set_target_address_at(pc_, constant_pool_,
                                   target | kThumbEntryBit, &jit_allocation_,
                                   icache_flush_mode);""", expect=1),
    Lit("reloc-set-off-heap", "v8/src/codegen/reloc-info.cc", """  DCHECK(IsCodeTargetMode(rmode_));
  Assembler::set_target_address_at(pc_, constant_pool_, target, nullptr,
                                   icache_flush_mode);""", """  DCHECK(IsCodeTargetMode(rmode_));
  Assembler::set_target_address_at(pc_, constant_pool_, target | kThumbEntryBit,
                                   nullptr, icache_flush_mode);""", expect=1),
    # ---- synthesized code addresses in generated code
    # (mov_label_offset/target_at_put add kThumbEntryBit in 030's bodies; the exit-frame lr in 040.)
    Lit("code-start-register", MASM, """  // We can use the register pc - 8 for the address of the current instruction.
  sub(dst, pc, Operand(pc_offset() + Instruction::kPcLoadDelta));""",
        """  // pc reads as the current instruction + 4; the code start is a Thumb address.
  sub(dst, pc,
      Operand(pc_offset() + Instruction::kPcLoadDelta - kThumbEntryBit));""", expect=1, code_only=False),
    Lit("code-start-register-load", MASM,
        "  int offset = InstructionStream::kCodeOffset - InstructionStream::kHeaderSize;",
        "  int offset = InstructionStream::kCodeOffset - InstructionStream::kHeaderSize -\n"
        "               kThumbEntryBit;  // the register holds a Thumb address", expect=1),
    # ---- lazy deopt patching writes bytes at a return address
    Lit("deopt-patch-to-jump", "v8/src/deoptimizer/arm/deoptimizer-arm.cc", """void Deoptimizer::PatchToJump(Address pc, Address new_pc) {
  int offset = new_pc - (pc + Instruction::kPcLoadDelta);""", """void Deoptimizer::PatchToJump(Address pc, Address new_pc) {
  // Both are Thumb addresses: patch the bytes at pc, branch to new_pc's bytes.
  pc &= ~kThumbEntryBit;
  new_pc &= ~kThumbEntryBit;
  int offset = new_pc - (pc + Instruction::kPcLoadDelta);""", expect=1),
    # ---- sampler: pcs of T32 code are reported with the Thumb bit, like return addresses
    Lit("sampler-thumb-pc", "v8/src/libsampler/sampler.cc", """    // Venetium 0025: Windows on ARM32 uses r11 as the frame pointer.
    state.pc = reinterpret_cast<void*>(context.Pc);""", """    // Venetium 0025: Windows on ARM32 uses r11 as the frame pointer.
    // Thumb code (CPSR.T, bit 5) is reported with the Thumb bit, as V8's code
    // starts and return addresses are.
    state.pc = reinterpret_cast<void*>(context.Pc | ((context.Cpsr >> 5) & 1));""", expect=1, code_only=False),
]
