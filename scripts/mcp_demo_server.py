"""可复现的 stdio 示例。故障工具只在显式 --faults 时注册。"""

import argparse
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

ECHO_SCHEMA = {
    "type": "object", "properties": {"text": {"$ref": "#/$defs/text"}}, "required": ["text"],
    "additionalProperties": False, "$defs": {"text": {"type": "string", "minLength": 1}},
}
WRITE_SCHEMA = {
    "type": "object", "properties": {"effect": {"enum": ["error", "timeout", "disconnect", "rpc"]}},
    "required": ["effect"], "additionalProperties": False,
}


def definition(name: str, schema: dict[str, Any]) -> dict[str, Any]:
    return {"name": name, "description": "Local MCP demo", "inputSchema": schema}


def send(**fields: Any) -> None:
    print(json.dumps({"jsonrpc": "2.0", **fields}, ensure_ascii=True), flush=True)


def serve(label: str, faults: bool, collision: bool) -> None:
    if faults:
        Path(f"{label}.pid").write_text(str(os.getpid()), encoding="ascii")
    initialized = False
    for line in sys.stdin:
        message = json.loads(line)
        method = message["method"]
        if method == "notifications/initialized":
            initialized = True
            continue
        request_id = message["id"]
        params = message.get("params", {})
        if method == "initialize":
            result = {"protocolVersion": message["params"]["protocolVersion"], "capabilities": {"tools": {}},
                      "serverInfo": {"name": label, "version": "1"}}
        elif not initialized:
            send(id=request_id, error={"code": -32000, "message": "not initialized"})
            continue
        elif method == "tools/list":
            if collision:
                result = {"tools": [definition("same.name", ECHO_SCHEMA), definition("same_name", ECHO_SCHEMA)]}
            elif faults and params.get("cursor") == "writes":
                result = {"tools": [definition("write_once", WRITE_SCHEMA)]}
            else:
                result = {"tools": [definition("echo", ECHO_SCHEMA)]}
                if faults:
                    result["nextCursor"] = "writes"
        elif method == "tools/call" and params.get("name") == "echo":
            result = {"content": [{"type": "text", "text": f"{label}: {params['arguments']['text']}"}]}
        elif method == "tools/call" and params.get("name") == "write_once" and faults:
            with Path(f"{label}.writes").open("a", encoding="utf-8") as stream:
                stream.write("written\n")
            effect = params["arguments"]["effect"]
            if effect == "timeout":
                time.sleep(60)
            if effect == "disconnect":
                return
            if effect == "rpc":
                send(id=request_id, error={"code": -32603, "message": "simulated RPC error"})
                continue
            result = {"content": [{"type": "text", "text": "written, then failed"}], "isError": True}
        else:
            send(id=request_id, error={"code": -32601, "message": "unknown method or tool"})
            continue
        send(id=request_id, result=result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="demo")
    parser.add_argument("--faults", action="store_true")
    parser.add_argument("--collision", action="store_true")
    args = parser.parse_args()
    serve(args.label, args.faults, args.collision)
