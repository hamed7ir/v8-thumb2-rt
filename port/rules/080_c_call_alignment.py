"""C-call stack alignment without a second scratch register.

PrepareCallCFunction keeps the old sp in `scratch` (usually ip, the only scratch register) while it aligns sp, and
T32 cannot AND into sp (only add/sub/mov write sp): the A32 `and sp, sp, #-8` would need another register, and
mksnapshot stopped there (Generate_DeoptimizationEntry -> PrepareCallCFunction -> EnforceStackAlignment ->
AddrMode1 -> UseScratchRegisterScope::Acquire on an empty list). Alignment is 8 (EABI; the simulator's
--sim-stack-alignment default) and sp and the allocation are multiples of 4, so the new sp is misaligned exactly when
bit 2 of the old sp differs from bit 2 of the allocation: test the old sp and drop one more word under IT.
"""
from t2run import Lit

RULES = [
    Lit("c-call-align", "v8/src/codegen/arm/macro-assembler-arm.cc", """    mov(scratch, sp);
    AllocateStackSpace((stack_passed_arguments + 1) * kPointerSize);
    EnforceStackAlignment();
    str(scratch, MemOperand(sp, stack_passed_arguments * kPointerSize));""", """    mov(scratch, sp);
    AllocateStackSpace((stack_passed_arguments + 1) * kPointerSize);
    // T32 cannot and into sp, and scratch holds the old sp: drop one more
    // word when the new sp is not 8-byte aligned. sp and the allocation are
    // multiples of 4, so bit 2 of the new sp is bit 2 of the old sp xor bit 2
    // of the allocation.
    CHECK_EQ(frame_alignment, 2 * kPointerSize);
    tst(scratch, Operand(kPointerSize));
    sub(sp, sp, Operand(kPointerSize), LeaveCC,
        (stack_passed_arguments + 1) % 2 == 0 ? ne : eq);
    str(scratch, MemOperand(sp, stack_passed_arguments * kPointerSize));""", expect=1),
]
