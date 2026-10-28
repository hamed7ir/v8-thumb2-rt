"""Sites where T32 needs a register that A32 did not, while the only scratch register (ip) is already taken.

double-to-i-saturation: TryInlineTruncateDoubleToI holds ip as `scratch` and compares it with #0x7FFFFFFE. A32 encodes
that as cmn #0x80000002 (0x0A ror 2); T32 has no modified immediate for either value, so it would need a second
register. scratch = result - 1 is 0x7FFFFFFE or 0x7FFFFFFF exactly for the two saturated results, and those are the
only values for which scratch + 2 overflows: cmn #2 sets V for them, so "not saturated" is vc (was lt).
"""
from t2run import Lit

RULES = [
    Lit("double-to-i-saturation", "v8/src/codegen/arm/macro-assembler-arm.cc", """  // If result is not saturated (0x7FFFFFFF or 0x80000000), we are done.
  sub(scratch, result, Operand(1));
  cmp(scratch, Operand(0x7FFFFFFE));
  b(lt, done);""", """  // If result is not saturated (0x7FFFFFFF or 0x80000000), we are done.
  // scratch = result - 1 is 0x7FFFFFFE or 0x7FFFFFFF exactly then, the only
  // values for which scratch + 2 overflows (T32 cannot encode #0x7FFFFFFE).
  sub(scratch, result, Operand(1));
  cmn(scratch, Operand(2));
  b(vc, done);""", expect=1, code_only=False),
]
