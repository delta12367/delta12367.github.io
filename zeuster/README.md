# Zeuster Enhancements – deobfuscated

`ZeusterEnhancements.deobfuscated.lua` is the Luraph-protected `ZeusterrEnhancements.txt`
lifted back to plain Luau. It compiles with the official Luau compiler (`luau-compile`).

Variable names are reconstructed (the originals are not in the file). Names of UI
elements, options, services and globals come straight from the program's constants.

## Pipeline (`tools/`)

```
python prepare.py ZeusterrEnhancements.txt     # writes head.lua + progdata.pkl
python batch_lift.py noroot                     # lifts all functions except the main chunk -> noroot.pkl
python runroot.py                               # lifts the main chunk (about 4 minutes) -> root.pkl
python assemble.py out.lua root.pkl noroot.pkl  # decompiles everything into one Lua file
luau-compile --null out.lua                     # syntax and compile check
```

Main stages: `interp.py` (Lua interpreter used to boot the VM), `lifter2.py` (abstract
interpretation of the Luraph interpreter closures -> IR), `opt.py`/`ssaspill.py`/`webs.py`
(IR clean-up, variables), `structure.py` (control flow), `patterns.py`/`loops.py`
(`for` loops), `scope.py`/`doblocks.py` (local placement, Luau's 200-local limit),
`emit.py`/`decompile.py` (Lua output).

## Fixes in this round

* `interp.py` now matches real Luau for `string.pack`/`unpack`/`packsize` (`T` and the
  `s` length prefix are 4 bytes, digits only after `i/I/s/c/!`, `X` alignment, range checks),
  `bit32` (negative/large shift counts, field checks), `math` edge cases and NaN equality.
  It was checked against the `luau` CLI with 1,175 differential tests. The old `T` = 8 bytes
  made some jump/operand resolvers compute wrong values (for example a `UDim2.new` read from
  register -2038), which hid about 900 blocks of the main chunk.
* Generic `for` loops in rotated form, loops whose exhaustion path returns, and numeric
  `for` loops with a non-constant step are recognised.
* If/else merge points are found when one branch returns early (previously everything after
  the anti-hook check was emitted twice).
* Every function now lifts without error paths. The lifter falls back to dropping stale
  patch overlays (jump-slot writes that only survived through merges of edges that later
  disappeared) when a function would otherwise fail; this recovered LinoriaLib's
  `AddDropdown` (proto 404) and three partially lifted functions.
* Output is valid Luau: statements starting with `(`, string-constant method calls,
  arguments read straight from `...`, and the 200-local limit (extra locals of a crowded
  block are stored in a `scope` table).

## Known limitations

* Anti-tamper paths are kept and marked `error("tamper detected")`. In the real script these
  overwrite the bytecode (`for i = 1, 20428 do opcodes[i] = {} end`) or spin forever.
* About 4,100 of the 20,413 main-chunk instructions are never reached by the lifter.
  Most are Luraph's decoy filler. A few hold loader code (Luarmor flags such as
  `is_from_loader`, executor checks such as `KRNL_LOADED`, a hardware-ID routine) that is
  not reachable in this build.
* A handful of functions still contain a `-- decompiler:` note where a generic `for` loop
  was not recovered, and a few loops use `exitLoop` flags for multi-level breaks.
