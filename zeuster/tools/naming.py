"""Name suggestion for webs based on how they are initialised / used."""
import re
from ir import walk

LUA_RESERVED = {"and", "break", "do", "else", "elseif", "end", "false", "for", "function", "if", "in", "local",
                "nil", "not", "or", "repeat", "return", "then", "true", "until", "while", "continue", "goto",
                "self", "type", "typeof", "_G", "_ENV", "getfenv", "setfenv", "select", "pairs", "ipairs", "next",
                "print", "error", "warn", "tostring", "tonumber", "string", "table", "math", "task", "coroutine",
                "os", "bit32", "buffer", "utf8", "debug", "game", "workspace", "script", "Instance", "Vector3",
                "Vector2", "CFrame", "Color3", "UDim2", "UDim", "Enum", "Ray", "RaycastParams", "TweenInfo",
                "NumberSequence", "ColorSequence", "Drawing", "unpack", "require", "rawget", "rawset", "pcall",
                "xpcall", "assert", "wait", "spawn", "delay", "tick", "time", "loadstring", "getgenv",
                "setmetatable", "getmetatable", "newproxy", "gethui", "cloneref", "hookfunction", "hookmetamethod",
                "getrawmetatable", "setreadonly", "isfile", "readfile", "writefile", "makefolder", "isfolder",
                "listfiles", "delfile", "request", "Players", "RunService"}

IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SERVICE_METHODS = {b"GetService"}
CHILD_METHODS = {b"FindFirstChild", b"WaitForChild", b"FindFirstChildOfClass", b"FindFirstChildWhichIsA",
                 b"FindFirstAncestor", b"FindFirstAncestorOfClass", b"FindFirstAncestorWhichIsA"}
METHOD_NAMES = {b"GetChildren": "children", b"GetDescendants": "descendants", b"GetPlayers": "players",
                b"Clone": "clone", b"GetMouse": "mouse", b"Connect": "connection", b"Once": "connection",
                b"GetAttribute": "attribute", b"Raycast": "result", b"IsA": "isA", b"GetPivot": "pivot",
                b"GetTags": "tags", b"GetPropertyChangedSignal": "signal", b"AddTab": "tab",
                b"AddLeftGroupbox": "groupbox", b"AddRightGroupbox": "groupbox", b"AddLeftTabbox": "tabbox",
                b"AddRightTabbox": "tabbox", b"AddToggle": "toggle", b"AddSlider": "slider",
                b"AddDropdown": "dropdown", b"AddButton": "button", b"AddLabel": "label", b"AddInput": "input",
                b"AddColorPicker": "colorPicker", b"AddKeyPicker": "keyPicker", b"CreateWindow": "window",
                b"Create": "obj", b"JSONDecode": "decoded", b"JSONEncode": "json", b"HttpGet": "body",
                b"GetFullName": "fullName", b"WorldToViewportPoint": "screenPos", b"WorldToScreenPoint": "screenPos",
                b"InvokeServer": "result", b"FireServer": "result", b"Lerp": "lerped", b"Dot": "dot",
                b"Cross": "cross", b"GetBoundingBox": "boxCFrame", b"PointToObjectSpace": "localPos",
                b"ToObjectSpace": "relative", b"lower": "lower", b"upper": "upper", b"sub": "sub",
                b"gsub": "replaced", b"split": "parts", b"format": "formatted", b"find": "found",
                b"match": "match", b"GetPlayerFromCharacter": "player", b"Kill": "result"}
GLOBAL_CALLS = {"tostring": "str", "tonumber": "num", "require": "module", "typeof": "kind", "type": "kind",
                "setmetatable": "obj", "loadstring": "chunk", "pcall": "ok", "getgenv": "genv", "tick": "now",
                "os.clock": "now", "math.floor": "n", "math.abs": "n", "math.clamp": "n", "math.max": "n",
                "math.min": "n", "math.random": "rand", "string.format": "text", "table.concat": "text",
                "table.find": "index", "table.remove": "removed", "select": "count", "unpack": "values",
                "workspace": "ws", "cloneref": "service", "Instance.new": "inst", "Drawing.new": "drawing",
                "Vector3.new": "vec", "Vector2.new": "vec2", "CFrame.new": "cf", "Color3.new": "color",
                "Color3.fromRGB": "color", "Color3.fromHSV": "color", "UDim2.new": "udim",
                "UDim2.fromOffset": "udim", "UDim2.fromScale": "udim", "TweenInfo.new": "tweenInfo",
                "RaycastParams.new": "params", "game.HttpGet": "body", "task.spawn": "thread",
                "coroutine.create": "co", "coroutine.wrap": "fn", "string.split": "parts"}


