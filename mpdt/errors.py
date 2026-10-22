"""错误类型 → 退出码（T1-L3：三态可区分终态）。

每类错误的 hint 都是「下一步怎么办」指引（新用户零文档可自救）；
统一兜底：mpdt doctor 一键诊断（只读）。
"""


class MpdtError(Exception):
    exit_code = 1
    hint = ""


class ConfigError(MpdtError):
    """缺配置/配置损坏"""

    exit_code = 2
    hint = (
        "下一步：\n"
        "  1. 运行 mpdt init 生成本机配置（~/.mpdt/config.json）\n"
        "  2. 仍失败 → 运行 mpdt doctor 一键诊断（只读，不改任何状态）"
    )


class AuthError(MpdtError):
    """凭据失效且自动刷新失败"""

    exit_code = 3
    hint = (
        "登录态自助修复（约 90 秒）：\n"
        "  1. 在本机 Chrome 打开 mp.weixin.qq.com 并确认已登录\n"
        "  2. 重新运行 mpdt fetch\n"
        "  3. 仍失败 → 运行 mpdt doctor 定位（CDP 连通/标签页/配置逐项检查），\n"
        "     或把下方原始返回片段发给开发者"
    )


class UpstreamError(MpdtError):
    """上游接口结构变更/异常"""

    exit_code = 4
    hint = "上游接口异常（微信端点可能已改版）。请把以下原始返回片段反馈给开发者：\n"
