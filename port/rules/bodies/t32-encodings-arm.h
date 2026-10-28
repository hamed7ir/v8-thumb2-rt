// Copyright 2026 the Venetium authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

// Thumb-2 (T32) instruction encodings for V8's ARM assembler. Venetium targets
// Windows RT 8, which runs no A32 code, so every instruction V8 generates is
// T32. All functions here use the canonical form of a 32-bit instruction,
// (hw1 << 16) | hw2, where hw1 is the halfword at the lower address; the
// Assembler swaps halfwords when it reads or writes code memory.
//
// The few 16-bit instructions V8 needs (IT, BX, BLX, the Windows breakpoint,
// mov/add pc) are always emitted in pairs filling one 4-byte unit, so every
// unit is kInstrSize and every 32-bit instruction is 4-byte aligned.
//
// Registers and conditions are plain numbers (Register::code(), condition
// 0 = eq .. 14 = al). This header has no V8 dependency beyond CHECK, so it is
// also compiled on its own and checked against an assembler (F:\cr\thumb2
// tests\t32enc).

#ifndef V8_CODEGEN_ARM_T32_ENCODINGS_ARM_H_
#define V8_CODEGEN_ARM_T32_ENCODINGS_ARM_H_

#include <cstdint>

#include "src/base/logging.h"

