"""Compiles single V8 translation units of the Thumb-2 tree (D:\\cr\\rt8\\src) with the exact flags of the ARM32
release build (F:\\cr\\src\\out\\arm-pgo), minus PGO/LTO, using that build's generated headers. One clang-cl
process per file, run sequentially; no ninja, no gn. Objects go to D:\\cr\\rt8\\src\\out\\t2check.
Usage: python compile_check.py <obj path as ninja names it>... (e.g. obj/v8/v8_base_without_compiler/assembler-arm.obj)
       python compile_check.py --arm   (every object built from v8/src/**/arm/*.cc)"""
import os, re, shlex, subprocess, sys

SRC_OUT = r"F:\cr\src\out\arm-pgo"
NINJA = r"F:\cr\src\third_party\ninja\ninja.exe"
TREE = r"D:\cr\rt8\src"
CHECK_OUT = os.path.join(TREE, "out", "t2check")
CLANG = r"F:\cr\rt21-cr\bin\clang-cl.exe"
DROP = re.compile(r"^(-flto=.*|-fprofile-use=.*|-fsplit-lto-unit|-fwhole-program-vtables|/showIncludes.*|"
                  r"-fcrash-diagnostics-dir=.*)$")


def command_for(obj):
    r = subprocess.run([NINJA, "-C", SRC_OUT, "-t", "commands", obj], capture_output=True, text=True)
    lines = [l for l in r.stdout.splitlines() if " /c " in l and obj.replace("/", "\\") in l.replace("/", "\\")]
    if not lines:
        print(f"SKIP {obj}: no compile command ({r.stderr.strip()[:200]})")
        return None
    return lines[-1]


def split_windows(cmd):
    """Split a command line exactly as CreateProcess / the MS C runtime does (handles \\" inside quoted defines such
    as -DSK_GPU_WORKAROUNDS_HEADER=\\"gpu/...\\"); subprocess re-quotes the list the same way."""
    import ctypes
    from ctypes import wintypes
    f = ctypes.windll.shell32.CommandLineToArgvW
    f.argtypes, f.restype = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)], ctypes.POINTER(wintypes.LPWSTR)
    n = ctypes.c_int()
    p = f(cmd, ctypes.byref(n))
    try:
        return [p[i] for i in range(n.value)]
    finally:
        ctypes.windll.kernel32.LocalFree(p)


def rewrite(cmd, obj):
    out = [CLANG]
    for a in split_windows(cmd)[1:]:
        if DROP.match(a):
            continue
        if a.startswith("/Fo"):
            a = "/Fo" + os.path.join(CHECK_OUT, os.path.basename(obj))
        elif a.startswith("-I") and not a.startswith("-I.."):
            a = "-I" + os.path.join(SRC_OUT, a[2:])  # out-dir relative (gen/...)
        elif a.startswith("/I") and not a.startswith("/I.."):
            a = "/I" + os.path.join(SRC_OUT, a[2:])
        elif a.startswith("@"):
            a = "@" + os.path.join(SRC_OUT, a[1:])
        out.append(a)
    return out


def arm_objects():
    r = subprocess.run([NINJA, "-C", SRC_OUT, "-t", "targets", "all"], capture_output=True, text=True)
    objs = []
    for line in r.stdout.splitlines():
        t = line.split(":")[0].strip()
        if t.startswith("obj/v8/") and t.endswith(".obj") and re.search(r"-arm\.obj$|arm/", t) and "arm64" not in t:
            objs.append(t)
    return sorted(set(objs))


def main():
    objs = arm_objects() if "--arm" in sys.argv else [a for a in sys.argv[1:] if not a.startswith("--")]
    os.makedirs(CHECK_OUT, exist_ok=True)
    cwd = os.path.join(TREE, "out", "arm-pgo")  # so ../../ resolves into the Thumb-2 tree
    os.makedirs(cwd, exist_ok=True)
    bad = 0
    for obj in objs:
        cmd = command_for(obj)
        if cmd is None:
            bad += 1
            continue
        argv = rewrite(cmd, obj)
        r = subprocess.run(argv, cwd=cwd, capture_output=True, text=True)
        errs = [l for l in (r.stdout + r.stderr).splitlines() if "error" in l or "warning" in l]
        print(f"{'OK  ' if r.returncode == 0 else 'FAIL'} {obj}  ({len(errs)} diagnostic line(s))")
        if r.returncode:
            bad += 1
            print("\n".join((r.stdout + r.stderr).splitlines()[:60]))
    print(f"-- {len(objs) - bad}/{len(objs)} compiled")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
