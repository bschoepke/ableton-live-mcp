from __future__ import absolute_import, print_function

import json
import hashlib
import os
import socket
import sys
import threading
import time
import traceback

import Live
from _Framework.ControlSurface import ControlSurface


HOST = "127.0.0.1"
PORT = 8765
CLIENT_TIMEOUT = 10
MAX_REQUEST_BYTES = 1024 * 1024
MAX_OBJECTS = 2000
DEFAULT_MAX_ITEMS = 200
DEFAULT_MAX_DEPTH = 8
DEFAULT_MAX_STRING_LENGTH = 4096
DEFAULT_CHILD_LIMIT = 200
DEFAULT_MAIN_THREAD_TIMEOUT = 30
DEFAULT_BROWSER_ROOTS = ("instruments", "audio_effects", "midi_effects", "drums", "samples", "sounds", "packs", "plugins", "user_library", "user_folders", "current_project")
AGENT_AUDIO_TAP_HOST = "127.0.0.1"
AGENT_AUDIO_TAP_PORT = 17654
AGENT_M4L_HOST = "127.0.0.1"
AGENT_M4L_PORT = 17655
AGENT_M4L_PORT_SPAN = 30000
AGENT_M4L_MAX_UDP_BYTES = 8192
AGENT_M4L_RECOVERY_PATCH_KEYS = (
    "objects", "connections", "ui_bindings", "bindings", "webui", "webuis",
    "device_width", "devicewidth", "width", "device_height", "deviceheight", "height",
)


class _EncodedResult(list):
    pass


def _temp_file(name):
    root = os.environ.get("ABLETON_MCP_STATE_DIR") or os.path.join(os.path.expanduser("~"), ".ableton-live-mcp")
    try:
        os.makedirs(root)
    except OSError:
        pass
    return os.path.join(root, name)


