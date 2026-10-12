"""Driver: lifted blocks -> Lua source."""
import sys, os, pickle, collections, re
from ir import build_func, stmt_exprs, term_exprs, walk, vars_read, is_var
from opt import Optimizer, stmt_exprs_any
from structure import Structurer, cleanup, merge_conditions
from emit import Emitter, lua_const, is_ident
from webs import compute_webs
from scope import place_locals
from naming import suggest, Names, LUA_RESERVED
import patterns


class ProtoInfo:
    def __init__(self, pid, nregs, F, body, nparams, vararg, log, webinfo):
        self.pid = pid
        self.nregs = nregs
        self.F = F
        self.body = body
        self.nparams = nparams
        self.vararg = vararg
        self.log = log
        self.webinfo = webinfo


def detect_params(F):
    """parameter copies R_i = p_i near the start of the entry block"""
    b = F.bbs[F.entry]
    n = 0
    drop = []
    for idx, s in enumerate(b.stmts[:40]):
        if s[0] == "set" and s[1][0] == "r" and s[2][0] == "p" and s[1][1] == s[2][1] and s[2][1] == n + 1:
            n += 1
            drop.append(idx)
    if drop:
        # only accept if the parameter registers are not read before their copy
        b.stmts = [st for i, st in enumerate(b.stmts) if i not in set(drop)]
    return n


def fold_param_copies(F, webinfo):
    """a web defined exactly once as `w = p_i` (and never captured) is just the parameter"""
    from ir import subst, term_with_exprs
    from opt import map_stmt
    ndefs = collections.Counter()
    rhs = {}
    for b in F.bbs.values():
        for s in b.stmts:
            if s[0] == "set" and s[1][0] == "w":
                ndefs[s[1][1]] += 1
                rhs[s[1][1]] = s[2]
            elif s[0] == "mset":
                for t in s[1]:
                    if t[0] == "w":
                        ndefs[t[1]] += 2
    sub = {}
    for w, n in ndefs.items():
        wi = webinfo.get(w, {})
        if n == 1 and rhs[w][0] == "p" and not wi.get("captured") and wi.get("param") is None and not wi.get("entry_used"):
            sub[("w", w)] = rhs[w]
    if not sub:
        return
    f = lambda e: sub.get(e)
    for b in F.bbs.values():
        out = []
        for s in b.stmts:
            if s[0] == "set" and s[1] in sub:
                continue
            out.append(map_stmt(s, lambda e: subst(e, f)))
        b.stmts = out
        b.term = term_with_exprs(b.term, [subst(e, f) for e in term_exprs(b.term)])


def analyse(pid, blocks, nregs):
    F = build_func(blocks)
    nparams = detect_params(F)
    log = []
    O = Optimizer(F, nregs, log)
    O.prepare()
    vararg = False
    for b in F.bbs.values():
        exprs = []
        for s in b.stmts:
            exprs.extend(stmt_exprs_any(s))
        exprs.extend(term_exprs(b.term))
        for e in exprs:
            for x in walk(e):
                if (x[0] in ("multi", "count") and x[1][0] == "va") or (x[0] == "p" and x[1] > nparams):
                    vararg = True
    # direct parameter reads (specialised interpreters read the argument pack directly)
    maxp = 0
    uses_multi_va = False
    for b in F.bbs.values():
        exprs = []
        for s in b.stmts:
            exprs.extend(stmt_exprs_any(s))
        exprs.extend(term_exprs(b.term))
        for e in exprs:
            for x in walk(e):
                if x[0] == "p":
                    maxp = max(maxp, x[1])
                if x[0] in ("multi", "count") and x[1][0] == "va":
                    uses_multi_va = True
    va_locals = 0
    if not uses_multi_va and maxp > nparams:
        nparams = maxp
        vararg = False
    elif uses_multi_va and maxp > nparams:
        # the function reads individual arguments and also forwards `...`: bind the individual
        # arguments to locals at entry (`local argN.. = ...`) so nested closures can capture them by name
        va_locals = maxp
    webinfo = compute_webs(F, nparams)
    O.inline_loop()
    import tailmerge
    nm = tailmerge.run(F)
    if nm:
        log.append("tail-merged %d blocks" % nm)
    import loops
    nd = loops.fold_dynamic_fornum(F)
    if nd:
        log.append("dynamic-step for loops %d" % nd)
    merge_conditions(F)
    fold_param_copies(F, webinfo)
    S = Structurer(F)
    body = cleanup(S.run())
    if S.problems:
        log.extend(S.problems[:20])
    body = patterns.apply(body, webinfo)
    body = place_locals(body, webinfo)
    body = patterns.post(body)
    import doblocks
    body = doblocks.apply(body, max(nparams, va_locals))
    info = ProtoInfo(pid, nregs, F, body, nparams, vararg, log, webinfo)
    info.va_locals = va_locals
    return info



