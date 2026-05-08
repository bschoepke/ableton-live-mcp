autowatch = 1;
inlets = 1;
outlets = 3;

var role = jsarguments.length > 1 ? String(jsarguments[1]) : "audio_effect";
var instanceId = jsarguments.length > 2 ? String(jsarguments[2]) : "default";
var commandFile = jsarguments.length > 3 ? String(jsarguments[3]) : "agent_m4l_default.json";
var statusFile = jsarguments.length > 4 ? String(jsarguments[4]) : "";
var lastCommandId = "";
var currentCommandId = "";
var pollTask = null;
var dynamicObjects = [];
var objectById = {};
var objectSpecById = {};
var webObjects = [];
var state = {};
var uiBindings = {};
var uiBindingUpdating = false;
var statusPadSize = 65536;
var lastConnectionErrors = [];

function loadbang() {
    start_polling();
    report("ready", { role: role, instance_id: instanceId, command_file: commandFile });
}

function start_polling() {
    if (!pollTask) {
        pollTask = new Task(pollCommandFile, this);
        pollTask.interval = 50;
        pollTask.repeat();
    }
}

function pollCommandFile() {
    var file = new File(commandFile, "read");
    if (!file.isopen) {
        return;
    }
    var raw = file.readstring(1048576);
    file.close();
    if (raw) {
        applyRaw(raw);
    }
}

function anything() {
    var atoms = arrayfromargs(arguments);
    if (messagename === "/agent_m4l") {
        handleOsc(atoms);
    } else if (uiBindings[messagename]) {
        if (!uiBindingUpdating) {
            applyUiBinding(messagename, atoms);
        }
    } else if (messagename === "set" || messagename === "param") {
        applyValues([{ id: String(atoms[0]), value: atoms[1] }]);
    } else {
        applyRaw([messagename].concat(atoms).join(" "));
    }
}

function handleOsc(args) {
    if (args.length < 2) {
        return;
    }
    var target = String(args[0]);
    if (target !== instanceId) {
        return;
    }
    applyRaw(String(args[1]));
}

function msg_string(value) {
    applyRaw(value);
}

function applyRaw(raw) {
    var command;
    raw = String(raw || "");
    if (raw.charAt(0) !== "{" && raw.charAt(0) !== "[") {
        return;
    }
    try {
        command = JSON.parse(raw);
    } catch (err) {
        report("error", { reason: "invalid_json", detail: String(err) });
        return;
    }
    if (command.instance_id && String(command.instance_id) !== instanceId) {
        return;
    }
    var id = command.id || raw;
    if (id === lastCommandId) {
        return;
    }
    lastCommandId = id;
    currentCommandId = id;
    ensureRecovered(command);
    if (command.command === "clear") {
        clearDynamic();
        report("clear", {});
    } else if (command.command === "set") {
        applyValues(command.values || command.parameters || []);
    } else if (command.command === "status") {
        report("status", { objects: dynamicObjects.length });
    } else {
        applySpec(command.patch || command.spec || command);
    }
}

function ensureRecovered(command) {
    if (dynamicObjects.length > 0) {
        return;
    }
    if (command.command !== "set" && command.command !== "status") {
        return;
    }
    var recovery = readCommandFileJson();
    if (!recovery) {
        return;
    }
    if (recovery.instance_id && String(recovery.instance_id) !== instanceId) {
        return;
    }
    var spec = recovery.patch || recovery.spec;
    if (!spec && (recovery.objects || recovery.webui || recovery.webuis)) {
        spec = recovery;
    }
    if (!spec) {
        return;
    }
    var pendingId = currentCommandId;
    currentCommandId = recovery.id || pendingId;
    applySpec(spec);
    currentCommandId = pendingId;
}

function readCommandFileJson() {
    var file = new File(commandFile, "read");
    if (!file.isopen) {
        return null;
    }
    var raw = file.readstring(1048576);
    file.close();
    if (!raw) {
        return null;
    }
    try {
        return JSON.parse(String(raw));
    } catch (err) {
        report("error", { reason: "invalid_recovery_json", detail: String(err) });
        return null;
    }
}

