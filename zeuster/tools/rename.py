"""Give generic local names (v12, tbl3, fn7, value030, arg2, ...) meaningful names, from how
each local is initialised and used, on the final Luau source.

The source is parsed with `luau-ast`, which reports every local's declaration and every
reference to it with exact positions, so a rename touches exactly the right identifiers.
A new name is only used when it cannot shadow or capture another binding: no other local with
that name may have an overlapping scope, and no global of that name may be referenced inside
the renamed local's scope.

usage: python rename.py in.lua out.lua [luau-ast binary]
"""
import collections, json, os, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
LUAU_AST = os.environ.get("LUAU_AST", os.path.join(HERE, "..", "bin", "luau-ast"))

GENERIC = re.compile(r"^(v|tbl|fn|value|n|num|flag|str|obj|arg|recovered\d*_?[A-Za-z]?|var|k|x|y|res|result)\d*$")
KEYWORDS = {"and", "break", "do", "else", "elseif", "end", "false", "for", "function", "if", "in", "local", "nil",
            "not", "or", "repeat", "return", "then", "true", "until", "while", "continue", "self", "type",
            "typeof", "export"}

# field / method name -> (role, weight)
FIELD_ROLES = {}


def _roles(role, names, w=2):
    for n in names.split():
        FIELD_ROLES.setdefault(n, []).append((role, w))


_roles("player", "Character UserId DisplayName Team TeamColor CharacterAdded CharacterRemoving Backpack PlayerGui "
       "GetMouse Kick LoadCharacter CharacterAppearanceLoaded AccountAge MembershipType")
_roles("character", "Humanoid HumanoidRootPart UpperTorso LowerTorso Torso LeftHand RightHand LeftFoot RightFoot "
       "RightUpperArm LeftUpperArm")
_roles("character", "Head PrimaryPart", 1)
_roles("humanoid", "Health MaxHealth WalkSpeed JumpPower JumpHeight MoveDirection RootPart ChangeState GetState "
       "HipHeight Died UseJumpPower PlatformStand Sit AutoRotate FloorMaterial HealthChanged StateChanged Animator "
       "LoadAnimation GetPlayingAnimationTracks MoveTo")
_roles("camera", "FieldOfView ViewportSize CameraType CameraSubject WorldToViewportPoint WorldToScreenPoint "
       "ViewportPointToRay ScreenPointToRay")
_roles("part", "Anchored CanCollide CanQuery CanTouch AssemblyLinearVelocity AssemblyAngularVelocity Velocity "
       "Material BrickColor Massless Touched TouchEnded GetTouchingParts")
_roles("cframe", "LookVector RightVector UpVector ToWorldSpace ToObjectSpace PointToObjectSpace PointToWorldSpace "
       "VectorToObjectSpace VectorToWorldSpace Inverse GetComponents ToEulerAnglesXYZ ToOrientation")
_roles("vector", "Magnitude Unit Dot Cross")
_roles("signal", "Connect Once ConnectParallel")
_roles("connection", "Disconnect Connected")
_roles("event", "Fire Event", 2)
_roles("option", "OnChanged SetValue SetValues Values Multi Default", 2)
_roles("input", "KeyCode UserInputType UserInputState Delta")
_roles("label", "Text TextColor3 TextSize Font FontFace TextTransparency TextStrokeTransparency TextStrokeColor3 "
       "TextXAlignment TextYAlignment TextScaled RichText TextWrapped TextBounds", 1)
_roles("frame", "BackgroundColor3 BackgroundTransparency BorderSizePixel BorderColor3 AnchorPoint ClipsDescendants "
       "AbsoluteSize AbsolutePosition", 1)
_roles("line", "From To", 2)
_roles("circle", "Radius Filled NumSides", 2)
_roles("sound", "SoundId Volume PlaybackSpeed", 2)
_roles("track", "AdjustSpeed AdjustWeight Looped Length", 2)
_roles("highlight", "FillColor OutlineColor FillTransparency OutlineTransparency DepthMode", 2)
_roles("gui", "ResetOnSpawn IgnoreGuiInset ZIndexBehavior DisplayOrder", 2)
_roles("model", "GetPivot PivotTo GetBoundingBox GetExtentsSize", 2)
_roles("tween", "Completed", 2)
_roles("item", "Info EquippedItem", 2)
_roles("emitter", "Emit Rate Lifetime SpreadAngle", 2)
_roles("attachment", "WorldPosition WorldCFrame", 2)
_roles("instance", "Parent ClassName IsA FindFirstChild WaitForChild GetChildren GetDescendants Destroy "
       "FindFirstChildOfClass FindFirstChildWhichIsA GetAttribute SetAttribute ClearAllChildren", 1)

