#!/usr/bin/env bash
# One mksnapshot iteration for the Thumb-2 port: rebuild the host mksnapshot in out\t2-arm (-j8), run it under cdb with
# the build's exact flags (outputs into runs\), print the crash stack or "SNAPSHOT OK". Read-only for the build's own
# gen/v8/embedded.S and snapshot_blob.bin. usage: bash snap_iter.sh [tag]
source D:/repo/venetium/env/build-env.sh > /dev/null 2>&1 || exit 2
OUT=/d/cr/rt8/src/out/t2-arm; R=/f/cr/thumb2/runs; T=${1:-$(date +%H%M%S)}
RW='F:\cr\thumb2\runs'
cd $OUT || exit 2
$NINJA -C . -j 8 clang_x86_v8_arm/mksnapshot.exe > $R/snap-$T-ninja.log 2>&1 || { echo "BUILD FAILED"; grep -A30 "^FAILED" $R/snap-$T-ninja.log | head -60; exit 1; }
tail -1 $R/snap-$T-ninja.log
timeout 1800 "/d/Windows Kits/10/Debuggers/x86/cdb.exe" -g -lines -y 'D:\cr\rt8\src\out\t2-arm\clang_x86_v8_arm' \
  -c "r; kn 40; q" ./clang_x86_v8_arm/mksnapshot.exe --turbo_instruction_scheduling --turbo-always-optimize-spills \
  --target_os=win --target_arch=arm --embedded_src "$RW\\snap-$T-embedded.S" --startup_blob "$RW\\snap-$T-blob.bin" \
  --predictable --no-use-ic --turbo-elide-frames --turbo-profiling-input ../../v8/tools/builtins-pgo/profiles/x86-rl.profile --warn-about-builtin-profile-data --reorder-builtins --embedded_variant Default --random-seed 314159265 --no-native-code-counters --concurrent-builtin-generation --concurrent-turbofan-max-threads=0 > $R/snap-$T-cdb.txt 2>&1
if grep -q "exception - code" $R/snap-$T-cdb.txt; then
  grep -E "exception - code|^eax|^eip|^ *[0-9a-f]{2} " $R/snap-$T-cdb.txt | cut -c1-230 | head -45
else
  ls -la $R/snap-$T-embedded.S $R/snap-$T-blob.bin 2>&1
  grep -iE "error|fatal|warning" $R/snap-$T-cdb.txt | head -20
  echo "SNAPSHOT OK? (exit status in $R/snap-$T-cdb.txt)"
fi