EVENT_PARAMS = {
    b"CharacterAdded": ["character"], b"CharacterRemoving": ["character"], b"CharacterAppearanceLoaded": ["character"],
    b"PlayerAdded": ["player"], b"PlayerRemoving": ["player"],
    b"InputBegan": ["input", "gameProcessed"], b"InputEnded": ["input", "gameProcessed"],
    b"InputChanged": ["input", "gameProcessed"], b"TouchStarted": ["touch", "gameProcessed"],
    b"RenderStepped": ["dt"], b"Heartbeat": ["dt"], b"Stepped": ["time", "dt"], b"PreRender": ["dt"],
    b"PostSimulation": ["dt"], b"PreSimulation": ["dt"], b"PreAnimation": ["dt"],
    b"ChildAdded": ["child"], b"ChildRemoved": ["child"], b"DescendantAdded": ["descendant"],
    b"DescendantRemoving": ["descendant"], b"Touched": ["hit"], b"TouchEnded": ["hit"],
    b"Changed": ["property"], b"AttributeChanged": ["attribute"], b"FocusLost": ["enterPressed"],
    b"MouseButton1Down": ["x", "y"], b"MouseEnter": ["x", "y"], b"MouseLeave": ["x", "y"],
    b"MouseMoved": ["x", "y"], b"HealthChanged": ["health"], b"StateChanged": ["oldState", "newState"],
    b"OnClientEvent": ["..."], b"Event": ["..."], b"AncestryChanged": ["child", "parent"],
    b"Chatted": ["message"], b"MessageReceived": ["message"], b"Died": [],
}
METHOD_CB = {b"OnChanged": ["value"], b"OnClick": [], b"BindToRenderStep": ["dt"]}
FIELD_CB = {b"Callback": ["value"], b"ChangedCallback": ["value"], b"Func": []}


def scan_param_hints(body, hints):
    def closure_pid(e):
        if e[0] in ("closure",):
            return e[1]
        return None

    def visit_expr(e):
        for x in walk(e):
            k = x[0]
            if k in ("mcallx", "mcallmx"):
                obj, m, args = x[1], x[2], x[3]
                if m in (b"Connect", b"Once", b"ConnectParallel") and args:
                    pid = closure_pid(args[0])
                    if pid is not None and obj[0] == "idx" and obj[2][0] == "k":
                        ev = obj[2][1]
                        if ev in EVENT_PARAMS:
                            hints.setdefault(pid, EVENT_PARAMS[ev])
                elif m in METHOD_CB:
                    for a in args:
                        pid = closure_pid(a)
                        if pid is not None:
                            hints.setdefault(pid, METHOD_CB[m])
            elif k == "newtable":
                for kk, vv in x[2]:
                    if kk[0] == "k" and kk[1] in FIELD_CB:
                        pid = closure_pid(vv)
                        if pid is not None:
                            hints.setdefault(pid, FIELD_CB[kk[1]])
            elif k in ("callx", "callmx"):
                f = x[1]
                if f == ("idx", ("g", ("k", b"table")), ("k", b"sort")) and len(x[2]) > 1:
                    pid = closure_pid(x[2][1])
                    if pid is not None:
                        hints.setdefault(pid, ["a", "b"])

    def visit(block):
        for s in block:
            for e in patterns.stmt_own_exprs(s):
                visit_expr(e)
            for cb in patterns.child_blocks(s):
                visit(cb)
    visit(body)


def code_end(line):
    """index just past the code part of a line (before a trailing -- comment outside strings)"""
    q = None
    i = 0
    n = len(line)
    while i < n:
        c = line[i]
        if q:
            if c == "\\":
                i += 2
                continue
            if c == q:
                q = None
        elif c in "\"'":
            q = c
        elif c == "-" and line.startswith("--", i):
            return i
        i += 1
    return n


def terminate_prev(out):
    j = len(out) - 1
    while j >= 0 and out[j].lstrip().startswith("--"):
        j -= 1
    if j < 0:
        return
    head, _, line = out[j].rpartition("\n")
    e = code_end(line)
    code = line[:e].rstrip()
    if not code.strip() or code.endswith(";"):
        return
    last = code.split()[-1]
    if last in ("then", "do", "else", "repeat") or code.endswith("("):
        return
    line = code + ";" + (" " + line[e:] if e < len(line) else "")
    out[j] = (head + "\n" + line) if _ else line


