"""Thumb-2 branch islands: conditional branches in code objects larger than ~1 MiB.

2026-11-21 (his Surface 2, v1.3.2 Thumb-2 and the profiling kit; jwa4's runs too): JetStream 3's transformersjs-bert-wasm
kills the tab with STATUS_BREAKPOINT, silently (no Crashpad dump, no WER event). Its ONNX Runtime module
(ort-wasm-simd-threaded.wasm) has two wasm functions of 745,197 and 595,474 bytes - several MB of ARM code each. A
conditional branch to a label is B<c>.W, which reaches only +-1 MiB (A32's b<cond> reaches +-32 MiB); Liftoff and
TurboFan put out-of-line code (stack check, traps, deopts) at the end of the function, so its forward conditional
branches became longer than that and t32::Branch's CHECK(FitsSigned(offset, 21)) fired while the function was being
compiled (DESIGN.md decision 7 had written that limit down). --liftoff-only and --no-liftoff crash alike (both make
more than 1 MiB of code); --no-sparkplug changes nothing.

Fix = the arm64 port's veneer pool, with positions only (no Label pointers are kept, so labels may still move -
Liftoff's Control stack moves them):
* every forward conditional branch to an unbound label stays one B<c>.W unit and is recorded by position;
* when the oldest has been pending for kBranchIslandTrigger (128 KB) bytes, the next instruction emitted while the
  constant pool is not blocked (Assembler::emit; never inside an IT block, jump table or fixed-size sequence, and never
  inside data: db/dd/dq do not check) is preceded by a branch island: a B.W over it, then one B.W veneer (+-16 MiB) for
  each pending branch older than kBranchIslandMinAge (16 KB). The veneer takes the branch's place in the label's link
  chain (branch -> veneer -> the branch's old link); bind_to() points the branch at its veneer, the veneer at the label;
* before the long sequences in which no island can go - the deopt exits (TurboFan PrepareForDeoptimizationExits, Maglev
  MaybeEmitDeoptBuiltinsCall: arm64 does the same with CheckVeneerPool) and the jump tables (TurboFan table switch,
  MacroAssembler::Switch) - CheckVeneerPoolBefore(bytes) veneers every branch that could otherwise run out of reach;
* a conditional branch backward to a label bound more than kFarConditionalBranchDistance (960 KB) away, or forward to
  a label whose previous use (the link-chain head) is that far away, is IT<cond> + B.W instead; TurboFan's table switch
  blocks the pool for one unit more because its default branch may be that two-unit form;
* ClearInternalState() (regexp AbortedCodeGeneration) drops the island records too;
* MacroAssembler::Call(Address) also checks for an island before it blocks the pool (arm64's BlockPoolsScope does the
  same): a call sequence is blocked from start to end, so a run of calls never let an island in. 2026-11-22, found
  with the host harness (F:\\cr\\rt23\\t2harness): Liftoff emits its out-of-line trap calls (8 bytes each) at the end of
  the function in source order, so the branches from the last ~128 KB of the body waited for their labels through the
  whole trap section; at 128,000+ trap sites that is more than 1 MiB (the 150,000-group test page function still
  crashed under Liftoff; TurboFan emits its out-of-line code in reverse order and was fine).
Code objects whose forward conditional branches all resolve within 128 KB (everything but huge functions) are emitted
exactly as before.
"""
from t2run import Func, Insert, Lit

ASM = "v8/src/codegen/arm/assembler-arm.cc"
ASM_H = "v8/src/codegen/arm/assembler-arm.h"
ASM_INL = "v8/src/codegen/arm/assembler-arm-inl.h"
MASM = "v8/src/codegen/arm/macro-assembler-arm.cc"
CG = "v8/src/compiler/backend/arm/code-generator-arm.cc"
MAGLEV = "v8/src/maglev/arm/maglev-assembler-arm.cc"

B_LABEL = """void Assembler::b(Label* L, Condition cond) {
  CheckBuffer();
  if (cond != al) {
    // Venetium port rule 083: B<c>.W reaches +-1 MiB. Backward to a label bound
    // further away, or forward to a label whose previous use (the head of its
    // link chain, which this branch links to) is further away, the branch is
    // IT<cond> + B.W (+-16 MiB) - only possible in code objects of more than
    // ~1 MiB. A forward B<c>.W is tracked until its label is bound, so that a
    // branch island can give it a B.W veneer in time (see EmitBranchIsland).
    if ((L->is_bound() || L->is_linked()) &&
        pc_offset() - L->pos() > kFarConditionalBranchDistance) {
      it(cond);
      BlockConstPoolFor(1);
      emit(t32::Branch(t32::kAl, false, branch_offset(L)));
      return;
    }
    if (!L->is_bound()) {
      pending_cond_branches_.insert(pc_offset());
      next_veneer_check_ =
          std::min(next_veneer_check_, pc_offset() + kBranchIslandTrigger);
    }
  }
  b(branch_offset(L), cond);
}"""

