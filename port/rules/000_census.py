"""Census: every A32-specific construct the Thumb-2 port must touch, as report-only Flags.

`t2run.py census` lists each site once (file:line + the line), grouped where a classify() is given, so the per-pattern
replacement rules can be written from this inventory instead of reading files one by one.
"""
import re
from t2run import Flag, ARM, ARM_TESTS, GENERIC, CONDS

SRC = ARM  # ARM32-specific V8 sources (arm64 excluded)
CORE = ["v8/src/codegen/arm/*.*"]
NOT_ENCODERS = [g for g in ARM]  # census helpers below filter by file where needed

REGISH = re.compile(r"^(?:r\d+|ip|lr|fp|sp|pc|cp|kRootRegister|\w*(?:[Rr]eg|[Ss]cratch|[Tt]e?mp)\w*|\w+Register)$")
IMMISH = re.compile(r"^(?:\d+|0x[0-9a-fA-F]+|k[A-Z]\w*|[A-Z][A-Z0-9_]+|.*(?:Size|Shift|Bits|sizeof|Log2|\d).*)$")


def shift_amount_class(m):
    amt = m.group("amt").strip()
    if REGISH.match(amt):
        return "reg-shift-reg"
    if IMMISH.match(amt):
        return "imm-shift"
    return "unknown:" + amt


def callee(m):
    return m.group("fn")


def first_group(m):
    return m.group(1)


