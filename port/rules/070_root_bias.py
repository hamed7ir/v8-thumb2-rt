"""Root register bias for T32 load/store reach (imm12 0..4095 upwards, imm8 down to -255).

Map (runtime-os, measured on the v1.2 blob): bias 256 keeps every IsolateData prefix field and roots 0..998 in
single-instruction reach (87.7% of the 31,388 root-relative accesses); the rest use a scratch register (correct,
+4 bytes each). 256 is a multiple of 4, so vldr/vstr and ldrd/strd (+-1020) also work root-relative. Field 0
(cage_base) is dead on 32-bit, so 256 loses nothing against 255. Offsets are baked into the snapshot/blob.
"""
from t2run import Lit

RULES = [
    Lit("code-is-thumb-comment", "v8/src/codegen/arm/macro-assembler-arm.cc",
        "// 'code' is always generated ARM code, never THUMB code",
        "// 'code' is generated T32 code; code targets carry the Thumb bit", expect=2, code_only=False),
    Lit("root-register-bias", "v8/src/codegen/arm/constants-arm.h", """// The actual value of the kRootRegister is offset from the IsolateData's start
// to take advantage of negative displacement values.
//
// Loads allow a uint12 value with a separate sign bit (range [-4095, +4095]),
// so the first root is still addressable with a single load instruction.
constexpr int kRootRegisterBias = 4095;""", """// The actual value of the kRootRegister is offset from the IsolateData's start
// to take advantage of negative displacement values.
//
// T32 loads and stores reach [-255, +4095] in one instruction (imm8 down,
// imm12 up), so a bias of 256 puts the first 4351 bytes of IsolateData in
// reach; a multiple of 4 also keeps vldr/ldrd offsets (+-1020) usable.
constexpr int kRootRegisterBias = 256;""", expect=1, code_only=False),
    Lit("deopt-exit-reach", "v8/src/deoptimizer/arm/deoptimizer-arm.cc", """// The deopt exit sizes below depend on the following IsolateData layout
// guarantees:
#define ASSERT_OFFSET(BuiltinName)                                       \\
  static_assert(IsolateData::builtin_tier0_entry_table_offset() +        \\
                    Builtins::ToInt(BuiltinName) * kSystemPointerSize <= \\
                0x1000)""", """// The deopt exit sizes below depend on the following IsolateData layout
// guarantees: the entry slot is reachable by a single ldr.w from the root
// register (T32 reach [-255, +4095]).
#define ASSERT_OFFSET(BuiltinName)                                          \\
  static_assert(IsolateData::builtin_tier0_entry_table_offset() +           \\
                        Builtins::ToInt(BuiltinName) * kSystemPointerSize >= \\
                    -255 &&                                                 \\
                IsolateData::builtin_tier0_entry_table_offset() +           \\
                        Builtins::ToInt(BuiltinName) * kSystemPointerSize <= \\
                    4095)""", expect=1, code_only=False),
]
