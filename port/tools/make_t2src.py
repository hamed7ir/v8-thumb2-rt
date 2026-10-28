"""F:\\cr\\t2src - the source tree of the incremental Thumb-2 browser build (his idea, 2026-10-27: "we are doing changes
to v8 only, can't we do an incremental build ... i don't care if it becomes a dirty build").

  link     hard-link copy of the release tree F:\\cr\\src without out\\ and .git: every file shares its data with
           F:\\cr\\src (no disk space), symlinks are recreated with the same target and type.
  install  the port's files (every path under F:\\cr\\thumb2\\pristine) become NEW files: the shared link is removed
           (the data stays for F:\\cr\\src) and the Thumb-2 version is copied from D:\\cr\\rt8\\src.
  verify   every port file in t2src has link count 1 and equals the D: version; every port file in F:\\cr\\src still
           equals its pristine copy (the release tree was not touched); a sample of shared files is byte-identical.

Nothing here opens a file under F:\\cr\\t2src for writing in place, so F:\\cr\\src can never change through a link.
The out dir is copied separately (robocopy of out\\arm-pgo, see run_detached_t2_pgo.sh)."""
import ctypes, filecmp, os, random, shutil, stat, sys

SRC, T2, D = "F:\\cr\\src", "F:\\cr\\t2src", "D:\\cr\\rt8\\src"
PRISTINE = "F:\\cr\\thumb2\\pristine"
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.CreateSymbolicLinkW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
k32.CreateSymbolicLinkW.restype = ctypes.c_ubyte
DIRATTR = stat.FILE_ATTRIBUTE_DIRECTORY


def port_files():
    out = []
    for root, _, files in os.walk(PRISTINE):
        for f in files:
            rel = os.path.relpath(os.path.join(root, f), PRISTINE)
            out.append(rel[:-len(".absent")] if rel.endswith(".absent") else rel)
    return sorted(out)


def link_tree():
    if os.path.exists(T2):
        sys.exit(T2 + " exists - not touching it")
    n_files = n_links = n_dirs = 0
    junctions = []
    stack = [""]
    while stack:
        rel = stack.pop()
        os.makedirs(os.path.join(T2, rel), exist_ok=True)
        n_dirs += 1
        with os.scandir(os.path.join(SRC, rel)) as it:
            for e in it:
                r = os.path.join(rel, e.name)
                if rel == "" and e.name in ("out", ".git"):
                    continue
                if e.name == ".git":
                    continue
                if e.is_junction():
                    junctions.append(r)
                    continue
                if e.is_symlink():
                    want_dir = bool(os.lstat(e.path).st_file_attributes & DIRATTR)
                    if not k32.CreateSymbolicLinkW(os.path.join(T2, r), os.readlink(e.path),
                                                   (1 if want_dir else 0) | 2):
                        raise OSError(ctypes.get_last_error(), "CreateSymbolicLinkW " + r)
                    n_links += 1
                elif e.is_dir(follow_symlinks=False):
                    stack.append(r)
                else:
                    os.link(e.path, os.path.join(T2, r))
                    n_files += 1
    print(f"linked {n_files} files, {n_dirs} dirs, recreated {n_links} symlinks; junctions skipped: {junctions}")


def install():
    for rel in port_files():
        dst, new = os.path.join(T2, rel), os.path.join(D, rel)
        if os.path.exists(dst):
            if filecmp.cmp(dst, new, shallow=False) and os.stat(dst).st_nlink == 1:
                st = os.stat(new)
                os.utime(dst, (st.st_atime, st.st_mtime))  # unchanged: keep ninja's view (no needless rebuild)
                continue
            os.remove(dst)  # drops this name only; F:\cr\src keeps the data
        shutil.copy2(new, dst)  # the Thumb-2 tree's mtime (when the rule wrote it), not "now"
    print(f"installed {len(port_files())} port files as new files")


def verify():
    bad = 0
    for rel in port_files():
        dst, new, rel_src = os.path.join(T2, rel), os.path.join(D, rel), os.path.join(SRC, rel)
        if os.stat(dst).st_nlink != 1 or not filecmp.cmp(dst, new, shallow=False):
            print("T2 NOT THE PORT VERSION / STILL SHARED:", rel); bad += 1
        pr = os.path.join(PRISTINE, rel)
        if os.path.exists(pr):
            if not filecmp.cmp(rel_src, pr, shallow=False):
                print("RELEASE TREE CHANGED:", rel); bad += 1
        elif os.path.exists(rel_src):
            print("RELEASE TREE HAS THE NEW FILE:", rel); bad += 1
    sample = []
    for root, dirs, files in os.walk(os.path.join(T2, "v8", "src")):
        sample += [os.path.join(root, f) for f in files]
    random.seed(20261027)
    port = {os.path.join(T2, r) for r in port_files()}
    for p in random.sample([p for p in sample if p not in port], 300):
        if not filecmp.cmp(p, SRC + p[len(T2):], shallow=False):
            print("SHARED FILE DIFFERS:", p); bad += 1
    print("verify:", "OK" if not bad else f"{bad} problem(s)")
    return bad


if __name__ == "__main__":
    {"link": link_tree, "install": install, "verify": lambda: sys.exit(1 if verify() else 0)}[sys.argv[1]]()
