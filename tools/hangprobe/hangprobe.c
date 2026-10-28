// hangprobe - catch a HANG or a CRASH of a child process and record where it is.
// usage: hangprobe.exe [--debug] <timeout_s> <outdir> <exe> [args...]      (runs <exe> in the current directory)
// Hang: if the child is still running after <timeout_s>: 20 samples of every thread's registers (pc/lr/sp/r0-r12/cpsr on
//   ARM32, rip/rsp on x64) 25 ms apart, the code bytes around the sampled pc and lr (pc-<tid>-<n>.bin / lr-..., base in the
//   log), a full-memory minidump (hang.dmp), then the child is terminated with exit code 0xDEAD.
// --debug: the child runs as a debuggee (DEBUG_ONLY_THIS_PROCESS). On a crash - an illegal or privileged instruction, a
//   breakpoint after the loader's initial one, a fast fail, or any second-chance exception - the exception, the faulting
//   thread's registers, the code bytes around its pc and lr (crash-pc.bin / crash-lr.bin) and a full-memory minidump
//   (crash.dmp) are written and the child is terminated. Other first-chance exceptions go back to the child.
// If the child ends on its own, only its exit code is logged. Child stdout+stderr -> <outdir>\stdout.txt.
// Log -> <outdir>\hangprobe.txt. CreateDirectory only makes the last level, so every missing level is created.
#include <windows.h>
#include <tlhelp32.h>
#include <stdarg.h>
#include <stdio.h>
#include <wchar.h>

static FILE* g_log;
static void logf(const char* fmt, ...) {
  va_list ap;
  va_start(ap, fmt);
  vfprintf(g_log, fmt, ap);
  va_end(ap);
  va_start(ap, fmt);
  vprintf(fmt, ap);
  va_end(ap);
  fflush(g_log);
}

static void append_arg(wchar_t* cmd, size_t cap, const wchar_t* a) {
  if (cmd[0]) wcscat_s(cmd, cap, L" ");
  if (wcschr(a, L' ') || wcschr(a, L'\t') || !a[0]) {
    wcscat_s(cmd, cap, L"\"");
    wcscat_s(cmd, cap, a);
    wcscat_s(cmd, cap, L"\"");
  } else {
    wcscat_s(cmd, cap, a);
  }
}

static void make_dirs(const wchar_t* dir) {
  wchar_t p[MAX_PATH];
  wcsncpy_s(p, MAX_PATH, dir, _TRUNCATE);
  for (wchar_t* s = p; *s; s++) {
    if ((*s == L'\\' || *s == L'/') && s > p && s[-1] != L':') {
      wchar_t c = *s;
      *s = 0;
      CreateDirectoryW(p, NULL);
      *s = c;
    }
  }
  CreateDirectoryW(p, NULL);
}

static void dump_code(HANDLE proc, const wchar_t* outdir, const wchar_t* name, const char* what, DWORD64 addr) {
  BYTE buf[1024];
  SIZE_T got = 0;
  DWORD64 base = (addr & ~(DWORD64)1) - 512;
  if (!ReadProcessMemory(proc, (LPCVOID)(ULONG_PTR)base, buf, sizeof(buf), &got) || got == 0) {
    base = addr & ~(DWORD64)1;  // the page before may be unmapped: try from the address itself
    if (!ReadProcessMemory(proc, (LPCVOID)(ULONG_PTR)base, buf, 512, &got)) got = 0;
  }
  wchar_t path[MAX_PATH];
  swprintf_s(path, MAX_PATH, L"%ls\\%ls.bin", outdir, name);
  FILE* f = _wfopen(path, L"wb");
  if (f) {
    fwrite(buf, 1, got, f);
    fclose(f);
  }
  logf("    %s 0x%llX: %zu bytes from 0x%llX -> %ls\n", what, (unsigned long long)addr, (size_t)got,
       (unsigned long long)base, path);
}

