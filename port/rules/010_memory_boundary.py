"""Memory boundary: an Instr is the canonical T32 word (hw1 << 16) | hw2; in code memory hw1 sits at the lower address,
so every instruction-word read or write in code memory swaps halfwords. Data words (dd, pool entries) never do.
(DESIGN.md decision 2.)
"""
from t2run import Lit, Re

CONSTANTS_H = "v8/src/codegen/arm/constants-arm.h"
CONSTANTS_CC = "v8/src/codegen/arm/constants-arm.cc"
ASM_H = "v8/src/codegen/arm/assembler-arm.h"
ASM_INL = "v8/src/codegen/arm/assembler-arm-inl.h"

SWAP_HELPER = """using Instr = int32_t;

// Thumb-2 (Venetium, Windows RT 8): all generated code is T32. Inside V8 an Instr holds a 32-bit T32 instruction in
// the ARM ARM's canonical form (hw1 << 16) | hw2; in memory the first halfword hw1 is at the lower address, so every
// read or write of an instruction word in code memory swaps the halfwords. Data words (dd, constant pool entries,
// jump table entries) are not instructions and are never swapped.
constexpr Instr T32SwapHalfwords(Instr x) {
  uint32_t u = static_cast<uint32_t>(x);
  return static_cast<Instr>((u << 16) | (u >> 16));
}
"""

RULES = [
    Re("swap-helper", CONSTANTS_H, r"^using Instr = int32_t;\n(?!\n// Thumb-2 \(Venetium)", SWAP_HELPER, expect=1,
       code_only=False, done=r"constexpr Instr T32SwapHalfwords\(Instr x\)", desc="T32SwapHalfwords helper"),
    Lit("instr-bits-read", CONSTANTS_H, "return *reinterpret_cast<const Instr*>(this);",
        "return T32SwapHalfwords(*reinterpret_cast<const Instr*>(this));", expect=1,
        desc="Instruction::InstructionBits reads the canonical word"),
    Lit("instr-bits-write-jit", CONSTANTS_CC, "jit_allocation->WriteValue(reinterpret_cast<Address>(this), value);",
        "jit_allocation->WriteValue(reinterpret_cast<Address>(this),\n"
        "                               T32SwapHalfwords(value));", expect=1,
        desc="Instruction::SetInstructionBits (jit_allocation path)"),
    Lit("instr-bits-write", CONSTANTS_CC, "*reinterpret_cast<Instr*>(this) = value;",
        "*reinterpret_cast<Instr*>(this) = T32SwapHalfwords(value);", expect=1,
        desc="Instruction::SetInstructionBits (direct path)"),
    Lit("instr-at-pos", ASM_H, "return *reinterpret_cast<Instr*>(buffer_start_ + pos);",
        "return T32SwapHalfwords(*reinterpret_cast<Instr*>(buffer_start_ + pos));", expect=1),
    Lit("instr-at-put-pos", ASM_H, "*reinterpret_cast<Instr*>(buffer_start_ + pos) = instr;",
        "*reinterpret_cast<Instr*>(buffer_start_ + pos) = T32SwapHalfwords(instr);", expect=1),
    Lit("instr-at-addr", ASM_H, "static Instr instr_at(Address pc) { return *reinterpret_cast<Instr*>(pc); }",
        "static Instr instr_at(Address pc) {\n    return T32SwapHalfwords(*reinterpret_cast<Instr*>(pc));\n  }",
        expect=1),
    Lit("instr-at-put-addr", ASM_H, "*reinterpret_cast<Instr*>(pc) = instr;",
        "*reinterpret_cast<Instr*>(pc) = T32SwapHalfwords(instr);", expect=1),
    Lit("emit-swap", ASM_INL, "*reinterpret_cast<Instr*>(pc_) = x;",
        "*reinterpret_cast<Instr*>(pc_) = T32SwapHalfwords(x);", expect=1, desc="Assembler::emit"),
    # Static accessors wrap a canonical Instr VALUE as an in-memory Instruction: store it in memory order first,
    # so the member accessors (which read through InstructionBits) see the canonical word again.
    Lit("static-accessor-memory-order", CONSTANTS_H, "char* temp = reinterpret_cast<char*>(&instr);",
        "Instr in_memory = T32SwapHalfwords(instr); \\\n    char* temp = reinterpret_cast<char*>(&in_memory);",
        expect=1, code_only=False, desc="DECLARE_STATIC_TYPED_ACCESSOR: canonical value -> memory order"),
    Re("inl-raw-read-qualified", ASM_INL, r"(Assembler::\w+\()Memory<int32_t>\(", r"\1Assembler::instr_at(", expect=4,
       done=r"Assembler::\w+\(Assembler::instr_at\(", desc="raw instruction reads -> instr_at (RelocInfo context)"),
    Re("inl-raw-read", ASM_INL, r"Memory<int32_t>\(", "instr_at(", expect=24, done=r"\binstr_at\(",
       desc="raw instruction reads -> instr_at (Assembler context)"),
]
