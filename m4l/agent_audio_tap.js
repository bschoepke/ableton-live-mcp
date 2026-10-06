autowatch = 1;
inlets = 1;
outlets = 3;

var isRecording = false;
var lastPath = "";
var commandFile = jsarguments.length > 1 ? String(jsarguments[1]) : "agent_audio_tap_command.json";
// Liveness handshake: every report() also writes a status FILE next to the
// command file, so a client can prove the device instance is actually alive
// (a stale instance — observed after Live's "Collect All and Save" on Windows,
// 2026-08-08 — silently ignores both the command file and UDP, and a freshly
// loaded instance records zeros until Max finishes wiring the audio graph).
var statusFile = commandFile.replace(/command(\.json)?$/, "status$1");
if (statusFile === commandFile) {
    statusFile = commandFile + ".status";
}
var statusSeq = 0;
var lastCommandId = "";
var pollTask = null;
var startTask = null;

function loadbang() {
    start_polling();
    report("loaded");
}

function start_polling() {
    if (!pollTask) {
        pollTask = new Task(pollCommandFile, this);
        pollTask.interval = 100;
        pollTask.repeat();
    }
}

function pollCommandFile() {
    var file = new File(commandFile, "read");
    if (!file.isopen) {
        return;
    }

    var raw = file.readstring(65536);
    file.close();
    if (!raw) {
        return;
    }

    var command;
    try {
        command = JSON.parse(raw);
    } catch (err) {
        outlet(2, "error", "invalid_command_file", String(err));
        return;
    }

    var id = command.id || raw;
    if (id === lastCommandId) {
        return;
    }
    lastCommandId = id;
    handleCommand([command.command, command.path]);
}

function anything() {
    var atoms = arrayfromargs(messagename, arguments);
    if (messagename === "/agent_audio_tap") {
        handleCommand(atoms.slice(1));
    } else {
        handle(atoms.join(" "));
    }
}

function list() {
    handle(arrayfromargs(arguments).join(" "));
}

function msg_string(value) {
    handle(value);
}

function handle(raw) {
    var command;

    if (raw === "start" || raw === "stop" || raw === "status") {
        handleCommand([raw]);
        return;
    }

    try {
        command = JSON.parse(raw);
    } catch (err) {
        outlet(2, "error", "invalid_json", String(err));
        return;
    }

    handleCommand([command.command, command.path]);
}

function handleCommand(parts) {
    var command = parts[0];
    var path = parts[1];

    if (!command) {
        outlet(2, "error", "missing_command");
        return;
    }

    if (command === "open") {
        openPath(path);
    } else if (command === "start") {
        if (path) {
            openPath(path);
            scheduleStartRecording();
            return;
        }
        startRecording();
    } else if (command === "stop") {
        stopRecording();
    } else if (command === "status") {
        report("status");
    } else {
        outlet(2, "error", "unknown_command", command);
    }
}

function scheduleStartRecording() {
    if (!startTask) {
        startTask = new Task(startRecording, this);
    }
    startTask.schedule(500);
}

function openPath(path) {
    if (!path || typeof path !== "string") {
        outlet(2, "error", "missing_path");
        return;
    }
    lastPath = path;
    outlet(0, "open", path, "wave");
    report("open");
}

function startRecording() {
    if (!lastPath) {
        outlet(2, "error", "no_output_path");
        return;
    }
    isRecording = true;
    outlet(0, 1);
    report("start");
}

function stopRecording() {
    isRecording = false;
    outlet(0, 0);
    report("stop");
}

function report(eventName) {
    statusSeq += 1;
    var payload = JSON.stringify({
        event: eventName,
        recording: isRecording,
        path: lastPath,
        // Handshake fields: last_command_id lets a client match a status write to
        // the exact command it sent; seq distinguishes fresh writes even when the
        // id repeats (e.g. loadbang before any command).
        last_command_id: lastCommandId,
        seq: statusSeq
    });
    outlet(1, payload);
    writeStatusFile(payload);
}

function writeStatusFile(payload) {
    // Overwrite-in-place; eof trim drops any longer stale tail so the file is
    // always exactly one JSON object.
    try {
        var file = new File(statusFile, "write");
        if (!file.isopen) {
            outlet(2, "error", "status_file_unwritable", statusFile);
            return;
        }
        file.position = 0;
        file.writestring(payload);
        file.eof = file.position;
        file.close();
    } catch (err) {
        outlet(2, "error", "status_file_write_failed", String(err));
    }
}