METHOD_INIT = {"GetChildren": "children", "GetDescendants": "descendants", "GetPlayers": "players",
               "Clone": "clone", "GetMouse": "mouse", "Connect": "connection", "Once": "connection",
               "GetAttribute": "attribute", "Raycast": "rayResult", "GetPivot": "pivot", "AddTab": "tab",
               "AddLeftGroupbox": "groupbox", "AddRightGroupbox": "groupbox", "AddLeftTabbox": "tabbox",
               "AddRightTabbox": "tabbox", "AddToggle": "toggle", "AddSlider": "slider", "AddDropdown": "dropdown",
               "AddButton": "button", "AddLabel": "label", "AddInput": "input", "AddColorPicker": "colorPicker",
               "AddKeyPicker": "keyPicker", "CreateWindow": "window", "JSONDecode": "decoded", "JSONEncode": "json",
               "HttpGet": "body", "GetFullName": "fullName", "WorldToViewportPoint": "screenPos",
               "WorldToScreenPoint": "screenPos", "InvokeServer": "response", "Lerp": "lerped",
               "GetBoundingBox": "boxCFrame", "PointToObjectSpace": "localPos", "ToObjectSpace": "relative",
               "lower": "lower", "upper": "upper", "sub": "sub", "gsub": "replaced", "split": "parts",
               "format": "formatted", "match": "match", "GetPlayerFromCharacter": "player",
               "GetMouseLocation": "mousePos", "GetPropertyChangedSignal": "signal", "LoadAnimation": "track",
               "GetState": "state", "GetTextBoundsAsync": "bounds", "GetEnumItems": "enumItems",
               "GetPartsInPart": "parts", "GetPartBoundsInRadius": "parts", "GetPlayingAnimationTracks": "tracks",
               "GenerateGUID": "guid", "GetValue": "value", "GetState": "state", "IsKeyDown": "isDown"}
GLOBAL_INIT = {"tostring": "str", "tonumber": "num", "typeof": "kind", "type": "kind", "setmetatable": "obj",
               "loadstring": "chunk", "getgenv": "genv", "tick": "now", "os.clock": "now", "math.floor": "n",
               "math.random": "rand", "string.format": "text", "table.concat": "text", "table.find": "index",
               "table.remove": "removed", "select": "count", "Instance.new": "instance", "Drawing.new": "drawing",
               "Vector3.new": "vec", "Vector2.new": "vec2", "CFrame.new": "cframe", "CFrame.lookAt": "cframe",
               "CFrame.Angles": "rotation", "Color3.new": "color", "Color3.fromRGB": "color",
               "Color3.fromHSV": "color", "Color3.fromHex": "color", "UDim2.new": "udim2",
               "UDim2.fromOffset": "udim2", "UDim2.fromScale": "udim2", "TweenInfo.new": "tweenInfo",
               "RaycastParams.new": "rayParams", "OverlapParams.new": "overlapParams", "task.spawn": "thread",
               "task.delay": "thread", "coroutine.create": "co", "coroutine.wrap": "fn", "string.split": "parts",
               "NumberSequence.new": "sequence", "ColorSequence.new": "colorSequence", "Ray.new": "ray",
               "NumberRange.new": "range", "buffer.fromstring": "buf", "getrawmetatable": "mt",
               "getmetatable": "mt", "debug.info": "info", "readfile": "contents", "listfiles": "files",
               "getcustomasset": "asset", "getrenv": "renv", "getsenv": "senv", "getfenv": "env", "string.rep": "text", "string.char": "text", "math.clamp": "n",
               "math.abs": "n", "math.max": "n", "math.min": "n", "math.sqrt": "n", "math.atan2": "angle",
               "math.rad": "angle", "math.deg": "degrees", "os.time": "timestamp"}
ITER_ITEM = {"GetChildren": "child", "GetDescendants": "descendant", "GetPlayers": "player",
             "GetEnumItems": "enumItem", "GetPlayingAnimationTracks": "track", "GetTouchingParts": "part",
             "GetPartsInPart": "part", "GetPartBoundsInRadius": "part", "GetPartBoundsInBox": "part",
             "listfiles": "file", "GetTags": "tag", "GetAccessories": "accessory", "GetJoints": "joint"}


