{
  "patcher": {
    "fileversion": 1,
    "appversion": {
      "major": 8,
      "minor": 6,
      "revision": 0,
      "architecture": "x64",
      "modernui": 1
    },
    "classnamespace": "box",
    "rect": [236.0, 105.0, 560.0, 320.0],
    "openrect": [0.0, 0.0, 560.0, 169.0],
    "bglocked": 0,
    "openinpresentation": 1,
    "default_fontsize": 10.0,
    "default_fontface": 0,
    "default_fontname": "Arial Bold",
    "gridonopen": 1,
    "gridsize": [8.0, 8.0],
    "gridsnaponopen": 1,
    "objectsnaponopen": 1,
    "statusbarvisible": 2,
    "toolbarvisible": 1,
    "lefttoolbarpinned": 0,
    "toptoolbarpinned": 0,
    "righttoolbarpinned": 0,
    "bottomtoolbarpinned": 0,
    "toolbars_unpinned_last_save": 0,
    "tallnewobj": 0,
    "boxanimatetime": 500,
    "enablehscroll": 1,
    "enablevscroll": 1,
    "devicewidth": 560.0,
    "description": "Agent-controlled audio tap for recording audio at this point in an Ableton signal chain.",
    "digest": "Agent audio tap",
    "tags": "agent,analysis,audio tap,recording",
    "style": "",
    "subpatcher_template": "",
    "boxes": [
      {
        "box": {
          "id": "obj-plugin",
          "maxclass": "newobj",
          "numinlets": 2,
          "numoutlets": 2,
          "outlettype": ["signal", "signal"],
          "patching_rect": [40.0, 48.0, 53.0, 20.0],
          "text": "plugin~"
        }
      },
      {
        "box": {
          "id": "obj-plugout",
          "maxclass": "newobj",
          "numinlets": 2,
          "numoutlets": 2,
          "outlettype": ["signal", "signal"],
          "patching_rect": [40.0, 216.0, 53.0, 20.0],
          "text": "plugout~"
        }
      },
      {
        "box": {
          "id": "obj-recorder",
          "maxclass": "newobj",
          "numinlets": 3,
          "numoutlets": 0,
          "patching_rect": [176.0, 216.0, 72.0, 20.0],
          "text": "sfrecord~ 2"
        }
      },
      {
        "box": {
          "id": "obj-udp",
          "maxclass": "newobj",
          "numinlets": 1,
          "numoutlets": 1,
          "outlettype": [""],
          "patching_rect": [176.0, 48.0, 116.0, 20.0],
          "text": "udpreceive 17654"
        }
      },
      {
        "box": {
          "id": "obj-tosymbol",
          "maxclass": "newobj",
          "numinlets": 2,
          "numoutlets": 1,
          "outlettype": [""],
          "patching_rect": [176.0, 80.0, 59.0, 20.0],
          "text": "tosymbol"
        }
      },
      {
        "box": {
          "id": "obj-js",
          "maxclass": "newobj",
          "numinlets": 1,
          "numoutlets": 3,
          "outlettype": ["", "", ""],
          "patching_rect": [176.0, 112.0, 139.0, 20.0],
          "text": "js agent_audio_tap.js"
        }
      },
      {
        "box": {
          "id": "obj-loadbang",
          "maxclass": "newobj",
          "numinlets": 1,
          "numoutlets": 1,
          "outlettype": ["bang"],
          "patching_rect": [328.0, 80.0, 55.0, 20.0],
          "text": "loadbang"
        }
      },
      {
        "box": {
          "id": "obj-notein",
          "maxclass": "newobj",
          "numinlets": 1,
          "numoutlets": 3,
          "outlettype": ["int", "int", "int"],
          "patching_rect": [328.0, 48.0, 45.0, 20.0],
          "text": "notein"
        }
      },
      {
        "box": {
          "id": "obj-stripnote",
          "maxclass": "newobj",
          "numinlets": 2,
          "numoutlets": 2,
          "outlettype": ["int", "int"],
          "patching_rect": [328.0, 112.0, 55.0, 20.0],
          "text": "stripnote"
        }
      },
      {
        "box": {
          "id": "obj-midi-select",
          "maxclass": "newobj",
          "numinlets": 1,
          "numoutlets": 3,
          "outlettype": ["bang", "bang", ""],
          "patching_rect": [328.0, 144.0, 59.0, 20.0],
          "text": "sel 60 61"
        }
      },
      {
        "box": {
          "id": "obj-midi-start",
          "maxclass": "message",
          "numinlets": 2,
          "numoutlets": 1,
          "outlettype": [""],
          "patching_rect": [328.0, 176.0, 35.0, 18.0],
          "text": "start"
        }
      },
      {
        "box": {
          "id": "obj-midi-stop",
          "maxclass": "message",
          "numinlets": 2,
          "numoutlets": 1,
          "outlettype": [""],
          "patching_rect": [376.0, 176.0, 32.0, 18.0],
          "text": "stop"
        }
      },
      {
        "box": {
          "id": "obj-status-send",
          "maxclass": "newobj",
          "numinlets": 1,
          "numoutlets": 0,
          "patching_rect": [272.0, 152.0, 139.0, 20.0],
          "text": "s agent_audio_tap_status"
        }
      },
      {
        "box": {
          "id": "obj-print",
          "maxclass": "newobj",
          "numinlets": 1,
          "numoutlets": 0,
          "patching_rect": [424.0, 152.0, 112.0, 20.0],
          "text": "print AgentAudioTap"
        }
      },
      {
        "box": {
          "id": "obj-title",
          "maxclass": "live.comment",
          "numinlets": 1,
          "numoutlets": 0,
          "patching_rect": [16.0, 8.0, 220.0, 18.0],
          "presentation": 1,
          "presentation_rect": [12.0, 10.0, 220.0, 18.0],
          "text": "Agent Audio Tap",
          "textjustification": 0
        }
      },
      {
        "box": {
          "id": "obj-note",
          "maxclass": "live.comment",
          "numinlets": 1,
          "numoutlets": 0,
          "patching_rect": [16.0, 272.0, 488.0, 18.0],
          "presentation": 1,
          "presentation_rect": [12.0, 40.0, 532.0, 18.0],
          "text": "Controlled by Ableton MCP on UDP 127.0.0.1:17654; audio passes through unchanged.",
          "textjustification": 0
        }
      },
      {
        "box": {
          "id": "obj-limit",
          "maxclass": "comment",
          "hidden": 1,
          "numinlets": 1,
          "numoutlets": 0,
          "patching_rect": [0.0, 170.0, 134.0, 20.0],
          "text": "Device vertical limit"
        }
      }
    ],
    "lines": [
      {
        "patchline": {
          "source": ["obj-plugin", 0],
          "destination": ["obj-plugout", 0]
        }
      },
      {
        "patchline": {
          "source": ["obj-plugin", 1],
          "destination": ["obj-plugout", 1]
        }
      },
      {
        "patchline": {
          "source": ["obj-plugin", 0],
          "destination": ["obj-recorder", 0]
        }
      },
      {
        "patchline": {
          "source": ["obj-plugin", 1],
          "destination": ["obj-recorder", 1]
        }
      },
      {
        "patchline": {
          "source": ["obj-udp", 0],
          "destination": ["obj-tosymbol", 0]
        }
      },
      {
        "patchline": {
          "source": ["obj-tosymbol", 0],
          "destination": ["obj-js", 0]
        }
      },
      {
        "patchline": {
          "source": ["obj-loadbang", 0],
          "destination": ["obj-js", 0]
        }
      },
      {
        "patchline": {
          "source": ["obj-notein", 0],
          "destination": ["obj-stripnote", 0]
        }
      },
      {
        "patchline": {
          "source": ["obj-notein", 1],
          "destination": ["obj-stripnote", 1]
        }
      },
      {
        "patchline": {
          "source": ["obj-stripnote", 0],
          "destination": ["obj-midi-select", 0]
        }
      },
      {
        "patchline": {
          "source": ["obj-midi-select", 0],
          "destination": ["obj-midi-start", 0]
        }
      },
      {
        "patchline": {
          "source": ["obj-midi-select", 1],
          "destination": ["obj-midi-stop", 0]
        }
      },
      {
        "patchline": {
          "source": ["obj-midi-start", 0],
          "destination": ["obj-js", 0]
        }
      },
      {
        "patchline": {
          "source": ["obj-midi-stop", 0],
          "destination": ["obj-js", 0]
        }
      },
      {
        "patchline": {
          "source": ["obj-js", 0],
          "destination": ["obj-recorder", 0]
        }
      },
      {
        "patchline": {
          "source": ["obj-js", 1],
          "destination": ["obj-status-send", 0]
        }
      },
      {
        "patchline": {
          "source": ["obj-js", 2],
          "destination": ["obj-print", 0]
        }
      }
    ],
    "dependency_cache": [
      {
        "name": "agent_audio_tap.js",
        "bootpath": ".",
        "patcherrelativepath": ".",
        "type": "TEXT",
        "implicit": 1
      }
    ],
    "latency": 0,
    "project": {
      "version": 1,
      "creationdate": 3987072000,
      "modificationdate": 3987072000,
      "viewrect": [0.0, 0.0, 300.0, 500.0],
      "autoorganize": 1,
      "hideprojectwindow": 1,
      "showdependencies": 1,
      "autolocalize": 0,
      "contents": {
        "patchers": {}
      },
      "layout": {},
      "searchpath": {},
      "detailsvisible": 0,
      "amxdtype": 1633771873,
      "readonly": 0,
      "devpathtype": 0,
      "devpath": ".",
      "sortmode": 0,
      "viewmode": 0
    },
    "autosave": 0
  }
}
