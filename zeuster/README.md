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
python assemble.py out.lua root.pkl noroot.pkl  # decompiles everything into one Lua file (then rename.py)
luau-compile --null out.lua                     # syntax and compile check
```

Main stages: `interp.py` (Lua interpreter used to boot the VM), `lifter2.py` (abstract
interpretation of the Luraph interpreter closures -> IR), `opt.py`/`ssaspill.py`/`webs.py`
(IR clean-up, variables), `structure.py` (control flow), `patterns.py`/`loops.py`
(`for` loops), `scope.py`/`regscope.py`/`doblocks.py` (local placement, scopes,
Luau's 200-local limit), `constfold.py` (encrypted constants),
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

* Scopes are rebuilt from the VM's register allocation (`regscope.py`). Luraph's compiler
  gives each local the next free register and frees them when a block ends, so a register
  that is reused marks where the original `do ... end` block closed; a local declared in a
  lower register than the locals just before it was declared earlier and assigned at the end
  of that block. This replaced the old fallback that stored extra locals in `scope` tables
  (710 references) and removed the deep `do` nesting (maximum indentation 496 -> 72 columns).
* Numeric `for` loops whose body uses the counter directly are recognised (16 more loops).

* Generic `for` loops whose iterator check reads `if ok then BODY else EXIT end`, or whose
  exit spins forever (`while true do end`), are recognised; no `-- decompiler:` notes remain.
* Specialised interpreter templates can keep program variables in interpreter locals. When
  treating such locals as data removes at least 20% of the duplicated blocks, the lifter now
  accepts it even though the locals become variables in the output: the ragebot weapon
  selection (proto 4320) shrank from 1,160 tangled lines to 100.

* `rename.py` renames generic locals (`v12`, `tbl3`, `value030`, `arg1`, ...) from how they
  are used, on the final source parsed with `luau-ast`: what they are initialised or later
  assigned with (`GetService`, `FindFirstChild`, `Instance.new`, `Create("Frame", ...)`,
  `GetX()` calls, wrapped functions), the field they are stored under, the fields and methods
  used on them (`.Character` -> `player`, `.Humanoid` -> `character`, `:Disconnect` ->
  `connection`, ...), what a loop iterates (`:GetChildren()` -> `child`), callback positions
  (`InputBegan` -> `input, gameProcessed`, `OnChanged` -> `value`), metamethod parameters and
  `self`. A name is only used when no other binding with that name has an overlapping scope and
  no global of that name is referenced inside the local's scope. 1,326 locals get names this
  way, for example the script's central state table is now `state`.

* A function whose error path reads patch slots that were merged away upstream is lifted
  once more with block states split on those slots (last-resort fallback, accepted only when
  it leaves fewer error/tamper paths). This recovered the "item status off" branch, which
  had been emitted as a tamper path although it is ordinary code
  (`list[name] = nil; WeaponStatusHandler:ApplyItemStatusToText(name, item.ClientItem.Info.Status)`).
* Block variants that only differ by passing through a first-run resolver are merged: dead
  scratch-register writes are dropped (never field reads, parameter copies or calls) and the
  resulting empty pass-through blocks are bypassed.

* Helpers the decompiled code introduces itself (`select` for vararg slices, the multi-value
  table fill) are bound once at the top of the file (`local __select, __pack, __move = ...`).
  The script replaces the environments of some of its hook functions with `setfenv`; the VM
  handles varargs without a global lookup, so the decompiled code must not depend on that
  environment either. Found by the fuzz phase below.

* Expressions that can raise or run a metamethod (field reads, arithmetic, concatenation,
  ordering comparisons, `#`) are no longer moved past side effects or past each other when a
  temporary is inlined, so the original evaluation order is kept (for example the camera's
  `LookVector` is read before `Position`, as in the VM). Found by the fuzz phase; it costs
  29 extra locals.

* Functions the script creates but never calls are kept. Creating a closure has no side
  effects, so the optimizer deleted the unused assignment together with the function body;
  9 functions were missing for this reason, for example a cosmetics-button builder in the
  skin changer and several main-chunk helpers. They are emitted as ordinary locals now.
* A string-library function called on a string literal is written as a method call on it,
  `("...%s..."):format(x)` (9 places), as in the original. It previously came out as
  `string.format("...%s...", x)`, which needs the global `string` and breaks inside the hook
  functions whose environment the script replaces. Found by the unit round below.

## Verification (`oracle/`)

The original script (with its Luraph VM) and the decompiled file are run under the same
deterministic mock of Roblox and the executor API in the real `luau` CLI. Every interaction
with the mocked world (service and property reads/writes, `Instance.new`, method calls,
signal connections and firing, `task` scheduling, file and HTTP calls, ...) is written to a
trace, and the two traces are compared.