META_PARAMS = {"__index": ["self", "key"], "__newindex": ["self", "key", "value"], "__call": ["self"],
               "__tostring": ["self"], "__len": ["self"], "__eq": ["a", "b"], "__lt": ["a", "b"], "__le": ["a", "b"],
               "__add": ["a", "b"], "__sub": ["a", "b"], "__mul": ["a", "b"], "__div": ["a", "b"],
               "__concat": ["a", "b"], "__unm": ["self"], "__iter": ["self"]}


EVENT_PARAMS = {"CharacterAdded": ["character"], "CharacterRemoving": ["character"], "PlayerAdded": ["player"],
                "PlayerRemoving": ["player"], "InputBegan": ["input", "gameProcessed"],
                "InputEnded": ["input", "gameProcessed"], "InputChanged": ["input", "gameProcessed"],
                "RenderStepped": ["dt"], "Heartbeat": ["dt"], "Stepped": ["time", "dt"], "ChildAdded": ["child"],
                "ChildRemoved": ["child"], "DescendantAdded": ["descendant"], "DescendantRemoving": ["descendant"],
                "Touched": ["hit"], "Changed": ["property"], "FocusLost": ["enterPressed"],
                "MouseButton1Down": ["x", "y"], "MouseEnter": ["x", "y"], "MouseMoved": ["x", "y"],
                "HealthChanged": ["health"], "Died": [], "Chatted": ["message"], "AncestryChanged": ["child", "parent"],
                "Event": ["..."], "OnClientEvent": ["..."], "AttributeChanged": ["attribute"]}


def verb_name(m):
    """GetHitPart -> hitPart, CreateLabel -> label, getTarget -> target"""
    mm = re.match(r"^(?:[Gg]et|[Cc]reate|[Nn]ew|[Mm]ake|[Ff]ind|[Ff]etch|[Ll]oad|[Bb]uild|[Rr]ead)_?([A-Z]\w+)$", m or "")
    if mm:
        return clean(lowerfirst(mm.group(1)))
    return None


def lowerfirst(s):
    if not s:
        return s
    i = 0
    while i < len(s) and s[i].isupper():
        i += 1
    if i == len(s):
        return s.lower()
    if i > 1:
        return s[:i - 1].lower() + s[i - 1:]
    return s[0].lower() + s[1:]


def clean(name):
    if not isinstance(name, str):
        return None
    name = re.sub(r"[^A-Za-z0-9_]", "_", name)
    name = re.sub(r"_+", "_", name).strip("_")
    if not name or len(name) > 30:
        return None
    if name[0].isdigit():
        return None
    if name in KEYWORDS:
        return None
    return name


def singular(name):
    if not name:
        return None
    for suf, rep in (("ies", "y"), ("ches", "ch"), ("shes", "sh"), ("sses", "ss"), ("s", "")):
        if name.endswith(suf) and len(name) > len(suf) + 2 and not name.endswith("ss"):
            return name[: -len(suf)] + rep
    return None


def unreg(e):
    """oracle builds wrap closures as __REG(function ... end, pid): look through the wrapper"""
    if e and e.get("type") == "AstExprCall" and e["func"].get("type") == "AstExprGlobal" and \
            e["func"].get("global") == "__REG" and e["args"]:
        return e["args"][0]
    return e


def loc(s):
    a, b = s.split(" - ")
    l1, c1 = a.split(",")
    l2, c2 = b.split(",")
    return (int(l1), int(c1)), (int(l2), int(c2))


def dotted(e):
    t = e.get("type")
    if t == "AstExprGlobal":
        return e["global"]
    if t == "AstExprIndexName" and e.get("op") == ".":
        d = dotted(e["expr"])
        if d:
            return d + "." + e["index"]
    return None


class Local:
    __slots__ = ("key", "name", "kind", "decl", "scope", "refs", "init", "fields", "uses", "stored", "iter",
                 "func", "first_param_of", "role_votes", "new", "assigns", "cb")

    def __init__(self, key, name, kind, decl, scope):
        self.key = key
        self.name = name
        self.kind = kind
        self.decl = decl
        self.scope = scope
        self.refs = []
        self.init = None
        self.fields = collections.Counter()
        self.uses = []
        self.stored = []
        self.iter = None
        self.func = None
        self.first_param_of = None
        self.new = None
        self.assigns = []
        self.cb = None


