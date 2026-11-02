"""CodePilot Crew 的最小本地服务。"""

from importlib.metadata import version


def get_version() -> str:
    """从安装元数据读取，避免客户端和服务端维护不同版本号。"""
    return version("codepilot")
