"""按当前阶段装配四个工具，不创建后续扩展层。"""

import os

from codepilot.tools.command import CommandArgs, prepare_command
from codepilot.tools.readonly import readonly_registry
from codepilot.tools.registry import ToolRegistry
from codepilot.tools.write import WriteFileArgs, prepare_write


def builtin_registry() -> ToolRegistry:
    registry = readonly_registry()
    registry.register("write_file", "Write a small UTF-8 file inside the workspace. Parent must exist; set overwrite=true for existing files. The program requires authorization and rejects stale approved content. Never retry blindly.", WriteFileArgs, prepare=prepare_write)
    shell = "PowerShell" if os.name == "nt" else "bash"
    registry.register("bash", f"Run a command with {shell} in an existing workspace cwd. Requires program authorization. Reports exit code/stdout/stderr and kills ordinary descendants on timeout or cancellation. Not an OS sandbox; do not mask failed checks or retry side effects blindly.", CommandArgs, prepare=prepare_command)
    return registry
