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
var webReadTask = null;
var deferredCommandTask = null;
var webReadTaskDueTime = 0;
var webMessageDepth = 0;
var deferredCommandTaskScheduled = 0;
var deferredCommandPoll = 0;
var deferredWebRead = 0;
var deferredRawCommands = [];
var dynamicObjects = [];
var objectById = {};
var objectSpecById = {};
var webObjects = [];
var webObjectById = {};
var webRouterById = {};
var webObjectNameById = {};
var webUiIdByTag = {};
var loadedWebUis = {};
var state = {};
var uiBindings = {};
var uiBindingUpdating = false;
var liveParameterObservers = [];
var liveParameterObserverRefreshTasks = [];
var selfDeviceId = 0;
var liveParameterIndexBySource = {};
var liveParameterSourceByTag = {};
var nextGeneratedLiveParameterIndex = 2;
var directLiveApiObserversEnabled = false;
var lastConnectionErrors = [];
var connectionErrorsTruncated = 0;
var lastReloadCommandId = "";
var pendingWebUiReads = [];
var WEBUI_READ_DELAYS = [100, 250, 500, 1000, 2000, 4000];
var FALLBACK_POLL_INTERVAL = 500;
var ACTIVITY_WAKE_MIN_INTERVAL = 500;
var DEFAULT_DEVICE_WIDTH = 420;
var MIN_DEVICE_WIDTH = 260;
var DEVICE_WIDTH_PADDING = 20;
var DEFAULT_DEVICE_HEIGHT = 170;
var MIN_DEVICE_HEIGHT = 120;
var DEVICE_HEIGHT_PADDING = 20;
var STATUS_STATE_KEY_LIMIT = 80;
var STATUS_ARRAY_PREVIEW = 12;
var STATUS_OBJECT_KEY_LIMIT = 12;
var STATUS_VALUE_DEPTH_LIMIT = 2;
var MAX_CONNECTION_ERRORS = 24;
var currentDeviceWidth = DEFAULT_DEVICE_WIDTH;
var currentDeviceHeight = DEFAULT_DEVICE_HEIGHT;
var lastActivityWakeAt = 0;
var pollTaskScheduled = 0;
var MAX_DEFERRED_RAW_COMMANDS = 8;
var HOST_RUNTIME_VERSION = "web-clear-guard-1";

function loadbang() {
    startStaticPolling();
    start_polling();
    report("ready", { role: role, instance_id: instanceId, command_file: commandFile, device_width: currentDeviceWidth, device_height: currentDeviceHeight });
    pollCommandFile();
}

function startStaticPolling() {
    var metro = getNamed("poll-metro");
    if (!metro) {
        return;
    }
    try {
        metro.message("active", 1);
    } catch (errActive) {
    }
    try {
        metro.message(1);
    } catch (errStart) {
    }
    try {
        metro.message("int", 1);
    } catch (errIntStart) {
    }
    try {
        metro.message("start");
    } catch (errStartMessage) {
    }
}

function start_polling() {
    if (!pollTask) {
        pollTask = new Task(handlePollTask, this);
    }
    schedulePollTask(FALLBACK_POLL_INTERVAL);
}

function schedulePollTask(delay) {
    if (!pollTask || pollTaskScheduled) {
        return;
    }
    try {
        pollTaskScheduled = 1;
        pollTask.schedule(Number(delay || FALLBACK_POLL_INTERVAL));
    } catch (err) {
        pollTaskScheduled = 0;
    }
}

function handlePollTask() {
    pollTaskScheduled = 0;
    handleActivityWake("task");
    schedulePollTask(FALLBACK_POLL_INTERVAL);
}

function beginWebMessage() {
    webMessageDepth += 1;
}

function endWebMessage() {
    webMessageDepth -= 1;
    if (webMessageDepth < 0) {
        webMessageDepth = 0;
    }
    scheduleDeferredCommandTaskIfNeeded();
}

function deferCommandPoll() {
    deferredCommandPoll = 1;
    scheduleDeferredCommandTaskIfNeeded();
}

function deferWebRead() {
    deferredWebRead = 1;
    scheduleDeferredCommandTaskIfNeeded();
}

function deferRawCommand(raw) {
    deferredRawCommands.push(String(raw || ""));
    while (deferredRawCommands.length > MAX_DEFERRED_RAW_COMMANDS) {
        deferredRawCommands.shift();
        deferredCommandPoll = 1;
        state.deferred_raw_commands_dropped = (state.deferred_raw_commands_dropped || 0) + 1;
        state.deferred_raw_command_limit = MAX_DEFERRED_RAW_COMMANDS;
    }
    scheduleDeferredCommandTaskIfNeeded();
}

function scheduleDeferredCommandTaskIfNeeded() {
    if (!deferredCommandPoll && !deferredWebRead && deferredRawCommands.length <= 0) {
        return;
    }
    if (!deferredCommandTask) {
        deferredCommandTask = new Task(handleDeferredCommandTask, this);
    }
    if (deferredCommandTaskScheduled) {
        return;
    }
    try {
        deferredCommandTaskScheduled = 1;
        deferredCommandTask.schedule(1);
    } catch (errScheduleDeferred) {
        deferredCommandTaskScheduled = 0;
    }
}

function handleDeferredCommandTask() {
    deferredCommandTaskScheduled = 0;
    if (webMessageDepth > 0) {
        scheduleDeferredCommandTaskIfNeeded();
        return;
    }
    var rawCommands = deferredRawCommands;
    var shouldPoll = deferredCommandPoll;
    var shouldReadWeb = deferredWebRead;
    deferredRawCommands = [];
    deferredCommandPoll = 0;
    deferredWebRead = 0;
    for (var i = 0; i < rawCommands.length; i++) {
        applyRaw(rawCommands[i]);
    }
    if (shouldPoll) {
        pollCommandFile();
    }
    if (shouldReadWeb) {
        drainPendingWebUiReads();
    }
    scheduleDeferredCommandTaskIfNeeded();
}

function pollCommandFile() {
    if (webMessageDepth > 0) {
        deferCommandPoll();
        return;
    }
    var file = new File(commandFile, "read");
    if (!file.isopen) {
        drainPendingWebUiReads();
        return;
    }
    var raw = file.readstring(1048576);
    file.close();
    if (raw) {
        applyRaw(raw);
    }
    drainPendingWebUiReads();
}