class FuncPrinter:
    def __init__(self, D, info, upnames, depth, taken):
        self.D = D
        self.info = info
        self.upnames = upnames
        self.depth = depth
        self.nparams = info.nparams
        self.em = Emitter(self)
        self.names = {}
        self.N = Names(taken | set(upnames) | D.reserved)
        self.assign_names()

    # naming ------------------------------------------------------------
    def collect_hints(self):
        hints = {}
        meta = getattr(self.D, "native_meta", {}) or {}

        def scan(block):
            for st in block:
                for e in patterns.stmt_own_exprs(st):
                    for x in walk(e):
                        if x[0] == "native" and x[1] in meta:
                            capn = meta[x[1]].cap_names()
                            for i, r in enumerate(x[2]):
                                nm = capn.get(i)
                                if not nm or re.match(r"^capture\d+$", nm):
                                    continue
                                tgt = None
                                if r[0] == "rrefv" and r[1][0] == "w":
                                    tgt = r[1][1]
                                elif r[0] == "val" and r[1][0] == "w":
                                    tgt = r[1][1]
                                if tgt is not None and tgt not in hints:
                                    hints[tgt] = nm
                for cb in patterns.child_blocks(st):
                    scan(cb)
        scan(self.info.body)
        return hints

    def assign_names(self):
        info = self.info
        self.hints = self.collect_hints()
        scan_param_hints(info.body, self.D.param_hints)
        ph = self.D.param_hints.get(info.pid, [])
        self.slots = {}
        for i, nm in enumerate(ph):
            if nm != "..." and i < info.nparams:
                self.slots[i + 1] = self.N.alloc(nm)
        for w, wi in info.webinfo.items():
            if wi.get("param") is not None and wi["param"] in self.slots:
                self.names[w] = self.slots[wi["param"]]
        # parameters
        for w, wi in info.webinfo.items():
            if wi.get("param") is not None and w not in self.names:
                self.names[w] = self.N.alloc("arg%d" % wi["param"])
        self.visit_names(info.body)

    def visit_names(self, block):
        for s in block:
            k = s[0]
            if k == "local":
                ws, init = s[1], s[2]
                for i, w in enumerate(ws):
                    if w in self.names:
                        continue
                    import doblocks as _db
                    if _db.is_spill_table(w):
                        self.names[w] = self.N.alloc("scope")
                        continue
                    base = self.hints.get(w)
                    if base is None and init is not None:
                        if len(ws) == 1 or i == 0:
                            base = suggest(init)
                    if base is None:
                        base = self.guess_from_use(w)
                    self.names[w] = self.N.alloc(base or "v")
            elif k == "fornum":
                if s[1] not in self.names:
                    if isinstance(s[1], int) and s[1] < 0:
                        self.names[s[1]] = "_"
                    else:
                        self.names[s[1]] = self.N.alloc("i")
            elif k == "forin":
                for i, w in enumerate(s[1]):
                    if isinstance(w, int) and w < 0:
                        self.names[w] = "_"
                        continue
                    if w not in self.names:
                        base = ["k", "v", "x", "y"][i] if i < 4 else "v"
                        hint = s[4] if len(s) > 4 else None
                        if hint == "ipairs":
                            base = ["i", "v"][i] if i < 2 else "v"
                        self.names[w] = self.N.alloc(base)
            for cb in patterns.child_blocks(s):
                self.visit_names(cb)

    def guess_from_use(self, w):
        return None

    def name(self, v):
        if v[0] == "w":
            n = self.names.get(v[1])
            if n is None:
                n = self.N.alloc("v")
                self.names[v[1]] = n
            return n
        return "--[[%r]]" % (v,)

    def flag_name(self, f):
        if not hasattr(self, "flagnames"):
            self.flagnames = {}
        if f not in self.flagnames:
            self.flagnames[f] = self.N.alloc("exitLoop")
        return self.flagnames[f]

    def upval(self, i):
        if i < len(self.upnames):
            return self.upnames[i]
        return "upval%d" % i

    def param(self, i):
        if i <= self.nparams or i <= getattr(self.info, "va_locals", 0):
            return self.slot_name(i)
        return "(select(%d, ...))" % (i - self.nparams)

    def slot_name(self, i):
        if not getattr(self, "_slots_filled", False):
            self._slots_filled = True
            for w, wi in self.info.webinfo.items():
                if wi.get("param") is not None and w in self.names:
                    self.slots.setdefault(wi["param"], self.names[w])
        if i not in self.slots:
            self.slots[i] = self.N.alloc("arg%d" % i)
        return self.slots[i]

    def closure(self, e, indent):
        pid, refs = e[1], e[2]
        names = [self.ref_name(r, indent) for r in refs]
        return self.D.function_text(pid, names, indent, self.depth + 1, self.N.taken)

    def ref_name(self, r, indent):
        if r[0] == "rref":
            return "r%d" % r[1]
        if r[0] == "rrefv":
            return self.em.expr(r[1], 0, indent)
        if r[0] == "u":
            return self.upval(r[1])
        if r[0] == "val":
            return self.em.expr(r[1], 0, indent)
        if r[0] == "k":
            return "nil"
        return "--[[ref?]]nil"

    def native(self, e, indent):
        name, refs = e[1], e[2]
        names = [self.ref_name(r, indent) for r in refs]
        return self.D.native_text(name, names, indent, self.N.taken)

    def hostfn(self, e):
        return self.D.hostfn_text(e)

    # statements -----------------------------------------------------------
    def block(self, stmts, indent, out):
        for s in stmts:
            self.stmt(s, indent, out)

    def stmt(self, s, indent, out):
        k = s[0]
        em = self.em
        if k == "ir":
            self.ir_stmt(s[1], indent, out)
        elif k == "local":
            names = ", ".join(self.name(("w", w)) for w in s[1])
            if s[2] is None:
                out.append(indent + "local " + names)
            else:
                out.append(indent + "local " + names + " = " + em.expr(s[2], 0, indent))
        elif k == "if":
            self.if_chain(s, indent, out, "if ")
            out.append(indent + "end")
        elif k == "while":
            out.append(indent + "while " + em.expr(s[1], 0, indent) + " do")
            self.block(s[2], indent + "    ", out)
            out.append(indent + "end")
        elif k == "repeat":
            out.append(indent + "repeat")
            self.block(s[1], indent + "    ", out)
            out.append(indent + "until " + em.expr(s[2], 0, indent))
        elif k == "fornum":
            v = self.name(("w", s[1]))
            parts = [em.expr(s[2], 0, indent), em.expr(s[3], 0, indent)]
            if s[4] is not None and not (s[4][0] == "k" and s[4][1] == 1.0):
                parts.append(em.expr(s[4], 0, indent))
            out.append(indent + "for " + v + " = " + ", ".join(parts) + " do")
            self.block(s[5], indent + "    ", out)
            out.append(indent + "end")
        elif k == "forin":
            vs = ", ".join(self.name(("w", w)) for w in s[1])
            out.append(indent + "for " + vs + " in " + em.args(list(s[2]), indent) + " do")
            self.block(s[5], indent + "    ", out)
            out.append(indent + "end")
        elif k == "do":
            out.append(indent + "do")
            self.block(s[1], indent + "    ", out)
            out.append(indent + "end")
        elif k == "break":
            out.append(indent + "break")
        elif k == "continue":
            out.append(indent + "continue")
        elif k == "return":
            vals = s[1]
            if vals:
                out.append(indent + "return " + em.args(list(vals), indent))
            else:
                out.append(indent + "return")
        elif k == "error":
            out.append(indent + self.D.error_text(s[1]))
        elif k == "goto":
            out.append(indent + "-- decompiler: unstructured jump to block %r" % (s[1],))
        elif k == "comment":
            out.append(indent + "-- " + " ".join(str(s[1]).split()))
        elif k == "flagdecl":
            out.append(indent + "local %s = false" % self.flag_name(s[1]))
        elif k == "flagset":
            out.append(indent + "%s = true" % self.flag_name(s[1]))
        else:
            out.append(indent + "-- ? " + repr(s)[:200])

    def if_chain(self, s, indent, out, kw):
        em = self.em
        out.append(indent + kw + em.expr(s[1], 0, indent) + " then")
        self.block(s[2], indent + "    ", out)
        fb = s[3]
        if fb:
            if len(fb) == 1 and fb[0][0] == "if":
                self.if_chain(fb[0], indent, out, "elseif ")
            else:
                out.append(indent + "else")
                self.block(fb, indent + "    ", out)

    def ir_stmt(self, s, indent, out):
        em = self.em
        k = s[0]

        def put(text):
            # a statement starting with '(' would be parsed as a call on the previous line:
            # terminate the previous statement with ';' (Luau has no empty statement)
            if text.startswith("("):
                terminate_prev(out)
            out.append(indent + text)
        if k == "set":
            put(em.expr(s[1], 0, indent) + " = " + em.expr(s[2], 0, indent))
        elif k == "callstat":
            put(em.expr(s[1], 0, indent))
        elif k == "mset":
            put(", ".join(em.expr(t, 0, indent) for t in s[1]) + " = " + em.expr(s[2], 0, indent))
        elif k == "setlist" and s[1][0] == "newtable" and s[2][0] == "k" and s[2][1] == len(s[1][1]) + 1:
            # filling a table that is never stored anywhere: keep the evaluation, drop the dead target
            arr = tuple(s[1][1]) + tuple(s[3])
            out.append(indent + "local _ = " + em.expr(("newtable", arr, s[1][2]), 0, indent))
        elif k == "setlist":
            tbl, start, vals = s[1], s[2], s[3]
            st = start[1] if start[0] == "k" else None
            if st is not None:
                for i, v in enumerate(vals):
                    if v[0] == "multi" or (v[0] in ("callmx", "mcallmx") and i == len(vals) - 1):
                        out.append(indent + "for _i, _v in ipairs({" + em.expr(v, 0, indent) + "}) do " +
                                   em.expr(tbl, 11, indent) + "[" + str(int(st) + i) + " + _i - 1] = _v end")
                    else:
                        out.append(indent + em.expr(tbl, 11, indent) + "[" + str(int(st) + i) + "] = " + em.expr(v, 0, indent))
            else:
                out.append(indent + "-- setlist ?")
        elif k == "close":
            pass
        elif k == "fginit":
            txt = ", ".join(em.expr(x, 0, indent) for x in s[2:5])
            out.append(indent + "-- decompiler: for-in init " + " ".join(txt.split()))
        elif k == "fgnext":
            out.append(indent + "-- decompiler: for-in step")
        else:
            out.append(indent + "-- ? " + repr(s)[:200])

    def text(self, indent):
        out = []
        body = self.info.body
        if body and body[-1][0] == "return" and not body[-1][1]:
            body = body[:-1]
        nva = getattr(self.info, "va_locals", 0)
        if nva > self.nparams:
            out.append(indent + "local " + ", ".join(self.slot_name(i) for i in range(self.nparams + 1, nva + 1)) + " = ...")
        self.block(body, indent, out)
        return out