class AbletonLiveMCP(ControlSurface):
    def __init__(self, c_instance):
        ControlSurface.__init__(self, c_instance)
        self._objects = {}
        self._listeners = {}
        self._events = []
        self._server = None
        self._running = True
        self._main_thread_id = threading.current_thread().ident
        self._handler_slots = threading.BoundedSemaphore(16)
        with self.component_guard():
            self._start_server()

    def disconnect(self):
        self._running = False
        if self._server:
            try:
                self._server.close()
            except Exception:
                pass
        self._remove_all_listeners()
        ControlSurface.disconnect(self)

    def _start_server(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((HOST, PORT))
        sock.listen(8)
        self._server = sock
        thread = threading.Thread(target=self._accept_loop)
        thread.daemon = True
        thread.start()
        self.log_message("Ableton_Live_MCP listening on %s:%s" % (HOST, PORT))

    def _accept_loop(self):
        while self._running:
            try:
                client, _addr = self._server.accept()
                thread = threading.Thread(target=self._handle_client, args=(client,))
                thread.daemon = True
                thread.start()
            except Exception:
                if self._running:
                    self.log_message("Ableton_Live_MCP accept error: %s" % traceback.format_exc())

    def _handle_client(self, client):
        acquired = self._handler_slots.acquire(False)
        if not acquired:
            try:
                err = {"jsonrpc": "2.0", "id": None, "error": {"code": -32000, "message": "Too many concurrent Ableton MCP requests"}}
                client.sendall((json.dumps(err, separators=(",", ":")) + "\n").encode("utf-8"))
            finally:
                try:
                    client.close()
                except Exception:
                    pass
            return
        try:
            client.settimeout(CLIENT_TIMEOUT)
            buffer = b""
            while self._running:
                data, buffer = self._read_line(client, buffer)
                if not data:
                    break
                request = json.loads(data.decode("utf-8"))
                response = self._dispatch(request)
                client.sendall((json.dumps(response, separators=(",", ":")) + "\n").encode("utf-8"))
        except socket.timeout:
            pass
        except Exception as exc:
            err = {"jsonrpc": "2.0", "id": None, "error": {"code": -32000, "message": str(exc)}}
            try:
                client.sendall((json.dumps(err, separators=(",", ":")) + "\n").encode("utf-8"))
            except Exception:
                pass
        finally:
            try:
                client.close()
            except Exception:
                pass
            self._handler_slots.release()

    def _read_line(self, client, buffer):
        chunks = [buffer] if buffer else []
        total = len(buffer)
        if b"\n" in buffer:
            line, remainder = buffer.split(b"\n", 1)
            return line, remainder
        while True:
            chunk = client.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_REQUEST_BYTES:
                raise ValueError("Request exceeds maximum size")
            if b"\n" in chunk:
                break
        data = b"".join(chunks)
        if b"\n" in data:
            line, remainder = data.split(b"\n", 1)
            return line, remainder
        return data, b""

    def _dispatch(self, request):
        req_id = request.get("id")
        method = request.get("method")
        params = request.get("params") or {}
        try:
            result = self._run_on_main(method, params)
            return {"jsonrpc": "2.0", "id": req_id, "result": result}
        except Exception as exc:
            error = {"code": -32000, "message": str(exc)}
            if params.get("include_traceback"):
                error["data"] = traceback.format_exc()
            return {"jsonrpc": "2.0", "id": req_id, "error": error}

    def _run_on_main(self, method, params):
        if threading.current_thread().ident == self._main_thread_id:
            self._check_expected_set_signature(params)
            value = getattr(self, "_rpc_" + method)(params)
            if isinstance(value, _EncodedResult):
                return value
            return self._encode(value, self._encode_options(params))
        done = threading.Event()
        result = {"value": None, "error": None}
        abandoned = {"value": False}

        def invoke():
            if abandoned["value"]:
                done.set()
                return
            try:
                self._check_expected_set_signature(params)
                value = getattr(self, "_rpc_" + method)(params)
                if isinstance(value, _EncodedResult):
                    result["value"] = value
                else:
                    result["value"] = self._encode(value, self._encode_options(params))
            except Exception:
                result["error"] = sys.exc_info()
            finally:
                done.set()

        self.schedule_message(0, invoke)
        timeout = self._main_thread_timeout(params)
        if not done.wait(timeout):
            abandoned["value"] = True
            raise RuntimeError("Timed out waiting for Live main thread")
        if result["error"]:
            exc_type, exc, tb = result["error"]
            raise exc.with_traceback(tb)
        return result["value"]

    def _main_thread_timeout(self, params):
        timeout = float(params.get("timeout") or DEFAULT_MAIN_THREAD_TIMEOUT)
        if not (params.get("strict_timeout") or params.get("timeout_strict")):
            timeout = max(timeout, float(DEFAULT_MAIN_THREAD_TIMEOUT))
        return timeout

    def _rpc_ping(self, _params):
        app = Live.Application.get_application()
        version = app.get_version_string() if hasattr(app, "get_version_string") else "unknown"
        return {"ok": True, "version": version, "major": self._major_version(version), "remote_script": self._remote_script_info()}

    def _remote_script_info(self):
        path = globals().get("__file__", "")
        info = {"path": str(path)}
        try:
            with open(path, "rb") as handle:
                info["bridge_sha256"] = hashlib.sha256(handle.read()).hexdigest()
        except Exception as exc:
            info["hash_error"] = str(exc)
        return info

    def _rpc_agent_audio_tap(self, params):
        command = params.get("command")
        if command not in ("open", "start", "stop", "status"):
            raise ValueError("command must be open, start, stop, or status")
        path = params.get("path")
        if command == "open" and not path:
            raise ValueError("path is required for open")
        args = [command]
        if path:
            args.append(path)
        command_id = params.get("id") or hashlib.sha1(json.dumps({"command": command, "path": path}, sort_keys=True).encode("utf-8")).hexdigest()
        command_file = params.get("command_file") or _temp_file("agent_audio_tap_command.json")
        with open(command_file, "w") as handle:
            json.dump({"id": command_id, "command": command, "path": path}, handle, separators=(",", ":"))
        sent = False
        payload_size = 0
        if params.get("udp", False):
            payload = self._osc_message("/agent_audio_tap", args)
            payload_size = len(payload)
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.sendto(payload, (AGENT_AUDIO_TAP_HOST, int(params.get("port") or AGENT_AUDIO_TAP_PORT)))
                sent = True
            finally:
                sock.close()
        return {"sent": sent, "command": command, "path": path, "bytes": payload_size, "command_file": command_file}

    def _rpc_agent_audio_tap_setup(self, params):
        song = self.song()
        placement = params.get("placement") or "master"
        if placement == "master":
            target_track = song.master_track
        elif params.get("target_track"):
            target_track = self._resolve(params.get("target_track"))
        else:
            raise ValueError("target_track is required unless placement is master")

        if params.get("remove_existing"):
            self._delete_named_devices("AgentAudioTap")

        loaded = False
        if not self._track_has_device(target_track, "AgentAudioTap"):
            item = self._find_browser_item_named("AgentAudioTap")
            if item is None:
                raise KeyError("Could not find AgentAudioTap in the Live browser; install/build the M4L device first")
            song.view.selected_track = target_track
            Live.Application.get_application().browser.load_item(item)
            loaded = True

        solo_ref = params.get("solo_track")
        if solo_ref:
            solo_track = self._resolve(solo_ref)
            if params.get("exclusive_solo", True):
                for track in song.tracks:
                    try:
                        track.solo = track is solo_track
                    except Exception:
                        pass
            else:
                solo_track.solo = True

        if params.get("stop", True):
            self._stop_transport(song)
        if params.get("reset_time") is not None:
            self._seek_song(song, float(params.get("reset_time")))

        return {
            "target_track": getattr(target_track, "name", ""),
            "loaded": loaded,
            "devices": [getattr(device, "name", "") for device in getattr(target_track, "devices", [])],
            "time": getattr(song, "current_song_time", None),
            "playing": bool(getattr(song, "is_playing", False)),
        }

    def _rpc_agent_m4l_device(self, params):
        instance_id = self._agent_m4l_slug(params.get("instance_id") or params.get("name") or "device")
        role = self._agent_m4l_role(params.get("role"))
        device_slug = self._agent_m4l_slug(params.get("name")) if params.get("name") else instance_id
        device_name = str(params.get("device_name") or ("AgentM4L_%s_%s" % (role, device_slug)))
        command_file = str(params.get("command_file") or _temp_file("agent_m4l_%s.json" % instance_id))
        status_file = str(params.get("status_file") or _temp_file("agent_m4l_%s_status.json" % instance_id))
        command = params.get("command") or ("set" if params.get("values") or params.get("parameters") else ("update" if params.get("patch") or params.get("spec") or params.get("webui") or params.get("webuis") else "status"))
        patch = params.get("patch") or params.get("spec")
        if patch is None and (params.get("webui") or params.get("webuis")):
            patch = {}
        if patch is not None and params.get("webui"):
            patch = dict(patch)
            patch["webui"] = params.get("webui")
        if patch is not None and params.get("webuis"):
            patch = dict(patch)
            patch["webuis"] = params.get("webuis")
        if patch is not None and params.get("device_width") is not None:
            patch = dict(patch)
            patch.setdefault("device_width", params.get("device_width"))
        if patch is not None and params.get("device_height") is not None:
            patch = dict(patch)
            patch.setdefault("device_height", params.get("device_height"))
        if patch is None and command in ("set", "status"):
            patch = self._agent_m4l_recovery_patch(command_file)
        command_hash = {
            "command": command,
            "instance_id": instance_id,
            "patch": patch,
            "values": params.get("values"),
            "parameters": params.get("parameters"),
            "webui": params.get("webui"),
            "webuis": params.get("webuis"),
        }
        if not params.get("id"):
            command_hash["nonce"] = time.time()
        command_id = params.get("id") or hashlib.sha1(json.dumps(command_hash, sort_keys=True).encode("utf-8")).hexdigest()
        payload = {
            "id": command_id,
            "command": command,
            "role": role,
            "instance_id": instance_id,
            "patch": patch,
            "values": params.get("values"),
            "parameters": params.get("parameters"),
            "webui": params.get("webui"),
            "webuis": params.get("webuis"),
        }
        write_command_file = params.get("write_command_file")
        if write_command_file is None:
            write_command_file = True
        if write_command_file:
            with open(command_file, "w") as handle:
                json.dump(payload, handle, separators=(",", ":"))
            self._agent_m4l_write_recovery_patch(command_file, patch)

        sent = False
        udp_bytes = 0
        udp_skipped = False
        if params.get("udp", True):
            port = int(params.get("port") or self._agent_m4l_port(instance_id))
            raw = json.dumps(self._agent_m4l_udp_payload(payload), separators=(",", ":"))
            message = self._osc_message("/agent_m4l", [instance_id, raw])
            udp_bytes = len(message)
            if udp_bytes <= AGENT_M4L_MAX_UDP_BYTES:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                try:
                    try:
                        sock.sendto(message, (AGENT_M4L_HOST, port))
                        sent = True
                    except OSError:
                        sent = False
                finally:
                    sock.close()
            else:
                udp_skipped = True
        else:
            port = int(params.get("port") or self._agent_m4l_port(instance_id))

        loaded = False
        load_error = None
        target_track = None
        if params.get("target_track"):
            target_track = self._resolve(params.get("target_track"))
        elif params.get("ref"):
            target_track = self._resolve(params.get("ref"))
        if target_track is not None and params.get("load", True):
            try:
                if not self._track_has_device(target_track, device_name, role):
                    loaded = self._load_agent_m4l_device(target_track, device_name, role, params)
            except Exception as exc:
                load_error = str(exc)
        triggered = False
        if target_track is not None:
            target_device = self._find_track_device(target_track, device_name, role)
            if target_device is not None:
                triggered = self._trigger_agent_m4l_poll(target_device)

        result = {
            "sent": sent,
            "command": command,
            "role": role,
            "instance_id": instance_id,
            "device_name": device_name,
            "command_id": command_id,
            "command_file": command_file,
            "command_file_written": bool(write_command_file),
            "status_file": status_file,
            "port": port,
            "loaded": loaded,
            "udp_bytes": udp_bytes,
            "udp_skipped": udp_skipped,
            "triggered": triggered,
        }
        if load_error:
            result["load_error"] = load_error
        if target_track is not None:
            result["track"] = self._track_summary(target_track, None, 0, 16, 0)
        return result

    def _load_agent_m4l_device(self, target_track, device_name, role, params):
        errors = []
        if hasattr(target_track, "insert_device") and not params.get("prefer_browser_load"):
            try:
                self._insert_track_device(target_track, device_name, role, params)
                return True
            except Exception as exc:
                errors.append(str(exc))
        item = self._find_browser_item_named(device_name)
        if item is not None:
            before_devices = list(getattr(target_track, "devices", []))
            before_ids = set([self._object_id(device) for device in before_devices])
            self.song().view.selected_track = target_track
            Live.Application.get_application().browser.load_item(item)
            self._rename_new_track_device(target_track, before_ids, device_name)
            return True
        if hasattr(target_track, "insert_device") and params.get("prefer_browser_load"):
            try:
                self._insert_track_device(target_track, device_name, role, params)
                return True
            except Exception as exc:
                errors.append(str(exc))
        detail = "; ".join([error for error in errors if error])
        if detail:
            raise RuntimeError(detail)
        raise KeyError("Could not find %s in the Live browser; build/install it with live_agent_m4l_device first or reload Live's browser" % device_name)

    def _insert_track_device(self, target_track, device_name, role, params):
        before_devices = list(getattr(target_track, "devices", []))
        before_ids = set([self._object_id(device) for device in before_devices])
        target_track.insert_device(device_name, self._agent_m4l_insert_index(target_track, role, params))
        self._rename_new_track_device(target_track, before_ids, device_name)

    def _rename_new_track_device(self, target_track, before_ids, device_name):
        new_device = self._find_new_track_device(target_track, before_ids)
        if new_device is not None:
            try:
                new_device.name = device_name
            except Exception:
                pass

    def _agent_m4l_insert_index(self, track, role, params):
        if params.get("device_index") is not None:
            return int(params.get("device_index"))
        devices = list(getattr(track, "devices", []))
        if role == "midi_effect":
            for index, device in enumerate(devices):
                if self._device_class_name(device) in ("MxDeviceInstrument", "MxDeviceAudioEffect"):
                    return index
        if role == "instrument":
            for index, device in enumerate(devices):
                if self._device_class_name(device) == "MxDeviceAudioEffect":
                    return index
        return -1

    def _device_class_name(self, device):
        try:
            return str(device.class_name)
        except Exception:
            return ""

    def _agent_m4l_port(self, instance_id):
        digest = hashlib.sha1(self._agent_m4l_slug(instance_id).encode("utf-8")).hexdigest()
        return AGENT_M4L_PORT + (int(digest[:8], 16) % AGENT_M4L_PORT_SPAN)

    def _agent_m4l_udp_payload(self, payload):
        command = payload.get("command")
        if command not in ("set", "status"):
            return payload
        slim = dict(payload)
        for key in ("patch", "spec", "webui", "webuis"):
            if key in slim:
                del slim[key]
        return slim

    def _agent_m4l_recovery_patch(self, command_file):
        try:
            with open(command_file, "r") as handle:
                existing = json.load(handle)
        except Exception:
            existing = {}
        if not isinstance(existing, dict):
            existing = {}
        patch = existing.get("patch") or existing.get("spec")
        if patch is not None:
            return patch
        recovered = {}
        for key in AGENT_M4L_RECOVERY_PATCH_KEYS:
            if key in existing:
                recovered[key] = existing[key]
        return recovered or self._agent_m4l_sidecar_recovery_patch(command_file)

    def _agent_m4l_sidecar_recovery_patch(self, command_file):
        try:
            with open(self._agent_m4l_recovery_file(command_file), "r") as handle:
                existing = json.load(handle)
        except Exception:
            return None
        if not isinstance(existing, dict):
            return None
        return existing.get("patch") or existing.get("spec")

    def _agent_m4l_write_recovery_patch(self, command_file, patch):
        if patch is None:
            return
        try:
            with open(self._agent_m4l_recovery_file(command_file), "w") as handle:
                json.dump({"patch": patch}, handle, separators=(",", ":"))
        except Exception:
            pass

    def _agent_m4l_recovery_file(self, command_file):
        return "%s.recovery.json" % command_file

    def _rpc_transport(self, params):
        song = self.song()
        if params.get("time") is not None:
            self._seek_song(song, float(params["time"]))
        action = params.get("action")
        if action == "play":
            self._start_transport(song)
        elif action == "continue":
            song.continue_playing()
            if not getattr(song, "is_playing", False):
                self._start_transport(song)
        elif action == "stop":
            self._stop_transport(song)
        elif action not in (None, "status"):
            raise ValueError("action must be play, continue, stop, or status")
        return {"playing": bool(getattr(song, "is_playing", False)), "time": getattr(song, "current_song_time", None)}

    def _osc_message(self, address, args):
        def pad(value):
            data = value.encode("utf-8") + b"\x00"
            return data + (b"\x00" * ((4 - (len(data) % 4)) % 4))

        tags = "," + ("s" * len(args))
        payload = pad(address) + pad(tags)
        for arg in args:
            payload += pad(str(arg))
        return payload

    def _seek_song(self, song, time_value):
        current = float(getattr(song, "current_song_time", 0.0))
        song.jump_by(time_value - current)

    def _start_transport(self, song):
        song.start_playing()
        if not getattr(song, "is_playing", False):
            try:
                song.continue_playing()
            except Exception:
                pass
        if not getattr(song, "is_playing", False):
            song.start_playing()

    def _stop_transport(self, song):
        song.stop_playing()
        if getattr(song, "is_playing", False):
            song.stop_playing()

    def _track_has_device(self, track, name, role=None):
        return self._find_track_device(track, name, role) is not None

    def _find_track_device(self, track, name, role=None):
        for device in getattr(track, "devices", []):
            if getattr(device, "name", "") == name and self._device_matches_agent_m4l_role(device, role):
                return device
        return None

    def _trigger_agent_m4l_poll(self, device):
        poll_names = ("Agent Poll", "Agent M4L Poll", "command-trigger")
        for parameter in getattr(device, "parameters", []):
            name = str(getattr(parameter, "name", ""))
            if name not in poll_names:
                continue
            try:
                current = float(getattr(parameter, "value", 0.0) or 0.0)
            except Exception:
                current = 0.0
            try:
                minimum = float(getattr(parameter, "min", 0.0))
            except Exception:
                minimum = 0.0
            try:
                maximum = float(getattr(parameter, "max", 1.0))
            except Exception:
                maximum = 1.0
            next_value = current + 1.0
            if next_value > maximum:
                next_value = minimum
            try:
                parameter.value = next_value
                return True
            except Exception:
                return False
        return False

    def _find_new_track_device(self, track, before_ids):
        for device in getattr(track, "devices", []):
            try:
                obj_id = self._object_id(device)
            except Exception:
                obj_id = id(device)
            if obj_id not in before_ids:
                return device
        return None

    def _device_matches_agent_m4l_role(self, device, role):
        if role is None:
            return True
        expected = {
            "audio_effect": "MxDeviceAudioEffect",
            "instrument": "MxDeviceInstrument",
            "midi_effect": "MxDeviceMidiEffect",
        }.get(role)
        if expected is None:
            return True
        try:
            class_name = device.class_name
        except Exception:
            return True
        return class_name == expected

    def _agent_m4l_role(self, role):
        value = str(role or "audio_effect").lower().replace("-", "_").replace(" ", "_")
        aliases = {"audio": "audio_effect", "effect": "audio_effect", "fx": "audio_effect", "synth": "instrument", "midi": "midi_effect"}
        value = aliases.get(value, value)
        if value not in ("audio_effect", "instrument", "midi_effect"):
            raise ValueError("role must be audio_effect, instrument, or midi_effect")
        return value

    def _agent_m4l_slug(self, value):
        text = str(value or "device")
        result = []
        for char in text:
            if char.isalnum() or char in "_.-":
                result.append(char)
            else:
                result.append("_")
        slug = "".join(result).strip("._-")
        return slug[:80] or "device"

    def _delete_named_devices(self, name):
        song = self.song()
        tracks = list(song.tracks) + list(song.return_tracks) + [song.master_track]
        for track in tracks:
            devices = getattr(track, "devices", [])
            for index in range(len(devices) - 1, -1, -1):
                if getattr(devices[index], "name", "") != name:
                    continue
                if hasattr(track, "delete_device"):
                    track.delete_device(index)
                else:
                    del devices[index]

    def _find_browser_item_named(self, name):
        browser = Live.Application.get_application().browser

        def children_of(item):
            try:
                return item.iter_children
            except Exception:
                return ()

        def walk(item, depth):
            if getattr(item, "name", "") == name and bool(getattr(item, "is_loadable", False)):
                return item
            if depth >= DEFAULT_MAX_DEPTH:
                return None
            for child in children_of(item):
                found = walk(child, depth + 1)
                if found is not None:
                    return found
            return None

        for root_name in ("user_library", "audio_effects", "max_for_live"):
            if not hasattr(browser, root_name):
                continue
            root = getattr(browser, root_name)
            found = walk(root, 0)
            if found is not None:
                return found
        return None

    def _rpc_set_summary(self, params):
        song = self.song()
        track_limit = int(params.get("track_limit") if params.get("track_limit") is not None else 64)
        clip_slot_limit = int(params.get("clip_slot_limit") if params.get("clip_slot_limit") is not None else 16)
        device_limit = int(params.get("device_limit") if params.get("device_limit") is not None else 16)
        arrangement_clip_limit = int(params.get("arrangement_clip_limit") if params.get("arrangement_clip_limit") is not None else 0)
        track_query = (params.get("track_query") or "").strip().lower()
        include_returns = params.get("include_return_tracks")
        if include_returns is None:
            include_returns = True
        include_master = params.get("include_master_track")
        if include_master is None:
            include_master = True
        tracks = []
        tracks_scanned = 0
        for index, track in enumerate(song.tracks):
            tracks_scanned += 1
            if track_query and track_query not in getattr(track, "name", "").lower():
                continue
            if track_limit >= 0 and len(tracks) >= track_limit:
                tracks.append({"truncated": True})
                break
            tracks.append(self._track_summary(track, index, clip_slot_limit, device_limit, arrangement_clip_limit))
        returns = []
        if include_returns:
            for index, track in enumerate(song.return_tracks):
                if track_query and track_query not in getattr(track, "name", "").lower():
                    continue
                returns.append(self._track_summary(track, index, clip_slot_limit, device_limit, 0))
        result = {
            "tempo": song.tempo,
            "signature_numerator": song.signature_numerator,
            "signature_denominator": song.signature_denominator,
            "current_song_time": song.current_song_time,
            "set_signature": self._set_signature(),
            "tracks": tracks,
            "tracks_scanned": tracks_scanned,
            "return_tracks": returns,
            "scene_count": len(song.scenes),
        }
        if include_master:
            result["master_track"] = self._track_summary(song.master_track, None, 0, device_limit, 0)
        return result

    def _rpc_get(self, params):
        obj = self._resolve(params.get("ref"))
        props = {}
        for name in params.get("properties") or []:
            props[name] = getattr(obj, name)
        children = {}
        detail = self._detail(params)
        child_specs = params.get("children") or []
        if isinstance(child_specs, dict):
            child_items = child_specs.items()
        else:
            child_items = [(name, params.get("child_limit")) for name in child_specs]
        for name, limit in child_items:
            values, truncated = self._take(getattr(obj, name), limit)
            children[name] = [self._object_summary(child, detail) for child in values]
            if truncated:
                children[name].append({"truncated": True})
        summary = self._object_summary(obj, detail)
        summary["properties"] = props
        summary["children"] = children
        return summary

    def _rpc_set(self, params):
        obj = self._resolve(params.get("ref"))
        setattr(obj, params["property"], params.get("value"))
        return self._object_summary(obj, self._detail(params))

    def _rpc_call(self, params):
        obj = self._resolve(params.get("ref"))
        fn = getattr(obj, params["method"])
        return fn(*(params.get("args") or []), **(params.get("kwargs") or {}))

    def _rpc_children(self, params):
        obj = self._resolve(params.get("ref"))
        limit = params.get("limit")
        values, truncated = self._take(getattr(obj, params["child"]), limit)
        result = [self._object_summary(child, self._detail(params)) for child in values]
        if truncated:
            result.append({"truncated": True})
        return result

    def _rpc_device_parameters(self, params):
        device = self._resolve(params.get("ref"))
        query = (params.get("query") or "").strip().lower()
        terms = [term for term in query.split() if term]
        limit = params.get("limit")
        result = []
        max_matches = DEFAULT_CHILD_LIMIT if limit is None else limit
        unlimited = max_matches is not None and max_matches < 0
        truncated = False
        for param in getattr(device, "parameters"):
            name = getattr(param, "name", "")
            if terms and not all(term in name.lower() for term in terms):
                continue
            if not unlimited and len(result) >= max_matches:
                truncated = True
                break
            item = self._parameter_summary(param)
            result.append(item)
        if truncated:
            result.append({"truncated": True})
        return result

    def _rpc_parameter_set(self, params):
        param = self._resolve(params.get("ref"))
        before = self._parameter_summary(param)
        if not hasattr(param, "value"):
            raise AttributeError("Object has no value property")
        value = params.get("value")
        if value is None:
            raise ValueError("value is required")
        min_value = before.get("min")
        max_value = before.get("max")
        if params.get("coerce"):
            if min_value is not None and value < min_value:
                value = min_value
            if max_value is not None and value > max_value:
                value = max_value
            if before.get("is_quantized"):
                value = int(round(value))
        else:
            if min_value is not None and value < min_value:
                raise ValueError("value %s is below parameter min %s" % (value, min_value))
            if max_value is not None and value > max_value:
                raise ValueError("value %s is above parameter max %s" % (value, max_value))
            if before.get("is_quantized") and int(value) != value:
                raise ValueError("quantized parameter requires an integer value or coerce:true")
        param.value = value
        after = self._parameter_summary(param)
        return {
            "parameter": after,
            "before": before,
            "requested_value": params.get("value"),
            "applied_value": after.get("value"),
            "changed": after.get("value") != before.get("value"),
        }

    def _rpc_clip_notes(self, params):
        clip = self._resolve(params.get("ref"))
        limit = int(params.get("limit") if params.get("limit") is not None else 512)
        start = params.get("start_time")
        end = params.get("end_time")
        notes = list(clip.get_all_notes_extended())
        if start is not None:
            notes = [note for note in notes if note.start_time >= float(start)]
        if end is not None:
            notes = [note for note in notes if note.start_time < float(end)]
        total = len(notes)
        truncated = False
        if limit >= 0 and len(notes) > limit:
            notes = notes[:limit]
            truncated = True
        return {
            "clip": self._clip_summary(clip, None),
            "note_count": total,
            "truncated": truncated,
            "notes": [self._note_summary(note) for note in notes],
        }

    def _rpc_clip_update_notes(self, params):
        clip = self._resolve(params.get("ref"))
        updates = params.get("updates") or []
        notes = clip.get_all_notes_extended()
        by_id = dict((note.note_id, note) for note in notes)
        changed = []
        for update in updates:
            note_id = update.get("note_id")
            if note_id not in by_id:
                raise KeyError("Unknown note_id %s in clip %s" % (note_id, getattr(clip, "name", "")))
            note = by_id[note_id]
            for attr in ("pitch", "start_time", "duration", "velocity", "mute", "probability", "velocity_deviation", "release_velocity"):
                if attr in update:
                    setattr(note, attr, update[attr])
            changed.append(note)
        clip.apply_note_modifications(notes)
        return {
            "clip": self._clip_summary(clip, None),
            "updated": len(changed),
            "notes": [self._note_summary(note) for note in changed],
        }

    def _rpc_clip_add_notes(self, params):
        target = self._resolve(params.get("ref"))
        created_clip = False
        launched = False
        if hasattr(target, "has_clip") and hasattr(target, "clip"):
            if not getattr(target, "has_clip", False):
                length = params.get("create_clip_length")
                if length is None:
                    raise ValueError("clip_add_notes requires create_clip_length when ref is an empty clip slot")
                target.create_clip(float(length))
                created_clip = True
            clip = target.clip
        else:
            clip = target
        if not getattr(clip, "is_midi_clip", False):
            raise ValueError("clip_add_notes requires a MIDI clip")
        if params.get("clip_name") is not None:
            try:
                clip.name = str(params.get("clip_name"))
            except Exception:
                pass
        for key, attr in (("loop_start", "loop_start"), ("loop_end", "loop_end")):
            if params.get(key) is not None:
                try:
                    setattr(clip, attr, float(params.get(key)))
                except Exception:
                    pass
        if params.get("clear"):
            try:
                clip.remove_notes_extended(from_pitch=0, pitch_span=128, from_time=0.0, time_span=float(getattr(clip, "length", 0.0)))
            except TypeError:
                clip.remove_notes(0.0, 0, float(getattr(clip, "length", 0.0)), 128)
        clear_range = params.get("clear_range")
        if clear_range:
            try:
                clip.remove_notes_extended(
                    from_pitch=int(clear_range["from_pitch"]),
                    pitch_span=int(clear_range["pitch_span"]),
                    from_time=float(clear_range["from_time"]),
                    time_span=float(clear_range["time_span"]),
                )
            except TypeError:
                clip.remove_notes(
                    float(clear_range["from_time"]),
                    int(clear_range["from_pitch"]),
                    float(clear_range["time_span"]),
                    int(clear_range["pitch_span"]),
                )
        specs = []
        for note in params.get("notes") or []:
            specs.append(Live.Clip.MidiNoteSpecification(
                pitch=int(note["pitch"]),
                start_time=float(note["start_time"]),
                duration=float(note["duration"]),
                velocity=float(note["velocity"]),
                mute=bool(note.get("mute", False)),
            ))
        if specs:
            clip.add_new_notes(tuple(specs))
        if params.get("fire") and hasattr(target, "fire"):
            target.fire()
            launched = True
        return {
            "clip": self._clip_summary(clip, None),
            "added": len(specs),
            "created_clip": created_clip,
            "launched": launched,
            "note_count": len(list(clip.get_all_notes_extended())),
        }

    def _rpc_clip_duplicate_to_arrangement(self, params):
        track = self._resolve(params.get("track"))
        clip = self._resolve(params.get("clip"))
        track.duplicate_clip_to_arrangement(clip, float(params["destination_time"]))
        return {
            "track": self._track_summary(track, None, 0, 0, 8),
            "clip": self._clip_summary(clip, None),
            "destination_time": float(params["destination_time"]),
        }

    def _rpc_clip_envelope(self, params):
        clip = self._resolve(params.get("ref"))
        parameter = self._resolve(params.get("parameter"))
        envelope = clip.automation_envelope(parameter)
        steps = params.get("insert_steps") or []
        if envelope is None and (params.get("create") or steps):
            envelope = clip.create_automation_envelope(parameter)
        if params.get("clear"):
            clip.clear_envelope(parameter)
            envelope = None
        if envelope is not None:
            delete_range = params.get("delete_range")
            if delete_range:
                envelope.delete_events_in_range(float(delete_range["start_time"]), float(delete_range["end_time"]))
            for step in steps:
                envelope.insert_step(float(step["time"]), float(step["duration"]), float(step["value"]))
        start = float(params.get("start_time") if params.get("start_time") is not None else 0.0)
        end = params.get("end_time")
        if end is None:
            end = getattr(clip, "length", start + 16.0)
        end = float(end)
        limit = int(params.get("limit") if params.get("limit") is not None else 128)
        events = []
        truncated = False
        total = 0
        if envelope is not None:
            all_events = list(envelope.events_in_range(start, end))
            total = len(all_events)
            if limit >= 0 and len(all_events) > limit:
                all_events = all_events[:limit]
                truncated = True
            events = [self._automation_event_summary(event) for event in all_events]
        return {
            "clip": self._clip_summary(clip, None),
            "parameter": self._parameter_summary(parameter),
            "has_envelope": envelope is not None,
            "event_count": total,
            "truncated": truncated,
            "events": events,
        }

    def _rpc_clip_velocity_envelope(self, params):
        clip = self._resolve(params.get("ref"))
        parameter = self._resolve(params.get("parameter"))
        if not getattr(clip, "is_midi_clip", False):
            raise ValueError("clip_velocity_envelope requires a MIDI clip")
        start = float(params.get("start_time") if params.get("start_time") is not None else 0.0)
        end = params.get("end_time")
        if end is None:
            end = getattr(clip, "length", start + 16.0)
        end = float(end)
        minimum = params.get("min_value")
        maximum = params.get("max_value")
        summary = self._parameter_summary(parameter)
        if minimum is None:
            minimum = summary.get("min", 0.0)
        if maximum is None:
            maximum = summary.get("max", 1.0)
        minimum = float(minimum)
        maximum = float(maximum)
        if params.get("invert"):
            minimum, maximum = maximum, minimum
        envelope = clip.automation_envelope(parameter)
        if envelope is None:
            envelope = clip.create_automation_envelope(parameter)
        if params.get("clear", True):
            envelope.delete_events_in_range(start, end)
        notes = [note for note in clip.get_all_notes_extended() if start <= note.start_time < end]
        for note in notes:
            velocity = float(getattr(note, "velocity", 0.0))
            normalized = max(0.0, min(1.0, velocity / 127.0))
            value = minimum + (maximum - minimum) * normalized
            duration = float(params.get("step_duration") if params.get("step_duration") is not None else getattr(note, "duration", 0.125))
            envelope.insert_step(float(note.start_time), duration, value)
        all_events = list(envelope.events_in_range(start, end))
        limit = int(params.get("limit") if params.get("limit") is not None else 128)
        truncated = False
        if limit >= 0 and len(all_events) > limit:
            all_events = all_events[:limit]
            truncated = True
        return {
            "clip": self._clip_summary(clip, None),
            "parameter": self._parameter_summary(parameter),
            "notes_mapped": len(notes),
            "event_count": len(list(envelope.events_in_range(start, end))),
            "truncated": truncated,
            "events": [self._automation_event_summary(event) for event in all_events],
        }

    def _rpc_clip_warp_markers(self, params):
        clip = self._resolve(params.get("ref"))
        if "warping" in params:
            clip.warping = bool(params.get("warping"))
        if params.get("warp_mode") is not None:
            clip.warp_mode = int(params.get("warp_mode"))
        for beat_time in params.get("remove_beat_times") or []:
            clip.remove_warp_marker(float(beat_time))
        for move in params.get("move_markers") or []:
            clip.move_warp_marker(float(move["beat_time"]), float(move["beat_time_delta"]))
        for marker in params.get("add_markers") or []:
            clip.add_warp_marker(Live.Clip.WarpMarker(float(marker["sample_time"]), float(marker["beat_time"])))
        limit = int(params.get("limit") if params.get("limit") is not None else 128)
        markers = list(getattr(clip, "warp_markers", []))
        total = len(markers)
        truncated = False
        if limit >= 0 and len(markers) > limit:
            markers = markers[:limit]
            truncated = True
        return {
            "clip": self._clip_summary(clip, None),
            "warping": getattr(clip, "warping", None),
            "warp_mode": getattr(clip, "warp_mode", None),
            "available_warp_modes": list(getattr(clip, "available_warp_modes", [])),
            "marker_count": total,
            "truncated": truncated,
            "markers": [self._warp_marker_summary(marker) for marker in markers],
        }

    def _rpc_track_create_audio_clip(self, params):
        track = self._resolve(params.get("ref"))
        clip = track.create_audio_clip(str(params["file_path"]), float(params["destination_time"]))
        if params.get("name"):
            clip.name = params.get("name")
        return {
            "track": self._track_summary(track, None, 0, 0, 8),
            "clip": self._clip_summary(clip, None),
            "destination_time": float(params["destination_time"]),
        }

    def _rpc_track_insert_device(self, params):
        track = self._resolve(params.get("ref"))
        index = int(params.get("device_index") if params.get("device_index") is not None else -1)
        before = len(getattr(track, "devices", []))
        result = track.insert_device(str(params["device_name"]), index)
        after = len(getattr(track, "devices", []))
        return {
            "track": self._track_summary(track, None, 0, 16, 0),
            "device_name": str(params["device_name"]),
            "device_index": index,
            "inserted": after > before,
            "result": result,
        }

    def _rpc_batch(self, params):
        results = []
        continue_on_error = bool(params.get("continue_on_error"))
        inherited = {}
        for name in ("detail", "include_repr", "max_items", "max_depth", "max_string_length", "timeout", "strict_timeout", "timeout_strict", "expected_set_signature"):
            if params.get(name) is not None:
                inherited[name] = params.get(name)
        for index, op in enumerate(params.get("operations") or []):
            method = op.get("method")
            op_params = op.get("params") or {}
            for name, value in inherited.items():
                if op_params.get(name) is None:
                    op_params[name] = value
            try:
                value = getattr(self, "_rpc_" + method)(op_params)
                results.append({"ok": True, "result": self._encode(value, self._encode_options(op_params))})
            except Exception as exc:
                item = {"ok": False, "index": index, "method": method, "error": str(exc)}
                if params.get("include_traceback"):
                    item["traceback"] = traceback.format_exc()
                results.append(item)
                if not continue_on_error:
                    break
        return _EncodedResult(results)

    def _rpc_browser_roots(self, _params):
        browser = Live.Application.get_application().browser
        roots = []
        for name in DEFAULT_BROWSER_ROOTS:
            if hasattr(browser, name):
                root = getattr(browser, name)
                roots.append({"name": name, "kind": root.__class__.__name__})
        return roots

    def _rpc_browser_capabilities(self, _params):
        browser = Live.Application.get_application().browser
        roots = self._rpc_browser_roots({})
        filter_types = {}
        try:
            for name, value in Live.Browser.FilterType.names.items():
                filter_types[str(name)] = int(value)
        except Exception:
            pass
        attrs = [name for name in dir(browser) if not name.startswith("_")]
        semantic_terms = ("semantic", "similar", "similarity")
        semantic_attrs = [name for name in attrs if any(term in name.lower() for term in semantic_terms)]
        return {
            "roots": roots,
            "filter_type": getattr(browser, "filter_type", None),
            "filter_types": filter_types,
            "semantic_search_exposed": bool(semantic_attrs),
            "semantic_search_attrs": semantic_attrs,
            "browser_attrs": attrs,
        }

    def _rpc_browser_search(self, params):
        browser = Live.Application.get_application().browser
        query = (params.get("query") or "").strip().lower()
        terms = [term for term in query.split() if term]
        root_names = params.get("roots") or list(DEFAULT_BROWSER_ROOTS)
        limit = int(params.get("limit") or 25)
        max_depth = int(params.get("max_depth") if params.get("max_depth") is not None else 8)
        max_visited = int(params.get("max_visited") or 5000)
        loadable_only = params.get("loadable_only")
        if loadable_only is None:
            loadable_only = True
        include_folders = bool(params.get("include_folders"))
        stop_on_limit = bool(params.get("stop_on_limit"))
        stop_score = int(params.get("stop_score") if params.get("stop_score") is not None else 0)
        match_all_terms = params.get("match_all_terms")
        if match_all_terms is None:
            match_all_terms = True

        matches = []
        visited = 0
        truncated = False

        def roots_for(name):
            if not hasattr(browser, name):
                return []
            root = getattr(browser, name)
            if self._is_browser_item(root):
                try:
                    return root.iter_children
                except Exception:
                    return (root,)
            try:
                return iter(root)
            except Exception:
                return ()

        def children_of(item):
            try:
                return item.iter_children
            except Exception:
                return ()

        def is_match(item, path_text):
            if not terms:
                return True
            haystack = (getattr(item, "name", "") + " " + path_text).lower()
            if match_all_terms:
                return all(term in haystack for term in terms)
            return any(term in haystack for term in terms)

        def score(item, path_text):
            name = getattr(item, "name", "").lower()
            if query and name == query:
                return 0
            if query and query in name:
                return 1
            if terms and all(term in name for term in terms):
                return 2
            if query and query in path_text.lower():
                return 3
            return 4

        def walk(root_name, item, path, depth):
            nonlocal visited, truncated
            if truncated or visited >= max_visited:
                truncated = True
                return
            visited += 1
            name = getattr(item, "name", "")
            current_path = path + [name]
            path_text = " > ".join([part for part in current_path if part])
            is_folder = bool(getattr(item, "is_folder", False))
            is_loadable = bool(getattr(item, "is_loadable", False))
            if is_match(item, path_text):
                if (include_folders or not is_folder) and (not loadable_only or is_loadable):
                    item_score = score(item, path_text)
                    matches.append((item_score, len(current_path), self._browser_item_result(root_name, item, path_text)))
                    good_matches = matches if not terms else [match for match in matches if match[0] <= stop_score]
                    if stop_on_limit and len(good_matches) >= limit:
                        truncated = True
                        return
            if depth >= max_depth:
                return
            for child in children_of(item):
                walk(root_name, child, current_path, depth + 1)
                if truncated:
                    return

        for root_name in root_names:
            for item in roots_for(root_name):
                walk(root_name, item, [root_name], 0)
                if truncated:
                    break
            if truncated:
                break

        matches.sort(key=lambda item: (item[0], item[1], item[2]["name"].lower()))
        results = [item[2] for item in matches[:limit]]
        return {"query": query, "roots": root_names, "visited": visited, "truncated": truncated, "results": results}

    def _rpc_browser_load(self, params):
        item = self._resolve_browser_item(params.get("item"))
        target = params.get("target_track")
        if target:
            self.song().view.selected_track = self._resolve(target)
        Live.Application.get_application().browser.load_item(item)
        return self._browser_item_result(None, item, None)

    def _rpc_browser_preview(self, params):
        browser = Live.Application.get_application().browser
        if params.get("stop"):
            browser.stop_preview()
            return {"previewing": False}
        item = self._resolve_browser_item(params.get("item"))
        browser.preview_item(item)
        return {"previewing": True, "item": self._browser_item_result(None, item, None)}

    def _rpc_eval(self, params):
        ref = params.get("ref")
        obj = self._resolve(ref) if ref else None
        env = {
            "Live": Live,
            "song": self.song(),
            "app": Live.Application.get_application(),
            "obj": obj,
            "this": self,
        }
        return eval(params["expr"], env, {})

    def _rpc_exec(self, params):
        ref = params.get("ref")
        obj = self._resolve(ref) if ref else None
        env = {
            "Live": Live,
            "song": self.song(),
            "app": Live.Application.get_application(),
            "obj": obj,
            "this": self,
            "result": None,
        }
        exec(params["code"], env, env)
        return env.get("result")

    def _rpc_observe(self, params):
        obj = self._resolve(params.get("ref"))
        prop = params["property"]
        key = (self._object_id(obj), prop)
        if params.get("enabled"):
            if key in self._listeners:
                return {"observing": True, "key": str(key)}
            callback = self._make_listener(obj, prop)
            add_name = "add_%s_listener" % prop
            getattr(obj, add_name)(callback)
            self._listeners[key] = (obj, prop, callback)
            return {"observing": True, "key": str(key)}
        if key in self._listeners:
            old_obj, old_prop, callback = self._listeners.pop(key)
            remove_name = "remove_%s_listener" % old_prop
            getattr(old_obj, remove_name)(callback)
        return {"observing": False, "key": str(key)}

    def _rpc_events(self, params):
        limit = params.get("limit") or 100
        events = self._events[:limit]
        self._events = self._events[limit:]
        return events

    def _resolve(self, ref):
        ref = ref or {"path": "live_set"}
        if "id" in ref:
            obj_id = int(ref["id"])
            if obj_id not in self._objects:
                raise KeyError("Unknown or stale object id %s; rerun get/search and use the new id" % obj_id)
            return self._objects[obj_id]
        return self._resolve_path(ref.get("path") or "live_set")

    def _resolve_browser_item(self, ref):
        if not ref:
            raise ValueError("Browser item ref is required")
        if "id" in ref:
            try:
                return self._resolve(ref)
            except KeyError:
                if not (ref.get("uri") or ref.get("path")):
                    raise
        uri = ref.get("uri")
        path = ref.get("path")
        if not (uri or path):
            return self._resolve(ref)
        item = self._find_browser_item(uri=uri, path=path)
        if item is None:
            raise KeyError("Could not resolve browser item by uri/path; rerun browser_search")
        return item

    def _find_browser_item(self, uri=None, path=None):
        browser = Live.Application.get_application().browser
        wanted_path = path.lower() if path else None

        def roots_for(name):
            if not hasattr(browser, name):
                return []
            root = getattr(browser, name)
            if self._is_browser_item(root):
                try:
                    return root.iter_children
                except Exception:
                    return (root,)
            try:
                return iter(root)
            except Exception:
                return ()

        def children_of(item):
            try:
                return item.iter_children
            except Exception:
                return ()

        def walk(item, current_path, depth):
            path_text = " > ".join([part for part in current_path if part])
            if uri:
                try:
                    if item.uri == uri:
                        return item
                except Exception:
                    pass
            if wanted_path and path_text.lower() == wanted_path:
                return item
            if depth >= DEFAULT_MAX_DEPTH:
                return None
            for child in children_of(item):
                found = walk(child, current_path + [getattr(child, "name", "")], depth + 1)
                if found is not None:
                    return found
            return None

        for root_name in DEFAULT_BROWSER_ROOTS:
            for item in roots_for(root_name):
                found = walk(item, [root_name, getattr(item, "name", "")], 0)
                if found is not None:
                    return found
        return None

    def _resolve_path(self, path):
        parts = path.split()
        if not parts:
            raise ValueError("Path must start with live_set, song, app, browser, or this")
        if parts[0] in ("live_set", "song"):
            obj = self.song()
        elif parts[0] == "app":
            obj = Live.Application.get_application()
        elif parts[0] == "browser":
            obj = Live.Application.get_application().browser
        elif parts[0] == "this":
            obj = self
        else:
            raise ValueError("Path must start with live_set, song, app, browser, or this")
        index = 1
        while index < len(parts):
            attr = parts[index]
            value = getattr(obj, attr)
            index += 1
            if index < len(parts):
                token = parts[index]
                try:
                    child_index = int(token)
                except ValueError:
                    obj = value
                    continue
                obj = value[child_index]
                index += 1
            else:
                obj = value
        return obj

    def _object_summary(self, obj, detail=False):
        obj_id = self._object_id(obj)
        self._remember_object(obj_id, obj)
        summary = {
            "id": obj_id,
            "class": obj.__class__.__name__,
        }
        if detail:
            summary["canonical_path"] = self._canonical_path(obj)
            summary["repr"] = repr(obj)
        return summary

    def _browser_item_result(self, root_name, item, path_text):
        obj_id = self._object_id(item)
        self._remember_object(obj_id, item)
        result = {
            "id": obj_id,
            "name": getattr(item, "name", ""),
            "class": item.__class__.__name__,
            "is_folder": bool(getattr(item, "is_folder", False)),
            "is_loadable": bool(getattr(item, "is_loadable", False)),
            "is_device": bool(getattr(item, "is_device", False)),
        }
        if root_name is not None:
            result["root"] = root_name
        if path_text:
            result["path"] = path_text
        try:
            result["uri"] = item.uri
        except Exception:
            pass
        try:
            result["source"] = item.source
        except Exception:
            pass
        return result

    def _parameter_summary(self, param):
        obj_id = self._object_id(param)
        self._remember_object(obj_id, param)
        result = {
            "id": obj_id,
            "name": getattr(param, "name", ""),
            "value": getattr(param, "value", None),
        }
        for attr in ("min", "max", "default_value", "display_value", "is_quantized"):
            try:
                result[attr] = getattr(param, attr)
            except Exception:
                pass
        try:
            result["display"] = param.str_for_value(param.value)
        except Exception:
            pass
        try:
            if getattr(param, "is_quantized", False):
                result["value_items"] = list(param.value_items)
        except Exception:
            pass
        return result

    def _track_summary(self, track, index, clip_slot_limit, device_limit, arrangement_clip_limit=0):
        summary = self._object_summary(track, False)
        if index is not None:
            summary["index"] = index
        summary["name"] = getattr(track, "name", "")
        for attr in ("is_foldable", "mute", "solo", "arm", "implicit_arm", "can_be_armed"):
            try:
                summary[attr] = getattr(track, attr)
            except Exception:
                pass
        devices = []
        try:
            device_values, device_truncated = self._take(track.devices, device_limit)
            devices = [self._device_summary(device) for device in device_values]
            if device_truncated:
                devices.append({"truncated": True})
        except Exception:
            pass
        summary["devices"] = devices
        clips = []
        try:
            clip_slots, slots_truncated = self._take(track.clip_slots, clip_slot_limit)
            summary["clip_slots_scanned"] = len(clip_slots)
            for slot_index, slot in enumerate(clip_slots):
                try:
                    if slot.has_clip:
                        clips.append(self._clip_summary(slot.clip, slot_index))
                except Exception:
                    pass
            if slots_truncated:
                summary["clip_slots_truncated"] = True
        except Exception:
            pass
        summary["clips"] = clips
        try:
            summary["arrangement_clip_count"] = len(track.arrangement_clips)
            if arrangement_clip_limit:
                arrangement_clips, arrangement_truncated = self._take(track.arrangement_clips, arrangement_clip_limit)
                summary["arrangement_clips"] = [self._clip_summary(clip, None) for clip in arrangement_clips]
                if arrangement_truncated:
                    summary["arrangement_clips"].append({"truncated": True})
        except Exception:
            pass
        return summary

    def _device_summary(self, device):
        summary = self._object_summary(device, False)
        summary["name"] = getattr(device, "name", "")
        try:
            summary["class_name"] = device.class_name
        except Exception:
            pass
        try:
            summary["can_have_chains"] = device.can_have_chains
        except Exception:
            pass
        return summary

    def _clip_summary(self, clip, slot_index):
        summary = self._object_summary(clip, False)
        if slot_index is not None:
            summary["slot"] = slot_index
        summary["name"] = getattr(clip, "name", "")
        for attr in ("is_midi_clip", "is_audio_clip", "is_session_clip", "is_arrangement_clip", "length", "loop_start", "loop_end", "muted", "has_envelopes"):
            try:
                summary[attr] = getattr(clip, attr)
            except Exception:
                pass
        return summary

    def _note_summary(self, note):
        return {
            "note_id": note.note_id,
            "pitch": note.pitch,
            "start_time": note.start_time,
            "duration": note.duration,
            "velocity": note.velocity,
            "mute": note.mute,
            "probability": note.probability,
            "velocity_deviation": note.velocity_deviation,
            "release_velocity": note.release_velocity,
        }

    def _automation_event_summary(self, event):
        return {
            "time": getattr(event, "time", None),
            "value": getattr(event, "value", None),
        }

    def _warp_marker_summary(self, marker):
        return {
            "beat_time": getattr(marker, "beat_time", None),
            "sample_time": getattr(marker, "sample_time", None),
        }

    def _object_id(self, obj):
        live_id = getattr(obj, "_live_ptr", None)
        if live_id is not None:
            return int(live_id)
        return id(obj)

    def _is_browser_item(self, obj):
        return hasattr(obj, "name") and hasattr(obj, "is_loadable") and hasattr(obj, "is_folder")

    def _canonical_path(self, obj):
        try:
            return obj.canonical_path
        except Exception:
            return None

    def _detail(self, params):
        return bool(params and (params.get("detail") or params.get("include_repr")))

    def _encode_options(self, params):
        max_items = DEFAULT_MAX_ITEMS
        max_depth = DEFAULT_MAX_DEPTH
        max_string_length = DEFAULT_MAX_STRING_LENGTH
        if params:
            if params.get("max_items") is not None:
                max_items = params.get("max_items")
            if params.get("max_depth") is not None:
                max_depth = params.get("max_depth")
            if params.get("max_string_length") is not None:
                max_string_length = params.get("max_string_length")
        return {"detail": self._detail(params), "max_items": max_items, "max_depth": max_depth, "max_string_length": max_string_length, "depth": 0, "seen": set()}

    def _encode(self, value, options=None):
        if options is None:
            options = self._encode_options(None)
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            limit = options.get("max_string_length")
            if limit is not None and limit >= 0 and len(value) > limit:
                return value[:limit] + "...<truncated %s chars>" % (len(value) - limit)
            return value
        obj_id = id(value)
        if obj_id in options["seen"]:
            return {"truncated": True, "reason": "cycle"}
        if options["max_depth"] is not None and options["max_depth"] >= 0 and options["depth"] >= options["max_depth"]:
            return {"truncated": True, "reason": "max_depth"}
        if isinstance(value, (list, tuple)):
            limit = options["max_items"]
            values = value if limit is None or limit < 0 else value[:limit]
            child_options = dict(options)
            child_options["depth"] = options["depth"] + 1
            child_options["seen"] = set(options["seen"])
            child_options["seen"].add(obj_id)
            result = [self._encode(item, child_options) for item in values]
            if limit is not None and limit >= 0 and len(value) > limit:
                result.append({"truncated": True, "omitted": len(value) - limit})
            return result
        if isinstance(value, dict):
            items = list(value.items())
            limit = options["max_items"]
            if limit is not None and limit >= 0:
                items = items[:limit]
            child_options = dict(options)
            child_options["depth"] = options["depth"] + 1
            child_options["seen"] = set(options["seen"])
            child_options["seen"].add(obj_id)
            result = dict((str(key), self._encode(item, child_options)) for key, item in items)
            if limit is not None and limit >= 0 and len(value) > limit:
                result["__truncated__"] = {"omitted": len(value) - limit}
            return result
        return self._object_summary(value, options["detail"])

    def _check_expected_set_signature(self, params):
        expected = params.get("expected_set_signature") if params else None
        if not expected:
            return
        current = self._set_signature()
        if current != expected:
            raise RuntimeError("Set changed since last inspection; expected set_signature %s but current is %s. Re-read live_set_summary before applying destructive edits." % (expected, current))

    def _set_signature(self):
        song = self.song()
        payload = {
            "tempo": getattr(song, "tempo", None),
            "signature_numerator": getattr(song, "signature_numerator", None),
            "signature_denominator": getattr(song, "signature_denominator", None),
            "scenes": [getattr(scene, "name", "") for scene in getattr(song, "scenes", [])],
            "tracks": [self._track_signature(track) for track in getattr(song, "tracks", [])],
            "returns": [self._track_signature(track) for track in getattr(song, "return_tracks", [])],
            "master": self._track_signature(getattr(song, "master_track", None)),
        }
        text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]

    def _track_signature(self, track):
        if track is None:
            return None
        clips = []
        try:
            for index, slot in enumerate(track.clip_slots):
                if getattr(slot, "has_clip", False):
                    clips.append((index, self._clip_signature(slot.clip)))
        except Exception:
            pass
        arrangement = []
        try:
            arrangement = [self._clip_signature(clip) for clip in track.arrangement_clips]
        except Exception:
            pass
        devices = []
        try:
            devices = [(self._object_id(device), getattr(device, "name", ""), getattr(device, "class_name", "")) for device in track.devices]
        except Exception:
            pass
        state = {}
        for attr in ("name", "mute", "solo", "arm", "implicit_arm"):
            try:
                state[attr] = getattr(track, attr)
            except Exception:
                pass
        return {
            "id": self._object_id(track),
            "state": state,
            "devices": devices,
            "clips": clips,
            "arrangement": arrangement,
        }

    def _clip_signature(self, clip):
        state = {}
        for attr in ("name", "is_midi_clip", "is_audio_clip", "is_session_clip", "is_arrangement_clip", "length", "loop_start", "loop_end", "muted", "start_time", "end_time"):
            try:
                state[attr] = getattr(clip, attr)
            except Exception:
                pass
        return (self._object_id(clip), state)

    def _take(self, values, limit):
        if limit is None:
            limit = DEFAULT_CHILD_LIMIT
        if limit is not None and limit < 0:
            return list(values), False
        result = []
        for index, value in enumerate(values):
            if index >= limit:
                return result, True
            result.append(value)
        return result, False

    def _remember_object(self, obj_id, obj):
        if obj_id in self._objects:
            try:
                del self._objects[obj_id]
            except Exception:
                pass
        self._objects[obj_id] = obj
        while len(self._objects) > MAX_OBJECTS:
            try:
                first = next(iter(self._objects))
                del self._objects[first]
            except Exception:
                break

    def _make_listener(self, obj, prop):
        obj_id = self._object_id(obj)

        def listener():
            event = {"id": obj_id, "property": prop}
            try:
                event["value"] = self._encode(getattr(obj, prop))
            except Exception as exc:
                event["error"] = str(exc)
            self._events.append(event)
            if len(self._events) > 1000:
                self._events = self._events[-1000:]

        return listener

    def _remove_all_listeners(self):
        for _key, (obj, prop, callback) in list(self._listeners.items()):
            try:
                getattr(obj, "remove_%s_listener" % prop)(callback)
            except Exception:
                pass
        self._listeners = {}

    def _major_version(self, version):
        try:
            return int(str(version).split(".")[0])
        except Exception:
            return None


AbletonObjectMCP = AbletonLiveMCP
