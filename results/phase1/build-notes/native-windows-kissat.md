# Preserved failed native Windows build

Date: 2026-08-16

This build was exploratory and is not used for any comparison. The upstream
Kissat `rel-4.0.4` source was not modified.

Command (MSYS2 UCRT64 GCC 16.1.0):

```sh
cd third_party/kissat
./configure -static
make -j8 kissat
```

Result: failed during compilation because the official source uses POSIX alarm
APIs that MinGW does not provide.

Representative compiler errors:

```text
../src/handle.c:77:29: error: 'SIGALRM' undeclared
../src/handle.c:85:18: error: 'SIGALRM' undeclared
../src/application.c:467:9: error: implicit declaration of function 'alarm'
```

Decision: do not patch upstream solver code. Build both solvers from their
locked commits in the same pinned Debian container instead.