class Analyzer:
    def __init__(self, root):
        self.locals = {}
        self.globals = collections.defaultdict(list)   # name -> [pos]
        self.method_names = set()
        self.func_fields = {}                           # id(func node) -> field name it is stored in
        self.callbacks = {}                             # id(func node) -> parameter names
        self.root = root

    def decl(self, node, kind, scope_end, scope_start=None):
        (a, b) = loc(node["location"])
        key = node["location"]
        if key not in self.locals:
            self.locals[key] = Local(key, node["name"], kind, (a, b), (scope_start or b, scope_end))
        return self.locals[key]

    def ref(self, node):
        key = node["local"]["location"]
        L = self.locals.get(key)
        if L is not None:
            L.refs.append(loc(node["location"]))
        return L

    # -------------------------------------------------------------------------------------------
    def block(self, node, end=None):
        end = end or loc(node["location"])[1]
        for st in node.get("body", []):
            self.stat(st, end)

    def stat(self, st, block_end):
        t = st["type"]
        if t == "AstStatLocal":
            for v in st.get("values", []):
                self.expr(v)
            vs = [self.decl(v, "local", block_end, loc(st["location"])[1]) for v in st["vars"]]
            vals = st.get("values", [])
            for i, L in enumerate(vs):
                if i < len(vals) and (i < len(vals) - 1 or len(vs) == len(vals)):
                    L.init = unreg(vals[i])
                    if L.init["type"] == "AstExprFunction":
                        L.func = L.init
            return
        if t == "AstStatLocalFunction":
            L = self.decl(st["name"], "localfunction", block_end, loc(st["name"]["location"])[0])
            L.func = st["func"]
            L.init = st["func"]
            self.expr(st["func"])
            return
        if t == "AstStatFunction":
            self.expr(st["name"])
            name = st["name"]
            if name["type"] == "AstExprIndexName":
                self.func_fields[id(st["func"])] = name["index"]
                if name.get("op") == ":":
                    self.method_names.add(name["index"])
            self.expr(st["func"])
            return
        if t == "AstStatAssign":
            for var, val in zip(st["vars"], st["values"]):
                val = unreg(val)
                if var["type"] == "AstExprIndexName" and val["type"] == "AstExprLocal":
                    L = self.locals.get(val["local"]["location"])
                    if L is not None:
                        L.stored.append(var["index"])
                if var["type"] == "AstExprIndexName" and val["type"] == "AstExprFunction":
                    self.func_fields[id(val)] = var["index"]
                if var["type"] == "AstExprLocal":
                    L = self.locals.get(var["local"]["location"])
                    if L is not None:
                        L.assigns.append(val)
                        if val["type"] == "AstExprFunction" and L.func is None:
                            L.func = val
            for v in st["vars"]:
                self.expr(v)
            for v in st["values"]:
                self.expr(v)
            return
        if t == "AstStatCompoundAssign":
            self.expr(st["var"])
            self.expr(st["value"])
            return
        if t in ("AstStatExpr",):
            self.expr(st["expr"])
            return
        if t == "AstStatReturn":
            for e in st["list"]:
                self.expr(e)
            return
        if t == "AstStatIf":
            self.expr(st["condition"])
            self.block(st["thenbody"])
            if st.get("elsebody"):
                eb = st["elsebody"]
                if eb["type"] == "AstStatBlock":
                    self.block(eb)
                else:
                    self.stat(eb, block_end)
            return
        if t == "AstStatWhile":
            self.expr(st["condition"])
            self.block(st["body"])
            return
        if t == "AstStatRepeat":
            body = st["body"]
            end = loc(st["location"])[1]
            for s2 in body.get("body", []):
                self.stat(s2, end)
            self.expr(st["condition"])
            return
        if t == "AstStatFor":
            for k in ("from", "to", "step"):
                if st.get(k):
                    self.expr(st[k])
            body_end = loc(st["body"]["location"])[1]
            self.decl(st["var"], "for", body_end, loc(st["body"]["location"])[0])
            self.block(st["body"])
            return
        if t == "AstStatForIn":
            for v in st["values"]:
                self.expr(v)
            body_end = loc(st["body"]["location"])[1]
            vs = [self.decl(v, "forin", body_end, loc(st["body"]["location"])[0]) for v in st["vars"]]
            for i, L in enumerate(vs):
                L.iter = (i, st["values"])
            self.block(st["body"])
            return
        if t == "AstStatBlock":
            self.block(st)
            return
        if t in ("AstStatBreak", "AstStatContinue", "AstStatTypeAlias", "AstStatDeclareGlobal"):
            return
        # unknown statement kinds: walk generically
        self.generic(st)

    def generic(self, node):
        for k, v in node.items():
            if isinstance(v, dict) and "type" in v:
                if v["type"].startswith("AstExpr"):
                    self.expr(v)
                elif v["type"].startswith("AstStat"):
                    self.stat(v, loc(node["location"])[1])
            elif isinstance(v, list):
                for x in v:
                    if isinstance(x, dict) and "type" in x and x["type"].startswith("AstExpr"):
                        self.expr(x)

    def expr(self, e, parent=None):
        t = e["type"]
        if t == "AstExprLocal":
            self.ref(e)
            return
        if t == "AstExprGlobal":
            self.globals[e["global"]].append(loc(e["location"])[0])
            return
        if t == "AstExprIndexName":
            inner = e["expr"]
            if inner["type"] == "AstExprLocal":
                L = self.ref(inner)
                if L is not None:
                    L.fields[e["index"]] += 1
            else:
                self.expr(inner)
            return
        if t == "AstExprIndexExpr":
            inner = e["expr"]
            if e["index"]["type"] == "AstExprLocal":
                K = self.locals.get(e["index"]["local"]["location"])
                if K is not None:
                    K.uses.append("key")
            if inner["type"] == "AstExprLocal":
                L = self.ref(inner)
                if L is not None and e["index"]["type"] in ("AstExprConstantNumber", "AstExprLocal"):
                    L.uses.append("index")
            else:
                self.expr(inner)
            self.expr(e["index"])
            return
        if t == "AstExprCall":
            f = e["func"]
            callee = None
            if f["type"] == "AstExprIndexName" and f.get("op") == ":":
                callee = ":" + f["index"]
                self.method_names.add(f["index"])
            else:
                callee = dotted(f)
            self.expr(f)
            if f["type"] == "AstExprIndexName" and f.get("op") == ":" and e["args"] and \
                    unreg(e["args"][0])["type"] == "AstExprFunction":
                fn0 = unreg(e["args"][0])
                if f["index"] in ("Connect", "Once", "ConnectParallel") and f["expr"]["type"] == "AstExprIndexName":
                    self.callbacks[id(fn0)] = EVENT_PARAMS.get(f["expr"]["index"])
                elif f["index"] == "OnChanged":
                    self.callbacks[id(fn0)] = ["value"]
            for i, a in enumerate(e["args"]):
                if a["type"] == "AstExprLocal":
                    L = self.locals.get(a["local"]["location"])
                    if L is not None and callee:
                        L.uses.append(("arg", callee, i))
                self.expr(a)
            return
        if t == "AstExprFunction":
            body = e["body"]
            end = loc(body["location"])[1]
            start = loc(body["location"])[0]
            off = 0
            if e.get("self"):
                # implicit `self` of a `function t:m()` definition (never renamed, but it binds the name)
                self.decl(e["self"], "self", end, start)
                off = 1
            params = [self.decl(a, "param", end, start) for a in e.get("args", [])]
            if params and not off:
                params[0].first_param_of = e
            for i, p in enumerate(params):
                p.uses.append(("param", id(e), i + off))
            self.block(body, end)
            return
        if t == "AstExprTable":
            for it in e.get("items", []):
                if it.get("kind") == "record" and it["key"]["type"] == "AstExprConstantString":
                    v = unreg(it["value"])
                    if v["type"] == "AstExprLocal":
                        L = self.locals.get(v["local"]["location"])
                        if L is not None:
                            L.stored.append(it["key"]["value"])
                    if v["type"] == "AstExprFunction":
                        self.func_fields[id(v)] = it["key"]["value"]
                        if it["key"]["value"] in ("Callback", "ChangedCallback"):
                            self.callbacks[id(v)] = ["value"]
                if it.get("key"):
                    self.expr(it["key"])
                self.expr(it["value"])
            return
        if t == "AstExprBinary" and e["op"] in ("CompareEq", "CompareNe"):
            for a, b in ((e["left"], e["right"]), (e["right"], e["left"])):
                if a["type"] == "AstExprLocal" and b["type"] == "AstExprConstantString":
                    L = self.locals.get(a["local"]["location"])
                    if L is not None:
                        L.uses.append(("strcmp", b["value"]))
        if t == "AstExprUnary":
            if e["op"] == "Len" and e["expr"]["type"] == "AstExprLocal":
                L = self.locals.get(e["expr"]["local"]["location"])
                if L is not None:
                    L.uses.append("len")
            self.expr(e["expr"])
            return
        for k, v in e.items():
            if isinstance(v, dict) and "type" in v and v["type"].startswith("AstExpr"):
                self.expr(v)
            elif isinstance(v, list):
                for x in v:
                    if isinstance(x, dict) and "type" in x and x["type"].startswith("AstExpr"):
                        self.expr(x)
                    elif isinstance(x, dict) and x.get("type") == "AstExprIfElse":
                        self.expr(x)

    # -------------------------------------------------------------------------------------------
    def from_init(self, e):
        if e is None:
            return None
        t = e["type"]
        if t == "AstExprCall":
            f = e["func"]
            args = e["args"]
            s0 = args[0]["value"] if args and args[0]["type"] == "AstExprConstantString" else None
            if f["type"] == "AstExprIndexName" and f.get("op") == ":":
                m = f["index"]
                if s0 and re.match(r"^[A-Z][A-Za-z0-9]+$", s0) and len(args) >= 2 and args[1]["type"] == "AstExprTable":
                    return clean(lowerfirst(s0))
                if m in ("GetService", "FindService") and s0:
                    return clean(s0)
                if m in ("FindFirstChild", "WaitForChild", "FindFirstChildOfClass", "FindFirstChildWhichIsA",
                         "FindFirstAncestor", "FindFirstAncestorOfClass") and s0:
                    return clean(lowerfirst(s0))
                if m in ("AddToggle", "AddSlider", "AddDropdown", "AddInput", "AddColorPicker", "AddKeyPicker") and s0:
                    return clean(s0)
                return METHOD_INIT.get(m) or verb_name(m)
            d = dotted(f)
            if d in ("Instance.new", "Drawing.new") and s0:
                return clean(lowerfirst(s0))
            if s0 and re.match(r"^[A-Z][A-Za-z0-9]+$", s0) and len(args) >= 2 and args[1]["type"] == "AstExprTable":
                return clean(lowerfirst(s0))     # Create("Frame", {...})-style helpers
            if d == "cloneref" and args:
                return self.from_init(args[0])
            if d == "require" and args:
                inner = args[0]
                if inner["type"] == "AstExprIndexName":
                    return clean(lowerfirst(inner["index"]))
                if inner["type"] == "AstExprCall":
                    strs = [a["value"] for a in inner["args"] if a["type"] == "AstExprConstantString"]
                    if strs:
                        return clean(lowerfirst(strs[-1]))
                return "module"
            if d in GLOBAL_INIT:
                return GLOBAL_INIT[d]
            fname = d or (self.cur_name(f) if f["type"] == "AstExprLocal" else None)
            if fname and len(args) == 1 and re.search(r"(?i)clone|ref$|cclosure|protect", fname):
                a = args[0]
                if a["type"] == "AstExprIndexName":
                    if a["index"] == "new" and a["expr"]["type"] == "AstExprIndexName":
                        return clean(lowerfirst(a["expr"]["index"]) + "New")
                    return clean(lowerfirst(a["index"]))
                if a["type"] == "AstExprGlobal":
                    return clean(a["global"] + "Fn")
                if a["type"] == "AstExprLocal" and not GENERIC.match(self.cur_name(a) or "v"):
                    return clean(self.cur_name(a))
            if fname and not GENERIC.match(fname):
                vn = verb_name(fname.split(".")[-1])
                if vn:
                    return vn
            if args and all(a["type"] == "AstExprConstantString" for a in args[1:]) and len(args) >= 2:
                strs = [a["value"] for a in args if a["type"] == "AstExprConstantString"]
                if strs and re.match(r"^[A-Za-z]\w*$", strs[-1]):
                    return clean(lowerfirst(strs[-1]))
            return None
        if t == "AstExprIndexName":
            n = e["index"]
            if n in ("Value",) and e["expr"]["type"] == "AstExprIndexName":
                return clean(lowerfirst(e["expr"]["index"]))
            if n in ("Value",) and e["expr"]["type"] == "AstExprLocal":
                base = self.cur_name(e["expr"])
                if base and not GENERIC.match(base):
                    return clean(re.sub(r"\d+$", "", base) + "Value")
                return None
            return clean(lowerfirst(n))
        if t == "AstExprBinary" and e["op"] in ("And", "Or"):
            return self.from_init(e["right"]) if e["op"] == "And" else (self.from_init(e["left"]) or self.from_init(e["right"]))
        if t == "AstExprLocal":
            base = self.cur_name(e)
            if base and not GENERIC.match(base) and base not in ("self", "_"):
                return clean(re.sub(r"\d+$", "", base))
            return None
        if t == "AstExprIndexExpr" and e["index"]["type"] == "AstExprConstantString":
            return clean(lowerfirst(e["index"]["value"]))
        if t == "AstExprGlobal":
            return None
        if t == "AstExprTable":
            recs = [it for it in e.get("items", []) if it.get("kind") == "record"]
            subt = [it for it in recs if it["value"]["type"] == "AstExprTable"]
            if len(recs) >= 8 and len(subt) >= 4:
                return "state"
        return None

    def cur_name(self, e):
        L = self.locals.get(e["local"]["location"])
        if L is None:
            return e["local"]["name"]
        return L.new or L.name

    def from_role(self, L):
        votes = collections.Counter()
        for f, c in L.fields.items():
            for role, w in FIELD_ROLES.get(f, ()):
                votes[role] += w
        for u in L.uses:
            if u == "len" or u == "index":
                votes["list"] += 1
            elif isinstance(u, tuple) and u[0] == "arg":
                callee, i = u[1], u[2]
                if callee in ("table.insert", "table.remove", "table.sort", "table.clear", "ipairs",
                              "table.concat", "table.unpack", "unpack") and i == 0:
                    votes["list"] += 2
                if callee == "setmetatable" and i == 0:
                    votes["obj"] += 1
        if not votes:
            return None
        (best, s), = votes.most_common(1)
        tied = [r for r, c in votes.items() if c == s]
        if len(tied) > 1:
            pref = ["player", "character", "humanoid", "camera", "item", "option", "highlight", "sound", "track",
                    "tween", "line", "circle", "connection", "signal", "event", "input", "cframe", "vector", "part",
                    "model", "emitter", "attachment", "gui", "label", "frame", "list", "instance", "obj"]
            best = min(tied, key=lambda r: pref.index(r) if r in pref else 99)
        if s < 2 and best in ("instance", "label", "frame", "obj", "list"):
            return None if best != "list" or s < 1 else best
        return best

    def from_iter(self, L):
        if L.iter is None:
            return None
        i, values = L.iter
        if not values:
            return None
        e = values[0]
        if e["type"] == "AstExprCall":
            f = e["func"]
            d = dotted(f)
            if d in ("pairs", "ipairs") and e["args"]:
                if d == "ipairs" and i == 0:
                    return "i"
                inner = e["args"][0]
                if i == 1:
                    return self.item_name(inner)
                return None
            if f["type"] == "AstExprIndexName" and f.get("op") == ":" and i == 1:
                return ITER_ITEM.get(f["index"])
            if d == "listfiles" and i == 1:
                return "file"
            return None
        if i == 1:
            return self.item_name(e)
        return None

    def item_name(self, e):
        if e["type"] == "AstExprCall":
            f = e["func"]
            if f["type"] == "AstExprIndexName" and f.get("op") == ":":
                return ITER_ITEM.get(f["index"])
            if dotted(f) == "listfiles":
                return "file"
            return None
        name = None
        if e["type"] == "AstExprLocal":
            name = e["local"]["name"]
            L = self.locals.get(e["local"]["location"])
            if L is not None and L.new:
                name = L.new
        elif e["type"] == "AstExprIndexName":
            name = e["index"]
        if name and not GENERIC.match(name):
            s = singular(lowerfirst(name))
            if s and clean(s):
                return clean(s)
        return None

    def is_self(self, L):
        f = L.first_param_of
        if f is None or L.kind != "param":
            return False
        field = self.func_fields.get(id(f))
        if not field or field not in self.method_names:
            return False
        return bool(L.fields) or any(isinstance(u, tuple) and u[0] == "arg" and u[1].startswith(":") for u in L.uses)

    def suggest(self, L):
        if L.kind == "param":
            for u in L.uses:
                if isinstance(u, tuple) and u[0] == "param":
                    fid, idx = u[1], u[2]
                    break
            else:
                fid = idx = None
            field = self.func_fields.get(fid)
            if field in META_PARAMS and idx is not None and idx < len(META_PARAMS[field]):
                return META_PARAMS[field][idx]
            cbn = self.callbacks.get(fid)
            if cbn and idx is not None and idx < len(cbn) and cbn[idx] != "...":
                return cbn[idx]
            for u in L.uses:
                if isinstance(u, tuple) and u[0] == "arg" and u[2] == 0 and u[1] in ("Instance.new", "Drawing.new"):
                    return "className"
            if self.is_self(L):
                return "self"
        if L.func is not None:
            field = self.func_fields.get(id(L.func))
            if field:
                return clean(field)
        strong = self.from_init(L.init) if (L.init is not None and L.init["type"] == "AstExprCall") else None
        if strong:
            return strong
        if L.stored and L.kind != "param" and len(set(L.stored)) == 1:
            c = L.stored[0]
            if re.match(r"^[A-Z][a-z]", c):
                c = lowerfirst(c)
            n = clean(c)
            if n:
                return n
        strs = {u[1] for u in L.uses if isinstance(u, tuple) and u[0] == "strcmp"}
        if len(strs) >= 2 and "key" in L.uses:
            return "key"
        if L.kind in ("for", "forin"):
            n = self.from_iter(L)
            if n:
                return n
        n = self.from_init(L.init)
        if n:
            return n
        if L.init is None or L.init["type"] == "AstExprConstantNil":
            names = {self.from_init(v) for v in L.assigns} - {None}
            if len(names) == 1:
                return names.pop()
        if L.kind == "forin" and L.iter and L.iter[0] == 1 and L.iter[1] and L.iter[1][0]["type"] == "AstExprTable":
            items = [it["value"]["type"] for it in L.iter[1][0].get("items", [])]
            if items and all(t == "AstExprConstantString" for t in items):
                return "name"
        r = self.from_role(L)
        if r:
            return r
        return None