static void log_context(const char* tag, DWORD tid, const CONTEXT* ctx, ULONGLONG user_ms) {
#if defined(_M_ARM)
  logf("  %s tid %lu user %llu ms pc 0x%08lX lr 0x%08lX sp 0x%08lX cpsr 0x%08lX r0 %08lX r1 %08lX r2 %08lX r3 %08lX "
       "r4 %08lX r5 %08lX r6 %08lX r7 %08lX r8 %08lX r9 %08lX r10 %08lX r11 %08lX r12 %08lX\n",
       tag, tid, user_ms, ctx->Pc, ctx->Lr, ctx->Sp, ctx->Cpsr, ctx->R0, ctx->R1, ctx->R2, ctx->R3, ctx->R4, ctx->R5,
       ctx->R6, ctx->R7, ctx->R8, ctx->R9, ctx->R10, ctx->R11, ctx->R12);
#elif defined(_M_X64)
  logf("  %s tid %lu user %llu ms rip 0x%016llX rsp 0x%016llX\n", tag, tid, user_ms, ctx->Rip, ctx->Rsp);
#else
  logf("  %s tid %lu user %llu ms eip 0x%08lX\n", tag, tid, user_ms, ctx->Eip);
#endif
}

static DWORD64 ctx_pc(const CONTEXT* c) {
#if defined(_M_ARM)
  return c->Pc;
#elif defined(_M_X64)
  return c->Rip;
#else
  return c->Eip;
#endif
}

typedef BOOL(WINAPI* MiniDumpWriteDump_t)(HANDLE, DWORD, HANDLE, int, void*, void*, void*);
typedef struct {  // dbghelp's MINIDUMP_EXCEPTION_INFORMATION (dbghelp.h is not included: no import lib needed)
  DWORD ThreadId;
  PEXCEPTION_POINTERS ExceptionPointers;
  BOOL ClientPointers;
} MdExceptionInfo;

static void write_dump(HANDLE proc, DWORD pid, const wchar_t* outdir, const wchar_t* name, MdExceptionInfo* mdei) {
  HMODULE dbghelp = LoadLibraryW(L"dbghelp.dll");
  MiniDumpWriteDump_t write =
      dbghelp ? (MiniDumpWriteDump_t)(void*)GetProcAddress(dbghelp, "MiniDumpWriteDump") : NULL;
  if (!write) {
    logf("dbghelp!MiniDumpWriteDump not available - no dump\n");
    return;
  }
  wchar_t path[MAX_PATH];
  swprintf_s(path, MAX_PATH, L"%ls\\%ls", outdir, name);
  HANDLE f = CreateFileW(path, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, 0, NULL);
  // MiniDumpWithFullMemory (2) | MiniDumpWithHandleData (4) | MiniDumpWithThreadInfo (0x1000)
  BOOL ok = write(proc, pid, f, 0x2 | 0x4 | 0x1000, mdei, NULL, NULL);
  DWORD e = ok ? 0 : GetLastError();
  if (!ok && mdei) {  // as dbgrun: retry without the exception stream (the exception is in the log)
    CloseHandle(f);
    f = CreateFileW(path, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, 0, NULL);
    ok = write(proc, pid, f, 0x2 | 0x4 | 0x1000, NULL, NULL, NULL);
    logf("minidump %s without the exception stream (with it: error 0x%08lX) -> %ls\n", ok ? "written" : "FAILED", e,
         path);
  } else {
    logf("minidump %s (error %lu) -> %ls\n", ok ? "written" : "FAILED", e, path);
  }
  CloseHandle(f);
}

static void sample_hang(PROCESS_INFORMATION* pi, const wchar_t* outdir) {
  for (int n = 0; n < 20; n++) {
    HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0);
    THREADENTRY32 te;
    ZeroMemory(&te, sizeof(te));
    te.dwSize = sizeof(te);
    for (BOOL ok = Thread32First(snap, &te); ok; ok = Thread32Next(snap, &te)) {
      if (te.th32OwnerProcessID != pi->dwProcessId) continue;
      HANDLE th = OpenThread(THREAD_SUSPEND_RESUME | THREAD_GET_CONTEXT | THREAD_QUERY_INFORMATION, FALSE,
                             te.th32ThreadID);
      if (!th) continue;
      SuspendThread(th);
      CONTEXT ctx;
      ZeroMemory(&ctx, sizeof(ctx));
      ctx.ContextFlags = CONTEXT_FULL;
      FILETIME c, e, k, u;
      GetThreadTimes(th, &c, &e, &k, &u);
      ULONGLONG user_ms = (((ULONGLONG)u.dwHighDateTime << 32) | u.dwLowDateTime) / 10000;
      char tag[16];
      sprintf_s(tag, sizeof(tag), "s%02d", n);
      if (GetThreadContext(th, &ctx)) {
        log_context(tag, te.th32ThreadID, &ctx, user_ms);
        if (n == 0 || n == 19) {
          wchar_t name[64];
          swprintf_s(name, 64, L"pc-%lu-%d", te.th32ThreadID, n);
          dump_code(pi->hProcess, outdir, name, "pc", ctx_pc(&ctx));
#if defined(_M_ARM)
          swprintf_s(name, 64, L"lr-%lu-%d", te.th32ThreadID, n);
          dump_code(pi->hProcess, outdir, name, "lr", ctx.Lr);
#endif
        }
      } else {
        logf("  %s tid %lu GetThreadContext failed %lu\n", tag, te.th32ThreadID, GetLastError());
      }
      ResumeThread(th);
      CloseHandle(th);
    }
    CloseHandle(snap);
    Sleep(25);
  }
  write_dump(pi->hProcess, pi->dwProcessId, outdir, L"hang.dmp", NULL);
}

