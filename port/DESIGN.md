# Venetium full-JIT Thumb-2 V8 (Windows RT 8.0) - design (living document, started 2026-10-26)

## Why
- Windows RT 8.0/8.1 cannot execute A32 (ARM-mode) code at all - device-confirmed 2026-10-26 (the d8 A/B kit failed there).
- V8's ARM port emits only A32: the 2.2 MB embedded builtins blob and every JIT tier. So *all* V8-generated code must become T32.
- His direction: **full JIT from the start** (Ignition + Sparkplug + Maglev + TurboFan + regexp JIT + Liftoff/Turboshaft wasm),
  no jitless milestone. Scope of the port overall: ARM32 Windows RT 8.0 (API gap + this V8 port); x86 Win7/8 later.

## Where
- Source tree: `D:\cr\rt8\src` (copy of the release tree `F:\cr\src` without `out\`; the release line stays untouched).
- Tools, docs, oracle: `F:\cr\thumb2` (`tools\t32oracle.py`).
- Toolchain: rt2.1 (`F:\cr\rt21-cr`) - C++ is already compiled as Thumb-2; nothing changes there.

## Method (his direction 2026-10-26: "replace fast to thumb2", no "or"/"if" for A32)
**Thumb-2 ONLY, direct replacement.** Every A32 encoding / decoding / pattern in V8's ARM code is replaced by its T32
equivalent in place - no `#if`, no runtime flag, no A32 fallback, no translation layer, no alternative variants. One fixed
rule per pattern (below), applied mechanically everywhere it occurs, each replacement verified against the oracle.

| A32 pattern | T32 replacement (the only rule) |
|---|---|
| encoder bit layout (data-processing, ld/st, mul/div, ldm/stm, branches, misc) | the T32 wide encoding of the same instruction |
| predicated non-branch instruction | `IT <cond>` + the instruction |
| register-shifted-register operand | shift into the scratch register, then the operation |
| immediate that is not a T32 modified immediate | `movw`/`movt` into scratch + the operation |
| ld/st offset below -255 | existing scratch-register path |
| PC read (`kPcLoadDelta = 8`) | 4 (literal/ADR base = Align(PC, 4)) |
| `svc #imm24` stop/trap | `udf.w #imm16` |
| writes to PC (`mov pc, x` / `ldr pc` / `ldm ..pc`) | `bx x` / `pop {pc}` / `ldr pc` (T32 forms) |
| code entry addresses / return addresses | Thumb bit 0 set |
| VFP / coprocessor word | same word with cond = AL; predicated -> `it(cond)` + AL word (proven 13/13) |
| NEON data-processing word | A32 `1111 001U` -> T32 `111U 1111` (base const + U bit 24 -> 28) (proven 91/91) |
| NEON load/store word | A32 `F4` -> T32 `F9` |
| data written through `emit()` (pool entries, patched addresses) | `dd()` (never swapped) |
| decoders/patchers reading A32 fields | read the T32 fields |

## Runner (how every rule is applied - nobody edits the tree by hand)
`tools\t2run.py` + `rules\NNN_*.py`. The port = pristine tree + the rules, applied in file order:
- `census` lists every A32 construct in one pass (`rules\000_census.py`, 33 detectors, report in `runs\`);
  `grep REGEX [--within FUNC-REGEX] [--region arm]`, `funcs`, `show file:line` read only the lines that matter.
- Rule kinds: `Lit`, `Re`, `Func` (whole definition by signature), `Insert` (new code at an anchor), `Flag` (census).
  Each replace rule states its exact site count (`expect`) and may be scoped to functions (`within=`); a count that
  differs, a failing oracle claim (`oracle=[("asm", 0xT32)]`, checked with llvm-mc) or a non-idempotent result
  stops the run and NOTHING is written.
- `scan` / `diff` (dry run + patch) -> `apply` (pristine copy kept in `pristine\`, `runs\journal.jsonl`)
  -> `delta` (`runs\thumb2-delta.patch` = the cumulative port patch vs F:\cr\src).
- Checks: `tools\neon_rule_check.py` (the NEON/VFP rules vs llvm-mc A32+T32), `tools\check_braceless.py RULE` (a
  rule that splits one statement into two must not land under a brace-less if/else), `tools\test_mask.py`.
- Applied so far: `010_memory_boundary` (12 rules, byte-identical to the hand edits + the static-accessor fix:
  `Instruction::Name(Instr)` wraps a canonical value, so it is stored in memory order first), `020_neon_vfp`
  (19 rules: NEON base/U-bit/ld-st, vmov imm, IT unit, 49 VFP/coproc + 10 vcvt predicated sites, pool/patch
  data -> dd, kPcLoadDelta 4, kMaxPCRelativeCodeRangeInMB 16), `030_t32_core` (47 rules: new header
  `src/codegen/arm/t32-encodings-arm.h` with every T32 bit layout; 79 Assembler bodies (AddrMode1-5,
  LoadStoreT32, branches, label links, movw/movt, multiply/bitfield/extend/misc, exclusives, barriers, traps,
  predicates, inl.h patchers); 36 dead A32 predicates/patterns + declarations deleted; pool marker = udf.w;
  ARMv7 baseline in ProbeImpl).
- `040_pc_sites` (3): jump tables (MacroAssembler::Switch, TF table switch) = `b<hs> default; lsl; [add pc, idx |
  nop16]; table of b.w`; StoreReturnAddressAndCall adr offset. `050_wasm_jump_table` (2): the raw A32 words of the
  wasm jump-table slots -> T32 `ldr.w pc/r4, [pc, #n]` (memory order). AddrMode1 writes to pc: mov pc,#imm = ldr pc
  literal; mov pc, rm = 16-bit unit; add pc, pc, rm<<n = jump table; anything else = scratch + mov pc.
- `060_thumb_bit` (38) + `061_thumb_bit_wasm` (22): the ALL-ODD convention, every site read (221+59
  instruction_start uses classified): kThumbEntryBit (globals.h), Code::raw_instruction_start(),
  EmbeddedData::RawInstructionStartOf(), WasmCode::entry(); odd at the producers (Code setter, InstructionStartOf,
  RelocInfo code/wasm-call setters, jump-table slot targets, code pointer table stores, remembered-set code
  entries, label offsets (+1 in mov_label_offset), exit-frame lr, code start register, sampler pc from CPSR.T);
  raw at byte consumers (copies, flushes, zap, disassembly, CodeReference, RelocIterator over the blob,
  perf/log/perfetto bytes, Win PDATA offsets, FromTargetAddress/FromEntryAddress, PatchToJump).
- `070_root_bias` (3): kRootRegisterBias 4095 -> 256 (T32 reach -255..+4095; map: 87.7% of root accesses stay
  one instruction), deopt-exit assert checks that window, stale "never THUMB" comments.
- **One owner per text**: a rule must not edit text another rule produces (030's FuncSet owns whole functions;
  040 owns the exit-frame lr line). `t2run.py replay` proves it: pristine + all rules in order == the tree
  (10-27: 143 rules, 39 files + 1 new, IDENTICAL).
- WasmCode::instruction_start() is already raw: byte-dump rules use `within=r"^(?!.*WasmCode)"`.
- Runner fix 10-27: find_functions blanks preprocessor directives (macro bodies with braces hid every function of
  e.g. wasm-objects.cc); `grep --within "."` now only labels, never drops matches.
- Tests: `tests\t32enc\run_t32enc.py [--tree]` compiles t32-encodings-arm.h standalone (MSVC) and checks 12,572
  encodings against llvm-mc + 20 decoder round trips (all pass). `tools\compile_check.py <obj>|--arm` compiles
  single TUs of this tree with the ARM32 release flags (minus PGO/LTO) and F:\cr\src\out\arm-pgo\gen headers,
  one clang-cl process at a time (no gn/ninja build needed). `tools\preview.py` shows a rule's result in memory.

## Decided (with evidence)
1. **Re-encode in place inside V8's ARM `Assembler`**, keeping its API (`Condition`, `Operand`, `MemOperand`, `Label`,
   `__ mov(..., LeaveCC, eq)`), emitting **only 32-bit wide T32 forms** so `kInstrSize = 4` stays valid. Same shape as
   Veresov's 2013 V8 Thumb backend (BSD) and Varan's SpiderMonkey port. Narrow forms can come later (~25-30% smaller code).
2. **Instruction word representation: canonical ARM ARM form `(hw1 << 16) | hw2`** everywhere inside V8 (constructors,
   predicates, decoders). Only `emit()`, `instr_at()`, `instr_at_put()` (and raw patch writers) swap halfwords, because in
   memory hw1 is at the lower address. Oracle: `mov.w r0,#1` = bytes `4f f0 01 00` = canonical `0xF04F0001`.
3. **VFP reuse:** a VFP instruction's canonical T32 word equals its A32 word with cond = AL (`vadd.f64 d0,d1,d2` =
   `0xEE310B02` in both), so the existing ~2,700 lines of VFP encoders are reused through the emit swap. Predicated VFP
   becomes IT + VFP. NEON: A32 top byte F2/F3/F4 -> T32 EF/FF/F9 (research probe), otherwise identical.
4. **Predicated non-branch instructions -> `it(cond)` + instruction** (the only rule). `it(cond)` emits ONE 4-byte
   unit `[nop16][it<cond>]` (canonical `0xBF00BFc8`) and blocks the constant pool for 2 slots, so every 32-bit
   instruction stays 4-byte aligned and every unit is `kInstrSize`; `al` emits nothing. A predicated instruction is
   therefore 2 slots: sequence-size code that counts predicated instructions must count the IT unit.
   Conditional branches need no IT (B<c>.W); conditional far code targets = IT + B.W.
5. **Register-shifted-register operands** (`add r0,r1,r2,lsl r3`) have no T32 form (oracle: rejected) -> split with the
   scratch register (`lsl.w ip, r2, r3; add.w r0, r1, ip`).
6. **Traps:** `svc #0xffffff` (A32 stop) -> 32-bit `udf.w #imm16` (`udf.w #0` = `0xF7F0A000`) to keep one slot.
7. **Branch ranges:** `B<c>.W` +-1 MiB, `B.W`/`BL` +-16 MiB -> `kMaxPCRelativeCodeRangeInMB = 16`. Label
   branches with a condition are B<c>.W (a function > 1 MiB with a far conditional branch CHECK-fails); a
   conditional code-target jump/call is `it(cond)` + B.W/BL with the reloc on the branch itself.
8. **PC reads:** `kPcLoadDelta = 4`; literal/ADR bases are `Align(PC, 4)` = PC because every 32-bit instruction
   is 4-byte aligned. pc as an operand = ADR (addw/subw rd, pc, #imm12) or `adr scratch, #0` first.
9. **16-bit instructions** always fill one 4-byte unit with a partner: `[nop16|IT][bx/blx/mov pc]` (return
   address = next unit), `[add pc, rm][nop16]` (jump tables: pc = unit + 4), `[nop16][udf #0xfe]` (bkpt = the
   Windows ARM breakpoint, as Venetium 0017 uses for C++).
10. **sp/pc legality:** T32 reads sp as rn only in add/sub/cmp/cmn, writes it only from add/sub (rn == sp) and
    mov (register), reads it as rm only in mov; AddrMode1 routes every other use through a scratch (rd when free).
    ldm/stm: ia/db, >= 2 registers, no sp, no pc+lr in loads, no pc in stores; anything else (and single-register
    push/pop) becomes ldr/str with the same layout. pc is never stored: `str pc` is UNPREDICTABLE in T32 (Tegra's
    Cortex-A9/A15 store it, Qualcomm Krait / Cortex-A7 trap it = the v1.3 crash on the Lumia 2520 / 735), so pc's slot
    gets lr (only DeoptimizationEntry stores pc, and the deoptimizer takes the return address from lr) and AddrMode2
    CHECKs that no store has rd == pc.
11. **svc** (simulator only: redirections, stops) = `udf.w #imm16` with bit 15 = stop flag, 14:0 = code.
12. **ARMv7 baseline**: ProbeImpl sets kArmv7 (= ARMv7 + VFPv3-D32 + NEON aliases) also for the snapshot.
13. **Interworking (recommended by the map, to verify + implement next): ALL-ODD** - every stored code entry and
    every pc has bit 0 set; raw (even) addresses only through masked accessors for byte access. Hardware return
    addresses are odd, so pc - instruction_start stays exact at ~60 sites with no masking. Assembler side already
    consistent (bx/blx/ldr pc interwork; set_target_address_at strips bit 0 for branch offsets).

## Open (filled from the full-JIT mapping workflow wf_404efbad-1b2 + verification)
- Thumb bit 0 tagging convention for JIT code (all stored entry points odd + masked data accessor, vs everything even +
  `orr #1` before indirect branches) and the exact site list.
- New `kRootRegisterBias` (T32 ld/st reach -255..+4095) and `IsolateData` reordering.
- Simulator T32 front end (translate each emitted wide T32 form to the equivalent A32 `Instruction` and reuse the A32
  executor; IT state -> cond field; pc +4) - needed for mksnapshot cross builds if they execute generated code, and for
  server-side testing of the whole JIT (mjsunit / cctest / unittests in simulator mode).
- Every decoder/patcher (label chains, `target_address_at`, deoptimizer, Liftoff/wasm jump tables, IC/debugger patching).

## Test strategy
1. Encoding conformance: every encoder form vs `tools\t32oracle.py` (rt2.1 llvm-mc), bit-exact.
2. Simulator build on this server (x86 host, `v8_target_cpu = "arm"`, T32 front end): V8's own unittests/cctest and mjsunit.
3. Device: full-JIT Thumb-2 d8 on the Surface 2 (Win10 15035 runs both A32 and T32) with the d8 A/B kit
   (mjsunit + js-perf-test vs the A32 d8), then on RT 8.0, then chrome.dll.
