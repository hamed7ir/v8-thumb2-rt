// Minimal stand-in for V8's src/base/logging.h so t32-encodings-arm.h can be
// compiled and tested on its own (F:\cr\thumb2\tests\t32enc).
#ifndef T32ENC_STUB_LOGGING_H_
#define T32ENC_STUB_LOGGING_H_
#include <cstdio>
#include <cstdlib>
#define CHECK(c)                                                       \
  do {                                                                 \
    if (!(c)) {                                                        \
      std::fprintf(stderr, "CHECK failed: %s (%s:%d)\n", #c, __FILE__, \
                   __LINE__);                                          \
      std::abort();                                                    \
    }                                                                  \
  } while (0)
#define CHECK_EQ(a, b) CHECK((a) == (b))
#define DCHECK(c) CHECK(c)
#endif  // T32ENC_STUB_LOGGING_H_