BIND_TO = """void Assembler::bind_to(Label* L, int pos) {
  DCHECK(0 <= pos && pos <= pc_offset());  // must have a valid binding position
  while (L->is_linked()) {
    int fixup_pos = L->pos();
    next(L);  // call next before overwriting link with target at fixup_pos
    // Venetium port rule 083: a conditional branch that got a branch-island
    // veneer goes to its veneer (which goes to pos); either way it is resolved.
    int target_pos = pos;
    if (!pending_cond_branches_.empty()) {
      pending_cond_branches_.erase(fixup_pos);
    }
    if (!cond_branch_veneers_.empty()) {
      auto veneer = cond_branch_veneers_.find(fixup_pos);
      if (veneer != cond_branch_veneers_.end()) {
        target_pos = veneer->second;
        cond_branch_veneers_.erase(veneer);
      }
    }
    target_at_put(fixup_pos, target_pos);
  }
  L->bind_to(pos);

  // Keep track of the last bound label so we don't eliminate any instructions
  // before a bound label.
  if (pos > last_bound_pos_) last_bound_pos_ = pos;
}"""

ISLANDS = """// Venetium port rule 083 - Thumb-2 branch islands. B<c>.W reaches +-1 MiB
// (A32's b<cond> +-32 MiB), so in a code object of more than ~1 MiB (JetStream
// 3's ONNX Runtime has wasm functions of 600-750 KB = several MB of code) a
// forward conditional branch to out-of-line code at the end CHECK-failed in
// t32::Branch. Each forward B<c>.W to an unbound label is tracked by position;
// once the oldest has been pending for kBranchIslandTrigger bytes, an island
// before the next instruction emitted while the constant pool is not blocked
// (never inside an IT block, a jump table or another blocked sequence) gives
// every branch older than kBranchIslandMinAge a B.W veneer (+-16 MiB). The
// veneer takes the branch's place in its label's link chain (branch -> veneer
// -> the branch's old link); bind_to() points the branch at its veneer and the
// veneer at the label.
void Assembler::CheckVeneerPool() {
  if (pending_cond_branches_.empty()) {
    next_veneer_check_ = kMaxInt;
    return;
  }
  if (is_const_pool_blocked()) return;  // the next emit() tries again
  int oldest = *pending_cond_branches_.begin();
  if (pc_offset() - oldest < kBranchIslandTrigger) {
    next_veneer_check_ = oldest + kBranchIslandTrigger;
    return;
  }
  EmitBranchIsland(pc_offset() - kBranchIslandMinAge);
}

// Before a sequence of blocked_bytes in which no island can go (the deopt
// exits, a jump table): veneer every pending branch whose label may lie behind
// it and would then be kBranchIslandTrigger or more away (arm64 does the same
// with CheckVeneerPool before its deopt exits).
void Assembler::CheckVeneerPoolBefore(int blocked_bytes) {
  if (pending_cond_branches_.empty() || is_const_pool_blocked()) return;
  const int veneer_up_to = pc_offset() + blocked_bytes - kBranchIslandTrigger;
  if (*pending_cond_branches_.begin() > veneer_up_to) return;
  EmitBranchIsland(veneer_up_to);
}

void Assembler::EmitBranchIsland(int veneer_up_to) {
  ASM_CODE_COMMENT_STRING(this, "Branch island");
  next_veneer_check_ = kMaxInt;  // no nested island while this one is emitted
  Label after_island;
  b(&after_island);
  while (!pending_cond_branches_.empty() &&
         *pending_cond_branches_.begin() <= veneer_up_to) {
    const int branch_pos = *pending_cond_branches_.begin();
    pending_cond_branches_.erase(pending_cond_branches_.begin());
    const int link = target_at(branch_pos);
    CheckBuffer();  // a constant pool may go here (dead code), not below
    BlockConstPoolFor(1);
    const int veneer_pos = pc_offset();
    // B.W to the branch's old link; the end of a chain links to itself.
    const int link_pos = link == branch_pos ? veneer_pos : link;
    emit(t32::Branch(t32::kAl, false,
                     link_pos - (veneer_pos + Instruction::kPcLoadDelta)));
    target_at_put(branch_pos, veneer_pos);
    cond_branch_veneers_[branch_pos] = veneer_pos;
  }
  bind(&after_island);
  next_veneer_check_ =
      pending_cond_branches_.empty()
          ? kMaxInt
          : *pending_cond_branches_.begin() + kBranchIslandTrigger;
}"""

