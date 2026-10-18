-- Deterministic mock of the Roblox / executor environment used to compare the original (Luraph VM)
-- script with the decompiled one. Every interaction with the mocked world is appended to a trace.
local __H = {}
do
    local env = getfenv(0)
    local envmt = getmetatable(env)
    local realIndex = envmt and envmt.__index
    local __rawget0 = rawget
    local function realget(k)
        return __rawget0(realIndex, k)
    end
    -- capture the real library functions we rely on before the script can touch anything
    local _print, _type, _tostring, _pairs, _ipairs, _select, _pcall, _error =
        realget("print"), realget("type"), realget("tostring"), realget("pairs"), realget("ipairs"), realget("select"), realget("pcall"), realget("error")
    local _setmetatable, _getmetatable, _rawget, _rawset, _rawequal = realget("setmetatable"), realget("getmetatable"), realget("rawget"), realget("rawset"), realget("rawequal")
    local _co = realget("coroutine")
    local _table = realget("table")
    local _string = realget("string")
    local _math = realget("math")
    local _typeof = realget("typeof")
    local _debug = realget("debug")
    local _os = realget("os")

    local TRACE = {}
    local NT = 0
    local MAXT = 10000000
    local ON = false
    local STOP = false
    local MAIN = _co.running()
    __H.trace = TRACE

    local STREAM = true
    local FUZZEV = nil
    local FUZZEVMAX = 20000
    local function ev(s)
        if not ON or STOP then
            return
        end
        if FUZZEV ~= nil then
            FUZZEV += 1
            if FUZZEV > FUZZEVMAX then
                FUZZEV = nil
                _error("FUZZ_EVENT_BUDGET", 0)
            end
        end
        NT += 1
        if NT > MAXT then
            STOP = true
            return
        end
        if STREAM then
            _print(s)
        else
            TRACE[NT] = s
        end
    end
    local STEPS = 0
    local STEPMAX = 400000000
    local FUZZSTEPS, FUZZMAX = nil, 20000000
    function __STEP()
        STEPS += 1
        if STOP then
            _error("HARNESS_STOP", 0)
        end
        if FUZZSTEPS ~= nil then
            FUZZSTEPS += 1
            if FUZZSTEPS > FUZZMAX then
                _error("FUZZ_STEP_BUDGET", 0)
            end
            return
        end
        if STEPS > STEPMAX then
            if not STOP then
                _print("HARNESS_STEP_BUDGET")
            end
            STOP = true
            _error("HARNESS_STEP_BUDGET", 0)
        end
    end
    __H.ev = ev
    __H.rawprint = _print

    local NUMKEYS = {}
    for _, k in _ipairs({"X", "Y", "Z", "W", "Offset", "Scale", "Magnitude", "Width", "Height", "R", "G", "B",
        "TextSize", "Transparency", "BackgroundTransparency", "TextTransparency", "ImageTransparency", "Rotation",
        "ZIndex", "LayoutOrder", "Health", "MaxHealth", "WalkSpeed", "JumpPower", "JumpHeight", "FieldOfView",
        "TimeLength", "Volume", "Brightness", "ClockTime", "FogEnd", "FogStart", "Density", "Speed", "Length",
        "TimePosition", "Distance", "Thickness", "Radius", "NumSides", "TextBounds_X", "Mass", "PlaybackSpeed",
        "CursorPosition", "SelectionStart",
        "UserId", "AccountAge", "Ping", "Delta", "Position_X", "HipHeight", "Gravity", "Elapsed", "Bottom", "Top",
        "Left", "Right", "Min", "Max", "Hue", "Saturation", "MinValue", "MaxValue", "Value_n", "Intensity", "Spread",
        "Exposure", "Contrast", "Range", "Angle", "Decay", "Diffuse", "Haze", "Glare", "Offset2", "Cover",
        "ExposureCompensation", "EnvironmentDiffuseScale", "EnvironmentSpecularScale", "GeographicLatitude",
        "MaxDistance", "Threshold", "BloomIntensity", "TextBoundsX", "Duration", "Weight", "Frequency"}) do
        NUMKEYS[k] = true
    end
    local pinfo = _setmetatable({}, {__mode = "k"})
    local Proxy = {}

    -- stable names for plain tables and functions used as keys (addresses differ between runs)
    local KEYIDS = _setmetatable({}, {__mode = "k"})
    local nkeyid = 0
    local function keyname(k)
        local t = _type(k)
        if t == "table" or t == "function" or t == "thread" or t == "userdata" then
            if pinfo[k] then
                return "<" .. pinfo[k].path .. ">"
            end
            local id = KEYIDS[k]
            if id == nil then
                nkeyid += 1
                id = t .. "#" .. nkeyid
                KEYIDS[k] = id
            end
            return "{" .. id .. "}"
        end
        return _tostring(k)
    end
    local function desc(v, depth)
        local t = _type(v)
        if t == "nil" or t == "boolean" then
            return _tostring(v)
        elseif t == "number" then
            if v ~= v then
                return "nan"
            end
            if v == _math.floor(v) and _math.abs(v) < 2 ^ 53 then
                return _string.format("%d", v)
            end
            return _string.format("%.6g", v)
        elseif t == "string" then
            local s = v
            if #s > 60 then
                s = _string.sub(s, 1, 60) .. "..(" .. #v .. ")"
            end
            return _string.format("%q", s)
        elseif t == "table" then
            local i = pinfo[v]
            if i then
                return "<" .. i.path .. ">"
            end
            return "{tbl}"
        elseif t == "function" then
            return "fn"
        elseif t == "thread" then
            return "thread"
        end
        return t
    end
    __H.desc = desc

    local function descargs(...)
        local n = _select("#", ...)
        local parts = {}
        for i = 1, n do
            parts[i] = desc((_select(i, ...)))
        end
        return _table.concat(parts, ", ")
    end

    local counters = {}
    local function fresh(base)
        local n = (counters[base] or 0) + 1
        counters[base] = n
        return base .. "#" .. n
    end

    local function P(path, class)
        local p = _setmetatable({}, Proxy)
        pinfo[p] = {path = path, props = {}, children = {}, class = class}
        return p
    end
    __H.P = P

    -- signals --------------------------------------------------------------------------------
    local CONNS = {}
    local function connect(sig, fn, once)
        local c = P(fresh(pinfo[sig].path .. ":conn"))
        local rec = {sig = pinfo[sig].path, fn = fn, alive = true, once = once, id = #CONNS + 1}
        CONNS[#CONNS + 1] = rec
        pinfo[c].props.Connected = true
        pinfo[c].conn = rec
        return c
    end

    -- method implementations: receive (self, ...)
    local METHODS = {}
    local function method(name, fn)
        METHODS[name] = fn
    end
    local services = {}
    method("GetService", function(self, name)
        ev("GetService " .. desc(name))
        local s = services[name]
        if not s then
            s = P("svc:" .. keyname(name), name)
            pinfo[s].props.Name = name
            pinfo[s].props.ClassName = name
            services[name] = s
        end
        return s
    end)
    method("FindService", METHODS.GetService)
    local ISA
    local function kids(self)
        local info = pinfo[self]
        local out = {}
        if info and info.kids then
            for _, k in _ipairs(info.kids) do
                if pinfo[k].props.Parent == self then
                    out[#out + 1] = k
                end
            end
        end
        return out
    end
    __H.kids = kids
    local function find(self, pred, recursive)
        for _, k in _ipairs(kids(self)) do
            if pred(k) then
                return k
            end
            if recursive then
                local r = find(k, pred, true)
                if r then
                    return r
                end
            end
        end
        return nil
    end
    local function byname(name)
        return function(k)
            return pinfo[k].props.Name == name
        end
    end
    local function byclass(cls)
        return function(k)
            return pinfo[k].class == cls
        end
    end
    local function lookupres(self, n, a, b, r)
        ev(pinfo[self].path .. ":" .. n .. "(" .. descargs(a, b) .. ") -> " .. desc(r))
        return r
    end
    local ABSENT = {LoadingScreen = true}
    local function gamechild(self, name)
        -- objects the game provides (not created by the script) are assumed to have the children asked for
        local info = pinfo[self]
        if info.created or ABSENT[name] or _type(name) ~= "string" then
            return nil
        end
        local key = "@" .. name
        local c = info.children[key]
        if not c then
            c = P(info.path .. "/" .. name)
            pinfo[c].props.Name = name
            info.children[key] = c
        end
        return c
    end
    method("FindFirstChild", function(self, name, rec)
        return lookupres(self, "FindFirstChild", name, rec, find(self, byname(name), rec) or gamechild(self, name))
    end)
    method("FindFirstChildOfClass", function(self, cls)
        return lookupres(self, "FindFirstChildOfClass", cls, nil, find(self, byclass(cls), false))
    end)
    method("FindFirstChildWhichIsA", function(self, cls, rec)
        return lookupres(self, "FindFirstChildWhichIsA", cls, rec, find(self, function(k)
            return pinfo[k].class ~= nil and ISA(pinfo[k].class, cls)
        end, rec))
    end)
    method("FindFirstDescendant", function(self, name)
        return lookupres(self, "FindFirstDescendant", name, nil, find(self, byname(name), true))
    end)
    for _, n in _ipairs({"FindFirstAncestor", "FindFirstAncestorOfClass", "FindFirstAncestorWhichIsA"}) do
        method(n, function(self, a)
            local cur = pinfo[self].props.Parent
            while cur ~= nil and pinfo[cur] do
                local pr = pinfo[cur]
                if (n == "FindFirstAncestor" and pr.props.Name == a) or (n ~= "FindFirstAncestor" and pr.class == a) then
                    return lookupres(self, n, a, nil, cur)
                end
                cur = pr.props.Parent
            end
            return lookupres(self, n, a, nil, nil)
        end)
    end
    method("WaitForChild", function(self, name, t)
        ev(pinfo[self].path .. ":WaitForChild(" .. desc(name) .. ")")
        local info = pinfo[self]
        local found = find(self, byname(name), false)
        if found then
            return found
        end
        local c = info.children["@" .. keyname(name)]
        if not c then
            c = P(info.path .. "/" .. _tostring(name))
            pinfo[c].props.Name = name
            info.children["@" .. keyname(name)] = c
        end
        return c
    end)
    method("GetChildren", function(self)
        ev(pinfo[self].path .. ":GetChildren()")
        return kids(self)
    end)
    method("GetDescendants", function(self)
        ev(pinfo[self].path .. ":GetDescendants()")
        local out = {}
        local function rec(o)
            for _, k in _ipairs(kids(o)) do
                out[#out + 1] = k
                rec(k)
            end
        end
        rec(self)
        return out
    end)
    for _, n in _ipairs({"GetTouchingParts", "GetConnectedParts",
        "GetPartsInPart", "GetPartBoundsInBox", "GetPartBoundsInRadius", "GetTags", "GetAttributes",
        "GetPlayingAnimationTracks", "GetJoints", "GetAccessories"}) do
        method(n, function(self, ...)
            ev(pinfo[self].path .. ":" .. n .. "()")
            return {}
        end)
    end
    local SUPER = {Part = "BasePart", MeshPart = "BasePart", UnionOperation = "BasePart", WedgePart = "BasePart",
        TrussPart = "BasePart", CornerWedgePart = "BasePart", SpawnLocation = "Part", Seat = "Part",
        BasePart = "PVInstance", Model = "PVInstance", PVInstance = "Instance", PointLight = "Light",
        SpotLight = "Light", SurfaceLight = "Light", Light = "Instance", ParticleEmitter = "Instance",
        Attachment = "Instance", Beam = "Instance", Trail = "Instance", Folder = "Instance", Sky = "Instance",
        Decal = "FaceInstance", Texture = "Decal", FaceInstance = "Instance"}
    local function isa(class, cls)
        while class ~= nil do
            if class == cls then
                return true
            end
            class = SUPER[class]
        end
        return cls == "Instance" and false
    end
    ISA = isa
    -- players: the local player plus two others, each with a character
    local function child(o, k)
        local info = pinfo[o]
        local c = info.children[k]
        if c == nil then
            c = P(info.path .. "." .. k)
            info.children[k] = c
        end
        return c
    end
    local OTHERS = nil
    method("GetPlayers", function(self)
        ev(pinfo[self].path .. ":GetPlayers()")
        local players = services.Players or METHODS.GetService(self, "Players")
        if OTHERS == nil then
            OTHERS = {}
            for i = 1, 2 do
                local pl = P("player" .. i, "Player")
                local pr = pinfo[pl].props
                pr.Name = "Player" .. i
                pr.DisplayName = "Player" .. i
                pr.UserId = 1000 + i
                pr.ClassName = "Player"
                local ch = P("player" .. i .. ".Character", "Model")
                pinfo[ch].props.Name = "Player" .. i
                pr.Character = ch
                local hum = P("player" .. i .. ".Character/Humanoid", "Humanoid")
                pinfo[hum].props.Name = "Humanoid"
                pinfo[hum].props.Health = 100
                pinfo[hum].props.MaxHealth = 100
                pinfo[hum].props.Parent = ch
                local root = P("player" .. i .. ".Character/HumanoidRootPart", "Part")
                pinfo[root].props.Name = "HumanoidRootPart"
                pinfo[root].props.Parent = ch
                local head = P("player" .. i .. ".Character/Head", "Part")
                pinfo[head].props.Name = "Head"
                pinfo[head].props.Parent = ch
                pinfo[ch].kids = {hum, root, head}
                pinfo[hum].props.RootPart = root
                OTHERS[i] = pl
            end
        end
        return {child(players, "LocalPlayer"), OTHERS[1], OTHERS[2]}
    end)
    method("BindToRenderStep", function(self, name, prio, fn)
        ev(pinfo[self].path .. ":BindToRenderStep(" .. descargs(name, prio) .. ")")
        local rec = {sig = "RenderStep:" .. keyname(name), fn = fn, alive = true, once = false}
        CONNS[#CONNS + 1] = rec
    end)
    method("UnbindFromRenderStep", function(self, name)
        ev(pinfo[self].path .. ":UnbindFromRenderStep(" .. desc(name) .. ")")
        for _, c in _ipairs(CONNS) do
            if c.sig == "RenderStep:" .. keyname(name) then
                c.alive = false
            end
        end
    end)
    method("IsA", function(self, cls)
        local info = pinfo[self]
        local r = info.class ~= nil and isa(info.class, cls)
        ev(info.path .. ":IsA(" .. desc(cls) .. ") -> " .. _tostring(r))
        return r
    end)
    method("IsLoaded", function(self)
        ev("IsLoaded")
        return true
    end)
    method("IsDescendantOf", function(self, o)
        ev(pinfo[self].path .. ":IsDescendantOf(" .. desc(o) .. ")")
        return true
    end)
    for _, n in _ipairs({"Connect", "ConnectParallel"}) do
        method(n, function(self, fn)
            ev(pinfo[self].path .. ":" .. n .. "(" .. desc(fn) .. ")")
            return connect(self, fn, false)
        end)
    end
    method("Fire", function(self, ...)
        -- BindableEvent: run the handlers connected to .Event with the fired arguments
        ev(pinfo[self].path .. ":Fire(" .. descargs(...) .. ")")
        local sig = pinfo[self].path .. ".Event"
        local snapshot = {}
        for i, c in _ipairs(CONNS) do
            if c.sig == sig and c.alive then
                snapshot[#snapshot + 1] = c
            end
        end
        for _, c in _ipairs(snapshot) do
            if c.once then
                c.alive = false
            end
            if _type(c.fn) == "function" then
                __H.resume(_co.create(c.fn), ...)
            end
        end
    end)
    method("Once", function(self, fn)
        ev(pinfo[self].path .. ":Once(" .. desc(fn) .. ")")
        return connect(self, fn, true)
    end)
    method("Disconnect", function(self)
        ev(pinfo[self].path .. ":Disconnect()")
        local rec = pinfo[self].conn
        if rec then
            rec.alive = false
        end
        pinfo[self].props.Connected = false
    end)
    method("Wait", function(self)
        ev(pinfo[self].path .. ":Wait()")
        local r = P(fresh(pinfo[self].path .. ":Wait"))
        __H.yield()
        return r
    end)
    method("Clone", function(self)
        ev(pinfo[self].path .. ":Clone()")
        local function clone(o, path)
            local c = P(path, pinfo[o].class)
            pinfo[c].created = pinfo[o].created
            for k, v in _pairs(pinfo[o].props) do
                if k ~= "Parent" then
                    pinfo[c].props[k] = v
                end
            end
            for i, k in _ipairs(kids(o)) do
                local kc = clone(k, path .. "/" .. i)
                pinfo[kc].props.Parent = c
                pinfo[c].kids = pinfo[c].kids or {}
                pinfo[c].kids[#pinfo[c].kids + 1] = kc
            end
            return c
        end
        return clone(self, fresh(pinfo[self].path .. ":Clone"))
    end)
    method("GetAttribute", function(self, n)
        local v = pinfo[self].props["@attr:" .. keyname(n)]
        ev(pinfo[self].path .. ":GetAttribute(" .. desc(n) .. ") -> " .. desc(v))
        return v
    end)
    method("SetAttribute", function(self, n, v)
        ev(pinfo[self].path .. ":SetAttribute(" .. desc(n) .. ", " .. desc(v) .. ")")
        pinfo[self].props["@attr:" .. keyname(n)] = v
    end)
    method("GetPropertyChangedSignal", function(self, n)
        ev(pinfo[self].path .. ":GetPropertyChangedSignal(" .. desc(n) .. ")")
        local key = "@sig:" .. keyname(n)
        local c = pinfo[self].children[key]
        if not c then
            c = P(pinfo[self].path .. ".Changed[" .. _tostring(n) .. "]")
            pinfo[self].children[key] = c
        end
        return c
    end)
    method("DeserializeInstancesAsync", function(self, b)
        ev("DeserializeInstancesAsync " .. desc(b))
        if _typeof(b) ~= "buffer" then
            local tn = "table"
            if pinfo[b] then
                tn = _string.match(pinfo[b].path, "^([%w_]+)") or "Instance"
            end
            _error("Unable to cast " .. tn .. " to buffer", 0)
        end
        -- the bundle's instance tree, read from the decoded RBXM (asset_tree.luau)
        local roots = {}
        local function build(spec, path, par)
            local o = P(path, spec[1])
            local info = pinfo[o]
            info.created = true
            info.props.ClassName = spec[1]
            info.props.Name = spec[2]
            if par ~= nil then
                info.props.Parent = par
                local pi = pinfo[par]
                pi.kids = pi.kids or {}
                pi.kids[#pi.kids + 1] = o
            end
            local used = {}
            for _, c in _ipairs(spec[3]) do
                local cp = path .. "/" .. c[2]
                used[cp] = (used[cp] or 0) + 1
                if used[cp] > 1 then
                    cp = cp .. "#" .. used[cp]
                end
                build(c, cp, o)
            end
            return o
        end
        for i, spec in _ipairs(ASSET_TREE) do
            roots[i] = build(spec, "asset" .. i, nil)
        end
        return roots
    end)
    method("HttpGet", function(self, url)
        ev("HttpGet " .. desc(url))
        return ""
    end)
    method("JSONDecode", function(self, s)
        ev("JSONDecode " .. desc(s))
        return {}
    end)
    method("JSONEncode", function(self, t)
        ev("JSONEncode")
        return "{}"
    end)

    Proxy.__index = function(self, k)
        local info = pinfo[self]
        local v = info.props[k]
        if v ~= nil then
            ev("get " .. info.path .. "." .. keyname(k) .. " -> " .. desc(v))
            return v
        end
        local m = METHODS[k]
        if m ~= nil then
            return m
        end
        if NUMKEYS[k] then
            local udim = (k == "X" or k == "Y") and (_string.find(info.path, "UDim2", 1, true)
                or _string.match(info.path, "%.Position$") or _string.match(info.path, "%.Size$")
                or _string.match(info.path, "%.CanvasSize$") or info.class == "UDim2")
            if not udim then
                ev("get " .. info.path .. "." .. keyname(k) .. " -> 0")
                return 0
            end
        end
        if info.kids and _type(k) == "string" then
            for _, kid in _ipairs(info.kids) do
                local ki = pinfo[kid]
                if ki.props.Parent == self and ki.props.Name == k then
                    ev("get " .. info.path .. "." .. k .. " -> " .. desc(kid))
                    return kid
                end
            end
        end
        local c = info.children[k]
        if c == nil then
            c = P(info.path .. "." .. keyname(k))
            info.children[k] = c
        end
        ev("get " .. info.path .. "." .. keyname(k))
        return c
    end
    Proxy.__newindex = function(self, k, v)
        local info = pinfo[self]
        info.props[k] = v
        if k == "Parent" and pinfo[v] then
            local pi = pinfo[v]
            pi.kids = pi.kids or {}
            pi.kids[#pi.kids + 1] = self
        end
        ev("set " .. info.path .. "." .. keyname(k) .. " = " .. desc(v))
    end
    Proxy.__call = function(self, ...)
        local info = pinfo[self]
        ev("call " .. info.path .. "(" .. descargs(...) .. ")")
        local base = fresh(info.path .. "()")
        local last = _string.match(info.path, "([%w_]+)$") or ""
        if last == "toHSV" or last == "ToHSV" or last == "ToOrientation" or last == "ToEulerAnglesXYZ"
            or last == "ToEulerAnglesYXZ" or last == "ToEulerAngles" then
            return 0, 0, 0
        elseif last == "WorldToViewportPoint" or last == "WorldToScreenPoint" then
            return P(base), true
        elseif last == "GetBoundingBox" then
            return P(base), P(base .. ":2")
        elseif last == "FindPartOnRay" or last == "FindPartOnRayWithIgnoreList" or last == "FindPartOnRayWithWhitelist" then
            return nil, P(base .. ":2"), P(base .. ":3"), P(base .. ":4")
        elseif last == "GetComponents" then
            return 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1
        end
        return P(base)
    end
    local function arith(op)
        return function(a, b)
            ev("arith " .. op .. " " .. desc(a) .. " " .. desc(b))
            return P(fresh("expr" .. op))
        end
    end
    for op, mm in _pairs({["+"] = "__add", ["-"] = "__sub", ["*"] = "__mul", ["/"] = "__div", ["%"] = "__mod",
        ["^"] = "__pow", ["//"] = "__idiv"}) do
        Proxy[mm] = arith(op)
    end
    Proxy.__unm = function(a)
        ev("arith unm " .. desc(a))
        return P(fresh("expr-"))
    end
    Proxy.__lt = function(a, b)
        ev("cmp < " .. desc(a) .. " " .. desc(b))
        return false
    end
    Proxy.__le = function(a, b)
        ev("cmp <= " .. desc(a) .. " " .. desc(b))
        return false
    end
    Proxy.__len = function(a)
        ev("len " .. desc(a))
        return 0
    end
    Proxy.__concat = function(a, b)
        ev("concat " .. desc(a) .. " " .. desc(b))
        return (_type(a) == "string" and a or desc(a)) .. (_type(b) == "string" and b or desc(b))
    end
    Proxy.__tostring = function(a)
        return pinfo[a].path
    end
    Proxy.__iter = function(a)
        ev("iter " .. desc(a))
        local info = pinfo[a]
        local items = info.iteritems
        if items == nil then
            items = {}
            local keys = {}
            -- tables keyed by players (`_duelers_by_player`) get player objects as keys
            local bykey = _string.find(_string.lower(info.path), "player", 1, true) ~= nil
            for i = 1, 2 do
                local key = "k" .. i
                if bykey then
                    key = P(info.path .. "{key" .. i .. "}", "Player")
                    pinfo[key].props.Name = "Player" .. i
                    pinfo[key].props.ClassName = "Player"
                end
                local c = P(info.path .. "[k" .. i .. "]")
                pinfo[c].props.Name = info.path:match("([%w_]+)$") .. i
                items[i] = {key, c}
            end
            info.iteritems = items
        end
        local i = 0
        return function()
            i += 1
            local it = items[i]
            if it == nil then
                return nil
            end
            return it[1], it[2]
        end
    end

    -- scheduler ---------------------------------------------------------------------------------
    local QUEUE = {}
    local clock = 1000
    local mainwaits = 0
    local resumes = 0
    local RESMAX = 200000
    local TRES = {}
    local TCAP = 300
    local SEQ = 0
    local function push(co, args, dt)
        SEQ += 1
        QUEUE[#QUEUE + 1] = {co, args, clock + (dt or 0), SEQ}
    end
    local function resume(co, ...)
        resumes += 1
        if resumes > RESMAX then
            STOP = true
            return
        end
        local n = (TRES[co] or 0) + 1
        TRES[co] = n
        if n > TCAP then
            return
        end
        local ok, err = _co.resume(co, ...)
        if not ok then
            ev("thread error " .. _tostring(err))
            if __H.lastcov then
                _print("COVDBG thread error at proto " .. _tostring(__H.lastcov()))
            end
        end
    end
    __H.resume = resume
    function __H.newphase()
        resumes = 0
        TRES = {}
    end
    local task = {}
    task.spawn = function(f, ...)
        ev("task.spawn " .. desc(f))
        local co = _type(f) == "thread" and f or _co.create(f)
        resume(co, ...)
        return co
    end
    task.defer = function(f, ...)
        ev("task.defer " .. desc(f))
        local co = _type(f) == "thread" and f or _co.create(f)
        push(co, _table.pack(...), 0)
        return co
    end
    task.delay = function(t, f, ...)
        ev("task.delay " .. desc(t) .. " " .. desc(f))
        local co = _type(f) == "thread" and f or _co.create(f)
        push(co, _table.pack(...), _type(t) == "number" and t or 0)
        return co
    end
    function __H.yield(t)
        local dt = 1 / 60
        if _type(t) == "number" and t > dt then
            dt = t
        end
        local cur = _co.running()
        if cur ~= MAIN and _co.isyieldable() then
            push(cur, _table.pack(dt), dt)
            return _co.yield()
        end
        clock += 1 / 60
        mainwaits += 1
        if mainwaits > 3000 then
            _error("HARNESS_MAIN_WAIT_BUDGET", 0)
        end
        return 1 / 60
    end
    task.wait = function(t)
        ev("task.wait " .. desc(t))
        return __H.yield(t)
    end
    task.cancel = function(co)
        ev("task.cancel")
        for i = #QUEUE, 1, -1 do
            if QUEUE[i][1] == co then
                _table.remove(QUEUE, i)
            end
        end
    end
    task.synchronize = function() end
    task.desynchronize = function() end
    __H.task = task

    -- run queued threads in wake-time order; threads sleeping past the horizon stay queued
    function __H.runqueue(limit)
        local n = 0
        local horizon = clock + 600
        while #QUEUE > 0 and n < limit and not STOP do
            local bi, best = nil, nil
            for i, it in _ipairs(QUEUE) do
                if best == nil or it[3] < best[3] or (it[3] == best[3] and it[4] < best[4]) then
                    bi, best = i, it
                end
            end
            if best[3] > horizon then
                break
            end
            n += 1
            _table.remove(QUEUE, bi)
            if best[3] > clock then
                clock = best[3]
            end
            local co = best[1]
            if _co.status(co) == "suspended" then
                resume(co, _table.unpack(best[2], 1, best[2].n))
            end
        end
    end

    -- frames: only per-frame signals (RenderStepped, Heartbeat, Stepped, render-step bindings)
    function __H.frames(n)
        for r = 1, n do
            local snapshot = {}
            for i, c in _ipairs(CONNS) do
                snapshot[i] = c
            end
            for _, c in _ipairs(snapshot) do
                if STOP then
                    return
                end
                if c.alive and _type(c.fn) == "function" and (c.sig:find("Heartbeat") or c.sig:find("RenderStep")
                    or c.sig:find("Stepped") or c.sig:find("PreRender") or c.sig:find("PostSimulation")
                    or c.sig:find("PreSimulation")) then
                    ev("frame " .. c.sig .. " " .. _tostring(c.id))
                    resume(_co.create(c.fn), 1 / 60, 1 / 60)
                end
            end
            __H.runqueue(200)
        end
    end
    function __H.fire(rounds)
        for r = 1, rounds do
            local snapshot = {}
            for i, c in _ipairs(CONNS) do
                snapshot[i] = c
            end
            for _, c in _ipairs(snapshot) do
                if STOP then
                    return
                end
                if c.alive and _type(c.fn) == "function" then
                    ev("fire " .. c.sig)
                    if c.once then
                        c.alive = false
                    end
                    local co = _co.create(c.fn)
                    if c.sig:find("Heartbeat") or c.sig:find("RenderStep") or c.sig:find("Stepped") or c.sig:find("PreRender") or c.sig:find("PostSimulation") or c.sig:find("PreSimulation") then
                        resume(co, 1 / 60, 1 / 60)
                    else
                        resume(co, P(fresh("arg:" .. c.sig)), P(fresh("arg2:" .. c.sig)))
                    end
                end
                __H.runqueue(50)
            end
        end
    end

    -- globals -------------------------------------------------------------------------------------
    local genv = {}
    __H.genv = genv
    local G = {}
    G.getgenv = function()
        return genv
    end
    G.task = task
    G.wait = task.wait
    G.spawn = function(f)
        return task.spawn(f)
    end
    G.delay = function(t, f)
        return task.delay(t, f)
    end
    G.tick = function()
        clock += 0.001
        return clock
    end
    G.time = G.tick
    G.elapsedTime = G.tick
    local osmock = {}
    for k, v in _pairs(_os) do
        osmock[k] = v
    end
    osmock.clock = function()
        clock += 0.001
        return clock
    end
    osmock.time = function()
        return 1700000000
    end
    G.os = osmock
    local mathmock = {}
    for k, v in _pairs(_math) do
        mathmock[k] = v
    end
    local seed = 12345
    mathmock.random = function(a, b)
        seed = (seed * 1103515245 + 12345) % 2147483648
        local f = seed / 2147483648
        if a == nil then
            return f
        end
        if b == nil then
            a, b = 1, a
        end
        return a + _math.floor(f * (b - a + 1))
    end
    mathmock.randomseed = function() end
    G.math = mathmock
    local function num(v)
        if pinfo[v] then
            return 0
        end
        return v
    end
    for k, f in _pairs(_math) do
        if _type(f) == "function" and k ~= "random" and k ~= "randomseed" then
            mathmock[k] = function(...)
                local n = _select("#", ...)
                local args = {...}
                for i = 1, n do
                    args[i] = num(args[i])
                end
                return f(_table.unpack(args, 1, n))
            end
        end
    end
    local stringmock = {}
    for k, v in _pairs(_string) do
        stringmock[k] = v
    end
    stringmock.format = function(fmt, ...)
        local n = _select("#", ...)
        local args = {...}
        local i = 0
        for spec in _string.gmatch(fmt, "%%[-+ #0]*%d*%.?%d*([%a%%])") do
            if spec ~= "%" then
                i += 1
                local v = args[i]
                if pinfo[v] then
                    if spec == "s" or spec == "q" then
                        args[i] = pinfo[v].path
                    else
                        args[i] = 0
                    end
                end
            end
        end
        return _string.format(fmt, _table.unpack(args, 1, n))
    end
    G.string = stringmock
    G.typeof = function(v)
        if pinfo[v] then
            return pinfo[v].class and "Instance" or "Instance"
        end
        return _typeof(v)
    end
    G.game = P("game", "DataModel")
    pinfo[G.game].props.PlaceId = 1
    pinfo[G.game].props.GameId = 1
    pinfo[G.game].props.JobId = "job"
    G.workspace = METHODS.GetService(G.game, "Workspace")
    G.Workspace = G.workspace
    G.script = P("script")
    for _, n in _ipairs({"Instance", "Vector3", "Vector2", "CFrame", "Color3", "UDim2", "UDim", "Enum", "Ray",
        "RaycastParams", "TweenInfo", "NumberSequence", "NumberSequenceKeypoint", "ColorSequence",
        "ColorSequenceKeypoint", "NumberRange", "Rect", "Region3", "PhysicalProperties", "BrickColor",
        "Drawing", "OverlapParams", "Axes", "Faces", "Font", "Content", "DockWidgetPluginGuiInfo", "PathWaypoint",
        "Random", "DateTime", "SharedTable", "Vector3int16", "Vector2int16", "CatalogSearchParams", "Path2DControlPoint",
        "WebSocket", "Drawing", "syn", "fluxus", "cache", "debug_lib"}) do
        G[n] = P(n)
    end
    local inst_new = function(self_or_cls, cls, parent)
        local class = cls
        if _type(self_or_cls) == "string" then
            class, parent = self_or_cls, cls
        end
        ev("Instance.new " .. desc(class))
        local o = P(fresh("new:" .. keyname(class)), class)
        pinfo[o].created = true
        pinfo[o].props.ClassName = class
        pinfo[o].props.Name = class
        if parent ~= nil then
            o.Parent = parent
        end
        return o
    end
    pinfo[G.Instance].props.new = function(cls, parent)
        return inst_new(cls, parent)
    end
    -- executor API
    local function noop(name, ret)
        G[name] = function(...)
            ev(name .. "(" .. descargs(...) .. ")")
            return ret
        end
    end
    -- functions that must look like C functions to the script's anti-hook checks
    local CFN = _setmetatable({}, {__mode = "k"})
    __H.CFN = CFN
    local _realinfo = _debug.info
    local _realsetfenv = realget("setfenv")
    local debugmock = {}
    for k, v in _pairs(_debug) do
        debugmock[k] = v
    end
    debugmock.info = function(a, b, ...)
        if _type(a) == "function" and CFN[a] then
            if b == "s" then
                return "[C]"
            elseif b == "l" then
                return -1
            elseif b == "a" then
                return 0, true
            elseif b == "n" then
                return ""
            elseif b == "f" then
                return a
            end
            local out = {}
            for i = 1, #b do
                local c = _string.sub(b, i, i)
                if c == "s" then
                    out[#out + 1] = "[C]"
                elseif c == "l" then
                    out[#out + 1] = -1
                elseif c == "n" then
                    out[#out + 1] = ""
                elseif c == "a" then
                    out[#out + 1] = 0
                    out[#out + 1] = true
                elseif c == "f" then
                    out[#out + 1] = a
                end
            end
            return _table.unpack(out)
        end
        if _type(a) == "number" then
            a += 1
        end
        return _realinfo(a, b, ...)
    end
    G.debug = debugmock
    local comock = {}
    for k, v in _pairs(_co) do
        comock[k] = v
    end
    local function callable(f)
        if pinfo[f] then
            return function(...)
                return f(...)
            end
        end
        return f
    end
    comock.wrap = function(f)
        return _co.wrap(callable(f))
    end
    comock.create = function(f)
        return _co.create(callable(f))
    end
    G.coroutine = comock
    local _realgetfenv = realget("getfenv")
    local fenvs = _setmetatable({}, {__mode = "k"})
    G.getfenv = function(f)
        if pinfo[f] or (_type(f) == "function" and CFN[f]) then
            local e = fenvs[f]
            if e == nil then
                e = {script = G.script}
                fenvs[f] = e
            end
            ev("getfenv " .. desc(f))
            return e
        end
        if f == nil then
            return _realgetfenv(2)
        end
        if _type(f) == "number" then
            return _realgetfenv(f + 1)
        end
        return _realgetfenv(f)
    end
    local B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    local function b64arg(data)
        local t = _type(data)
        if t == "number" then
            return _tostring(data)
        end
        if t ~= "string" then
            _error("invalid argument #1 (string expected, got " .. t .. ")", 2)
        end
        return data
    end
    local function b64enc(data)
        data = b64arg(data)
        local out = {}
        for i = 1, #data, 3 do
            local a, b, c = _string.byte(data, i, i + 2)
            local n = a * 65536 + (b or 0) * 256 + (c or 0)
            local c1 = _math.floor(n / 262144) % 64
            local c2 = _math.floor(n / 4096) % 64
            local c3 = _math.floor(n / 64) % 64
            local c4 = n % 64
            out[#out + 1] = _string.sub(B64, c1 + 1, c1 + 1) .. _string.sub(B64, c2 + 1, c2 + 1)
                .. (b and _string.sub(B64, c3 + 1, c3 + 1) or "=") .. (c and _string.sub(B64, c4 + 1, c4 + 1) or "=")
        end
        return _table.concat(out)
    end
    local function b64dec(data)
        data = _string.gsub(b64arg(data), "[^%w%+/=]", "")
        local out = {}
        for i = 1, #data, 4 do
            local n, pad = 0, 0
            for j = 0, 3 do
                local ch = _string.sub(data, i + j, i + j)
                local v = _string.find(B64, ch, 1, true)
                if ch == "=" or ch == "" or v == nil then
                    pad += 1
                    v = 1
                end
                n = n * 64 + (v - 1)
            end
            local b1 = _math.floor(n / 65536) % 256
            local b2 = _math.floor(n / 256) % 256
            local b3 = n % 256
            out[#out + 1] = _string.char(b1) .. (pad < 2 and _string.char(b2) or "") .. (pad < 1 and _string.char(b3) or "")
        end
        return _table.concat(out)
    end
    G.base64 = {encode = b64enc, decode = b64dec}
    G.base64_encode = b64enc
    G.base64_decode = b64dec
    G.crypt = {base64encode = b64enc, base64decode = b64dec, base64 = G.base64,
        generatebytes = function(n) return _string.rep("A", n or 16) end,
        generatekey = function() return _string.rep("A", 32) end,
        hash = function(s) return "0000" end}
    G.rawget = function(t, k)
        local info = pinfo[t]
        if info then
            local v = info.props[k]
            if v == nil then
                v = info.children[k]
                if v == nil then
                    v = P(info.path .. "." .. keyname(k))
                    info.children[k] = v
                end
            end
            ev("rawget " .. info.path .. "." .. keyname(k))
            return v
        end
        return _rawget(t, k)
    end
    G.rawset = function(t, k, v)
        local info = pinfo[t]
        if info then
            info.props[k] = v
            ev("rawset " .. info.path .. "." .. keyname(k) .. " = " .. desc(v))
            return t
        end
        return _rawset(t, k, v)
    end
    G.getmetatable = function(t)
        local info = pinfo[t]
        if info then
            if _string.sub(info.path, 1, 7) == "module:" then
                -- objects from game modules are plain Lua tables with a class metatable
                ev("getmetatable " .. desc(t))
                if info.rawmt == nil then
                    info.rawmt = {__index = P(info.path .. "<mt>.__index")}
                end
                return info.rawmt
            end
            return "The metatable is locked"
        end
        return _getmetatable(t)
    end
    local MODULE_DATA
    local modules = {}
    local WEAPONS = {"Assault Rifle", "Handgun", "Knife", "Grenade"}
    local CLASSES = {["Assault Rifle"] = "Primary", Handgun = "Secondary", Knife = "Melee", Grenade = "Utility"}
    MODULE_DATA = {
        ItemLibrary = function()
            local items = {}
            for _, w in _ipairs(WEAPONS) do
                items[w] = {Class = CLASSES[w], Name = w}
            end
            return {Items = items}
        end,
        ShopLibrary = function()
            return {GetReleasedOwnableWeapons = function()
                ev("ShopLibrary.GetReleasedOwnableWeapons")
                return {_table.unpack(WEAPONS)}
            end}
        end,
        CONSTANTS = function()
            return {DEFAULT_WEAPONS = {_table.unpack(WEAPONS)}, WEAPON_EARLY_ACCESS_TIME_OFFSET = 0}
        end,
    }
    G.require = function(m)
        local key = pinfo[m] and pinfo[m].path or _tostring(m)
        ev("require " .. key)
        local r = modules[key]
        if r == nil then
            r = P("module:" .. key)
            modules[key] = r
            local name = _string.match(key, "([%w_]+)$") or ""
            local seed = MODULE_DATA[name]
            if seed then
                for k, v in _pairs(seed()) do
                    pinfo[r].props[k] = v
                end
            end
        end
        return r
    end
    G.setfenv = function(f, e)
        if pinfo[f] then
            -- a game function (mocked as an object): remember the environment
            ev("setfenv " .. desc(f))
            pinfo[f].fenv = e
            return f
        end
        if _type(f) == "function" and CFN[f] then
            _error("'setfenv' cannot change environment of given object", 2)
        end
        if _type(f) == "number" then
            return _realsetfenv(f + 1, e)
        end
        return _realsetfenv(f, e)
    end
    G.clonefunction = function(f)
        ev("clonefunction")
        local c = function(...)
            return f(...)
        end
        CFN[c] = true
        return c
    end
    G.islclosure = function(f)
        return not (CFN[f] or _realinfo(f, "s") == "[C]")
    end
    G.iscclosure = function(f)
        return not G.islclosure(f)
    end
    local renv = _setmetatable({shared = {r = P("shared.r")}}, {
        __index = function(t, k)
            local v = G[k]
            if v ~= nil then
                return v
            end
            return realget(k)
        end,
    })
    G.getrenv = function()
        return renv
    end
    G.hookfunction = function(f, g)
        ev("hookfunction(" .. descargs(f, g) .. ")")
        return f
    end
    G.replaceclosure = G.hookfunction
    G.hookmetamethod = function(o, n, g)
        ev("hookmetamethod(" .. descargs(o, n) .. ")")
        return function(...)
            return nil
        end
    end
    G.newcclosure = function(f)
        return f
    end
    G.cloneref = function(x)
        return x
    end
    G.compareinstances = function(a, b)
        return a == b
    end
    G.isfunctionhooked = function(f)
        ev("isfunctionhooked")
        return false
    end
    G.restorefunction = function(f)
        ev("restorefunction")
    end
    G.isourclosure = function(f)
        return false
    end
    G.checkcaller = function()
        return false
    end
    G.isexecutorclosure = G.isourclosure
    noop("queue_on_teleport")
    noop("setclipboard")
    noop("writefile")
    noop("appendfile")
    noop("makefolder")
    noop("delfile")
    noop("delfolder")
    noop("rconsoleprint")
    noop("setfpscap")
    noop("firesignal")
    noop("fireclickdetector")
    noop("fireproximityprompt")
    noop("firetouchinterest")
    noop("setscriptable")
    noop("sethiddenproperty")
    noop("setsimulationradius")
    noop("mouse1click")
    noop("mouse1press")
    noop("mouse1release")
    noop("keypress")
    noop("keyrelease")
    G.isfile = function(p)
        ev("isfile " .. desc(p))
        return false
    end
    G.isfolder = function(p)
        ev("isfolder " .. desc(p))
        return false
    end
    G.readfile = function(p)
        ev("readfile " .. desc(p))
        return ""
    end
    G.listfiles = function(p)
        ev("listfiles " .. desc(p))
        return {}
    end
    G.getconnections = function(s)
        ev("getconnections")
        return {}
    end
    G.gethui = function()
        return METHODS.GetService(G.game, "CoreGui")
    end
    G.getrawmetatable = function(o)
        ev("getrawmetatable " .. desc(o))
        local info = pinfo[o]
        if info and _string.sub(info.path, 1, 7) == "module:" then
            -- objects from game modules are Lua tables whose metatable __index is their class table
            if info.rawmt == nil then
                info.rawmt = {__index = P(info.path .. "<mt>.__index")}
            end
            return info.rawmt
        end
        return {__index = function() end, __namecall = function() end, __newindex = function() end}
    end
    G.setrawmetatable = function(t, mt)
        ev("setrawmetatable " .. desc(t))
        if pinfo[t] then
            pinfo[t].rawmt = mt
            return t
        end
        if _type(t) ~= "table" then
            -- executors set the shared metatable of a primitive type here; plain Luau cannot
            return t
        end
        return _setmetatable(t, mt)
    end
    G.gethiddenproperty = function(o, n)
        ev("gethiddenproperty " .. desc(o) .. " " .. desc(n))
        return nil, true
    end
    G.isrbxactive = function()
        return true
    end
    G.isnetworkowner = function()
        return true
    end
    G.protectinstance = function(i)
        ev("protectinstance")
    end
    G.replicatesignal = function(...)
        ev("replicatesignal")
    end
    G.getcallbackvalue = function()
        return nil
    end
    for _, n in _ipairs({"setupvalue", "setfflag", "setconstant", "setstack", "setproto"}) do
        G[n] = function(...)
            ev(n .. "(" .. descargs(...) .. ")")
        end
    end
    for _, n in _ipairs({"getupvalues", "getconstants", "getprotos", "getstack", "getinfo"}) do
        G[n] = function(...)
            ev(n .. "()")
            return {}
        end
    end
    G.getupvalue = function(...)
        ev("getupvalue")
        return nil
    end
    G.getconstant = function(...)
        ev("getconstant")
        return nil
    end
    G.getproto = function(f, i, active)
        ev("getproto")
        if active then
            return {}
        end
        return function() end
    end
    G.getfflag = function(n)
        ev("getfflag " .. desc(n))
        return "false"
    end
    G.setreadonly = function() end
    G.make_writeable = function() end
    G.make_readonly = function() end
    G.isreadonly = function()
        return false
    end
    G.getnamecallmethod = function()
        return "x"
    end
    G.identifyexecutor = function()
        return "Mock", "1.0"
    end
    G.getexecutorname = G.identifyexecutor
    G.request = function(t)
        ev("request")
        return {StatusCode = 200, Body = "", Success = true, Headers = {}}
    end
    G.http_request = G.request
    G.getcustomasset = function(p)
        return "rbxasset://" .. keyname(p)
    end
    G.getscriptclosure = function()
        return function() end
    end
    G.getgc = function()
        return {}
    end
    G.getreg = function()
        return {}
    end
    G.getloadedmodules = function()
        return {}
    end
    G.getnilinstances = function()
        return {}
    end
    G.getinstances = function()
        return {}
    end
    G.getsenv = function()
        return {}
    end
    G.getcallingscript = function()
        return nil
    end
    G.setthreadidentity = function() end
    G.getthreadidentity = function()
        return 8
    end
    G.setidentity = G.setthreadidentity
    G.getidentity = G.getthreadidentity
    -- tostring of plain tables/functions/threads embeds an address, which differs between runs
    local ADDR = _setmetatable({}, {__mode = "k"})
    local naddr = 0
    G.tostring = function(v)
        local t = _type(v)
        if (t == "table" or t == "function" or t == "thread" or t == "userdata") and not pinfo[v] then
            local mt = t == "table" and _getmetatable(v) or nil
            if not (_type(mt) == "table" and _rawget(mt, "__tostring") ~= nil) and mt == nil then
                local a = ADDR[v]
                if a == nil then
                    naddr += 1
                    a = _string.format("0x%016x", 0x10000 + naddr * 16)
                    ADDR[v] = a
                end
                return t .. ": " .. a
            end
        end
        return _tostring(v)
    end
    G.print = function(...)
        ev("print(" .. descargs(...) .. ")")
    end
    G.warn = function(...)
        ev("warn(" .. descargs(...) .. ")")
    end
    G.printidentity = function() end
    G.UserSettings = function()
        ev("UserSettings()")
        return P("UserSettings")
    end
    G.settings = function()
        return P("settings")
    end
    G.version = function()
        return "0.0"
    end
    G.DebuggerManager = function()
        return P("DebuggerManager")
    end
    G.shared = {}
    G._G = G
    for k, v in _pairs(G) do
        if _type(v) == "function" then
            CFN[v] = true
        end
    end
    __H.G = G

    _setmetatable(env, {
        __index = function(t, k)
            local v = G[k]
            if v ~= nil then
                return v
            end
            v = genv[k]
            if v ~= nil then
                return v
            end
            return realget(k)
        end,
    })

    -- coverage of VM instructions (original only)
    local COV = {}
    __H.cov = COV
    local covon = false
    local COVE = {}
    local LASTPC = {}
    __H.cove = COVE
    local LASTOPS, LASTCOVPC = nil, nil
    function __H.lastcov()
        return (__H.protos and __H.protos[LASTOPS] or "?") .. " pc " .. _tostring(LASTCOVPC)
    end
    function __COV(ops, pc, act)
        LASTOPS, LASTCOVPC = ops, pc
        if covon then
            local t = COV[ops]
            if t == nil then
                t = {}
                COV[ops] = t
                COVE[ops] = {}
            end
            t[pc] = true
            local lp = act and act.last
            if lp ~= nil then
                COVE[ops][lp * 1000000 + pc] = true
            end
            if act then
                act.last = pc
            end
        end
    end
    function __H.covreset(ops)
        LASTPC[ops] = nil
    end
    _rawset(env, "__COV", __COV)
    _rawset(env, "__STEP", __STEP)
    -- canonical table iteration: Lua leaves the order of next/pairs unspecified, and it depends on how
    -- a table was filled (key by key in the VM, preallocated by a constructor in the decompiled code).
    -- Both runs route their generic for loops through __ITER, which visits number/string/boolean keys
    -- in sorted order (other keys follow in native order).
    local _next = realget("next")
    local RANK = {boolean = 1, number = 2, string = 3, table = 4}
    local function keyless(a, b)
        local ta, tb = _type(a), _type(b)
        if ta ~= tb then
            return RANK[ta] < RANK[tb]
        end
        if ta == "boolean" then
            return (not a) and b
        end
        if ta == "table" then
            return pinfo[a].path < pinfo[b].path
        end
        return a < b
    end
    local function canon(t)
        local keys, others = {}, {}
        local k = _next(t)
        while k ~= nil do
            local tk = _type(k)
            if (RANK[tk] and k == k) or (tk == "table" and pinfo[k] ~= nil) then
                keys[#keys + 1] = k
            else
                others[#others + 1] = k
            end
            k = _next(t, k)
        end
        _table.sort(keys, keyless)
        for _, o in _ipairs(others) do
            keys[#keys + 1] = o
        end
        local i = 0
        return function()
            while true do
                i += 1
                local kk = keys[i]
                if kk == nil then
                    return nil
                end
                local v = _rawget(t, kk)
                if v ~= nil then
                    return kk, v
                end
            end
        end
    end
    function __ITER(f, s, c, ...)
        if _type(f) == "table" and _select("#", ...) == 0 and s == nil and c == nil then
            local ok, mt = _pcall(_getmetatable, f)
            if ok and mt == nil then
                return canon(f)
            end
            return f, s, c
        end
        if f == _next and _type(s) == "table" and c == nil then
            return canon(s)
        end
        return f, s, c, ...
    end
    _rawset(env, "__ITER", __ITER)
    -- closures by proto id, in creation order (original: VM closure creation; decompiled: __REG wrappers)
    local REGS = {}
    function __REG(f, pid)
        if ON then
            local list = REGS[pid]
            if list == nil then
                list = {}
                REGS[pid] = list
            end
            if #list < 4 then
                list[#list + 1] = f
            end
        end
        return f
    end
    __H.COV, __H.STEP, __H.ITER, __H.REG = __COV, __STEP, __ITER, __REG
    -- fuzz phase: call registered closures of functions the normal run never executed
    -- broad round: every registered function (first instance) with several argument profiles
    function __H.fuzzall(pids)
        local profiles = {
            function(b) return P(b .. ":a1"), P(b .. ":a2"), P(b .. ":a3") end,
            function(b) return 1, 2, 3 end,
            function(b) return "a", "b", "c" end,
            function(b) return true, false, true end,
            function(b) return nil, nil, nil end,
            function(b) return {}, {}, {} end,
        }
        for _, pid in _ipairs(pids) do
            local list = REGS[pid]
            local f = list and list[1]
            if f then
                for pi, prof in _ipairs(profiles) do
                    if STOP then
                        return
                    end
                    __H.newphase()
                    ev("fuzzall " .. _tostring(pid) .. "/" .. pi)
                    FUZZSTEPS = 0
                    FUZZEV = 0
                    resume(_co.create(f), prof("fa:" .. _tostring(pid) .. ":" .. pi))
                    __H.runqueue(10)
                    FUZZSTEPS = nil
                    FUZZEV = nil
                end
            end
        end
    end
    -- unit round: functions instantiated on their own, outside their parents (unitgen.py); the
    -- original run builds each with the VM's closure constructor, the decompiled run with its
    -- standalone text, both from the same upvalue values (mock objects, or the number an integrity
    -- check in the function expects)
    function __H.units(pids)
        local profiles = {
            function(b) return P(b .. ":a1"), P(b .. ":a2"), P(b .. ":a3") end,
            function(b) return 1, 2, 3 end,
            function(b) return "a", "b", "c" end,
            function(b) return true, false, true end,
            function(b) return nil, nil, nil end,
            function(b) return {}, {}, {} end,
        }
        for _, pid in _ipairs(pids) do
            local sp = UNIT_SPEC[pid]
            if sp then
                local n, fixed = sp[1], sp[2]
                for pi, prof in _ipairs(profiles) do
                    if STOP then
                        return
                    end
                    __H.newphase()
                    ev("unit " .. _tostring(pid) .. "/" .. pi)
                    FUZZSTEPS = 0
                    FUZZEV = 0
                    local vals = {}
                    for i = 1, n do
                        local v = fixed[i]
                        if v == nil then
                            v = P("up:" .. _tostring(pid) .. ":" .. pi .. ":" .. i)
                        end
                        vals[i] = v
                    end
                    local f = __H.mkunit(pid, vals, n, sp[3] or {})
                    resume(_co.create(f), prof("un:" .. _tostring(pid) .. ":" .. pi))
                    __H.runqueue(10)
                    FUZZSTEPS = nil
                    FUZZEV = nil
                end
            end
        end
    end
    function __H.fuzz(pids)
        for _, pid in _ipairs(pids) do
            local list = REGS[pid]
            if list then
                for i, f in _ipairs(list) do
                    if i > 2 or STOP then
                        break
                    end
                    __H.newphase()
                    ev("fuzz " .. _tostring(pid) .. "#" .. i)
                    FUZZSTEPS = 0
                    FUZZEV = 0
                    local base = "fz:" .. _tostring(pid) .. ":" .. i
                    resume(_co.create(f), P(base .. ":a1"), P(base .. ":a2"), P(base .. ":a3"))
                    __H.runqueue(30)
                    FUZZSTEPS = nil
                    FUZZEV = nil
                end
            end
        end
    end

    function __H.run(fn, protos)
        __H.protos = protos
        covon = true
        ON = true
        local ok, err = _pcall(fn)
        ev("main done " .. _tostring(ok))
        if not ok then
            _print("MAIN ERROR: " .. _tostring(err))
        end
        __H.runqueue(2000)
        __H.fire(2)
        __H.newphase()
        -- exercise the UI: flip every toggle and option the script registered
        local Toggles, Options = genv.Toggles or _rawget(env, "Toggles"), genv.Options or _rawget(env, "Options")
        do
            local nt, no = 0, 0
            if _type(Toggles) == "table" then for _ in _pairs(Toggles) do nt += 1 end end
            if _type(Options) == "table" then for _ in _pairs(Options) do no += 1 end end
            _print("HARNESS ui tables: toggles=" .. _tostring(nt) .. " options=" .. _tostring(no) .. " STOP=" .. _tostring(STOP))
        end
        for _, pair in _ipairs({{"Toggles", Toggles}, {"Options", Options}}) do
            local tbl = pair[2]
            if _type(tbl) == "table" then
                local keys = {}
                for k in _pairs(tbl) do
                    if _type(k) == "string" then
                        keys[#keys + 1] = k
                    end
                end
                _table.sort(keys)
                for _, k in _ipairs(keys) do
                    if STOP then
                        break
                    end
                    local o = tbl[k]
                    if _type(o) == "table" and _type(_rawget(o, "SetValue")) == "function" then
                        ev("ui " .. pair[1] .. "." .. k)
                        local typ = _rawget(o, "Type")
                        local vals
                        if pair[1] == "Toggles" then
                            vals = {true, false}
                        elseif typ == "Slider" then
                            vals = {_rawget(o, "Max"), _rawget(o, "Min")}
                        elseif typ == "Dropdown" then
                            local values = _rawget(o, "Values")
                            local first = _type(values) == "table" and values[1] or nil
                            if _rawget(o, "Multi") then
                                vals = {first ~= nil and {[first] = true} or {}}
                            else
                                vals = {first}
                            end
                        elseif typ == "Input" then
                            vals = {"test"}
                        else
                            vals = {}
                        end
                        for _, v in _ipairs(vals) do
                            local co = _co.create(function()
                                o:SetValue(v)
                            end)
                            resume(co)
                            __H.runqueue(50)
                        end
                    end
                end
            end
        end
        __H.newphase()
        __H.fire(2)
        __H.runqueue(2000)
        -- everything on: enable every toggle, then run frames
        __H.newphase()
        if _type(Toggles) == "table" then
            local keys = {}
            for k in _pairs(Toggles) do
                if _type(k) == "string" then
                    keys[#keys + 1] = k
                end
            end
            _table.sort(keys)
            for _, k in _ipairs(keys) do
                local o = Toggles[k]
                if STOP then
                    break
                end
                if _type(o) == "table" and _type(_rawget(o, "SetValue")) == "function" then
                    ev("ui on Toggles." .. k)
                    resume(_co.create(function()
                        o:SetValue(true)
                    end))
                    __H.runqueue(20)
                end
            end
        end
        __H.newphase()
        __H.frames(5)
        __H.runqueue(2000)
        -- closure registry sizes after each fuzz round (filtered from the trace by the ^COV prefix)
        local function dumpreg(r)
            local ids = {}
            for pid, list in _pairs(REGS) do
                ids[#ids + 1] = pid
            end
            _table.sort(ids)
            for _, pid in _ipairs(ids) do
                _print("COVREG " .. r .. " " .. _tostring(pid) .. " " .. _tostring(#REGS[pid]))
            end
        end
        dumpreg(0)
        for r, pids in _ipairs(FUZZ_ROUNDS or {}) do
            if pids[1] == -1 then
                -- marker round: every function both runs registered equally often
                local all = {}
                for i = 2, #pids do
                    all[#all + 1] = pids[i]
                end
                __H.fuzzall(all)
            else
                __H.fuzz(pids)
            end
            dumpreg(r)
        end
        if UNIT_PIDS and __H.mkunit then
            __H.units(UNIT_PIDS)
            dumpreg(#(FUZZ_ROUNDS or {}) + 1)
        end
        ON = false
        covon = false
        if not STREAM then
            for i = 1, NT do
                _print(TRACE[i])
            end
        end
        _print("TRACE_END " .. NT .. (STOP and " (budget)" or ""))
        if protos then
            for ops, t in _pairs(COV) do
                local id = protos[ops]
                if id then
                    local pcs = {}
                    for pc in _pairs(t) do
                        pcs[#pcs + 1] = pc
                    end
                    _table.sort(pcs)
                    _print("COV " .. id .. " " .. _table.concat(pcs, ","))
                    local es = {}
                    for e in _pairs(COVE[ops]) do
                        es[#es + 1] = _string.format("%d>%d", e // 1000000, e % 1000000)
                    end
                    _table.sort(es)
                    _print("COVE " .. id .. " " .. _table.concat(es, ","))
                end
            end
        end
    end
end
