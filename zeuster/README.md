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

* Luraph's encrypted string constants are decrypted. Ten small closures are called inline
  with an encrypted argument; they check the VM's own register table
  (`for k, v in next, registers`), which only works inside the VM, so the decompiled copies
  looped forever (the asset loader hung). `constfold.py` runs each such call with the
  original VM in Luau and replaces it by the result, for example
  `U1(decoded, "ender and re- rem on top.")` (the asset XOR key) and the kick messages
  `"buffer.fromstring hooked"`, `"__iter m table"`, ... Results are cached in
  `constfold_cache.json`.

## Verification (`oracle/`)

The original script (with its Luraph VM) and the decompiled file are run under the same
deterministic mock of Roblox and the executor API in the real `luau` CLI. Every interaction
with the mocked world (service and property reads/writes, `Instance.new`, method calls,
signal connections, `task` scheduling, file and HTTP calls, ...) is written to a trace.

```
cd oracle
./run.sh ../ZeusterEnhancements.deobfuscated.lua   # needs ../bin/luau and ../data/z.lua
```

The run executes the main script, the queued threads and signal handlers, and then flips
every registered UI toggle and option (129 toggles, 329 options). Current result:

* 2,631,033 trace events on both sides; all 1,610,557 events other than plain reads are
  identical and in the same order.
* The 1,020,476 reads are identical as a multiset; 16 are swapped in order. Luraph's VM
  looks a method up before evaluating the arguments (`SELF`), Luau's `obj:m(args)` looks it
  up after them. Reading a method of an Instance has no side effects.
* Both runs iterate tables in a canonical key order (`__ITER` in the harness). Lua leaves
  `next`/`pairs` order unspecified and it depends on how a table was filled: the VM fills
  tables key by key, the decompiled code uses table constructors.
* The mock does not provide the contents of the downloaded asset bundle, so both runs stop
  the main chunk at the same point (`asset.main.particles`), after which the remaining
  phases (threads, signals, UI) still run.

## Known limitations

* Anti-tamper paths are kept and marked `error("tamper detected")` (8 places). In the real
  script these overwrite the bytecode (`for i = 1, 20428 do opcodes[i] = {} end`) or spin
  forever. None of them is reached in the oracle run.
* About 4,100 of the 20,413 main-chunk instructions are never reached by the lifter.
  Most are Luraph's decoy filler. A few hold loader code (Luarmor flags such as
  `is_from_loader`, executor checks such as `KRNL_LOADED`, a hardware-ID routine) that is
  not reachable in this build.
* A handful of functions still contain a `-- decompiler:` note where a generic `for` loop
  was not recovered, and a few loops use `exitLoop` flags for multi-level breaks.