RULES = [
    Func("branch-islands-b-label", ASM, "void Assembler::b(Label* L, Condition cond)", B_LABEL),
    Func("branch-islands-bind-to", ASM, "void Assembler::bind_to(Label* L, int pos)", BIND_TO),
    Insert("branch-islands-functions", ASM, r"^void Assembler::bind\(Label\* L\) \{", ISLANDS, where="before"),
    Lit("branch-islands-emit", ASM_INL, """void Assembler::emit(Instr x) {
  CheckBuffer();
  *reinterpret_cast<Instr*>(pc_) = T32SwapHalfwords(x);""", """void Assembler::emit(Instr x) {
  CheckBuffer();
  // Venetium port rule 083: branch islands go between instructions only, never
  // into data (db/dd/dq call CheckBuffer() too).
  MaybeCheckVeneerPool();
  *reinterpret_cast<Instr*>(pc_) = T32SwapHalfwords(x);""", expect=1),
    Insert("branch-islands-include-map", ASM_H, r"^#include <memory>$", "#include <map>", where="before",
           blank=False),
    Insert("branch-islands-include-set", ASM_H, r"^#include <memory>$", "#include <set>", where="after",
           blank=False),
    Insert("branch-islands-maybe-check", ASM_H, r"^  V8_INLINE void MaybeCheckConstPool\(\) \{$",
           """  // Venetium port rule 083: emit a branch island when a pending forward
  // B<c>.W is getting old (see Assembler::EmitBranchIsland).
  V8_INLINE void MaybeCheckVeneerPool() {
    if (V8_UNLIKELY(pc_offset() >= next_veneer_check_)) {
      CheckVeneerPool();
    }
  }

  // Venetium port rule 083: before a sequence in which no branch island can go
  // (deopt exits, a jump table), emit one now if a pending forward B<c>.W could
  // otherwise run out of reach inside it.
  void CheckVeneerPoolBefore(int blocked_bytes);""", where="before"),
    Insert("branch-islands-members", ASM_H, r"^  int first_const_pool_32_use_;$",
           """  // Venetium port rule 083 - Thumb-2 branch islands (see EmitBranchIsland).
  static constexpr int kFarConditionalBranchDistance = 960 * KB;
  static constexpr int kBranchIslandTrigger = 128 * KB;
  static constexpr int kBranchIslandMinAge = 16 * KB;
  // Forward B<c>.W branches to unbound labels, by position.
  std::set<int> pending_cond_branches_;
  // Branches that got a veneer: branch position -> veneer position.
  std::map<int, int> cond_branch_veneers_;
  // pc offset of the next island check (kMaxInt: nothing pending).
  int next_veneer_check_ = kMaxInt;
  void CheckVeneerPool();
  void EmitBranchIsland(int veneer_up_to);""", where="after"),
    Lit("branch-islands-clear-state", ASM_H, """    first_const_pool_32_use_ = -1;
    constant_pool_deadline_ = kMaxInt;
  }""", """    first_const_pool_32_use_ = -1;
    constant_pool_deadline_ = kMaxInt;
    // Venetium port rule 083: the branch-island records too.
    pending_cond_branches_.clear();
    cond_branch_veneers_.clear();
    next_veneer_check_ = kMaxInt;
  }""", expect=1),
    Lit("branch-islands-table-switch", CG, """  __ CheckConstPool(true, true);
  __ BlockConstPoolFor(case_count + 3);
  __ b(GetLabel(i.InputRpo(1)), hs);""", """  __ CheckConstPool(true, true);
  // Venetium port rule 083: no branch island can go inside the table, and in a
  // code object of more than ~1 MiB the default branch may be IT + B.W.
  __ CheckVeneerPoolBefore(static_cast<int>(case_count + 4) * kInstrSize);
  __ BlockConstPoolFor(case_count + 4);
  __ b(GetLabel(i.InputRpo(1)), hs);""", expect=1),
    Lit("branch-islands-deopt-exits-turbofan", CG, """    ZoneDeque<DeoptimizationExit*>* exits) {
  __ CheckConstPool(true, false);
}""", """    ZoneDeque<DeoptimizationExit*>* exits) {
  __ CheckConstPool(true, false);
  // Venetium port rule 083: no branch island can go between the exits (2 units
  // each, in a BlockConstPoolScope); 4 units each leaves slack.
  __ CheckVeneerPoolBefore(static_cast<int>(exits->size()) * 4 * kInstrSize);
}""", expect=1),
    Lit("branch-islands-deopt-exits-maglev", MAGLEV, """                                                 Label* lazy_deopt_entry) {
  CheckConstPool(true, false);
}""", """                                                 Label* lazy_deopt_entry) {
  CheckConstPool(true, false);
  // Venetium port rule 083: no branch island can go between the deopt exits (2
  // units each, in a BlockConstPoolScope); 4 units each leaves slack.
  CheckVeneerPoolBefore(
      static_cast<int>(eager_deopt_count + lazy_deopt_count) * 4 * kInstrSize);
}""", expect=1),
    Lit("branch-islands-switch", MASM, """  CheckConstPool(true, true);
  BlockConstPoolFor(num_labels + 3);""", """  CheckConstPool(true, true);
  // Venetium port rule 083: no branch island can go inside the table.
  CheckVeneerPoolBefore((num_labels + 3) * kInstrSize);
  BlockConstPoolFor(num_labels + 3);""", expect=1),
    Lit("branch-islands-call", MASM, """if (check_constant_pool) MaybeCheckConstPool();""", """if (check_constant_pool) {
    MaybeCheckConstPool();
    // Venetium port rule 083: and a branch island. The call sequence blocks
    // both, so a run of calls (Liftoff's out-of-line trap calls at the end of a
    // function) would never let an island in.
    MaybeCheckVeneerPool();
  }""", expect=1),
]