function applySpec(spec) {
    if (!spec || (!spec.objects && !spec.webui && !spec.webuis)) {
        report("error", { reason: "missing_objects" });
        return;
    }
    clearDynamic();
    var byId = seedStaticObjects();
    createWebUis(spec.webuis || spec.webui, byId);
    var objects = spec.objects || [];
    for (var i = 0; i < objects.length; i++) {
        var item = objects[i];
        var obj = createObject(item, i);
        if (!obj) {
            continue;
        }
        configureObject(obj, item);
        dynamicObjects.push(obj);
        if (item.id) {
            objectById[String(item.id)] = obj;
            objectSpecById[String(item.id)] = item;
            byId[String(item.id)] = obj;
        }
    }
    configureUiBindings(spec, objects, byId);
    var connections = connectPatchlines(spec.connections || [], byId);
    report("reload", { objects: dynamicObjects.length, connections: connections.connected, connection_errors: connections.errors });
    pushWebState();
}

function connectPatchlines(connections, byId) {
    var connected = 0;
    var errors = [];
    for (var j = 0; j < connections.length; j++) {
        var c = connections[j];
        var srcId = String(c.from);
        var dstId = String(c.to);
        var src = byId[srcId];
        var dst = byId[dstId];
        if (!src || !dst) {
            errors.push({ from: srcId, to: dstId, reason: src ? "destination_missing" : "source_missing" });
            continue;
        }
        try {
            this.patcher.connect(src, Number(c.outlet || 0), dst, Number(c.inlet || 0));
            connected += 1;
        } catch (err) {
            errors.push({ from: srcId, to: dstId, outlet: Number(c.outlet || 0), inlet: Number(c.inlet || 0), reason: String(err) });
        }
    }
    lastConnectionErrors = errors;
    return { connected: connected, errors: errors };
}

function createWebUis(webuiSpec, byId) {
    if (!webuiSpec) {
        return;
    }
    var list = webuiSpec instanceof Array ? webuiSpec : [webuiSpec];
    for (var i = 0; i < list.length; i++) {
        createWebUi(list[i], i, byId);
    }
}

function createWebUi(webui, index, byId) {
    var rect = webui.presentation_rect || [0, 0, 320, 160];
    var path = webui.html_path || webui.path || webui.url;
    if (!path) {
        return;
    }
    var id = String(webui.id || (index ? "webui_" + index : "webui"));
    var objectName = normalizeWebObject(webui.object || "jweb~");
    var args = webObjectArgs(webui, objectName);
    var patchRect = webui.patching_rect || rect;
    var obj = createNamedDefault(safeScriptName(id), Number(patchRect[0]), Number(patchRect[1]), args);
    if (!obj) {
        return;
    }
    configureObject(obj, {
        id: id,
        presentation: webui.presentation !== false,
        presentation_rect: rect,
        patching_rect: patchRect,
        box_attrs: webui.box_attrs || webui.boxAttrs || {}
    });
    dynamicObjects.push(obj);
    webObjects.push(obj);
    objectById[id] = obj;
    objectSpecById[id] = webui;
    byId[id] = obj;
    try {
        this.patcher.connect(obj, webMessageOutlet(objectName), this.box, 0);
    } catch (err) {
    }
    if (webui.audio_out) {
        var plugout = getNamed("plugout");
        if (plugout) {
            try {
                this.patcher.connect(obj, 0, plugout, 0);
                this.patcher.connect(obj, 1, plugout, 1);
            } catch (errAudio) {
            }
        }
    }
    try {
        if (String(path).indexOf("file:") === 0) {
            obj.message("read", path);
        } else {
            obj.message("readfile", path);
        }
    } catch (err2) {
        report("error", { reason: "webui_load_failed", detail: String(err2) });
    }
}