namespace v8 {
namespace internal {
namespace t32 {

constexpr uint32_t kAl = 14;     // condition number of "always"
constexpr uint32_t kNoReg = 15;  // register field value 1111

inline bool FitsSigned(int32_t value, int bits) {
  return value >= -(int32_t{1} << (bits - 1)) &&
         value < (int32_t{1} << (bits - 1));
}

inline int CountLeadingZeros(uint32_t value) {
  if (value == 0) return 32;
  int n = 0;
  while ((value & 0x80000000u) == 0) {
    value <<= 1;
    n++;
  }
  return n;
}

// ---------------------------------------------------------------- 16-bit units
constexpr uint32_t kNop16 = 0xBF00;
// IT <cond> covering exactly one instruction.
constexpr uint32_t It(uint32_t cond) { return 0xBF08 | cond << 4; }
// The first half of a unit holding a 16-bit instruction: IT when predicated,
// a nop otherwise.
constexpr uint32_t Prefix16(uint32_t cond) {
  return cond == kAl ? kNop16 : It(cond);
}
constexpr uint32_t Unit(uint32_t first, uint32_t second) {
  return first << 16 | second;
}
constexpr uint32_t Bx(uint32_t rm) { return 0x4700 | rm << 3; }
constexpr uint32_t Blx(uint32_t rm) { return 0x4780 | rm << 3; }
constexpr uint32_t MovPc(uint32_t rm) { return 0x4687 | rm << 3; }  // mov pc, rm
constexpr uint32_t AddPc(uint32_t rm) { return 0x4487 | rm << 3; }  // add pc, rm
// add rdn, pc (rdn += pc; the only T32 add that reads pc with a register).
constexpr uint32_t AddRegPc(uint32_t rdn) {
  return 0x4478 | (rdn & 8) << 4 | (rdn & 7);
}
// udf #0xfe: the breakpoint of Windows on ARM (__debugbreak).
constexpr uint32_t kWindowsBreakpoint = 0xDEFE;

// ---------------------------------------------------------------- immediates
// ThumbExpandImm. On success *placed holds i:imm3:imm8 at their canonical
// positions (bit 26, bits 14:12, bits 7:0).
inline bool ModifiedImmediate(uint32_t imm32, uint32_t* placed) {
  uint32_t lo = imm32 & 0xFF;
  uint32_t b1 = (imm32 >> 8) & 0xFF;
  uint32_t enc;
  if (imm32 <= 0xFF) {
    enc = imm32;
  } else if (imm32 == (lo | lo << 16)) {
    enc = 0x100 | lo;
  } else if (imm32 == (b1 << 8 | b1 << 24)) {
    enc = 0x200 | b1;
  } else if (imm32 == lo * 0x01010101u) {
    enc = 0x300 | lo;
  } else {
    // '1bcdefgh' rotated right by 8..31: a byte with its top bit set, shifted
    // left by 1..24.
    int shift = 24 - CountLeadingZeros(imm32);
    if ((imm32 & ((uint32_t{1} << shift) - 1)) != 0) return false;
    enc = (32 - shift) << 7 | ((imm32 >> shift) & 0x7F);
  }
  *placed = (enc & 0x800) << 15 | (enc & 0x700) << 4 | (enc & 0xFF);
  return true;
}

// A plain 12-bit immediate (addw/subw/adr), i:imm3:imm8 placed.
constexpr uint32_t Imm12(uint32_t imm12) {
  return (imm12 & 0x800) << 15 | (imm12 & 0x700) << 4 | (imm12 & 0xFF);
}

// movw/movt: imm16 = imm4:i:imm3:imm8 placed at 19:16, 26, 14:12, 7:0.
constexpr uint32_t Imm16(uint32_t imm16) {
  return (imm16 & 0xF000) << 4 | (imm16 & 0x800) << 15 |
         (imm16 & 0x700) << 4 | (imm16 & 0xFF);
}
constexpr uint32_t DecodeImm16(uint32_t instr) {
  return (instr >> 4 & 0xF000) | (instr >> 15 & 0x800) | (instr >> 4 & 0x700) |
         (instr & 0xFF);
}
constexpr uint32_t Movw(uint32_t rd, uint32_t imm16) {
  return 0xF2400000 | rd << 8 | Imm16(imm16);
}
constexpr uint32_t Movt(uint32_t rd, uint32_t imm16) {
  return 0xF2C00000 | rd << 8 | Imm16(imm16);
}
constexpr bool IsMovw(uint32_t instr) {
  return (instr & 0xFBF08000) == 0xF2400000;
}
constexpr bool IsMovt(uint32_t instr) {
  return (instr & 0xFBF08000) == 0xF2C00000;
}

// ---------------------------------------------------------- data processing
// T32 op field of the data-processing instructions.
constexpr uint32_t kAnd = 0x0, kBic = 0x1, kOrr = 0x2, kOrn = 0x3, kEor = 0x4,
                   kAdd = 0x8, kAdc = 0xA, kSbc = 0xB, kSub = 0xD, kRsb = 0xE;

// <op>{s}.w rd, rn, #imm (rd = 15 for tst/teq/cmp/cmn, rn = 15 for mov/mvn).
constexpr uint32_t DPImmediate(uint32_t op, bool s, uint32_t rn, uint32_t rd,
                               uint32_t placed) {
  return 0xF0000000 | op << 21 | (s ? 1u << 20 : 0) | rn << 16 | rd << 8 |
         placed;
}
// <op>{s}.w rd, rn, rm, <type> #imm5 (type: 0 lsl, 1 lsr, 2 asr, 3 ror/rrx).
constexpr uint32_t DPShifted(uint32_t op, bool s, uint32_t rn, uint32_t rd,
                             uint32_t rm, uint32_t type, uint32_t imm5) {
  return 0xEA000000 | op << 21 | (s ? 1u << 20 : 0) | rn << 16 |
         (imm5 >> 2) << 12 | rd << 8 | (imm5 & 3) << 6 | type << 4 | rm;
}
// lsl/lsr/asr/ror{s}.w rd, rm, rs
constexpr uint32_t ShiftRegister(uint32_t type, bool s, uint32_t rd,
                                 uint32_t rm, uint32_t rs) {
  return 0xFA00F000 | type << 21 | (s ? 1u << 20 : 0) | rm << 16 | rd << 8 |
         rs;
}
// adr rd, #offset (addw/subw rd, pc, #imm12): rd = Align(pc, 4) + offset.
constexpr uint32_t Adr(uint32_t rd, int32_t offset) {
  return (offset >= 0 ? 0xF20F0000 : 0xF2AF0000) | rd << 8 |
         Imm12(static_cast<uint32_t>(offset >= 0 ? offset : -offset));
}

// ------------------------------------------------------------- loads/stores
// t3 = hw1 of the positive imm12 form with rn = 0. The imm8 (P/U/W),
// register and literal forms use t3 without bit 7.
constexpr uint32_t kLdr = 0xF8D0, kStr = 0xF8C0, kLdrb = 0xF890,
                   kStrb = 0xF880, kLdrh = 0xF8B0, kStrh = 0xF8A0,
                   kLdrsb = 0xF990, kLdrsh = 0xF9B0;
constexpr bool IsLoad(uint32_t t3) { return (t3 & 0x10) != 0; }

constexpr uint32_t LoadStoreImm12(uint32_t t3, uint32_t rn, uint32_t rt,
                                  uint32_t imm12) {
  return t3 << 16 | rn << 16 | rt << 12 | imm12;
}
constexpr uint32_t LoadStoreImm8(uint32_t t3, uint32_t rn, uint32_t rt, bool p,
                                 bool u, bool w, uint32_t imm8) {
  return (t3 & ~0x80u) << 16 | rn << 16 | rt << 12 | 0x800 |
         (p ? 0x400u : 0) | (u ? 0x200u : 0) | (w ? 0x100u : 0) | imm8;
}
constexpr uint32_t LoadStoreRegister(uint32_t t3, uint32_t rn, uint32_t rt,
                                     uint32_t rm, uint32_t lsl) {
  return (t3 & ~0x80u) << 16 | rn << 16 | rt << 12 | lsl << 4 | rm;
}
// ldr<x> rt, [pc, #offset]
constexpr uint32_t LoadLiteral(uint32_t t3, uint32_t rt, int32_t offset) {
  return (t3 & ~0x80u) << 16 | (offset >= 0 ? 1u << 23 : 0) | 0xF0000 |
         rt << 12 | static_cast<uint32_t>(offset >= 0 ? offset : -offset);
}
constexpr bool IsLdrLiteral(uint32_t instr) {
  return (instr & 0xFF7F0000) == 0xF85F0000;
}
constexpr int32_t LdrLiteralOffset(uint32_t instr) {
  return (instr & (1u << 23)) ? static_cast<int32_t>(instr & 0xFFF)
                              : -static_cast<int32_t>(instr & 0xFFF);
}
constexpr uint32_t SetLdrLiteralOffset(uint32_t instr, int32_t offset) {
  return (instr & ~(1u << 23 | 0xFFFu)) | (offset >= 0 ? 1u << 23 : 0) |
         static_cast<uint32_t>(offset >= 0 ? offset : -offset);
}
// ldrd/strd rt, rt2, [rn, #+-imm8*4] with P/U/W.
constexpr uint32_t LoadStoreDual(bool load, uint32_t rn, uint32_t rt,
                                 uint32_t rt2, bool p, bool u, bool w,
                                 uint32_t imm8) {
  return 0xE8400000 | (p ? 1u << 24 : 0) | (u ? 1u << 23 : 0) |
         (w ? 1u << 21 : 0) | (load ? 1u << 20 : 0) | rn << 16 | rt << 12 |
         rt2 << 8 | imm8;
}
// ldm/stm ia or db: two or more registers, never sp, no pc in stores and not
// pc together with lr in loads.
constexpr uint32_t LoadStoreMultiple(bool load, bool increment, bool w,
                                     uint32_t rn, uint32_t list) {
  return (increment ? 0xE8800000 : 0xE9000000) | (w ? 1u << 21 : 0) |
         (load ? 1u << 20 : 0) | rn << 16 | list;
}

// ---------------------------------------------------------------- branches
// B<c>.W (T3, +-1 MiB), B.W (T4) and BL (+-16 MiB). offset is relative to the
// branch's pc (its address + 4).
inline uint32_t Branch(uint32_t cond, bool link, int32_t offset) {
  CHECK_EQ(offset & 1, 0);
  uint32_t off = static_cast<uint32_t>(offset);
  if (cond == kAl || link) {
    CHECK(FitsSigned(offset, 25));
    uint32_t s = off >> 24 & 1;
    uint32_t j1 = (~(off >> 23) ^ s) & 1;
    uint32_t j2 = (~(off >> 22) ^ s) & 1;
    return 0xF0000000 | s << 26 | (off >> 12 & 0x3FF) << 16 |
           (link ? 0xD000u : 0x9000u) | j1 << 13 | j2 << 11 | (off >> 1 & 0x7FF);
  }
  CHECK(FitsSigned(offset, 21));
  return 0xF0008000 | (off >> 20 & 1) << 26 | cond << 22 |
         (off >> 12 & 0x3F) << 16 | (off >> 18 & 1) << 13 |
         (off >> 19 & 1) << 11 | (off >> 1 & 0x7FF);
}
constexpr bool IsBranch(uint32_t instr) {
  return (instr & 0xF8008000) == 0xF0008000 &&
         ((instr & 0x1000) != 0 ||
          ((instr & 0x4000) == 0 && (instr >> 22 & 0xF) < 14));
}
constexpr bool IsBranchAndLink(uint32_t instr) {
  return (instr & 0xF800D000) == 0xF000D000;
}
inline int32_t BranchOffset(uint32_t instr) {
  uint32_t s = instr >> 26 & 1;
  uint32_t j1 = instr >> 13 & 1;
  uint32_t j2 = instr >> 11 & 1;
  if (instr & 0x1000) {
    uint32_t off = s << 24 | (~(j1 ^ s) & 1) << 23 | (~(j2 ^ s) & 1) << 22 |
                   (instr >> 16 & 0x3FF) << 12 | (instr & 0x7FF) << 1;
    return static_cast<int32_t>(off << 7) >> 7;
  }
  uint32_t off = s << 20 | j2 << 19 | j1 << 18 | (instr >> 16 & 0x3F) << 12 |
                 (instr & 0x7FF) << 1;
  return static_cast<int32_t>(off << 11) >> 11;
}
inline uint32_t SetBranchOffset(uint32_t instr, int32_t offset) {
  DCHECK(IsBranch(instr));
  uint32_t cond = (instr & 0x1000) ? kAl : instr >> 22 & 0xF;
  return Branch(cond, IsBranchAndLink(instr), offset);
}

// ---------------------------------------------------- multiply / bit fields
// base | rn << 16 | r12 << 12 | r8 << 8 | rm
//   mul rd, rn, rm: (kMul, rn, 0, rd, rm)    mla rd, rn, rm, ra: (kMla, rn, ra, rd, rm)
//   smull rdlo, rdhi, rn, rm: (kSmull, rn, rdlo, rdhi, rm)
constexpr uint32_t kMul = 0xFB00F000, kMla = 0xFB000000, kMls = 0xFB000010,
                   kSdiv = 0xFB90F0F0, kUdiv = 0xFBB0F0F0,
                   kSmmla = 0xFB500000, kSmmul = 0xFB50F000,
                   kSmull = 0xFB800000, kUmull = 0xFBA00000,
                   kSmlal = 0xFBC00000, kUmlal = 0xFBE00000;
constexpr uint32_t Reg4(uint32_t base, uint32_t rn, uint32_t r12, uint32_t r8,
                        uint32_t rm) {
  return base | rn << 16 | r12 << 12 | r8 << 8 | rm;
}
// sxtab/sxtah/uxtab/uxtah/uxtab16 rd, rn, rm, ror #rotate; rn = 15 gives
// sxtb/sxth/uxtb/uxth/uxtb16.
constexpr uint32_t kSxtab = 0xFA40F080, kSxtah = 0xFA00F080,
                   kUxtab = 0xFA50F080, kUxtah = 0xFA10F080,
                   kUxtab16 = 0xFA30F080;
constexpr uint32_t Extend(uint32_t base, uint32_t rn, uint32_t rd,
                          uint32_t rotate, uint32_t rm) {
  return base | rn << 16 | rd << 8 | (rotate >> 3) << 4 | rm;
}
constexpr uint32_t Clz(uint32_t rd, uint32_t rm) {
  return 0xFAB0F080 | rm << 16 | rd << 8 | rm;
}
constexpr uint32_t Rbit(uint32_t rd, uint32_t rm) {
  return 0xFA90F0A0 | rm << 16 | rd << 8 | rm;
}
constexpr uint32_t Rev(uint32_t rd, uint32_t rm) {
  return 0xFA90F080 | rm << 16 | rd << 8 | rm;
}
// usat rd, #sat, rn{, lsl|asr #amount}
constexpr uint32_t Usat(uint32_t rd, uint32_t sat, uint32_t rn, bool asr,
                        uint32_t amount) {
  return 0xF3800000 | (asr ? 1u << 21 : 0) | rn << 16 | (amount >> 2) << 12 |
         rd << 8 | (amount & 3) << 6 | sat;
}
// ubfx/sbfx rd, rn, #lsb, #width
constexpr uint32_t Bfx(bool is_signed, uint32_t rd, uint32_t rn, uint32_t lsb,
                       uint32_t width) {
  return (is_signed ? 0xF3400000 : 0xF3C00000) | rn << 16 | (lsb >> 2) << 12 |
         rd << 8 | (lsb & 3) << 6 | (width - 1);
}
// bfi rd, rn, #lsb, #width; rn = 15 gives bfc rd, #lsb, #width.
constexpr uint32_t Bfi(uint32_t rd, uint32_t rn, uint32_t lsb, uint32_t width) {
  return 0xF3600000 | rn << 16 | (lsb >> 2) << 12 | rd << 8 | (lsb & 3) << 6 |
         (lsb + width - 1);
}
// pkhbt rd, rn, rm, lsl #amount / pkhtb rd, rn, rm, asr #amount
constexpr uint32_t Pkh(bool tb, uint32_t rd, uint32_t rn, uint32_t rm,
                       uint32_t amount) {
  return 0xEAC00000 | rn << 16 | (amount >> 2) << 12 | rd << 8 |
         (amount & 3) << 6 | (tb ? 1u << 5 : 0) | rm;
}

// ------------------------------------------------------------ system / misc
constexpr uint32_t Mrs(uint32_t rd, bool spsr) {
  return 0xF3EF8000 | (spsr ? 1u << 20 : 0) | rd << 8;
}
// msr <psr>_<mask>, rn (mask bits: 8 c, 9 x, 10 s, 11 f)
constexpr uint32_t Msr(bool spsr, uint32_t mask, uint32_t rn) {
  return 0xF3808000 | (spsr ? 1u << 20 : 0) | rn << 16 | mask << 8;
}
constexpr uint32_t Ldrex(uint32_t rt, uint32_t rn) {
  return 0xE8500F00 | rn << 16 | rt << 12;
}
constexpr uint32_t Strex(uint32_t rd, uint32_t rt, uint32_t rn) {
  return 0xE8400000 | rn << 16 | rt << 12 | rd << 8;
}
constexpr uint32_t Ldrexb(uint32_t rt, uint32_t rn) {
  return 0xE8D00F4F | rn << 16 | rt << 12;
}
constexpr uint32_t Ldrexh(uint32_t rt, uint32_t rn) {
  return 0xE8D00F5F | rn << 16 | rt << 12;
}
constexpr uint32_t Strexb(uint32_t rd, uint32_t rt, uint32_t rn) {
  return 0xE8C00F40 | rn << 16 | rt << 12 | rd;
}
constexpr uint32_t Strexh(uint32_t rd, uint32_t rt, uint32_t rn) {
  return 0xE8C00F50 | rn << 16 | rt << 12 | rd;
}
constexpr uint32_t Ldrexd(uint32_t rt, uint32_t rt2, uint32_t rn) {
  return 0xE8D0007F | rn << 16 | rt << 12 | rt2 << 8;
}
constexpr uint32_t Strexd(uint32_t rd, uint32_t rt, uint32_t rt2,
                          uint32_t rn) {
  return 0xE8C00070 | rn << 16 | rt << 12 | rt2 << 8 | rd;
}
// pld [rn, #offset], -255 <= offset <= 4095
constexpr uint32_t Pld(uint32_t rn, int32_t offset) {
  return offset >= 0
             ? 0xF890F000 | rn << 16 | static_cast<uint32_t>(offset)
             : 0xF810FC00 | rn << 16 | static_cast<uint32_t>(-offset);
}
constexpr uint32_t Dmb(uint32_t option) { return 0xF3BF8F50 | option; }
constexpr uint32_t Dsb(uint32_t option) { return 0xF3BF8F40 | option; }
constexpr uint32_t Isb(uint32_t option) { return 0xF3BF8F60 | option; }
constexpr uint32_t kCsdb = 0xF3AF8014;
// udf.w #imm16: traps, stops and the constant pool marker.
constexpr uint32_t Udf(uint32_t imm16) {
  return 0xF7F0A000 | (imm16 & 0xF000) << 4 | (imm16 & 0xFFF);
}
constexpr bool IsUdf(uint32_t instr) {
  return (instr & 0xFFF0F000) == 0xF7F0A000;
}
constexpr uint32_t UdfImmediate(uint32_t instr) {
  return (instr >> 4 & 0xF000) | (instr & 0xFFF);
}
// mov.w rT, rT: the nop whose register T carries a marker type.
constexpr uint32_t MarkerNop(uint32_t type) {
  return 0xEA4F0000 | type << 8 | type;
}

}  // namespace t32
}  // namespace internal
}  // namespace v8

#endif  // V8_CODEGEN_ARM_T32_ENCODINGS_ARM_H_