function scheduleLiveParameterObserverRefresh(delay) {
    if (!directLiveApiObserversEnabled) {
        return;
    }
    if (typeof LiveAPI === "undefined") {
        return;
    }
    try {
        var task = new Task(startLiveParameterObservers, this);
        liveParameterObserverRefreshTasks.push(task);
        task.schedule(Number(delay || 0));
    } catch (err) {
    }
}

function startLiveParameterObservers() {
    if (!directLiveApiObserversEnabled) {
        liveParameterObservers = [];
        state.live_parameter_observers = 0;
        return;
    }
    if (typeof LiveAPI === "undefined") {
        return;
    }
    liveParameterObservers = [];
    try {
        var devicePath = selfDeviceId > 0 ? "id " + selfDeviceId : "this_device";
        var device = new LiveAPI(null, devicePath);
        var rawParameters = device.get("parameters");
        state.live_parameter_raw = shortStatusText(liveApiList(rawParameters).join(" "));
        state.live_parameter_device_id = selfDeviceId;
        var ids = liveApiIds(rawParameters);
        for (var i = 0; i < ids.length; i++) {
            observeLiveParameter(ids[i]);
        }
        state.live_parameter_observers = liveParameterObservers.length;
    } catch (err) {
        state.live_parameter_observer_error = shortStatusText(String(err));
    }
}

function cancelLiveParameterObserverRefreshTasks() {
    for (var i = 0; i < liveParameterObserverRefreshTasks.length; i++) {
        try {
            liveParameterObserverRefreshTasks[i].cancel();
        } catch (err) {
        }
    }
    liveParameterObserverRefreshTasks = [];
}

function observeLiveParameter(id) {
    try {
        var api = new LiveAPI(null, "id " + id);
        var name = liveApiText(api.get("name"));
        if (!name) {
            return;
        }
        api = new LiveAPI(makeLiveParameterObserver(name), "id " + id);
        api.property = "value";
        liveParameterObservers.push(api);
    } catch (err) {
    }
}

function handleSelfDevicePath(atoms) {
    var id = firstLiveApiId(atoms);
    if (id > 0) {
        selfDeviceId = id;
        state.live_parameter_device_id = selfDeviceId;
        if (directLiveApiObserversEnabled) {
            startLiveParameterObservers();
            scheduleLiveParameterObserverRefresh(250);
        }
    }
}

function makeLiveParameterObserver(name) {
    return function(args) {
        handleLiveParameterChange(String(name), args);
    };
}

function handleLiveParameterChange(name, args) {
    var value = liveApiObservedValue(args);
    if (isCommandTriggerName(name)) {
        pollCommandFile();
        drainPendingWebUiReads();
        return;
    }
    if (uiBindings[name] && !uiBindingUpdating) {
        applyUiBinding(name, [value]);
    }
}

function isCommandTriggerName(name) {
    return name === "Agent Poll" || name === "Agent M4L Poll" || name === "command-trigger";
}

function liveApiObservedValue(args) {
    var values = liveApiList(args);
    for (var i = 0; i < values.length - 1; i++) {
        if (String(values[i]) === "value") {
            return values[i + 1];
        }
    }
    if (values.length > 1) {
        return values[1];
    }
    return values.length ? values[0] : 0;
}

function liveApiIds(values) {
    var result = [];
    var list = liveApiList(values);
    for (var i = 0; i < list.length; i++) {
        if (String(list[i]) === "id" && i + 1 < list.length) {
            result.push(Number(list[i + 1]));
            i += 1;
        }
    }
    return result;
}

function firstLiveApiId(values) {
    var ids = liveApiIds(values);
    if (ids.length) {
        return ids[0];
    }
    var list = liveApiList(values);
    for (var i = 0; i < list.length; i++) {
        var id = Number(list[i]);
        if (id > 0) {
            return id;
        }
    }
    return 0;
}

function liveApiText(values) {
    var list = liveApiList(values);
    if (!list.length) {
        return "";
    }
    if (list.length > 1 && String(list[0]) === "name") {
        return String(list[1]);
    }
    return String(list[0]);
}

function liveApiList(values) {
    if (values instanceof Array) {
        return values;
    }
    if (typeof values === "string") {
        return values.split(/\s+/);
    }
    return values === undefined || values === null ? [] : [values];
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
        applyValues([{ id: String(atoms[0]), value: atoms[1] }], true);
    } else if (messagename === "set_silent" || messagename === "param_silent") {
        applyValues([{ id: String(atoms[0]), value: atoms[1] }], false);
    } else if (messagename === "set_many" || messagename === "param_many") {
        applyValues(valuesFromAtoms(atoms), true);
    } else if (messagename === "set_many_silent" || messagename === "param_many_silent") {
        applyValues(valuesFromAtoms(atoms), false);
    } else if (webUiIdByTag[messagename]) {
        beginWebMessage();
        try {
            handleTaggedWebUiMessage(messagename, atoms);
        } finally {
            endWebMessage();
        }
    } else if (messagename === "__self_device") {
        handleSelfDevicePath(atoms);
    } else if (messagename === "__command_trigger") {
        handleCommandTrigger();
    } else if (messagename === "__filewatch") {
        handleFilewatchWake(atoms);
    } else if (messagename === "__signal_wake") {
        handleSignalWake();
    } else if (messagename === "__midi_wake") {
        handleMidiWake();
    } else if (messagename.indexOf("__live_param_") === 0) {
        handleLiveParameterObserverMessage(messagename, atoms);
    } else if (messagename === "url" || messagename === "title") {
        beginWebMessage();
        try {
            handleWebUiLoadMessage(messagename, atoms, "");
        } finally {
            endWebMessage();
        }
    } else if (messagename === "web_ready" || messagename === "webReady" || messagename === "agent_web_ready") {
        beginWebMessage();
        try {
            handleWebUiReadyMessage(atoms, "");
        } finally {
            endWebMessage();
        }
    } else if (messagename === "web_error" || messagename === "webError") {
        beginWebMessage();
        try {
            handleWebUiErrorMessage(atoms, "");
        } finally {
            endWebMessage();
        }
    } else if (messagename === "web_tick" || messagename === "agent_web_tick") {
        beginWebMessage();
        try {
            handleWebTick();
        } finally {
            endWebMessage();
        }
    } else {
        applyRaw([messagename].concat(atoms).join(" "));
    }
}

function handleCommandTrigger() {
    start_polling();
    markCommandWake("command_trigger");
    pollCommandFile();
    drainPendingWebUiReads();
}

