"""Turn the already-readable Native.* functions into inline function expressions with captures substituted."""
import re

NATIVE_DEF = re.compile(r"^function Native\.(\w+)\((captures)?\)\s*$")
CAP_LOCAL = re.compile(r"^\s*local (\w+) = captures\[(\d+)\]\s*$")
STEP = re.compile(r"^\s*__step\(\d+\)\s*$")


class NativeSource:
    def __init__(self, name, caps, body_lines, has_captures):
        self.name = name
        self.caps = caps            # local name -> capture index
        self.body = body_lines      # lines of the returned function expression (first line 'function(...)')
        self.has_captures = has_captures

    def cap_names(self):
        return {i: n for n, i in self.caps.items()}


def load(path_lines):
    """parse native definitions out of the head file lines"""
    out = {}
    i = 0
    n = len(path_lines)
    while i < n:
        m = NATIVE_DEF.match(path_lines[i])
        if not m:
            i += 1
            continue
        name = m.group(1)
        has_caps = m.group(2) is not None
        j = i + 1
        caps = {}
        while j < n and CAP_LOCAL.match(path_lines[j]):
            mm = CAP_LOCAL.match(path_lines[j])
            caps[mm.group(1)] = int(mm.group(2))
            j += 1
        # find 'return function(' line
        while j < n and not path_lines[j].strip().startswith("return function"):
            j += 1
        start = j
        # function ends at the line '^end$' (Native def end) -> the returned function ends one line before at indent 4
        k = start + 1
        while k < n and path_lines[k] != "end":
            k += 1
        body = path_lines[start:k]
        # strip 'return ' from first line and trailing lines until the closing '    end'
        if body:
            body[0] = body[0].replace("return function", "function", 1)
        # remove trailing blank lines
        while body and not body[-1].strip():
            body.pop()
        out[name] = NativeSource(name, caps, body, has_caps)
        i = k + 1
    return out


def render(ns, upnames, indent):
    """substitute captures; returns text of a function expression"""
    names = {}
    for local, idx in ns.caps.items():
        if idx < len(upnames):
            rep = upnames[idx]
            if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$", rep) and not (rep.startswith("(") and rep.endswith(")")):
                rep = "(" + rep + ")"
            names[local] = rep
        else:
            names[local] = "nil --[[missing capture %d]]" % idx
    lines = []
    for ln in ns.body:
        if STEP.match(ln):
            continue
        lines.append(ln)
    text = "\n".join(lines)
    # box access  cap[3][cap[2] ]
    for local, rep in sorted(names.items(), key=lambda x: -len(x[0])):
        text = re.sub(r"\b%s\[3\]\[%s\[2\]\s*\]" % (re.escape(local), re.escape(local)), rep, text)
    for local, rep in sorted(names.items(), key=lambda x: -len(x[0])):
        text = re.sub(r"(?<![\w.:])%s\b" % re.escape(local), rep, text)
    # re-indent: body lines are indented with 4 spaces (inside Native def); target indent
    out = []
    for k, ln in enumerate(text.split("\n")):
        if k == 0:
            out.append(ln.strip())
            continue
        if ln.startswith("    "):
            ln = ln[4:]
        out.append(indent + ln if ln.strip() else "")
    # collapse multiple blank lines
    res = []
    for ln in out:
        if not ln.strip() and res and not res[-1].strip():
            continue
        res.append(ln)
    return "\n".join(res)