function webObjectArgs(webui, objectName) {
    if (webui.text) {
        return tokenize(String(webui.text));
    }
    var args = [objectName].concat(asArray(webui.args));
    var attrs = webui.attrs || webui.attributes || {};
    if (webui.rendermode !== undefined && attrs.rendermode === undefined && attrs["@rendermode"] === undefined) {
        attrs = cloneObject(attrs);
        attrs.rendermode = Number(webui.rendermode);
    } else if (objectName.indexOf("jweb") === 0 && attrs.rendermode === undefined && attrs["@rendermode"] === undefined) {
        attrs = cloneObject(attrs);
        attrs.rendermode = 1;
    }
    appendAttrs(args, attrs);
    return args;
}

function configureUiBindings(spec, objects, byId) {
    uiBindings = {};
    var bindings = [];
    var explicit = spec.ui_bindings || spec.bindings || [];
    for (var i = 0; i < explicit.length; i++) {
        bindings.push(normalizeUiBinding(explicit[i], null));
    }
    for (var j = 0; j < objects.length; j++) {
        var item = objects[j];
        var bind = item.ui_bind || item.bind || item.binding || item.param_bind;
        if (bind) {
            bindings.push(normalizeUiBinding(bind, item));
        }
    }
    for (var k = 0; k < bindings.length; k++) {
        installUiBinding(bindings[k], byId);
    }
}

function normalizeUiBinding(raw, item) {
    var binding = raw || {};
    var source = String(binding.source || binding.from || (item && item.id) || "");
    var target = String(binding.target || binding.to || binding.id || binding.param || source);
    return {
        source: source,
        target: target,
        outlet: Number(binding.outlet || 0),
        message: binding.message,
        args: binding.args,
        source_min: binding.source_min,
        source_max: binding.source_max,
        target_min: binding.target_min !== undefined ? binding.target_min : binding.min,
        target_max: binding.target_max !== undefined ? binding.target_max : binding.max,
        scale: !!binding.scale || !!binding.normalized || binding.source_min !== undefined || binding.source_max !== undefined,
        report: binding.report !== false
    };
}

function installUiBinding(binding, byId) {
    if (!binding || !binding.source || !binding.target) {
        return;
    }
    var source = byId[String(binding.source)];
    if (!source) {
        report("error", { reason: "ui_binding_source_missing", source: String(binding.source), target: String(binding.target) });
        return;
    }
    uiBindings[String(binding.source)] = binding;
    var prependId = safeScriptName("__bind_" + binding.source);
    var prepend = createNamedDefault(prependId, 20, 20, ["prepend", String(binding.source)]);
    if (!prepend) {
        return;
    }
    dynamicObjects.push(prepend);
    try {
        this.patcher.connect(source, binding.outlet, prepend, 0);
        this.patcher.connect(prepend, 0, this.box, 0);
    } catch (err) {
        report("error", { reason: "ui_binding_connect_failed", source: String(binding.source), detail: String(err) });
    }
}

function applyUiBinding(source, atoms) {
    var binding = uiBindings[source];
    if (!binding) {
        return;
    }
    var value = valueFromUiBinding(binding, atoms[0]);
    setBoundTarget(binding, value, source);
    if (binding.report) {
        report("set", { changed: 1, source: source, target: binding.target });
    }
    pushWebState();
}

function valueFromUiBinding(binding, rawValue) {
    var numeric = Number(rawValue);
    if (!binding.scale || isNaN(numeric)) {
        return rawValue;
    }
    var sourceMin = numberOr(binding.source_min, 0);
    var sourceMax = numberOr(binding.source_max, 1);
    var targetMin = numberOr(binding.target_min, 0);
    var targetMax = numberOr(binding.target_max, 1);
    if (sourceMax === sourceMin) {
        return targetMin;
    }
    var normalized = clamp((numeric - sourceMin) / (sourceMax - sourceMin), 0, 1);
    return targetMin + normalized * (targetMax - targetMin);
}

function sourceValueFromUiBinding(binding, targetValue) {
    var numeric = Number(targetValue);
    if (!binding.scale || isNaN(numeric)) {
        return targetValue;
    }
    var sourceMin = numberOr(binding.source_min, 0);
    var sourceMax = numberOr(binding.source_max, 1);
    var targetMin = numberOr(binding.target_min, 0);
    var targetMax = numberOr(binding.target_max, 1);
    if (targetMax === targetMin) {
        return sourceMin;
    }
    var normalized = clamp((numeric - targetMin) / (targetMax - targetMin), 0, 1);
    return sourceMin + normalized * (sourceMax - sourceMin);
}

