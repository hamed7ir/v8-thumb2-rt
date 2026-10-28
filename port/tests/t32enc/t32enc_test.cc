// Prints "<assembly>\t<canonical hex | ERR>" for every encoder in
// t32-encodings-arm.h over many operand values, plus "SELF <name> ok|FAIL"
// for decoder round trips. run_t32enc.py assembles the left column with llvm-mc
// (thumbv7) and compares. ERR means our encoder refused the operand, so
// llvm-mc must refuse it too. "A ; B" lines are two 16-bit instructions that
// form one 4-byte unit.
#include <cstdint>
#include <cstdio>
#include <string>
#include <vector>

#include "src/codegen/arm/t32-encodings-arm.h"

using namespace v8::internal::t32;

namespace {

const char* kReg[] = {"r0", "r1", "r2", "r3", "r4",  "r5", "r6", "r7",
                      "r8", "r9", "r10", "r11", "r12", "sp", "lr", "pc"};
const char* kCond[] = {"eq", "ne", "hs", "lo", "mi", "pl", "vs",
                       "vc", "hi", "ls", "ge", "lt", "gt", "le"};
const char* kShift[] = {"lsl", "lsr", "asr", "ror"};

int failures = 0;

void Out(const std::string& text, uint32_t word) {
  std::printf("%s\t%08X\n", text.c_str(), word);
}
void Err(const std::string& text) { std::printf("%s\tERR\n", text.c_str()); }
void Self(const std::string& name, bool ok) {
  std::printf("SELF %s %s\n", name.c_str(), ok ? "ok" : "FAIL");
  if (!ok) failures++;
}
std::string Hex(uint32_t v) {
  char b[24];
  std::snprintf(b, sizeof(b), "#0x%x", v);
  return b;
}
std::string Dec(int32_t v) {
  char b[24];
  std::snprintf(b, sizeof(b), "#%d", v);
  return b;
}
std::string R(uint32_t r) { return kReg[r]; }

uint32_t lcg = 12345;
uint32_t Rand() {
  lcg = lcg * 1103515245u + 12345u;
  return (lcg >> 16) | ((lcg * 1103515245u + 12345u) & 0xFFFF0000u);
}

void ModifiedImmediates() {
  std::vector<uint32_t> values;
  for (uint32_t b = 0; b < 256; b++) {
    values.push_back(b);
    values.push_back(b | b << 16);
    values.push_back(b << 8 | b << 24);
    values.push_back(b * 0x01010101u);
  }
  for (uint32_t v = 0x80; v <= 0xFF; v += 5) {
    for (int s = 0; s <= 24; s++) values.push_back(v << s);
  }
  uint32_t misses[] = {0x101,      0x1FE,      0xF000000F, 0xFF0000FF,
                       0x00FF00FE, 0x12345678, 0x80000001, 0x1FE00001,
                       0x00010001, 0x01000100, 0xFFFFFFFE, 0x7FFFFFFF};
  for (uint32_t m : misses) values.push_back(m);
  for (int i = 0; i < 3000; i++) {
    uint32_t r = Rand();
    values.push_back(r);
    values.push_back((r & 0xFF) << (r >> 27));
  }
  for (uint32_t v : values) {
    uint32_t placed;
    std::string text = "teq.w r1, " + Hex(v);
    if (ModifiedImmediate(v, &placed)) {
      Out(text, DPImmediate(kEor, true, 1, kNoReg, placed));
    } else {
      Err(text);
    }
  }
}

void DataProcessing() {
  struct Op {
    const char* name;
    uint32_t op;
  } ops[] = {{"and", kAnd}, {"bic", kBic}, {"orr", kOrr}, {"orn", kOrn},
             {"eor", kEor}, {"add", kAdd}, {"adc", kAdc}, {"sbc", kSbc},
             {"sub", kSub}, {"rsb", kRsb}};
  uint32_t placed;
  ModifiedImmediate(0x3F0, &placed);
  for (const Op& o : ops) {
    for (int s = 0; s < 2; s++) {
      std::string m = std::string(o.name) + (s ? "s" : "") + ".w ";
      // adc/sbc have no 16-bit immediate form, and llvm-mc rejects ".w" there.
      bool no_w = o.op == kAdc || o.op == kSbc;
      Out((no_w ? std::string(o.name) + (s ? "s " : " ") : m) + "r1, r2, #0x3f0",
          DPImmediate(o.op, s, 2, 1, placed));
      for (uint32_t t = 0; t < 4; t++) {
        for (uint32_t amount : {1u, 7u, 31u, 32u}) {
          if (amount == 32 && (t == 0 || t == 3)) continue;
          Out(m + "r1, r2, r3, " + kShift[t] + " " + Dec(amount),
              DPShifted(o.op, s, 2, 1, 3, t, amount & 31));
        }
      }
      Out(m + "r8, r9, r10", DPShifted(o.op, s, 9, 8, 10, 0, 0));
      Out(m + "r1, r2, r3, rrx", DPShifted(o.op, s, 2, 1, 3, 3, 0));
    }
  }
  // Moves and compares: rn or rd is 1111.
  Out("mov.w r1, #0x3f0", DPImmediate(kOrr, false, kNoReg, 1, placed));
  Out("movs.w r1, #0x3f0", DPImmediate(kOrr, true, kNoReg, 1, placed));
  Out("mvn.w r1, #0x3f0", DPImmediate(kOrn, false, kNoReg, 1, placed));
  Out("cmp.w r2, #0x3f0", DPImmediate(kSub, true, 2, kNoReg, placed));
  Out("cmn.w r2, #0x3f0", DPImmediate(kAdd, true, 2, kNoReg, placed));
  Out("tst.w r2, #0x3f0", DPImmediate(kAnd, true, 2, kNoReg, placed));
  Out("teq.w r2, #0x3f0", DPImmediate(kEor, true, 2, kNoReg, placed));
  Out("mov.w r1, r3", DPShifted(kOrr, false, kNoReg, 1, 3, 0, 0));
  Out("mov.w r1, sp", DPShifted(kOrr, false, kNoReg, 1, 13, 0, 0));
  Out("mov.w sp, r11", DPShifted(kOrr, false, kNoReg, 13, 11, 0, 0));
  Out("lsl.w r1, r3, #5", DPShifted(kOrr, false, kNoReg, 1, 3, 0, 5));
  Out("lsrs.w r1, r3, #32", DPShifted(kOrr, true, kNoReg, 1, 3, 1, 0));
  Out("asr.w r1, r3, #31", DPShifted(kOrr, false, kNoReg, 1, 3, 2, 31));
  Out("ror.w r1, r3, #7", DPShifted(kOrr, false, kNoReg, 1, 3, 3, 7));
  Out("mvn.w r1, r3, lsl #2", DPShifted(kOrn, false, kNoReg, 1, 3, 0, 2));
  Out("cmp.w r2, r3, lsl #2", DPShifted(kSub, true, 2, kNoReg, 3, 0, 2));
  Out("cmn.w r2, r3", DPShifted(kAdd, true, 2, kNoReg, 3, 0, 0));
  Out("tst.w r2, r3, asr #3", DPShifted(kAnd, true, 2, kNoReg, 3, 2, 3));
  Out("teq.w r2, r3", DPShifted(kEor, true, 2, kNoReg, 3, 0, 0));
  // sp forms.
  ModifiedImmediate(16, &placed);
  Out("add.w sp, sp, #16", DPImmediate(kAdd, false, 13, 13, placed));
  Out("sub.w sp, sp, #16", DPImmediate(kSub, false, 13, 13, placed));
  Out("sub.w r1, sp, #16", DPImmediate(kSub, false, 13, 1, placed));
  Out("cmp.w sp, #16", DPImmediate(kSub, true, 13, kNoReg, placed));
  Out("add.w r1, sp, r3, lsl #2", DPShifted(kAdd, false, 13, 1, 3, 0, 2));
  Out("add.w sp, sp, r3, lsl #3", DPShifted(kAdd, false, 13, 13, 3, 0, 3));
  Out("sub.w sp, sp, r3", DPShifted(kSub, false, 13, 13, 3, 0, 0));
  Out("cmp.w sp, r3", DPShifted(kSub, true, 13, kNoReg, 3, 0, 0));
  // Register-shifted register moves.
  for (uint32_t t = 0; t < 4; t++) {
    for (int s = 0; s < 2; s++) {
      Out(std::string(kShift[t]) + (s ? "s" : "") + ".w r1, r2, r3",
          ShiftRegister(t, s, 1, 2, 3));
    }
  }
  Out("lsl.w r12, r14, r10", ShiftRegister(0, false, 12, 14, 10));
}

void PlainImmediates() {
  for (int32_t off : {0, 1, 123, 255, 256, 2047, 2048, 4095}) {
    Out("addw r1, pc, " + Dec(off), Adr(1, off));
    if (off) Out("subw r9, pc, " + Dec(off), Adr(9, -off));
  }
  std::vector<uint32_t> imms = {0, 1, 0xFF, 0x100, 0x7FF, 0x800, 0xFFF,
                                0x1000, 0x8000, 0xFFFF, 0x1234, 0xABCD};
  for (int i = 0; i < 200; i++) imms.push_back(Rand() & 0xFFFF);
  bool round_trip = true;
  for (uint32_t v : imms) {
    Out("movw r1, " + Hex(v), Movw(1, v));
    Out("movt r12, " + Hex(v), Movt(12, v));
    round_trip &= DecodeImm16(Movw(1, v)) == v && DecodeImm16(Movt(12, v)) == v;
    round_trip &= IsMovw(Movw(1, v)) && !IsMovt(Movw(1, v));
    round_trip &= IsMovt(Movt(12, v)) && !IsMovw(Movt(12, v));
  }
  Self("imm16 round trip + IsMovw/IsMovt", round_trip);
}

void LoadsStores() {
  struct Kind {
    const char* name;
    uint32_t t3;
  } kinds[] = {{"ldr", kLdr},   {"str", kStr},   {"ldrb", kLdrb},
               {"strb", kStrb}, {"ldrh", kLdrh}, {"strh", kStrh},
               {"ldrsb", kLdrsb}, {"ldrsh", kLdrsh}};
  for (const Kind& k : kinds) {
    std::string n = k.name;
    for (uint32_t imm : {0u, 1u, 255u, 256u, 4095u}) {
      Out(n + ".w r1, [r2, " + Dec(imm) + "]", LoadStoreImm12(k.t3, 2, 1, imm));
    }
    Out(n + ".w r1, [sp, #8]", LoadStoreImm12(k.t3, 13, 1, 8));
    for (uint32_t imm : {1u, 4u, 255u}) {
      int32_t neg = -static_cast<int32_t>(imm);
      Out(n + " r1, [r2, " + Dec(neg) + "]",
          LoadStoreImm8(k.t3, 2, 1, true, false, false, imm));
      Out(n + " r1, [r2, " + Dec(imm) + "]!",
          LoadStoreImm8(k.t3, 2, 1, true, true, true, imm));
      Out(n + " r1, [r2, " + Dec(neg) + "]!",
          LoadStoreImm8(k.t3, 2, 1, true, false, true, imm));
      Out(n + " r1, [r2], " + Dec(imm),
          LoadStoreImm8(k.t3, 2, 1, false, true, true, imm));
      Out(n + " r1, [r2], " + Dec(neg),
          LoadStoreImm8(k.t3, 2, 1, false, false, true, imm));
    }
    for (uint32_t lsl = 0; lsl < 4; lsl++) {
      std::string sh = lsl ? ", lsl " + Dec(lsl) : "";
      Out(n + ".w r1, [r2, r3" + sh + "]",
          LoadStoreRegister(k.t3, 2, 1, 3, lsl));
    }
    if (IsLoad(k.t3)) {
      for (int32_t off : {0, 8, -8, 4095, -4095}) {
        Out(n + ".w r1, [pc, " + Dec(off) + "]", LoadLiteral(k.t3, 1, off));
      }
    }
  }
  Out("ldr.w pc, [r2, #4]", LoadStoreImm12(kLdr, 2, 15, 4));
  Out("ldr pc, [sp], #4", LoadStoreImm8(kLdr, 13, 15, false, true, true, 4));
  Out("str r1, [sp, #-4]!", LoadStoreImm8(kStr, 13, 1, true, false, true, 4));
  Out("str.w sp, [r2, #8]", LoadStoreImm12(kStr, 2, 13, 8));
  bool lit = true;
  for (int32_t off : {0, 8, -8, 4095, -4095, 1, -1}) {
    uint32_t w = LoadLiteral(kLdr, 5, 0);
    w = SetLdrLiteralOffset(w, off);
    lit &= IsLdrLiteral(w) && LdrLiteralOffset(w) == off &&
           w == LoadLiteral(kLdr, 5, off);
  }
  lit &= !IsLdrLiteral(LoadStoreImm12(kLdr, 2, 1, 8)) &&
         !IsLdrLiteral(LoadLiteral(kLdrb, 1, 8));
  Self("ldr literal offset get/set + IsLdrLiteral", lit);
  // Dual.
  for (int32_t off : {0, 8, -8, 1020, -1020}) {
    uint32_t imm8 = (off >= 0 ? off : -off) >> 2;
    bool u = off >= 0;
    Out("ldrd r0, r1, [r2, " + Dec(off) + "]",
        LoadStoreDual(true, 2, 0, 1, true, u, false, imm8));
    Out("strd r4, r5, [r2, " + Dec(off) + "]!",
        LoadStoreDual(false, 2, 4, 5, true, u, true, imm8));
    Out("ldrd r6, r7, [r2], " + Dec(off),
        LoadStoreDual(true, 2, 6, 7, false, u, true, imm8));
  }
  Out("strd r4, r5, [sp, #-8]!",
      LoadStoreDual(false, 13, 4, 5, true, false, true, 2));
  // Multiple.
  Out("ldm.w r0!, {r1, r2, lr}",
      LoadStoreMultiple(true, true, true, 0, 1 << 1 | 1 << 2 | 1 << 14));
  Out("ldm.w r0, {r1, r2}", LoadStoreMultiple(true, true, false, 0, 6));
  Out("ldmdb r0!, {r1, r2}", LoadStoreMultiple(true, false, true, 0, 6));
  Out("ldmdb r0, {r4, pc}",
      LoadStoreMultiple(true, false, false, 0, 1 << 4 | 1 << 15));
  Out("stm.w r0!, {r1, r2}", LoadStoreMultiple(false, true, true, 0, 6));
  Out("stmdb sp!, {r4, r5, lr}",
      LoadStoreMultiple(false, false, true, 13, 1 << 4 | 1 << 5 | 1 << 14));
  Out("ldm.w sp!, {r4, r5, pc}",
      LoadStoreMultiple(true, true, true, 13, 1 << 4 | 1 << 5 | 1 << 15));
  Out("stm.w r9, {r0, r1, r2, r3, r4, r5, r6, r7, r8, r10, r11, r12, lr}",
      LoadStoreMultiple(false, true, false, 9, 0x5DFF));
}

void Branches() {
  std::vector<int32_t> wide = {0,        2,        4,        -2,       -4,
                               4094,     4096,     -4096,    1048574,  -1048576,
                               1048576,  -1048578, 16777214, -16777216, 8388608,
                               -8388608, 4194304,  -4194304};
  for (int i = 0; i < 300; i++) {
    wide.push_back(static_cast<int32_t>(Rand() << 7) >> 7 & ~1);
  }
  bool round_trip = true;
  for (int32_t off : wide) {
    uint32_t b = Branch(kAl, false, off);
    uint32_t bl = Branch(kAl, true, off);
    Out("b.w " + Dec(off), b);
    Out("bl " + Dec(off), bl);
    round_trip &= IsBranch(b) && IsBranch(bl) && !IsBranchAndLink(b) &&
                  IsBranchAndLink(bl) && BranchOffset(b) == off &&
                  BranchOffset(bl) == off &&
                  SetBranchOffset(b, 2) == Branch(kAl, false, 2) &&
                  SetBranchOffset(bl, -2) == Branch(kAl, true, -2);
  }
  std::vector<int32_t> narrow = {0, 2, -2, 4, -4, 4096, -4096, 1048574,
                                 -1048576, 262144, -262144, 524288, -524288};
  for (int i = 0; i < 200; i++) {
    narrow.push_back(static_cast<int32_t>(Rand() << 11) >> 11 & ~1);
  }
  for (uint32_t c = 0; c < 14; c++) {
    for (int32_t off : narrow) {
      uint32_t b = Branch(c, false, off);
      Out(std::string("b") + kCond[c] + ".w " + Dec(off), b);
      round_trip &= IsBranch(b) && !IsBranchAndLink(b) &&
                    BranchOffset(b) == off &&
                    SetBranchOffset(b, 8) == Branch(c, false, 8);
    }
  }
  Self("branch offset round trip + kind preserved", round_trip);
  uint32_t placed;
  ModifiedImmediate(0x3F0, &placed);
  uint32_t not_branches[] = {
      DPImmediate(kAdd, false, 2, 1, placed), Dmb(0xB), Dsb(0xF), Isb(0xF),
      Msr(false, 8, 1), Mrs(1, false), Udf(0x1234), Movw(1, 0x1234),
      Movt(1, 0x1234), Pld(1, 8), kCsdb, LoadStoreImm12(kLdr, 2, 1, 8),
      Ldrex(1, 2), MarkerNop(3)};
  bool none = true;
  for (uint32_t w : not_branches) none &= !IsBranch(w);
  Self("IsBranch false on non-branches", none);
}

void MultiplyAndBits() {
  Out("mul r1, r2, r3", Reg4(kMul, 2, 0, 1, 3));
  Out("mla r1, r2, r3, r4", Reg4(kMla, 2, 4, 1, 3));
  Out("mls r1, r2, r3, r4", Reg4(kMls, 2, 4, 1, 3));
  Out("sdiv r1, r2, r3", Reg4(kSdiv, 2, 0, 1, 3));
  Out("udiv r10, r11, r12", Reg4(kUdiv, 11, 0, 10, 12));
  Out("smmla r1, r2, r3, r4", Reg4(kSmmla, 2, 4, 1, 3));
  Out("smmul r1, r2, r3", Reg4(kSmmul, 2, 0, 1, 3));
  Out("smull r1, r2, r3, r4", Reg4(kSmull, 3, 1, 2, 4));
  Out("umull r1, r2, r3, r4", Reg4(kUmull, 3, 1, 2, 4));
  Out("smlal r1, r2, r3, r4", Reg4(kSmlal, 3, 1, 2, 4));
  Out("umlal r1, r2, r3, r4", Reg4(kUmlal, 3, 1, 2, 4));
  for (uint32_t rot : {0u, 8u, 16u, 24u}) {
    std::string r = rot ? ", ror " + Dec(rot) : "";
    Out("sxtb.w r1, r2" + r, Extend(kSxtab, kNoReg, 1, rot, 2));
    Out("sxth.w r1, r2" + r, Extend(kSxtah, kNoReg, 1, rot, 2));
    Out("uxtb.w r1, r2" + r, Extend(kUxtab, kNoReg, 1, rot, 2));
    Out("uxth.w r1, r2" + r, Extend(kUxtah, kNoReg, 1, rot, 2));
    Out("uxtb16 r1, r2" + r, Extend(kUxtab16, kNoReg, 1, rot, 2));
    Out("sxtab r1, r2, r3" + r, Extend(kSxtab, 2, 1, rot, 3));
    Out("sxtah r1, r2, r3" + r, Extend(kSxtah, 2, 1, rot, 3));
    Out("uxtab r1, r2, r3" + r, Extend(kUxtab, 2, 1, rot, 3));
    Out("uxtah r1, r2, r3" + r, Extend(kUxtah, 2, 1, rot, 3));
  }
  Out("clz r1, r2", Clz(1, 2));
  Out("rbit r1, r2", Rbit(1, 2));
  Out("rev.w r1, r2", Rev(1, 2));
  for (uint32_t sat : {0u, 5u, 31u}) {
    Out("usat r1, " + Dec(sat) + ", r2", Usat(1, sat, 2, false, 0));
    Out("usat r1, " + Dec(sat) + ", r2, lsl #4", Usat(1, sat, 2, false, 4));
    Out("usat r1, " + Dec(sat) + ", r2, asr #31", Usat(1, sat, 2, true, 31));
  }
  for (uint32_t lsb : {0u, 3u, 16u, 31u}) {
    for (uint32_t width : {1u, 5u, 32u - lsb}) {
      if (lsb + width > 32 || width == 0) continue;
      std::string a = Dec(lsb) + ", " + Dec(width);
      Out("ubfx r1, r2, " + a, Bfx(false, 1, 2, lsb, width));
      Out("sbfx r1, r2, " + a, Bfx(true, 1, 2, lsb, width));
      Out("bfi r1, r2, " + a, Bfi(1, 2, lsb, width));
      Out("bfc r1, " + a, Bfi(1, kNoReg, lsb, width));
    }
  }
  for (uint32_t amount : {0u, 1u, 16u, 31u}) {
    Out("pkhbt r1, r2, r3, lsl " + Dec(amount), Pkh(false, 1, 2, 3, amount));
  }
  for (uint32_t amount : {1u, 16u, 31u, 32u}) {
    Out("pkhtb r1, r2, r3, asr " + Dec(amount),
        Pkh(true, 1, 2, 3, amount & 31));
  }
}

void SystemAndMisc() {
  Out("mrs r1, apsr", Mrs(1, false));
  Out("msr apsr_nzcvq, r1", Msr(false, 8, 1));
  Out("ldrex r1, [r2]", Ldrex(1, 2));
  Out("strex r3, r1, [r2]", Strex(3, 1, 2));
  Out("ldrexb r1, [r2]", Ldrexb(1, 2));
  Out("ldrexh r1, [r2]", Ldrexh(1, 2));
  Out("strexb r3, r1, [r2]", Strexb(3, 1, 2));
  Out("strexh r3, r1, [r2]", Strexh(3, 1, 2));
  Out("ldrexd r0, r1, [r2]", Ldrexd(0, 1, 2));
  Out("strexd r3, r0, r1, [r2]", Strexd(3, 0, 1, 2));
  Out("ldrexd r4, r7, [r2]", Ldrexd(4, 7, 2));
  for (int32_t off : {0, 8, 4095, -1, -255}) {
    Out("pld [r1, " + Dec(off) + "]", Pld(1, off));
  }
  Out("dmb ish", Dmb(0xB));
  Out("dmb sy", Dmb(0xF));
  Out("dmb ishst", Dmb(0xA));
  Out("dsb sy", Dsb(0xF));
  Out("isb sy", Isb(0xF));
  Out("csdb", kCsdb);
  bool udf = true;
  for (uint32_t imm : {0u, 1u, 0xFEu, 0x1234u, 0x8000u, 0xFFFFu}) {
    Out("udf.w " + Hex(imm), Udf(imm));
    udf &= IsUdf(Udf(imm)) && UdfImmediate(Udf(imm)) == imm;
  }
  udf &= !IsUdf(Dmb(0xF)) && !IsUdf(Movw(1, 2));
  Self("udf round trip", udf);
  for (uint32_t t = 0; t <= 14; t++) {
    if (t == 13) continue;
    Out("mov.w " + R(t) + ", " + R(t), MarkerNop(t));
  }
}

void Units() {
  Out("nop ; bx lr", Unit(kNop16, Bx(14)));
  Out("nop ; blx r3", Unit(kNop16, Blx(3)));
  Out("nop ; mov pc, r3", Unit(kNop16, MovPc(3)));
  Out("add pc, r3 ; nop", Unit(AddPc(3), kNop16));
  for (uint32_t r = 0; r <= 14; r++) {
    if (r == 13) continue;
    Out("add " + R(r) + ", pc ; nop", Unit(AddRegPc(r), kNop16));
  }
  Out("nop ; udf #0xfe", Unit(kNop16, kWindowsBreakpoint));
  for (uint32_t c = 0; c < 14; c++) {
    std::string cc = kCond[c];
    Out("it " + cc + " ; bx" + cc + " lr", Unit(It(c), Bx(14)));
    Out("it " + cc + " ; blx" + cc + " r12", Unit(It(c), Blx(12)));
    Out("it " + cc + " ; mov" + cc + " pc, r1", Unit(It(c), MovPc(1)));
    Self("Prefix16 " + cc, Prefix16(c) == It(c));
  }
  Self("Prefix16 al", Prefix16(kAl) == kNop16);
}

}  // namespace

int main() {
  ModifiedImmediates();
  DataProcessing();
  PlainImmediates();
  LoadsStores();
  Branches();
  MultiplyAndBits();
  SystemAndMisc();
  Units();
  std::fprintf(stderr, "self-check failures: %d\n", failures);
  return failures ? 1 : 0;
}