class Decompiler:
    def __init__(self, results, nregs_of, native_src, hostfn_src, reserved=()):
        self.results = results
        self.nregs_of = nregs_of
        self.cache = {}
        self.native_src = native_src
        self.hostfn_src = hostfn_src
        self.logs = {}
        self.reserved = set(LUA_RESERVED) | set(reserved)
        self.param_hints = {}

    def info(self, pid):
        if pid not in self.cache:
            blocks = self.results.get(pid)
            if blocks is None:
                self.cache[pid] = None
            else:
                try:
                    import time as _t
                    _t0 = _t.time()
                    self.cache[pid] = analyse(pid, blocks, self.nregs_of(pid))
                    _dt = _t.time() - _t0
                    if _dt > 3:
                        print("  slow analyse", pid, "%.1fs" % _dt, flush=True)
                except Exception as ex:
                    import traceback
                    traceback.print_exc()
                    self.cache[pid] = None
                    self.logs[pid] = ["analyse failed: %r" % ex]
        return self.cache[pid]

    def function_text(self, pid, upnames, indent, depth, taken=frozenset()):
        info = self.info(pid)
        if info is None:
            return "function(...) --[[decompile failed for proto %d]] end" % pid
        fp = FuncPrinter(self, info, upnames, depth, set(taken))
        params = [fp.slot_name(i) for i in range(1, info.nparams + 1)]
        if info.vararg:
            params.append("...")
        lines = fp.text(indent + "    ")
        head = "function(" + ", ".join(params) + ")"
        if not lines:
            return head + " end"
        return head + "\n" + "\n".join(lines) + "\n" + indent + "end"

    def native_text(self, name, upnames, indent, taken):
        src = self.native_src.get(name)
        if src is None:
            return "--[[native %s]]nil" % name
        return src(upnames, indent, taken)

    def hostfn_text(self, e):
        return self.hostfn_src(e)

    def error_text(self, msg):
        m = str(msg)
        if m.startswith("tamper") or "write to program data" in m:
            return "error(\"tamper detected\") -- original: script wipes its own bytecode here (anti-tamper)"
        return "error(" + lua_const(m.encode()) + ") -- decompiler: unresolved path"