function setBoundTarget(binding, value, skipSource) {
    var id = String(binding.target);
    var obj = objectById[id];
    if (obj && id !== skipSource) {
        sendObjectValue(obj, objectSpecById[id] || {}, value, binding);
    }
    state[id] = value;
    updateUiBindings(id, value, skipSource);
}

function updateUiBindings(id, value, skipSource) {
    for (var source in uiBindings) {
        if (!uiBindings.hasOwnProperty(source)) {
            continue;
        }
        var binding = uiBindings[source];
        if (binding.target === id && source !== skipSource) {
            setUiSourceValue(source, sourceValueFromUiBinding(binding, value));
        }
    }
}

function hasUiBindingTarget(id) {
    for (var source in uiBindings) {
        if (!uiBindings.hasOwnProperty(source)) {
            continue;
        }
        if (uiBindings[source].target === id) {
            return true;
        }
    }
    return false;
}

function setUiSourceValue(source, value) {
    var obj = objectById[source];
    if (!obj) {
        return;
    }
    uiBindingUpdating = true;
    try {
        obj.message("set", value);
    } catch (err) {
        try {
            obj.message(value);
        } catch (err2) {
        }
    }
    uiBindingUpdating = false;
}

function normalizeWebObject(name) {
    var value = String(name || "jweb~").toLowerCase();
    if (value === "jbrowser~") {
        return "jweb";
    }
    if (value === "jbrowser") {
        return "jweb";
    }
    return String(name || "jweb~");
}

function webMessageOutlet(name) {
    return 0;
}

function createObject(item, index) {
    var x = Number(item.x || 120);
    var y = Number(item.y || (120 + index * 35));
    var args = objectArgs(item);
    var scriptName = item.id ? safeScriptName(item.id) : "";
    if (scriptName) {
        var named = createNamedDefault(scriptName, x, y, args);
        if (named) {
            return named;
        }
    }
    try {
        return this.patcher.newdefault.apply(this.patcher, [x, y].concat(args));
    } catch (err) {
        report("error", {
            reason: "object_create_failed",
            id: String(item.id || ""),
            object: args.join(" "),
            detail: String(err)
        });
        return null;
    }
}

function createNamedDefault(scriptName, x, y, args) {
    var script = getNamed("script");
    if (!script) {
        return null;
    }
    try {
        script.message.apply(script, ["script", "newdefault", scriptName, x, y].concat(args));
        return getNamed(scriptName);
    } catch (err) {
        report("error", {
            reason: "named_object_create_failed",
            id: scriptName,
            object: args.join(" "),
            detail: String(err)
        });
        return null;
    }
}

function objectArgs(item) {
    var args;
    if (item.text) {
        args = tokenize(String(item.text));
    } else {
        args = [String(item.object || item.maxclass || "newobj")].concat(asArray(item.args));
    }
    appendAttrs(args, item.attrs || item.attributes || {});
    return args;
}

function appendAttrs(args, attrs) {
    for (var key in attrs) {
        if (!attrs.hasOwnProperty(key)) {
            continue;
        }
        var attr = String(key);
        if (attr.charAt(0) !== "@") {
            attr = "@" + attr;
        }
        args.push(attr);
        var value = attrs[key];
        if (value instanceof Array) {
            for (var i = 0; i < value.length; i++) {
                args.push(value[i]);
            }
        } else {
            args.push(value);
        }
    }
}