def lowerfirst(s):
    if not s:
        return s
    if len(s) > 1 and s[:2].isupper():
        # acronyms like "UIStroke" -> "uiStroke"
        i = 0
        while i < len(s) and s[i].isupper():
            i += 1
        if i == len(s):
            return s.lower()
        if i > 1:
            return s[:i - 1].lower() + s[i - 1:]
    return s[0].lower() + s[1:]


def clean(name):
    if name is None:
        return None
    if isinstance(name, bytes):
        try:
            name = name.decode("utf-8")
        except UnicodeDecodeError:
            return None
    name = re.sub(r"[^A-Za-z0-9_]", "_", name)
    name = re.sub(r"_+", "_", name).strip("_")
    if not name:
        return None
    if name[0].isdigit():
        name = "n" + name
    if len(name) > 28:
        name = name[:28]
    return name


def dotted(e):
    """global.field chains -> 'a.b'"""
    if e[0] == "g" and e[1][0] == "k" and isinstance(e[1][1], bytes):
        return e[1][1].decode("latin-1")
    if e[0] == "idx" and e[2][0] == "k" and isinstance(e[2][1], bytes):
        d = dotted(e[1])
        if d:
            return d + "." + e[2][1].decode("latin-1")
    return None


def suggest(e):
    k = e[0]
    if k in ("mcallx", "mcallmx"):
        m = e[2]
        args = e[3]
        if m in SERVICE_METHODS and args and args[0][0] == "k" and isinstance(args[0][1], bytes):
            return clean(args[0][1])
        if m in CHILD_METHODS and args and args[0][0] == "k" and isinstance(args[0][1], bytes):
            return clean(lowerfirst(args[0][1].decode("latin-1")))
        if m in METHOD_NAMES:
            return METHOD_NAMES[m]
        return None
    if k in ("callx", "callmx"):
        f = e[1]
        d = dotted(f)
        if d in ("Instance.new", "Drawing.new") and e[2] and e[2][0][0] == "k" and isinstance(e[2][0][1], bytes):
            return clean(lowerfirst(e[2][0][1].decode("latin-1")))
        if d == "cloneref" and e[2]:
            inner = suggest(e[2][0])
            if inner:
                return inner
        if d in GLOBAL_CALLS:
            return GLOBAL_CALLS[d]
        return None
    if k == "idx":
        key = e[2]
        if key[0] == "k" and isinstance(key[1], bytes):
            n = clean(key[1])
            if n:
                return lowerfirst(n)
        return None
    if k == "g":
        if e[1][0] == "k" and isinstance(e[1][1], bytes):
            return clean(e[1][1])
        return None
    if k == "newtable":
        return "tbl"
    if k in ("closure", "native"):
        return "fn"
    if k == "k":
        v = e[1]
        if isinstance(v, bytes):
            return "str"
        if isinstance(v, bool):
            return "flag"
        if isinstance(v, float):
            return "num"
        return None
    if k == "un" and e[1] == "#":
        return "len"
    if k == "un" and e[1] == "not":
        return "flag"
    if k == "bin" and e[1] in ("==", "~=", "<", "<=", ">", ">="):
        return "flag"
    if k == "bin" and e[1] == "..":
        return "str"
    if k == "bin":
        return "n"
    return None


class Names:
    def __init__(self, taken):
        self.taken = set(taken)
        self.count = {}

    def alloc(self, base):
        base = base or "v"
        if base in LUA_RESERVED or not IDENT.match(base):
            base = base + "_"
        if base not in self.taken:
            self.taken.add(base)
            return base
        n = self.count.get(base, 1)
        while True:
            n += 1
            cand = "%s%d" % (base, n)
            if cand not in self.taken:
                self.count[base] = n
                self.taken.add(cand)
                return cand