function handleFilewatchWake(atoms) {
    start_polling();
    state.filewatch_bangs = (state.filewatch_bangs || 0) + 1;
    state.filewatch_last = shortStatusText((atoms && atoms.length ? atoms : ["bang"]).join(" "));
    markCommandWake("filewatch");
    var before = lastCommandId;
    pollCommandFile();
    drainPendingWebUiReads();
    if (before === lastCommandId) {
        report("filewatch", { filewatch_bangs: state.filewatch_bangs });
    }
}

function handleSignalWake() {
    handleActivityWake("signal");
}

function handleMidiWake() {
    handleActivityWake("midi");
}

function handleWebTick() {
    handleActivityWake("web");
}

function handleLiveParameterObserverMessage(tag, atoms) {
    var source = liveParameterSourceByTag[tag];
    if (!source || !uiBindings[source] || uiBindingUpdating) {
        return;
    }
    applyUiBinding(source, [liveApiObservedValue(atoms)]);
}

function handleTaggedWebUiMessage(tag, atoms) {
    if (!atoms.length) {
        return;
    }
    var id = webUiIdByTag[tag] || "";
    var name = String(atoms[0]);
    var rest = atoms.slice(1);
    markWebUiLoaded(id);
    if (name === "url" || name === "title") {
        handleWebUiLoadMessage(name, rest, id);
    } else if (name === "web_ready" || name === "webReady" || name === "agent_web_ready") {
        handleWebUiReadyMessage(rest, id);
    } else if (name === "web_error" || name === "webError" || name === "error") {
        handleWebUiErrorMessage(rest, id);
    } else if (name === "web_tick" || name === "agent_web_tick") {
        handleWebTick();
    } else if (name === "set" || name === "param") {
        applyValues([{ id: String(rest[0]), value: rest[1] }], true);
    } else if (name === "set_silent" || name === "param_silent") {
        applyValues([{ id: String(rest[0]), value: rest[1] }], false);
    } else if (name === "set_many" || name === "param_many") {
        applyValues(valuesFromAtoms(rest), true);
    } else if (name === "set_many_silent" || name === "param_many_silent") {
        applyValues(valuesFromAtoms(rest), false);
    } else if (uiBindings[name]) {
        if (!uiBindingUpdating) {
            applyUiBinding(name, rest);
        }
    } else {
        applyRaw([name].concat(rest).join(" "));
    }
}

function handleWebUiLoadMessage(name, atoms, id) {
    var value = shortStatusText(atoms.length ? atoms[0] : "");
    if (id) {
        markWebUiLoaded(id);
        state["web_" + safeStateKey(id) + "_" + String(name)] = value;
    }
    state["web_" + String(name)] = value;
    report("webui", { id: String(id || ""), message: String(name), value: value });
    pushWebState();
}

function handleWebUiReadyMessage(atoms, id) {
    var value = atoms.length ? atoms[0] : 1;
    if (id) {
        markWebUiLoaded(id);
        state["web_" + safeStateKey(id) + "_ready"] = value;
    }
    state.web_ready = value;
    report("webui", { id: String(id || ""), message: "ready", value: shortStatusText(value) });
    pushWebState();
}

function handleWebUiErrorMessage(atoms, id) {
    var value = shortStatusText(atoms.length ? atoms.join(" ") : "unknown");
    if (id) {
        markWebUiLoaded(id);
        state["web_" + safeStateKey(id) + "_error"] = value;
    }
    state.web_error = value;
    report("webui_error", { id: String(id || ""), message: "error", value: value });
    pushWebState();
}

function markWebUiLoaded(id) {
    if (!id) {
        return;
    }
    loadedWebUis[String(id)] = 1;
    clearPendingWebUiReadsForId(id);
    state["web_" + safeStateKey(id) + "_loaded"] = 1;
    state.web_loaded = countKeys(loadedWebUis);
}

function clearPendingWebUiReadsForId(id) {
    var target = String(id);
    var kept = [];
    for (var i = 0; i < pendingWebUiReads.length; i++) {
        if (String(pendingWebUiReads[i].id) !== target) {
            kept.push(pendingWebUiReads[i]);
        }
    }
    pendingWebUiReads = kept;
    state.web_read_pending = pendingWebUiReads.length;
}

function handleOsc(args) {
    start_polling();
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
    start_polling();
    applyRaw(value);
}

function msg_int(value) {
    start_polling();
    markCommandWake("int");
    pollCommandFile();
    drainPendingWebUiReads();
}

function msg_float(value) {
    start_polling();
    markCommandWake("float");
    pollCommandFile();
    drainPendingWebUiReads();
}

function list() {
    handleActivityWake("list");
}

function bang() {
    handleActivityWake("bang");
}

function handleActivityWake(source) {
    start_polling();
    var now = currentTimeMs();
    if (lastActivityWakeAt && now - lastActivityWakeAt < ACTIVITY_WAKE_MIN_INTERVAL) {
        state.command_wake_skipped = (state.command_wake_skipped || 0) + 1;
        state.command_wake_skip_source = source;
        return;
    }
    lastActivityWakeAt = now;
    markCommandWake(source);
    pollCommandFile();
    drainPendingWebUiReads();
}

function currentTimeMs() {
    try {
        return new Date().getTime();
    } catch (err) {
        return 0;
    }
}

function markCommandWake(source) {
    state.command_wake_source = source;
    state.command_wake_count = (state.command_wake_count || 0) + 1;
}

function applyRaw(raw) {
    var command;
    raw = String(raw || "");
    if (webMessageDepth > 0) {
        deferRawCommand(raw);
        return;
    }
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
        if (clearDynamic() !== false) {
            report("clear", {});
        }
    } else if (command.command === "set") {
        applyValues(command.values || command.parameters || [], true);
    } else if (command.command === "status") {
        report("status", { objects: dynamicObjects.length, device_width: currentDeviceWidth });
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
    var recovery = readRecoveryPayload();
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

function readRecoveryPayload() {
    var recovery = readCommandFileJson();
    if (recoverySpecFromPayload(recovery)) {
        return recovery;
    }
    var sidecar = readJsonFile(recoveryFilePath(), "invalid_recovery_sidecar_json");
    if (recoverySpecFromPayload(sidecar)) {
        return sidecar;
    }
    return recovery;
}

function recoverySpecFromPayload(recovery) {
    if (!recovery) {
        return null;
    }
    return recovery.patch || recovery.spec || ((recovery.objects || recovery.webui || recovery.webuis) ? recovery : null);
}

function readCommandFileJson() {
    return readJsonFile(commandFile, "invalid_recovery_json");
}

function recoveryFilePath() {
    return String(commandFile) + ".recovery.json";
}

function readJsonFile(path, errorReason) {
    var file = new File(path, "read");
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
        report("error", { reason: errorReason || "invalid_json_file", detail: String(err) });
        return null;
    }
}