function tokenize(text) {
    var tokens = [];
    var regex = /"([^"\\]*(?:\\.[^"\\]*)*)"|'([^'\\]*(?:\\.[^'\\]*)*)'|\S+/g;
    var match;
    while ((match = regex.exec(text)) !== null) {
        if (match[1] !== undefined) {
            tokens.push(match[1].replace(/\\"/g, "\""));
        } else if (match[2] !== undefined) {
            tokens.push(match[2].replace(/\\'/g, "'"));
        } else {
            tokens.push(match[0]);
        }
    }
    return tokens;
}

function configureObject(obj, item) {
    var scriptName = "";
    try {
        if (item.id) {
            scriptName = safeScriptName(item.id);
            obj.varname = scriptName;
        }
    } catch (err) {
    }
    if (item.presentation || item.presentation_rect) {
        setObjectBoxAttr(obj, scriptName, "presentation", [1]);
    }
    if (item.presentation_rect) {
        setObjectBoxAttr(obj, scriptName, "presentation_rect", item.presentation_rect);
    }
    if (item.patching_rect) {
        setObjectBoxAttr(obj, scriptName, "patching_rect", item.patching_rect);
    }
    var boxAttrs = item.box_attrs || item.boxAttrs || {};
    for (var key in boxAttrs) {
        if (!boxAttrs.hasOwnProperty(key)) {
            continue;
        }
        setObjectBoxAttr(obj, scriptName, key, asArray(boxAttrs[key]));
    }
    if (item.send_to_back || item.layer === "back") {
        scriptCommand(scriptName, "sendtoback", []);
    } else if (item.bring_to_front || item.layer === "front") {
        scriptCommand(scriptName, "bringtofront", []);
    }
    applyInitialMessages(obj, item);
}

function applyInitialMessages(obj, item) {
    var messages = item.messages || [];
    for (var i = 0; i < messages.length; i++) {
        var message = messages[i];
        if (message instanceof Array) {
            try {
                obj.message.apply(obj, message);
            } catch (err) {
            }
        } else if (message && message.name) {
            try {
                obj.message.apply(obj, [String(message.name)].concat(asArray(message.args)));
            } catch (err2) {
            }
        }
    }
    if (item.value !== undefined) {
        try {
            obj.message(item.value);
            if (item.id) {
                state[String(item.id)] = item.value;
                updateUiBindings(String(item.id), item.value, "");
            }
        } catch (err3) {
        }
    }
}

function setObjectBoxAttr(obj, scriptName, messageName, values) {
    var args = asArray(values);
    if (scriptName) {
        scriptSendBox(scriptName, String(messageName), args);
    }
    try {
        obj.setattr.apply(obj, [String(messageName)].concat(args));
    } catch (errSetAttr) {
    }
    try {
        obj.box.setattr.apply(obj.box, [String(messageName)].concat(args));
    } catch (errBoxSetAttr) {
    }
    try {
        obj[String(messageName)] = args.length === 1 ? args[0] : args;
    } catch (err) {
    }
}

function scriptSendBox(scriptName, messageName, args) {
    scriptCommand(scriptName, "sendbox", [messageName].concat(args));
}

function scriptCommand(scriptName, commandName, args) {
    var script = getNamed("script");
    if (!script || !scriptName) {
        return;
    }
    try {
        script.message.apply(script, ["script", commandName, scriptName].concat(args));
    } catch (err) {
    }
}

function safeScriptName(value) {
    return String(value || "obj").replace(/[^A-Za-z0-9_.-]+/g, "_") || "obj";
}

function seedStaticObjects() {
    var byId = {};
    var names = ["plugin", "plugout", "midiin", "midiout"];
    for (var i = 0; i < names.length; i++) {
        var obj = getNamed(names[i]);
        if (obj) {
            byId[names[i]] = obj;
        }
    }
    return byId;
}

function getNamed(name) {
    try {
        return this.patcher.getnamed(String(name));
    } catch (err) {
        return null;
    }
}

function asArray(value) {
    if (value === undefined || value === null) {
        return [];
    }
    if (value instanceof Array) {
        return value;
    }
    return [value];
}

function cloneObject(value) {
    var result = {};
    for (var key in value) {
        if (value.hasOwnProperty(key)) {
            result[key] = value[key];
        }
    }
    return result;
}

function numberOr(value, fallback) {
    var number = Number(value);
    return isNaN(number) ? fallback : number;
}

function clamp(value, minimum, maximum) {
    return Math.max(minimum, Math.min(maximum, value));
}

function applyValues(values) {
    var changed = 0;
    for (var i = 0; i < values.length; i++) {
        var item = values[i];
        var id = String(item.id);
        var obj = objectById[id];
        if (!obj && !hasUiBindingTarget(id)) {
            continue;
        }
        try {
            if (obj) {
                sendObjectValue(obj, objectSpecById[id] || {}, item.value, item);
            }
            state[id] = item.value;
            updateUiBindings(id, item.value, "");
            changed += 1;
        } catch (err) {
            report("error", { reason: "set_failed", id: id, detail: String(err) });
        }
    }
    report("set", { changed: changed });
    pushWebState();
}

function sendObjectValue(obj, spec, value, command) {
    if (command && command.message) {
        var args = command.args || [];
        obj.message.apply(obj, [String(command.message)].concat(args));
    } else if (spec && spec.set_message) {
        obj.message.apply(obj, [String(spec.set_message)].concat(asArray(value)));
    } else if (typeof value === "number") {
        sendNumericValue(obj, spec || {}, value);
    } else if (value !== undefined) {
        obj.message(String(value));
    }
}

function sendNumericValue(obj, spec, value) {
    if (shouldOutputStoredValue(spec)) {
        try {
            obj.message("set", value);
            obj.message("bang");
            return;
        } catch (err) {
        }
    }
    obj.message("float", value);
}

function shouldOutputStoredValue(spec) {
    if (spec.output_on_set || spec.outputOnSet || spec.output_value || spec.outputValue) {
        return true;
    }
    var text = String(spec.text || spec.object || spec.maxclass || "").toLowerCase();
    return text.indexOf("flonum") === 0 || text.indexOf("number") === 0;
}

function pushWebState() {
    var raw = JSON.stringify(state);
    for (var i = 0; i < webObjects.length; i++) {
        try {
            webObjects[i].message("state", raw);
        } catch (err) {
            try {
                webObjects[i].message("executejavascript", "window.dispatchEvent(new CustomEvent('agentm4lstate',{detail:" + raw + "}));");
            } catch (err2) {
            }
        }
    }
}

function clearDynamic() {
    for (var i = dynamicObjects.length - 1; i >= 0; i--) {
        try {
            this.patcher.remove(dynamicObjects[i]);
        } catch (err) {
        }
    }
    dynamicObjects = [];
    objectById = {};
    objectSpecById = {};
    webObjects = [];
    state = {};
    uiBindings = {};
    lastConnectionErrors = [];
}

function report(eventName, payload) {
    payload = payload || {};
    payload.event = eventName;
    payload.command_id = currentCommandId;
    payload.role = role;
    payload.instance_id = instanceId;
    payload.dynamic_objects = dynamicObjects.length;
    payload.webuis = webObjects.length;
    payload.bindings = bindingSummaries();
    payload.state = state;
    if (lastConnectionErrors.length) {
        payload.connection_errors = lastConnectionErrors;
    }
    writeStatus(payload);
    outlet(2, JSON.stringify(payload));
}

function bindingSummaries() {
    var result = [];
    for (var source in uiBindings) {
        if (!uiBindings.hasOwnProperty(source)) {
            continue;
        }
        result.push({
            source: source,
            target: uiBindings[source].target,
            scale: !!uiBindings[source].scale
        });
    }
    return result;
}

function statusFilePath() {
    if (statusFile) {
        return statusFile;
    }
    return String(commandFile).replace(/\.json$/, "_status.json");
}

function writeStatus(payload) {
    var path = statusFilePath();
    if (!path) {
        return;
    }
    try {
        var raw = JSON.stringify(payload);
        if (raw.length < statusPadSize) {
            raw += repeatString(" ", statusPadSize - raw.length);
        }
        var file = new File(path, "write");
        if (!file.isopen) {
            return;
        }
        file.writestring(raw);
        file.close();
    } catch (err) {
    }
}

function repeatString(value, count) {
    var result = "";
    while (count > 0) {
        if (count & 1) {
            result += value;
        }
        count = Math.floor(count / 2);
        if (count > 0) {
            value += value;
        }
    }
    return result;
}
