---
{"name":"review","description":"根据文件证据审查代码","allowed_tools":["read_file","list_dir"]}
---
审查目标

$ARGUMENTS

先读取相关文件，再按影响程度列出可以复现的问题。每项说明路径、触发条件和影响。若没有发现问题，说明检查了哪些范围以及哪些行为仍需验证。保持工作区只读。
