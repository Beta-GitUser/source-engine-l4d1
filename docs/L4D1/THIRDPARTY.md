# Third-party code and the local patches to it

This tree vendors other people's code. Patching it is allowed — every licence
here is permissive — but a patch that nobody records is a patch that gets
dropped on the next update, and then the same bug comes back wearing a
different hat. This file is the record.

| Component | Upstream | Licence | Local changes | Guarded by |
| --- | --- | --- | --- | --- |
| `common/sse2neon.h` | [DLTcollab/sse2neon](https://github.com/DLTcollab/sse2neon) | MIT (text at the top of the file) | `float64x2_t` reinterpret intrinsics defined for GCC 4.9 | `l4d1ctl/tests/test_sse2neon.py` |
| `waflib/` (inside `waf`) | [waf](https://gitlab.com/ita1024/waf) 2.0.26 | BSD-3-Clause | none; the `waf` script carries it embedded | — |
| `scripts/waifulib/` | this fork (nillerusr's tree) | as the repository | maintained here, not vendored | `scripts/test-tooling.sh` |
| `lib/android/*` prebuilts | see the submodule's own licence file | per component | none in git; rebuilt only when the toolchain changes | — |
| Android NDK r10e | downloaded by `scripts/build-android.sh` | Android NDK licence | not vendored, fetched on demand | workflow cache key |

## What we may do, in one line

The MIT text in `sse2neon.h` grants the right to "use, copy, modify, merge,
publish, distribute, sublicense, and/or sell". Modification is explicitly
permitted. The only condition is that the copyright and permission notice stay
in copies — so: **never delete the licence block**, and add your note below it.
There is no obligation to publish the change, and MIT imposes no conditions on
the rest of this repository.

## Adding a patch to vendored code

1. Keep the patch as small and as *findable* as possible. Prefer a named macro
   or a clearly delimited block over scattered edits, so `grep` finds it and a
   future merge does not eat it.
2. Add a dated bullet to the file's `Modifications in this fork` comment block
   (or the equivalent at the top of the file) saying what changed and why.
3. Add a row to the table above, naming the test that fails if the patch is
   lost.
4. Make that test exist. A patch with no test is a patch that returns as a
   build failure six months later.

## Updating `sse2neon.h`

The point of the structural test is that it fails loudly, and tells you what to
do, when the patch is missing:

```sh
# 1. Take the new upstream header.
curl -o common/sse2neon.h \
  https://raw.githubusercontent.com/DLTcollab/sse2neon/master/sse2neon.h

# 2. Ask what broke. Missing coverage is reported per intrinsic name.
python3 -m unittest l4d1ctl.tests.test_sse2neon

# 3. Re-apply: the "Modifications in this fork" note, then the
#    SSE2NEON_DEFINE_REINTERPRET block, covering every name step 2 listed.
#    Newer upstream releases may use intrinsics GCC 4.9 never had; add each to
#    the shim the same way - a bit cast is a bit cast.

# 4. Re-run the behavioural tests: they prove the shim casts rather than
#    converts (a value conversion would lose the sign bit of -0.0).
python3 -m unittest l4d1ctl.tests.test_sse2neon -v
```

If a future upstream release depends on NEON features that GCC 4.9 simply does
not have, the shim stops being the cheap answer and the real fix is a newer NDK
(see "Why the first Android builds fail" in [BUILDING.md](BUILDING.md)) — a
toolchain migration, not another patch.

## Why the prebuilts are listed as "no local changes"

`lib/android/*/libSDL2.so` is a binary that was linked against a different libc
version than NDK r10e's, which is why the Android link needs
`-Wl,--allow-shlib-undefined`. Nobody patched it; it is simply older than the
rest of the toolchain. The permanent fix is rebuilding it with r10e, at which
point that flag can go, and this row gets a real "rebuilt with r10e" note.