```
cd oracle
./run.sh ../ZeusterEnhancements.deobfuscated.lua   # needs ../bin/luau and ../data/z.lua
python3 segdiff.py o.tr d.tr                       # reads between matching events
python3 gapcheck.py orig.txt root.pkl noroot.pkl    # executed VM code missing from the lift
python3 covstat.py orig.txt root.pkl noroot.pkl     # coverage

# with the fuzz phase: build the decompiled file with closure registration first
ORACLE_REG=1 python ../tools/assemble.py out_reg.lua root.pkl noroot.pkl
# unit round: standalone texts and upvalue specs of the functions in unit_pids.txt
ORACLE_REG=1 python ../tools/unitgen.py units.lua unitspec.luau $(cat unit_pids.txt)
./run.sh out_reg.lua                               # uses fuzz_pids.txt, unit_pids.txt
python3 unitdiff.py o.tr d.tr                      # call-by-call comparison
python3 readcheck.py                               # reads, call by call
```

The run executes the main chunk to the end, the queued threads and signal handlers, then
flips every registered UI toggle and option (130 toggles, 332 options), and finally turns
every toggle on and runs five frames of RenderStepped/Heartbeat/render-step bindings with
two other players in the game. The embedded
asset bundle is decoded by the script itself; `rbxm.py`/`gen_asset.py` read its instance
tree (943 instances) so `DeserializeInstancesAsync` returns the real hierarchy.

Current result (`unitdiff.py`, `readcheck.py`):

* The main run, then 250 fuzz calls, 3,708 fuzz calls with argument profiles and 4,254
  unit calls (below). 3,833,257 events other than plain property reads in the original's
  trace. After normalising error positions and two error wordings that differ between the
  VM and Luau for the same failure (calling a missing method, a numeric `for` over a
  non-number), the main run and 8,206 of the 8,212 calls are identical, event for event.
* The other 6 calls differ in one known way: `obj:m(args)` where looking `m` up fails (a
  number or nil passed as `obj`). Luraph's VM looks the method up before evaluating the
  arguments (`SELF`) and fails at once; Luau evaluates the arguments first, so the decompiled
  side logs those argument events and then raises the same error.
* Reads (`readcheck.py`, main run and fuzz calls): 30,075 method lookups are moved past
  their arguments, and once, when an argument fails, the original's lookup happens and the
  decompiled one does not; the same `SELF`/`obj:m(args)` ordering as above. Reading a method
  of an Instance has no side effects.
* Both runs iterate tables in a canonical key order (`__ITER` in the harness). Lua leaves
  `next`/`pairs` order unspecified and it depends on how a table was filled: the VM fills
  tables key by key, the decompiled code uses table constructors.
* Fuzz phase: both runs register every closure they create (`vmState[56]` in the VM, a
  `__REG(function ... end, pid)` wrapper in an `ORACLE_REG=1` build of the decompiled file,
  which is otherwise identical text). Functions the normal run never executed are called
  with placeholder arguments (`fuzzlist.py` builds `fuzz_pids.txt`), and every registered
  function is called with six argument profiles (objects, numbers, strings, booleans, nil,
  tables). Closures that only the original creates are the bodies inside the folded
  encrypted-constant closures.
* Unit round: every one of the 709 emitted functions (all but the main chunk) is also built
  on its own, so code the mocked game never reaches is compared too. The original run
  builds it with the VM's closure constructor, the decompiled run from its standalone text
  (`unitgen.py`); both get the same upvalues (mock objects, boxed or not as the VM passes
  them, and the number a function's own integrity check expects) and the same six argument
  profiles.
* All 710 emitted functions run, covering 88.2% of their lifted VM instructions
  (`covstat.py`; the folded constant closures are not counted). `gapcheck.py` confirms that
  every executed instruction is in the lifted code, except Luraph's first-run jump resolvers
  (which only patch operand slots) and the encrypted-constant closures that are folded away.

## Known limitations

* Anti-tamper paths are kept and marked `error("tamper detected")` (7 places). In the real
  script these overwrite the bytecode (`for i = 1, 20428 do opcodes[i] = {} end`) or spin
  forever. None of them is reached in the oracle run.
* About 4,130 of the 20,413 main-chunk instructions are not decompiled because they never
  run. About 160 are Luraph's decoy branches behind opaque predicates (3 to 7 instructions
  each, rejoining the real code). The rest, about 3,970, are skipped by the file's own
  loader: `Backend.initialize` rewrites two instructions at load time (pc 17366 and 17880)
  into jumps to 15 instructions it appends, which load fixed values (`false`, `"?"`, `0`,
  `math.huge`, `"Not specified"`, the integrity numbers `9395`/`964` and three integrity
  functions) in place of what the skipped code computes. That code creates 84 functions
  (48 directly, 8,445 instructions in all) that look like Luarmor's loader-side runtime:
  key/whitelist checks, a heartbeat connection, anti-hook checks and a kick routine. It is
  disabled in this build and is left out.
* One loop uses an `exitLoop` flag for a two-level break (Lua has no multi-level `break`).
