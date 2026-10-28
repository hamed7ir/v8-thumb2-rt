"""Interworking, wasm side (all-odd, see 060). WasmCode::instruction_start() stays the address of the first byte (a
map key and byte pointer in ~40 places of wasm-code-manager.cc); WasmCode::entry() adds the Thumb bit and is used
wherever a wasm code address is jumped to or is the base of a pc offset (pcs are odd). Call targets stored in code,
jump-table slots and the code pointer table get the bit at their single store points.
"""
from t2run import Insert, Lit, Re

WCM_H = "v8/src/wasm/wasm-code-manager.h"
FRAMES = "v8/src/execution/frames.cc"
DEBUG = "v8/src/wasm/wasm-debug.cc"
EXTREF = "v8/src/wasm/wasm-external-refs.cc"
DEOPT = "v8/src/deoptimizer/deoptimizer.cc"

RULES = [
    Insert("wasm-code-entry", WCM_H,
           r"^  Address instruction_start\(\) const \{\n    return reinterpret_cast<Address>\(instructions_\);\n  \}",
           """  // instruction_start() with the Thumb bit (kThumbEntryBit): the address to
  // call or jump to, and the base of pc offsets (pcs carry the bit too).
  Address entry() const { return instruction_start() | kThumbEntryBit; }""", where="after", blank=False),
    Lit("wasm-jump-table-start", WCM_H, "return main_jump_table_ ? main_jump_table_->instruction_start()",
        "return main_jump_table_ ? main_jump_table_->entry()", expect=1,
        desc="jump_table_start + slot offset is a jump target (WasmCompileLazy)"),
    # pc - start: wasm frames
    Re("wasm-frame-pc-offsets", FRAMES,
       r"\b(pc\(\)|maybe_unauthenticated_pc\(\)|callee_pc\(\))(\s*-\s*)((?:top_summary\.code\(\)|code|wasm_code))"
       r"->instruction_start\(\)", r"\1\2\3->entry()", expect=5, within=r"^WasmFrame::",
       done=r"->entry\(\)"),
    Lit("wasm-safepoint-table", "v8/src/codegen/safepoint-table.cc", """    : SafepointTable(
          code->instruction_start(),
          code->instruction_start() + code->safepoint_table_offset()) {}""", """    : SafepointTable(
          code->entry(),
          code->instruction_start() + code->safepoint_table_offset()) {}""", expect=1),
    Lit("wasm-found-handler", "v8/src/execution/isolate.cc",
        "return FoundHandler(iter, {}, wasm_code->instruction_start(), offset,",
        "return FoundHandler(iter, {}, wasm_code->entry(), offset,", expect=1),
    Lit("wasm-deopt-liftoff-pc", DEOPT,
        "Address pc = wasm_code->instruction_start() + liftoff_description->pc_offset;",
        "Address pc = wasm_code->entry() + liftoff_description->pc_offset;", expect=1),
    Lit("wasm-deopt-shadow-stack", DEOPT, "shadow_stack.push(wasm_code->instruction_start() +",
        "shadow_stack.push(wasm_code->entry() +", expect=1),
    Lit("wasm-debug-old-pc", DEBUG, "int pc_offset = static_cast<int>(frame->pc() - old_code->instruction_start());",
        "int pc_offset = static_cast<int>(frame->pc() - old_code->entry());", expect=1),
    Lit("wasm-debug-new-pc-1", DEBUG, "return wasm_code->instruction_start() + it.code_offset() +",
        "return wasm_code->entry() + it.code_offset() +", expect=1),
    Lit("wasm-debug-new-pc-2", DEBUG, "return wasm_code->instruction_start() + code_offset + call_instruction_size;",
        "return wasm_code->entry() + code_offset + call_instruction_size;", expect=1),
    Lit("wasm-debug-pc-offset", DEBUG, "pc_offset(static_cast<int>(pc - code->instruction_start())),",
        "pc_offset(static_cast<int>(pc - code->entry())),", expect=1),
    Lit("wasm-extref-call-pc", EXTREF, "wasm_code->instruction_start() + call_offset == target_pc",
        "wasm_code->entry() + call_offset == target_pc", expect=2),
    Lit("wasm-extref-jmpbuf", EXTREF, "to->jmpbuf()->pc = wasm_code->instruction_start() + handler_offset;",
        "to->jmpbuf()->pc = wasm_code->entry() + handler_offset;", expect=1),
    # a new continuation starts in the stack entry wrapper: WasmFXResume does bx to jmpbuf->pc
    Lit("wasm-stack-entry-pc", "v8/src/runtime/runtime-wasm.cc",
        "stack->jmpbuf()->pc = wrapper->code()->instruction_start();",
        "stack->jmpbuf()->pc = wrapper->code()->entry();", expect=1),
    # call targets stored in code (RelocInfo), getters give the byte address back
    Lit("wasm-call-address-get", "v8/src/codegen/reloc-info.cc", """  DCHECK_EQ(rmode_, WASM_CALL);
  return Assembler::target_address_at(pc_, constant_pool_);""", """  DCHECK_EQ(rmode_, WASM_CALL);
  return Assembler::target_address_at(pc_, constant_pool_) & ~kThumbEntryBit;""", expect=1),
    Lit("wasm-call-address-set", "v8/src/codegen/reloc-info.cc", """  DCHECK_EQ(rmode_, WASM_CALL);
  Assembler::set_target_address_at(pc_, constant_pool_, address,
                                   &jit_allocation_, SKIP_ICACHE_FLUSH);""", """  DCHECK_EQ(rmode_, WASM_CALL);
  Assembler::set_target_address_at(pc_, constant_pool_, address | kThumbEntryBit,
                                   &jit_allocation_, SKIP_ICACHE_FLUSH);""", expect=1),
    Lit("wasm-stub-call-address-get", "v8/src/codegen/reloc-info.cc", """  DCHECK_EQ(rmode_, WASM_STUB_CALL);
  return Assembler::target_address_at(pc_, constant_pool_);""", """  DCHECK_EQ(rmode_, WASM_STUB_CALL);
  return Assembler::target_address_at(pc_, constant_pool_) & ~kThumbEntryBit;""", expect=1),
    Lit("wasm-stub-call-address-set", "v8/src/codegen/reloc-info.cc", """  DCHECK_EQ(rmode_, WASM_STUB_CALL);
  Assembler::set_target_address_at(pc_, constant_pool_, address,
                                   &jit_allocation_, SKIP_ICACHE_FLUSH);""", """  DCHECK_EQ(rmode_, WASM_STUB_CALL);
  Assembler::set_target_address_at(pc_, constant_pool_, address | kThumbEntryBit,
                                   &jit_allocation_, SKIP_ICACHE_FLUSH);""", expect=1),
    # jump-table slots: the loaded word is jumped to with ldr pc (interworking)
    Lit("wasm-jump-slot-target", "v8/src/wasm/jump-table-assembler.cc", "emit<uint32_t>(target, kRelaxedStore);",
        "emit<uint32_t>(target | kThumbEntryBit, kRelaxedStore);", expect=1, region="arm"),
    Lit("wasm-lazy-slot-target", "v8/src/wasm/jump-table-assembler.cc", "emit<Address>(lazy_compile_target);",
        "emit<Address>(lazy_compile_target | kThumbEntryBit);", expect=1, region="arm"),
    # code pointer table: entries are called with blx
    Lit("wasm-cpt-make", "v8/src/wasm/wasm-code-pointer-table-inl.h", """                                                     uint64_t signature_hash) {
  entrypoint_.store(entrypoint, std::memory_order_relaxed);""", """                                                     uint64_t signature_hash) {
  entrypoint_.store(entrypoint | kThumbEntryBit, std::memory_order_relaxed);""", expect=1),
    Lit("wasm-cpt-update", "v8/src/wasm/wasm-code-pointer-table-inl.h", """  SBXCHECK_EQ(signature_hash_, signature_hash);
#endif
  entrypoint_.store(entrypoint, std::memory_order_relaxed);""", """  SBXCHECK_EQ(signature_hash_, signature_hash);
#endif
  entrypoint_.store(entrypoint | kThumbEntryBit, std::memory_order_relaxed);""", expect=1, code_only=False),
    Lit("wasm-cpt-equal", "v8/src/wasm/wasm-code-pointer-table.cc",
        "return at(index.value()).GetEntrypointWithoutSignatureCheck() == address;",
        "return at(index.value()).GetEntrypointWithoutSignatureCheck() ==\n         (address | kThumbEntryBit);",
        expect=1),
]