function applySpec(spec) {
    if (!spec || (!spec.objects && !spec.webui && !spec.webuis)) {
        report("error", { reason: "missing_objects" });
        return;
    }
    var previousState = cloneObject(state);
    directLiveApiObserversEnabled = !!(spec.live_api_observers || spec.observe_live_parameters || spec.observe_live_api_parameters);
    configureDeviceBounds(spec);
    if (clearDynamic(reusableWebIdsForSpec(spec)) === false) {
        return;
    }
    state.live_api_observers_enabled = directLiveApiObserversEnabled ? 1 : 0;
    if (!directLiveApiObserversEnabled) {
        liveParameterObservers = [];
        state.live_parameter_observers = 0;
    }
    var byId = seedStaticObjects();
    createWebUis(spec.webuis || spec.webui, byId);
    nextGeneratedLiveParameterIndex = 2;
    liveParameterIndexBySource = {};
    liveParameterSourceByTag = {};
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
            trackGeneratedLiveParameter(item);
        }
    }
    configureUiBindings(spec, objects, byId);
    createDynamicPoller();
    if (directLiveApiObserversEnabled) {
        startLiveParameterObservers();
        scheduleLiveParameterObserverRefresh(250);
        scheduleLiveParameterObserverRefresh(1000);
    }
    var connections = connectPatchlines(spec.connections || [], byId);
    var restored = restoreState(previousState);
    var reapplied = reapplyStateValues();
    report("reload", {
        objects: dynamicObjects.length,
        connections: connections.connected,
        connection_errors: connections.errors,
        device_width: currentDeviceWidth,
        restored_state: restored,
        reapplied_state: reapplied
    });
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
    recordConnectionErrors(errors);
    return { connected: connected, errors: errors };
}

function configureDeviceBounds(spec) {
    var bounds = inferDeviceBounds(spec, currentDeviceWidth || DEFAULT_DEVICE_WIDTH, currentDeviceHeight || DEFAULT_DEVICE_HEIGHT);
    currentDeviceWidth = bounds.width;
    currentDeviceHeight = bounds.height;
    setPatcherAttr("devicewidth", [bounds.width]);
    setPatcherAttr("openrect", [0, 0, bounds.width, bounds.height]);
    setPatcherAttr("rect", [0, 0, bounds.width, bounds.height]);
}

function inferDeviceWidth(spec, fallback) {
    var explicit = positiveNumber(spec.device_width || spec.devicewidth || spec.width);
    if (explicit > 0) {
        return Math.max(MIN_DEVICE_WIDTH, Math.round(explicit));
    }
    var width = 0;
    var objects = spec.objects || [];
    for (var i = 0; i < objects.length; i++) {
        width = Math.max(width, rectRight(objects[i].presentation_rect));
    }
    var webuis = webuiList(spec.webuis || spec.webui);
    for (var j = 0; j < webuis.length; j++) {
        width = Math.max(width, rectRight(webuis[j].presentation_rect));
    }
    if (width <= 0) {
        width = fallback || DEFAULT_DEVICE_WIDTH;
        return Math.max(MIN_DEVICE_WIDTH, Math.round(width));
    }
    return Math.max(MIN_DEVICE_WIDTH, Math.round(width + DEVICE_WIDTH_PADDING));
}

function inferDeviceHeight(spec, fallback) {
    var explicit = positiveNumber(spec.device_height || spec.deviceheight || spec.height);
    if (explicit > 0) {
        return Math.max(MIN_DEVICE_HEIGHT, Math.round(explicit));
    }
    var height = 0;
    var objects = spec.objects || [];
    for (var i = 0; i < objects.length; i++) {
        height = Math.max(height, rectBottom(objects[i].presentation_rect));
    }
    var webuis = webuiList(spec.webuis || spec.webui);
    for (var j = 0; j < webuis.length; j++) {
        height = Math.max(height, rectBottom(webuis[j].presentation_rect));
    }
    if (height <= 0) {
        height = fallback || DEFAULT_DEVICE_HEIGHT;
        return Math.max(MIN_DEVICE_HEIGHT, Math.round(height));
    }
    return Math.max(MIN_DEVICE_HEIGHT, Math.round(height + DEVICE_HEIGHT_PADDING));
}

function inferDeviceBounds(spec, fallbackWidth, fallbackHeight) {
    return {
        width: inferDeviceWidth(spec, fallbackWidth),
        height: inferDeviceHeight(spec, fallbackHeight)
    };
}

function positiveNumber(value) {
    var number = Number(value);
    return isNaN(number) ? 0 : number;
}

function rectRight(rect) {
    if (!(rect instanceof Array) || rect.length < 4) {
        return 0;
    }
    var x = Number(rect[0]);
    var width = Number(rect[2]);
    if (isNaN(x) || isNaN(width)) {
        return 0;
    }
    return x + width;
}

function rectBottom(rect) {
    if (!(rect instanceof Array) || rect.length < 4) {
        return 0;
    }
    var y = Number(rect[1]);
    var height = Number(rect[3]);
    if (isNaN(y) || isNaN(height)) {
        return 0;
    }
    return y + height;
}

function webuiList(webui) {
    if (!webui) {
        return [];
    }
    if (webui instanceof Array) {
        return webui;
    }
    return [webui];
}

