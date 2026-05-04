autowatch = 1;
inlets = 1;
outlets = 3;

var isRecording = false;
var lastPath = "";
var commandFile = "/tmp/agent_audio_tap_command.json";
var lastCommandId = "";
var pollTask = null;

function loadbang() {
    start_polling();
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
    outlet(1, JSON.stringify({
        event: eventName,
        recording: isRecording,
        path: lastPath
    }));
}
