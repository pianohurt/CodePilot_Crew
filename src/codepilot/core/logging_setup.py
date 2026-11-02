"""S0 使用标准日志；运行事件和 Trace 由后续阶段引入。"""

import logging


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