function setPatcherAttr(messageName, values) {
    var args = [String(messageName)].concat(asArray(values));
    try {
        this.patcher.setattr.apply(this.patcher, args);
        return;
    } catch (errSetAttr) {
    }
    try {
        this.patcher.message.apply(this.patcher, ["setattr"].concat(args));
        return;
    } catch (errMessage) {
    }
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
    var path = webui.html_path || webui.path || webui.url || webui.html_url;
    var readMessage = webui.read_message || webui.readMessage || ((webui.html_path || webui.path) ? "readfile" : "read");
    var fallbackPath = webui.url || webui.html_url || "";
    if (!path) {
        return;
    }
    var id = String(webui.id || (index ? "webui_" + index : "webui"));
    var objectName = normalizeWebObject(webui.object || "jweb~");
    var args = webObjectArgs(webui, objectName);
    var patchRect = webui.patching_rect || rect;
    var existing = reusableWebObject(id, objectName, webui);
    var obj = existing || createNamedDefault(safeScriptName(id), Number(patchRect[0]), Number(patchRect[1]), args);
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
    rememberDynamicObject(obj);
    rememberWebObject(id, objectName, obj);
    objectById[id] = obj;
    objectSpecById[id] = webui;
    byId[id] = obj;
    var tag = "__webui_" + safeScriptName(id);
    webUiIdByTag[tag] = id;
    var router = webRouterById[id] || createNamedDefault(safeScriptName(tag), Number(patchRect[0]), Number(patchRect[1]) + 30, ["prepend", tag]);
    if (router) {
        webRouterById[id] = router;
        rememberDynamicObject(router);
    }
    if (!existing) {
        try {
            if (router) {
                this.patcher.connect(obj, webMessageOutlet(objectName), router, 0);
                this.patcher.connect(router, 0, this.box, 0);
            } else {
                this.patcher.connect(obj, webMessageOutlet(objectName), this.box, 0);
            }
        } catch (err) {
            recordConnectionError({ from: id, to: "js", outlet: webMessageOutlet(objectName), inlet: 0, reason: String(err) });
        }
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
    scheduleWebUiReadSeries(obj, path, id, readMessage, fallbackPath);
}

function reusableWebIdsForSpec(spec) {
    var preserve = {};
    var list = webuiList(spec.webuis || spec.webui);
    for (var i = 0; i < list.length; i++) {
        var webui = list[i] || {};
        var id = String(webui.id || (i ? "webui_" + i : "webui"));
        var objectName = normalizeWebObject(webui.object || "jweb~");
        if (webui.reuse === false) {
            continue;
        }
        if (webObjectById[id] && webObjectNameById[id] === objectName) {
            preserve[id] = 1;
        }
    }
    return preserve;
}

function reusableWebObject(id, objectName, webui) {
    if (webui.reuse === false) {
        return null;
    }
    if (webObjectNameById[id] !== objectName) {
        return null;
    }
    return webObjectById[id] || null;
}

function rememberWebObject(id, objectName, obj) {
    webObjectById[id] = obj;
    webObjectNameById[id] = objectName;
    if (!containsObject(webObjects, obj)) {
        webObjects.push(obj);
    }
}

function rememberDynamicObject(obj) {
    if (obj && !containsObject(dynamicObjects, obj)) {
        dynamicObjects.push(obj);
    }
}

function containsObject(list, obj) {
    for (var i = 0; i < list.length; i++) {
        if (list[i] === obj) {
            return true;
        }
    }
    return false;
}

function scheduleWebUiReadSeries(obj, path, id, readMessage, fallbackPath) {
    var baseTime = nowMs();
    var dueTime = baseTime;
    for (var attempt = 0; attempt < WEBUI_READ_DELAYS.length; attempt++) {
        if (attempt > 0) {
            dueTime += webUiReadDelay(attempt);
        }
        pendingWebUiReads.push({ obj: obj, path: String(path), id: String(id), attempt: attempt, read_message: String(readMessage || "read"), fallback_path: String(fallbackPath || ""), due_time: dueTime });
        if (attempt > 0) {
            armWebReadTask(dueTime - baseTime);
        }
    }
    state.web_read_scheduled = (state.web_read_scheduled || 0) + WEBUI_READ_DELAYS.length;
    state.web_read_pending = pendingWebUiReads.length;
    drainPendingWebUiReads();
    scheduleNextPendingWebRead();
}

function scheduleWebReadTask(delay) {
    var dueTime = nowMs() + Math.max(1, Number(delay || 1));
    if (webReadTaskDueTime && webReadTaskDueTime <= dueTime + 1) {
        return;
    }
    webReadTaskDueTime = dueTime;
    armWebReadTask(delay);
}

function armWebReadTask(delay) {
    webReadTask = new Task(readPendingWebUis, this);
    webReadTask.schedule(Math.max(1, Number(delay || 1)));
}

function scheduleNextPendingWebRead() {
    if (!pendingWebUiReads.length) {
        return;
    }
    var now = nowMs();
    var delay = WEBUI_READ_DELAYS[WEBUI_READ_DELAYS.length - 1];
    for (var i = 0; i < pendingWebUiReads.length; i++) {
        delay = Math.min(delay, Math.max(1, Number(pendingWebUiReads[i].due_time || now) - now));
    }
    scheduleWebReadTask(delay);
}

function readPendingWebUis() {
    if (webMessageDepth > 0) {
        deferWebRead();
        return;
    }
    webReadTaskDueTime = 0;
    var now = nowMs();
    var reads = [];
    var deferred = [];
    for (var pendingIndex = 0; pendingIndex < pendingWebUiReads.length; pendingIndex++) {
        var pending = pendingWebUiReads[pendingIndex];
        if (Number(pending.due_time || 0) <= now) {
            reads.push(pending);
        } else {
            deferred.push(pending);
        }
    }
    pendingWebUiReads = [];
    state.web_read_pending = deferred.length;
    for (var i = 0; i < reads.length; i++) {
        var id = String(reads[i].id);
        if (loadedWebUis[id]) {
            continue;
        }
        var key = safeStateKey(id);
        state.web_read_attempts = (state.web_read_attempts || 0) + 1;
        state["web_" + key + "_read_attempts"] = (state["web_" + key + "_read_attempts"] || 0) + 1;
        try {
            var request = webUiReadRequest(reads[i]);
            state["web_" + key + "_read_message"] = request.message;
            state["web_" + key + "_last_read_attempt"] = reads[i].attempt + 1;
            reads[i].obj.message(request.message, request.path);
            report("webui_read", { id: id, attempt: reads[i].attempt + 1, message: request.message });
        } catch (err) {
            report("error", { reason: "webui_load_failed", id: id, detail: String(err) });
        }
        if (!loadedWebUis[id] && reads[i].attempt + 1 >= WEBUI_READ_DELAYS.length) {
            state["web_" + key + "_read_exhausted"] = 1;
            report("error", { reason: "webui_read_exhausted", id: id, attempts: reads[i].attempt + 1 });
        }
    }
    pendingWebUiReads = deferred.concat(pendingWebUiReads);
    state.web_read_pending = pendingWebUiReads.length;
    scheduleNextPendingWebRead();
}

function nowMs() {
    return (new Date()).getTime();
}

function webUiReadRequest(read) {
    if (read.read_message === "readfile" && read.fallback_path && read.attempt > 0 && read.attempt % 2 === 1) {
        return { message: "read", path: read.fallback_path };
    }
    return { message: read.read_message || "read", path: read.path };
}

function webUiReadDelay(attempt) {
    var index = Math.max(0, Math.min(Number(attempt || 0), WEBUI_READ_DELAYS.length - 1));
    return WEBUI_READ_DELAYS[index];
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

function createDynamicPoller() {
    var id = "__agent_m4l_poll";
    var poller = createNamedDefault(id, 20, 300, ["qmetro", FALLBACK_POLL_INTERVAL]);
    if (!poller) {
        return;
    }
    dynamicObjects.push(poller);
    try {
        this.patcher.connect(poller, 0, this.box, 0);
    } catch (err) {
        recordConnectionError({ from: id, to: "js", outlet: 0, inlet: 0, reason: String(err) });
    }
    try {
        poller.message("active", 1);
    } catch (errActive) {
    }
    try {
        poller.message(1);
    } catch (errStart) {
    }
    try {
        poller.message("int", 1);
    } catch (errIntStart) {
    }
    try {
        poller.message("start");
    } catch (errStartMessage) {
    }
}

function trackGeneratedLiveParameter(item) {
    if (!item || !item.id || !isGeneratedLiveParameter(item)) {
        return;
    }
    liveParameterIndexBySource[String(item.id)] = nextGeneratedLiveParameterIndex;
    nextGeneratedLiveParameterIndex += 1;
}

function isGeneratedLiveParameter(item) {
    if (item.parameter_enable === 0 || item.parameter_enable === false) {
        return false;
    }
    var attrs = item.box_attrs || item.boxAttrs || {};
    if (attrs.parameter_enable === 0 || attrs.parameter_enable === false) {
        return false;
    }
    var text = String(item.text || "").toLowerCase();
    var maxclass = String(item.maxclass || "").toLowerCase();
    return text.indexOf("live.") === 0 || maxclass.indexOf("live.") === 0;
}

function createLiveParameterObserverForSource(source) {
    var parameterIndex = liveParameterIndexBySource[source];
    if (parameterIndex === undefined) {
        return;
    }
    var tag = "__live_param_" + safeScriptName(source);
    liveParameterSourceByTag[tag] = source;
    var base = safeScriptName("__observer_" + source);
    var msg = createNamedDefault(base + "_path_message", 40, 340 + parameterIndex * 24, ["message", "path", "this_device", "parameters", parameterIndex]);
    var path = createNamedDefault(base + "_path", 220, 340 + parameterIndex * 24, ["live.path"]);
    var observer = createNamedDefault(base + "_observer", 340, 340 + parameterIndex * 24, ["live.observer", "value"]);
    var prepend = createNamedDefault(base + "_prepend", 500, 340 + parameterIndex * 24, ["prepend", tag]);
    if (!msg || !path || !observer || !prepend) {
        return;
    }
    dynamicObjects.push(msg);
    dynamicObjects.push(path);
    dynamicObjects.push(observer);
    dynamicObjects.push(prepend);
    try {
        this.patcher.connect(msg, 0, path, 0);
        this.patcher.connect(path, 0, observer, 1);
        this.patcher.connect(observer, 0, prepend, 0);
        this.patcher.connect(prepend, 0, this.box, 0);
        msg.message("bang");
        state.live_parameter_box_observers = countKeys(liveParameterSourceByTag);
    } catch (err) {
        recordConnectionError({ from: base, to: "js", outlet: 0, inlet: 0, reason: String(err) });
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
        source_message: binding.source_message || binding.sourceMessage || binding.source_set_message || binding.sourceSetMessage,
        source_args: binding.source_args || binding.sourceArgs,
        scale: !!binding.scale || !!binding.normalized || binding.source_min !== undefined || binding.source_max !== undefined,
        report: binding.report !== false,
        source_settable: binding.source_settable !== undefined ? binding.source_settable !== false : (
            binding.set_source !== undefined ? binding.set_source !== false : (
                binding.write_source !== undefined ? binding.write_source !== false : binding.report !== false
            )
        )
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
    createLiveParameterObserverForSource(String(binding.source));
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
    drainPendingWebUiReads();
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
            if (canSetUiSource(binding)) {
                setUiSourceValue(source, sourceValueFromUiBinding(binding, value), binding);
            }
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

function setUiSourceValue(source, value, binding) {
    var obj = objectById[source];
    if (!obj) {
        return;
    }
    uiBindingUpdating = true;
    try {
        if (binding && binding.source_message) {
            obj.message.apply(obj, [String(binding.source_message)].concat(binding.source_args !== undefined ? asArray(binding.source_args) : messageValueArgs(value)));
        } else {
            obj.message("set", value);
        }
    } catch (err) {
        try {
            sendObjectValue(obj, objectSpecById[source] || {}, value, null);
        } catch (err2) {
        }
    }
    uiBindingUpdating = false;
}

function canSetUiSource(binding) {
    return !binding || binding.source_settable !== false;
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
    var value = String(name || "").toLowerCase();
    if (value.indexOf("jweb~") === 0) {
        return 2;
    }
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
        setBoxOnlyAttr(obj, scriptName, key, asArray(boxAttrs[key]));
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

function setBoxOnlyAttr(obj, scriptName, messageName, values) {
    var args = asArray(values);
    if (scriptName) {
        scriptSendBox(scriptName, String(messageName), args);
    }
    try {
        obj.box.setattr.apply(obj.box, [String(messageName)].concat(args));
    } catch (errBoxSetAttr) {
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

function safeStateKey(value) {
    return safeScriptName(value);
}

function countKeys(value) {
    var count = 0;
    for (var key in value) {
        if (value.hasOwnProperty(key)) {
            count += 1;
        }
    }
    return count;
}

function seedStaticObjects() {
    var byId = {};
    var names = ["plugin", "plugout", "midiin", "midiout", "audio-in-l", "audio-in-r", "audio-out-l", "audio-out-r"];
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

function valuesFromAtoms(atoms) {
    if (atoms.length === 1) {
        var parsed = valuesFromJson(atoms[0]);
        if (parsed.length) {
            return parsed;
        }
    }
    var values = [];
    for (var i = 0; i + 1 < atoms.length; i += 2) {
        values.push({ id: String(atoms[i]), value: atoms[i + 1] });
    }
    return values;
}

function valuesFromJson(raw) {
    var parsed;
    try {
        parsed = JSON.parse(String(raw));
    } catch (err) {
        return [];
    }
    if (parsed instanceof Array) {
        return parsed;
    }
    var values = parsed.values || parsed.parameters;
    if (values instanceof Array) {
        return values;
    }
    var result = [];
    for (var id in parsed) {
        if (parsed.hasOwnProperty(id)) {
            result.push({ id: id, value: parsed[id] });
        }
    }
    return result;
}

function shortStatusText(value) {
    var text = String(value);
    if (text.length > 240) {
        return text.substring(0, 237) + "...";
    }
    return text;
}

function restoreState(previousState) {
    var restored = 0;
    for (var id in previousState) {
        if (!previousState.hasOwnProperty(id)) {
            continue;
        }
        if (setStateValue(id, previousState[id], "", null)) {
            restored += 1;
        }
    }
    return restored;
}

function reapplyStateValues() {
    var snapshot = cloneObject(state);
    var reapplied = 0;
    for (var id in snapshot) {
        if (!snapshot.hasOwnProperty(id)) {
            continue;
        }
        if (setStateValue(id, snapshot[id], "", null)) {
            reapplied += 1;
        }
    }
    return reapplied;
}

function setStateValue(id, value, skipSource, command) {
    id = String(id);
    var binding = uiBindings[id];
    if (binding) {
        if (canSetUiSource(binding)) {
            setUiSourceValue(id, value, binding);
        }
        setBoundTarget(binding, valueFromUiBinding(binding, value), id);
        return true;
    }
    var obj = objectById[id];
    if (!obj && !hasUiBindingTarget(id)) {
        return false;
    }
    if (obj) {
        sendObjectValue(obj, objectSpecById[id] || {}, value, command || { id: id, value: value });
    }
    state[id] = value;
    updateUiBindings(id, value, skipSource || "");
    return true;
}

function applyValues(values, shouldReport) {
    if (shouldReport === undefined) {
        shouldReport = true;
    }
    var changed = 0;
    var shouldPushWebState = shouldReport || valuesRequestWebStatePush(values);
    for (var i = 0; i < values.length; i++) {
        var item = values[i];
        var id = String(item.id);
        try {
            if (setStateValue(id, item.value, "", item)) {
                changed += 1;
            }
        } catch (err) {
            report("error", { reason: "set_failed", id: id, detail: String(err) });
        }
    }
    if (shouldReport) {
        report("set", { changed: changed });
    }
    drainPendingWebUiReads();
    if (shouldPushWebState) {
        pushWebState();
    }
}

function valuesRequestWebStatePush(values) {
    for (var i = 0; i < values.length; i++) {
        var item = values[i] || {};
        if (item.push_state || item.pushState || item.echo_state || item.echoState) {
            return true;
        }
    }
    return false;
}

function sendObjectValue(obj, spec, value, command) {
    if (command && command.message) {
        var args = command.args !== undefined ? command.args : messageValueArgs(value);
        obj.message.apply(obj, [String(command.message)].concat(args));
    } else if (spec && spec.set_message) {
        obj.message.apply(obj, [String(spec.set_message)].concat(messageValueArgs(value)));
    } else if (value instanceof Array) {
        obj.message.apply(obj, [String((spec && (spec.list_message || spec.listMessage)) || "list")].concat(value));
    } else if (value && typeof value === "object") {
        sendObjectDataValue(obj, spec || {}, value);
    } else if (typeof value === "number") {
        sendNumericValue(obj, spec || {}, value);
    } else if (value !== undefined) {
        obj.message(String(value));
    }
}

function messageValueArgs(value) {
    if (value instanceof Array) {
        return value;
    }
    if (value && typeof value === "object") {
        if (value.values instanceof Array) {
            return value.values;
        }
        return [JSON.stringify(value)];
    }
    return asArray(value);
}

function sendObjectDataValue(obj, spec, value) {
    if (value.values instanceof Array) {
        obj.message.apply(obj, [String(spec.list_message || spec.listMessage || "list")].concat(value.values));
        return;
    }
    var message = String(spec.object_message || spec.objectMessage || spec.json_message || spec.jsonMessage || "symbol");
    var raw = JSON.stringify(value);
    if (message === "symbol") {
        obj.message(raw);
    } else {
        obj.message(message, raw);
    }
}

function sendNumericValue(obj, spec, value) {
    if (shouldSendToggleValue(spec)) {
        obj.message("int", Math.round(value) ? 1 : 0);
        return;
    }
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

function shouldSendToggleValue(spec) {
    var text = String(spec.text || spec.object || spec.maxclass || "").toLowerCase();
    return text.indexOf("live.toggle") === 0 || text.indexOf("toggle") === 0;
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
        sendWebState(webObjects[i], raw);
    }
}

function drainPendingWebUiReads() {
    if (pendingWebUiReads.length) {
        if (webMessageDepth > 0) {
            deferWebRead();
            return;
        }
        readPendingWebUis();
    }
}

function sendWebState(obj, raw) {
    try {
        obj.message("state", raw);
    } catch (errStateMessage) {
    }
    try {
        obj.message("executejavascript", webStateDispatchScript(raw));
    } catch (errDispatch) {
    }
}

function webStateDispatchScript(raw) {
    return "(function(s){window.agentM4L=window.agentM4L||{};window.agentM4L.state=s;window.dispatchEvent(new CustomEvent('agentm4lstate',{detail:s}));if(typeof window.agentM4L.onstate==='function'){window.agentM4L.onstate(s)}})(" + raw + ");";
}

function clearDynamic(preserveWebIds) {
    preserveWebIds = preserveWebIds || {};
    if (webMessageDepth > 0 && hasRemovableWebObjects(preserveWebIds)) {
        state.web_clear_deferred = (state.web_clear_deferred || 0) + 1;
        lastCommandId = "";
        deferCommandPoll();
        report("error", { reason: "web_clear_deferred", detail: "refusing to remove browser UI during web callback" });
        return false;
    }
    var keepDynamicObjects = [];
    var keepWebObjectById = {};
    var keepWebRouterById = {};
    var keepWebObjectNameById = {};
    var preservedObjects = [];
    for (var preserveId in preserveWebIds) {
        if (!preserveWebIds.hasOwnProperty(preserveId)) {
            continue;
        }
        if (webObjectById[preserveId]) {
            keepWebObjectById[preserveId] = webObjectById[preserveId];
            keepWebObjectNameById[preserveId] = webObjectNameById[preserveId];
            preservedObjects.push(webObjectById[preserveId]);
        }
        if (webRouterById[preserveId]) {
            keepWebRouterById[preserveId] = webRouterById[preserveId];
            preservedObjects.push(webRouterById[preserveId]);
        }
    }
    if (webReadTask) {
        try {
            webReadTask.cancel();
        } catch (errCancel) {
        }
    }
    pendingWebUiReads = [];
    cancelLiveParameterObserverRefreshTasks();
    liveParameterObservers = [];
    for (var i = dynamicObjects.length - 1; i >= 0; i--) {
        if (containsObject(preservedObjects, dynamicObjects[i])) {
            keepDynamicObjects.unshift(dynamicObjects[i]);
            continue;
        }
        try {
            this.patcher.remove(dynamicObjects[i]);
        } catch (err) {
        }
    }
    dynamicObjects = keepDynamicObjects;
    objectById = {};
    objectSpecById = {};
    webObjects = [];
    webObjectById = keepWebObjectById;
    webRouterById = keepWebRouterById;
    webObjectNameById = keepWebObjectNameById;
    webUiIdByTag = {};
    loadedWebUis = {};
    state = {};
    uiBindings = {};
    liveParameterIndexBySource = {};
    liveParameterSourceByTag = {};
    nextGeneratedLiveParameterIndex = 2;
    lastConnectionErrors = [];
    connectionErrorsTruncated = 0;
    return true;
}

function hasRemovableWebObjects(preserveWebIds) {
    for (var i = 0; i < webObjects.length; i++) {
        if (!isPreservedWebObject(webObjects[i], preserveWebIds || {})) {
            return true;
        }
    }
    return false;
}

function isPreservedWebObject(obj, preserveWebIds) {
    for (var preserveId in preserveWebIds) {
        if (!preserveWebIds.hasOwnProperty(preserveId)) {
            continue;
        }
        if (webObjectById[preserveId] === obj || webRouterById[preserveId] === obj) {
            return true;
        }
    }
    return false;
}

function report(eventName, payload) {
    payload = payload || {};
    payload.event = eventName;
    payload.command_id = currentCommandId;
    payload.role = role;
    payload.instance_id = instanceId;
    payload.host_runtime_version = HOST_RUNTIME_VERSION;
    payload.dynamic_objects = dynamicObjects.length;
    payload.webuis = webObjects.length;
    payload.device_width = currentDeviceWidth;
    payload.device_height = currentDeviceHeight;
    if (eventName === "reload") {
        lastReloadCommandId = currentCommandId;
    }
    payload.last_reload_command_id = lastReloadCommandId;
    payload.bindings = bindingSummaries();
    payload.state = statusStateSnapshot();
    if (lastConnectionErrors.length) {
        payload.connection_errors = lastConnectionErrors;
    }
    if (connectionErrorsTruncated) {
        payload.connection_errors_truncated = connectionErrorsTruncated;
    }
    writeStatus(payload);
    outlet(2, "status", eventName, currentCommandId || "", dynamicObjects.length, webObjects.length);
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
            scale: !!uiBindings[source].scale,
            source_settable: uiBindings[source].source_settable !== false
        });
    }
    return result;
}

function recordConnectionErrors(errors) {
    for (var i = 0; i < errors.length; i++) {
        recordConnectionError(errors[i]);
    }
}

function recordConnectionError(error) {
    if (lastConnectionErrors.length >= MAX_CONNECTION_ERRORS) {
        connectionErrorsTruncated += 1;
        return;
    }
    lastConnectionErrors.push(compactConnectionError(error || {}));
}

function compactConnectionError(error) {
    return {
        from: shortStatusText(error.from || ""),
        to: shortStatusText(error.to || ""),
        outlet: Number(error.outlet || 0),
        inlet: Number(error.inlet || 0),
        reason: shortStatusText(error.reason || error.detail || "")
    };
}

function statusStateSnapshot() {
    var result = {};
    var keys = [];
    for (var key in state) {
        if (state.hasOwnProperty(key)) {
            keys.push(String(key));
        }
    }
    keys.sort();
    var limit = Math.min(keys.length, STATUS_STATE_KEY_LIMIT);
    for (var i = 0; i < limit; i++) {
        result[keys[i]] = compactStatusValue(state[keys[i]], 0);
    }
    if (keys.length > STATUS_STATE_KEY_LIMIT) {
        result._truncated_keys = keys.length - STATUS_STATE_KEY_LIMIT;
    }
    return result;
}

function compactStatusValue(value, depth) {
    depth = Number(depth || 0);
    if (typeof value === "string") {
        return shortStatusText(value);
    }
    if (value instanceof Array) {
        var preview = [];
        var count = Math.min(value.length, STATUS_ARRAY_PREVIEW);
        for (var i = 0; i < count; i++) {
            preview.push(depth >= STATUS_VALUE_DEPTH_LIMIT ? shortStatusText(value[i]) : compactStatusValue(value[i], depth + 1));
        }
        if (value.length <= STATUS_ARRAY_PREVIEW && depth < STATUS_VALUE_DEPTH_LIMIT) {
            return preview;
        }
        return { items: value.length, preview: preview };
    }
    if (value && typeof value === "object") {
        var keys = [];
        for (var key in value) {
            if (value.hasOwnProperty(key)) {
                keys.push(String(key));
            }
        }
        keys.sort();
        if (keys.length <= STATUS_OBJECT_KEY_LIMIT && depth < STATUS_VALUE_DEPTH_LIMIT) {
            var result = {};
            for (var j = 0; j < keys.length; j++) {
                result[keys[j]] = compactStatusValue(value[keys[j]], depth + 1);
            }
            return result;
        }
        return { key_count: keys.length, keys: keys.slice(0, STATUS_OBJECT_KEY_LIMIT) };
    }
    return value;
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
        var file = new File(path, "write");
        if (!file.isopen) {
            return;
        }
        file.writestring(raw);
        file.close();
    } catch (err) {
    }
}