def scopes_overlap(a, b):
    (s1, e1), (s2, e2) = a, b
    return s1 < e2 and s2 < e1


def run(src_path, out_path):
    raw = subprocess.run([LUAU_AST, src_path], capture_output=True, check=True).stdout
    ast = json.loads(raw.decode("utf-8", "replace"))
    A = Analyzer(ast["root"])
    A.block(ast["root"])
    # current names by scope, for conflict checks
    by_name = collections.defaultdict(list)
    for L in A.locals.values():
        by_name[L.name].append(L)
    renamed = 0
    order = sorted(A.locals.values(), key=lambda L: L.decl)
    for L in order:
        if not GENERIC.match(L.name) or L.name == "_":
            continue
        base = A.suggest(L)
        if not base or base == L.name:
            continue
        for k in range(1, 50):
            cand = base if k == 1 else "%s%d" % (base, k)
            if cand in KEYWORDS and cand != "self":
                continue
            bad = False
            for M in by_name.get(cand, ()):
                if M is not L and scopes_overlap(M.scope, L.scope):
                    bad = True
                    break
            if not bad:
                for pos in A.globals.get(cand, ()):
                    if L.scope[0] <= pos <= L.scope[1]:
                        bad = True
                        break
            if not bad:
                break
        else:
            continue
        if cand == L.name or (re.sub(r"\d+$", "", cand) == re.sub(r"\d+$", "", L.name) and len(cand) >= len(L.name)):
            continue
        by_name[L.name].remove(L)
        if os.environ.get("RENAME_LOG"):
            print("RN %d %s -> %s" % (L.decl[0][0] + 1, L.name, cand))
        L.new = cand
        L.name = cand
        by_name[cand].append(L)
        renamed += 1
    # apply edits
    lines = open(src_path, "rb").read().split(b"\n")
    edits = collections.defaultdict(list)
    for L in A.locals.values():
        if not L.new:
            continue
        spans = [L.decl] + L.refs
        for (a, b) in spans:
            edits[a[0]].append((a[1], b[1], L.new.encode()))
    for ln, es in edits.items():
        s = lines[ln]
        for c1, c2, new in sorted(set(es), reverse=True):
            s = s[:c1] + new + s[c2:]
        lines[ln] = s
    open(out_path, "wb").write(b"\n".join(lines))
    print("locals", len(A.locals), "renamed", renamed)


if __name__ == "__main__":
    if len(sys.argv) > 3:
        LUAU_AST = sys.argv[3]
    run(sys.argv[1], sys.argv[2])