RULES = [
    # ---- operands
    Flag("rsr-operand", SRC,
         r"\bOperand\s*\(\s*[^,();]+(?:\([^()]*\))?\s*,\s*(?:LSL|LSR|ASR|ROR)\s*,\s*(?P<amt>[^();]+?(?:\([^()]*\))?)\s*\)",
         classify=shift_amount_class,
         desc="Operand(rm, SHIFT, x): x a register = register-shifted-register (no T32 form -> scratch split)"),
    Flag("memop-shift", SRC,
         r"\bMemOperand\s*\(\s*[^,();]+\s*,\s*[^,();]+\s*,\s*(?P<sh>LSL|LSR|ASR|ROR)\s*,\s*(?P<amt>[^,();]+)"
         r"(?:,\s*(?P<am>\w+))?\s*\)",
         classify=lambda m: f"{m.group('sh')} {m.group('amt').strip()} {m.group('am') or 'Offset'}",
         desc="MemOperand with scaled register: T32 allows only LSL #0-3, Offset mode, positive"),
    Flag("memop-reg-mode", SRC,
         r"\bMemOperand\s*\(\s*[^,();]+\s*,\s*[^,();]+\s*,\s*(?P<am>NegOffset|PreIndex|PostIndex|NegPreIndex|NegPostIndex)\s*\)",
         classify=lambda m: m.group("am"),
         desc="MemOperand addressing modes T32 lacks for register offsets (neg / writeback reg)"),
    # ---- pc
    Flag("pc-dest", SRC,
         r"\b(?P<fn>mov|add|sub|ldr|orr|bic|and_|eor|rsb|adc|sbc|ldm|pop)\s*\(\s*(?:\w+\s*,\s*)?pc\b",
         classify=callee, desc="instructions that write pc (T32: bx / pop{pc} / ldr pc / add pc only via mov)"),
    Flag("pc-src", SRC,
         r"\bOperand\s*\(\s*pc\s*\)|\bMemOperand\s*\(\s*pc\b|,\s*pc\s*,|,\s*pc\s*\)|\(\s*pc\s*,\s*Operand",
         classify=lambda m: re.sub(r"\s+", "", m.group(0)),
         desc="pc read as an operand (reads instr+4 in T32, literal base Align(pc,4))"),
    Flag("pc-load-delta", ARM + GENERIC, r"\bkPcLoadDelta\b", region=None, desc="kPcLoadDelta consumers (8 -> 4)"),
    Flag("pc-offset-arith", SRC, r"\bpc_offset\s*\(\s*\)\s*[-+]\s*[^;,)]+", desc="pc_offset() +- k (sequence geometry)"),
    # ---- sequence geometry
    Flag("instr-size-mult", ARM + ARM_TESTS,
         r"\b\d+\s*\*\s*(?:Assembler::|Instruction::)?kInstrSize\b|\b(?:Assembler::|Instruction::)?kInstrSize\s*\*\s*\d+",
         desc="N * kInstrSize sequence lengths (stay valid if every unit is 4 bytes)"),
    Flag("instr-size-generic", GENERIC, r"\bkInstrSize\b", region="arm", desc="kInstrSize inside ARM branches of generic files"),
    # ---- predication
    Flag("cond-call", SRC,
         r"\b(?P<fn>[a-z_]\w*)\s*\((?:[^;{}()]|\([^;{}()]*(?:\([^;{}()]*\)[^;{}()]*)*\))*?,\s*" + CONDS + r"\s*\)\s*;",
         classify=callee, desc="calls whose last argument is a literal condition (predicated instructions)"),
    Flag("cond-param", ["v8/src/codegen/arm/macro-assembler-arm.cc", "v8/src/codegen/arm/assembler-arm.cc"],
         r"\b(?P<fn>[a-z_]\w*)\s*\([^;{}]*\bcond\s*\)\s*;", classify=callee,
         desc="calls forwarding a Condition variable (predicated when cond != al)"),
    # ---- control flow / traps
    Flag("bx-blx", SRC, r"\b(?P<fn>bx|blx)\s*\(", classify=callee, desc="bx/blx (16-bit only in T32 register forms)"),
    Flag("trap", SRC, r"\b(?P<fn>svc|bkpt|stop|udf|DebugBreak|Trap)\s*\(", classify=callee, desc="traps -> udf.w"),
    Flag("nop", SRC, r"\bnop\s*\(\s*(?P<t>[^)]*)\)", classify=lambda m: m.group("t").strip() or "()",
         desc="nop / marker nops (A32 mov rT,rT markers)"),
    # ---- ld/st multiple
    Flag("ldm-stm-mode", SRC, r"\b(?P<fn>ldm|stm)\s*\(\s*(?P<am>\w+)", classify=lambda m: f"{m.group('fn')} {m.group('am')}",
         desc="ldm/stm block modes (T32 has IA and DB only)"),
    Flag("ldrd-strd", SRC, r"\b(?P<fn>ldrd|strd)\s*\(", classify=callee, desc="ldrd/strd (T32 imm8*4 offsets)"),
    # ---- flags / special ops
    Flag("setcc-mul", SRC, r"\b(?P<fn>mul|mla|mls|smull|umull|smlal|umlal|smmul|smmla)\s*\([^;]*\bSetCC\b",
         classify=callee, desc="flag-setting multiplies (no T32 form)"),
    Flag("rsc-swp", SRC, r"\b(?P<fn>rsc|swp|swpb)\s*\(", classify=callee, desc="A32-only data ops"),
    Flag("cpu-feature", ARM + GENERIC, r"\bIsSupported\s*\(\s*(?P<fn>ARMv7|ARMv8|SUDIV|NEON|VFPv3|VFP32DREGS|ARMv7_SUDIV|"
                                       r"VFPv4|SAT32|MOVW_MOVT_IMMEDIATE_LOADS)\s*\)",
         region=None, classify=callee, desc="CpuFeatures gates (Thumb-2 implies ARMv7)"),
    # ---- raw code reads/writes and decoders
    Flag("raw-code-rw", ARM + GENERIC,
         r"\bMemory<\s*(?P<fn>int32_t|uint32_t|Instr|uint16_t)\s*>|reinterpret_cast<\s*(?:const\s+)?(?P<fn2>Instr|int32_t|uint32_t|uint16_t)\s*\*\s*>",
         region="arm", classify=lambda m: m.group("fn") or m.group("fn2"),
         desc="raw 32-bit reads/writes in ARM code paths (instruction words must go through the swap)"),
    Flag("raw-code-rw-armfiles", ARM,
         r"\bMemory<\s*(?P<fn>int32_t|uint32_t|Instr|uint16_t)\s*>|reinterpret_cast<\s*(?:const\s+)?(?P<fn2>Instr|int32_t|uint32_t|uint16_t)\s*\*\s*>",
         classify=lambda m: m.group("fn") or m.group("fn2"), desc="same, inside the ARM files"),
    Flag("instr-decoder", ARM + GENERIC,
         r"(?:Instruction::At\s*\(|->\s*(?P<fn>SImmed24Value|GetBranchOffset|SetBranchOffset|ImmedMovwMovtValue|"
         r"ConditionField|ConditionValue|InstructionBits|SetInstructionBits|IsBranch|Bits|Bit)\s*\()",
         region="arm", classify=lambda m: m.group("fn") or "Instruction::At", desc="Instruction class decoders"),
    Flag("asm-predicates", ARM + GENERIC,
         r"\b(?:Assembler::)?(?P<fn>Is[A-Z]\w*|Get[A-Z]\w*Offset|Set[A-Z]\w*Offset|PatchMovwImmediate|"
         r"EncodeMovwImmediate|DecodeShiftImm|PatchShiftImm|GetCondition|GetRd|GetRn|GetRm)\s*\(\s*(?:instr|Memory|"
         r"instr_at|Assembler::instr_at|\w+_instr|instruction)",
         region="arm", classify=callee, desc="A32 instruction-word predicates/patchers"),
    Flag("instr-consts", ARM, r"\b(?:const|constexpr)\s+(?:Instr|int32_t|uint32_t)\s+(?P<fn>k\w+)\s*=",
         classify=lambda m: "k-consts", desc="instruction pattern/mask constants"),
    Flag("bit-fields", ["v8/src/codegen/arm/macro-assembler-arm.cc", "v8/src/compiler/backend/arm/*.cc",
                        "v8/src/maglev/arm/*.*", "v8/src/baseline/arm/*.*", "v8/src/regexp/arm/*.*",
                        "v8/src/wasm/baseline/arm/*.*", "v8/src/builtins/arm/*.*", "v8/src/deoptimizer/arm/*.*"],
         r"\bB(?:[0-9]|[12][0-9]|3[01])\b", desc="B<n> bit constants outside the encoder/decoder files"),
    # ---- encoder census (assembler-arm.cc only)
    Flag("emit", ARM, r"\bemit\s*\(", classify=lambda m: "emit", desc="emit() call sites per file"),
    Flag("neon-prefix", ["v8/src/codegen/arm/assembler-arm.cc"],
         r"\b(?P<fn>0x1E[4-9A-F]U?\s*\*\s*B23|0x1F[0-9A-F]U?\s*\*\s*B2[0-4]|0xFU?\s*\*\s*B28|0xF2U?\s*\*\s*B24|"
         r"0xF3U?\s*\*\s*B24|0xF4U?\s*\*\s*B24|kSpecialCondition)",
         classify=lambda m: re.sub(r"\s+", "", m.group("fn")), desc="NEON / unconditional top-byte constants"),
    Flag("const-pool", ARM, r"\b(?P<fn>CheckConstPool|BlockConstPoolScope|BlockConstPoolFor|StartBlockConstPool|"
                            r"EndBlockConstPool|kMaxDistToIntPool|kMaxDistToFPPool|kCheckPoolInterval|"
                            r"ConstantPoolAddEntry|kMaxNumPending\w*)\b", classify=callee,
         desc="constant pool machinery"),
    Flag("dd-data", SRC, r"\b(?P<fn>dd|dq|dp|db|emit_code_target_address)\s*\(", classify=callee,
         desc="data words emitted into code (never swapped)"),
    Flag("root-bias", ARM + GENERIC, r"\bkRootRegisterBias\b", region=None, desc="root register bias users"),
    Flag("generic-arm-blocks", GENERIC, r"^[ \t]*#[ \t]*(?:if|elif)\b[^\n]*\bV8_TARGET_ARCH_ARM\b(?!64)",
         desc="generic files with ARM-only preprocessor branches"),
    Flag("max-pcrel-range", ARM + GENERIC, r"\bkMaxPCRelativeCodeRangeInMB\b", region=None, desc="branch range constant"),
    Flag("arm-code-alignment", ARM, r"\b(?P<fn>CodeTargetAlign|LoopHeaderAlign|DataAlign|Align)\s*\(",
         classify=callee, desc="alignment helpers (nop padding)"),
]