static int is_fatal(DWORD code, BOOL first, BOOL* seen_initial_bp) {
  if (code == EXCEPTION_BREAKPOINT && !*seen_initial_bp) {
    *seen_initial_bp = TRUE;
    return 0;
  }
  if (!first) return 1;
  return code == EXCEPTION_ILLEGAL_INSTRUCTION || code == EXCEPTION_PRIV_INSTRUCTION || code == EXCEPTION_BREAKPOINT ||
         code == 0xC0000409 /* fast fail */ || code == 0xC0000374 /* heap corruption */;
}

static int run_debug(PROCESS_INFORMATION* pi, const wchar_t* outdir, int timeout_s) {
  BOOL seen_initial_bp = FALSE;
  ULONGLONG start = GetTickCount64();
  for (;;) {
    DEBUG_EVENT ev;
    if (!WaitForDebugEvent(&ev, 200)) {
      if (GetTickCount64() - start > (ULONGLONG)timeout_s * 1000) {
        logf("child still running after %d s: sampling\n", timeout_s);
        sample_hang(pi, outdir);
        TerminateProcess(pi->hProcess, 0xDEAD);
        logf("child terminated (0xDEAD)\n");
        start = (ULONGLONG)-1 / 2;  // no second sampling; wait for the exit event
      }
      continue;
    }
    DWORD cont = DBG_CONTINUE;
    switch (ev.dwDebugEventCode) {
      case CREATE_PROCESS_DEBUG_EVENT:
        if (ev.u.CreateProcessInfo.hFile) CloseHandle(ev.u.CreateProcessInfo.hFile);
        break;
      case LOAD_DLL_DEBUG_EVENT:
        if (ev.u.LoadDll.hFile) CloseHandle(ev.u.LoadDll.hFile);
        break;
      case EXCEPTION_DEBUG_EVENT: {
        EXCEPTION_RECORD rec = ev.u.Exception.ExceptionRecord;
        BOOL first = ev.u.Exception.dwFirstChance;
        BOOL initial = rec.ExceptionCode == EXCEPTION_BREAKPOINT && !seen_initial_bp;
        if (!is_fatal(rec.ExceptionCode, first, &seen_initial_bp)) {
          if (!initial)
            logf("  (%s-chance exception 0x%08lX at 0x%p handed back to the child)\n", first ? "first" : "second",
                 rec.ExceptionCode, rec.ExceptionAddress);
          cont = rec.ExceptionCode == EXCEPTION_BREAKPOINT ? DBG_CONTINUE : DBG_EXCEPTION_NOT_HANDLED;
          break;
        }
        logf("CRASH: %s-chance exception 0x%08lX at 0x%p (thread %lu)", first ? "first" : "second",
             rec.ExceptionCode, rec.ExceptionAddress, ev.dwThreadId);
        for (DWORD i = 0; i < rec.NumberParameters && i < 4; i++) logf(" p%lu=0x%p", i, (void*)rec.ExceptionInformation[i]);
        logf("\n");
        HANDLE th = OpenThread(THREAD_GET_CONTEXT | THREAD_QUERY_INFORMATION, FALSE, ev.dwThreadId);
        __declspec(align(16)) CONTEXT ctx;  // x64 CONTEXT must be 16-byte aligned
        ZeroMemory(&ctx, sizeof(ctx));
        ctx.ContextFlags = CONTEXT_FULL;
        if (th && GetThreadContext(th, &ctx)) {
          log_context("crash", ev.dwThreadId, &ctx, 0);
          dump_code(pi->hProcess, outdir, L"crash-pc", "pc", ctx_pc(&ctx));
#if defined(_M_ARM)
          dump_code(pi->hProcess, outdir, L"crash-lr", "lr", ctx.Lr);
#endif
          EXCEPTION_POINTERS ep = {&rec, &ctx};
          MdExceptionInfo mdei = {ev.dwThreadId, &ep, FALSE};
          write_dump(pi->hProcess, pi->dwProcessId, outdir, L"crash.dmp", &mdei);
        } else {
          logf("  GetThreadContext failed %lu\n", GetLastError());
          dump_code(pi->hProcess, outdir, L"crash-pc", "address", (DWORD64)(ULONG_PTR)rec.ExceptionAddress);
          write_dump(pi->hProcess, pi->dwProcessId, outdir, L"crash.dmp", NULL);
        }
        if (th) CloseHandle(th);
        TerminateProcess(pi->hProcess, 0xDEAD);
        logf("child terminated (0xDEAD)\n");
        break;
      }
      case EXIT_PROCESS_DEBUG_EVENT:
        logf("child exited: exit code 0x%lX\n", ev.u.ExitProcess.dwExitCode);
        ContinueDebugEvent(ev.dwProcessId, ev.dwThreadId, DBG_CONTINUE);
        return 0;
      default:
        break;
    }
    ContinueDebugEvent(ev.dwProcessId, ev.dwThreadId, cont);
  }
}

