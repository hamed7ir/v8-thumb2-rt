#!/usr/bin/env bash
# Scratch-exhaustion census (needs rules/990 applied): rebuild mksnapshot in out\t2-arm, run it under cdb with a
# breakpoint on T2DiagScratchExhausted that prints the stack and continues; any other exception stops the run with a
# stack. Then summarise the distinct calling sites. usage: bash snap_census.sh [tag]
source D:/repo/venetium/env/build-env.sh > /dev/null 2>&1 || exit 2
OUT=/d/cr/rt8/src/out/t2-arm; R=/f/cr/thumb2/runs; T=${1:-census}; RW='F:\cr\thumb2\runs'
cd $OUT || exit 2
$NINJA -C . -j 8 clang_x86_v8_arm/mksnapshot.exe > $R/snap-$T-ninja.log 2>&1 || { echo "BUILD FAILED"; grep -A30 "^FAILED" $R/snap-$T-ninja.log | head -60; exit 1; }
tail -1 $R/snap-$T-ninja.log
timeout 3000 "/d/Windows Kits/10/Debuggers/x86/cdb.exe" -lines -y 'D:\cr\rt8\src\out\t2-arm\clang_x86_v8_arm' \
  -c "bp mksnapshot_exe!v8::internal::T2DiagScratchExhausted \".echo T2DIAG-HIT; kn 8; g\"; g; .echo T2DIAG-STOP; .lastevent; r; kn 40; q" \
  ./clang_x86_v8_arm/mksnapshot.exe --turbo_instruction_scheduling --turbo-always-optimize-spills \
  --target_os=win --target_arch=arm --embedded_src "$RW\\snap-$T-embedded.S" --startup_blob "$RW\\snap-$T-blob.bin" \
  --predictable --no-use-ic --turbo-elide-frames --turbo-profiling-input ../../v8/tools/builtins-pgo/profiles/x86-rl.profile --warn-about-builtin-profile-data --reorder-builtins --embedded_variant Default --random-seed 314159265 --no-native-code-counters --concurrent-builtin-generation --concurrent-turbofan-max-threads=0 > $R/snap-$T-cdb.txt 2>&1
echo "hits: $(grep -cx T2DIAG-HIT $R/snap-$T-cdb.txt)"
python -I F:/cr/thumb2/tools/census_sites.py $R/snap-$T-cdb.txt
if grep -qx "T2DIAG-STOP" $R/snap-$T-cdb.txt; then
  echo "=== stopped by:"; sed -n '/^T2DIAG-STOP$/,$p' $R/snap-$T-cdb.txt | grep -E "exception|^ *[0-9a-f]{2} " | cut -c1-220 | head -30
fi
ls -la $R/snap-$T-embedded.S $R/snap-$T-blob.bin 2>&1 | tail -2
