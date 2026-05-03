from __future__ import absolute_import, print_function

import json
import socket
import sys
import threading
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
DEFAULT_MAIN_THREAD_TIMEOUT = 10
DEFAULT_BROWSER_ROOTS = ("instruments", "audio_effects", "midi_effects", "drums", "samples", "sounds", "packs", "plugins", "user_library", "user_folders", "current_project")


class AbletonObjectMCP(ControlSurface):
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
        self.log_message("Ableton_Object_MCP listening on %s:%s" % (HOST, PORT))

    def _accept_loop(self):
        while self._running:
            try:
                client, _addr = self._server.accept()
                thread = threading.Thread(target=self._handle_client, args=(client,))
                thread.daemon = True
                thread.start()
            except Exception:
                if self._running:
                    self.log_message("Ableton_Object_MCP accept error: %s" % traceback.format_exc())

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
            while self._running:
                data = self._read_line(client)
                if not data:
                    break
                request = json.loads(data.decode("utf-8"))
                response = self._dispatch(request)
                client.sendall((json.dumps(response, separators=(",", ":")) + "\n").encode("utf-8"))
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

    def _read_line(self, client):
        chunks = []
        while True:
            chunk = client.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
            if sum(len(item) for item in chunks) > MAX_REQUEST_BYTES:
                raise ValueError("Request exceeds maximum size")
            if b"\n" in chunk:
                break
        return b"".join(chunks).split(b"\n", 1)[0]

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
            return self._encode(getattr(self, "_rpc_" + method)(params), self._encode_options(params))
        done = threading.Event()
        result = {"value": None, "error": None}
        abandoned = {"value": False}

        def invoke():
            if abandoned["value"]:
                done.set()
                return
            try:
                value = getattr(self, "_rpc_" + method)(params)
                result["value"] = self._encode(value, self._encode_options(params))
            except Exception:
                result["error"] = sys.exc_info()
            finally:
                done.set()

        self.schedule_message(0, invoke)
        timeout = float(params.get("timeout") or DEFAULT_MAIN_THREAD_TIMEOUT)
        if not done.wait(timeout):
            abandoned["value"] = True
            raise RuntimeError("Timed out waiting for Live main thread")
        if result["error"]:
            exc_type, exc, tb = result["error"]
            raise exc.with_traceback(tb)
        return result["value"]

    def _rpc_ping(self, _params):
        app = Live.Application.get_application()
        version = app.get_version_string() if hasattr(app, "get_version_string") else "unknown"
        return {"ok": True, "version": version, "major": self._major_version(version)}

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

    def _rpc_batch(self, params):
        results = []
        continue_on_error = bool(params.get("continue_on_error"))
        inherited = {}
        for name in ("detail", "include_repr", "max_items", "max_depth", "max_string_length", "timeout"):
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
        return results

    def _rpc_browser_roots(self, _params):
        browser = Live.Application.get_application().browser
        roots = []
        for name in DEFAULT_BROWSER_ROOTS:
            if hasattr(browser, name):
                root = getattr(browser, name)
                roots.append({"name": name, "kind": root.__class__.__name__})
        return roots

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
                    matches.append((score(item, path_text), len(current_path), self._browser_item_result(root_name, item, path_text)))
                    if stop_on_limit and len(matches) >= limit:
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
        item = self._resolve(params.get("item"))
        target = params.get("target_track")
        if target:
            self.song().view.selected_track = self._resolve(target)
        Live.Application.get_application().browser.load_item(item)
        return self._browser_item_result(None, item, None)

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