int wmain(int argc, wchar_t** argv) {
  int debug = 0, a = 1;
  if (argc > 1 && !wcscmp(argv[1], L"--debug")) {
    debug = 1;
    a = 2;
  }
  if (argc < a + 3) {
    fwprintf(stderr, L"usage: hangprobe.exe [--debug] <timeout_s> <outdir> <exe> [args...]\n");
    return 2;
  }
  int timeout_s = _wtoi(argv[a]);
  const wchar_t* outdir = argv[a + 1];
  make_dirs(outdir);
  wchar_t path[MAX_PATH];
  swprintf_s(path, MAX_PATH, L"%ls\\hangprobe.txt", outdir);
  g_log = _wfopen(path, L"w");
  if (!g_log) {
    fwprintf(stderr, L"hangprobe: cannot create %ls (error %lu)\n", path, GetLastError());
    return 3;
  }
  static wchar_t cmd[32768];
  for (int i = a + 2; i < argc; i++) append_arg(cmd, 32768, argv[i]);
  logf("hangprobe 2%s: timeout %d s, command: %ls\n", debug ? " (debug)" : "", timeout_s, cmd);

  SECURITY_ATTRIBUTES sa = {sizeof(sa), NULL, TRUE};
  swprintf_s(path, MAX_PATH, L"%ls\\stdout.txt", outdir);
  HANDLE out = CreateFileW(path, GENERIC_WRITE, FILE_SHARE_READ, &sa, CREATE_ALWAYS, 0, NULL);
  STARTUPINFOW si;
  ZeroMemory(&si, sizeof(si));
  si.cb = sizeof(si);
  si.dwFlags = STARTF_USESTDHANDLES;
  si.hStdOutput = out;
  si.hStdError = out;
  si.hStdInput = GetStdHandle(STD_INPUT_HANDLE);
  PROCESS_INFORMATION pi;
  if (!CreateProcessW(NULL, cmd, NULL, NULL, TRUE, debug ? DEBUG_ONLY_THIS_PROCESS : 0, NULL, NULL, &si, &pi)) {
    logf("CreateProcess failed: error %lu\n", GetLastError());
    return 4;
  }
  if (debug) return run_debug(&pi, outdir, timeout_s);

  DWORD w = WaitForSingleObject(pi.hProcess, (DWORD)timeout_s * 1000);
  if (w == WAIT_OBJECT_0) {
    DWORD code = 0;
    GetExitCodeProcess(pi.hProcess, &code);
    logf("child ended by itself: exit code 0x%lX - no hang\n", code);
    return 0;
  }
  logf("child still running after %d s: sampling\n", timeout_s);
  sample_hang(&pi, outdir);
  TerminateProcess(pi.hProcess, 0xDEAD);
  logf("child terminated (0xDEAD)\n");
  return 1;
}
