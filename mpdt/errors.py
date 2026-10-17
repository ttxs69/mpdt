"""错误类型 → 退出码（T1-L3：三态可区分终态）"""


class MpdtError(Exception):
    exit_code = 1
    hint = ''


class ConfigError(MpdtError):
    """缺配置/配置损坏"""
    exit_code = 2
    hint = '先运行: mpdt init （生成本机配置，见 README 三步指引）'


class AuthError(MpdtError):
    """凭据失效且自动刷新失败"""
    exit_code = 3
    hint = ('token 刷新失败自助路径（约 90 秒）：\n'
            '  1. 在本机 Chrome 打开 mp.weixin.qq.com 并确认已登录\n'
            '  2. 重新运行 mpdt fetch\n'
            '  3. 仍失败 → 把下方原始返回片段发给开发者')


class UpstreamError(MpdtError):
    """上游接口结构变更/异常"""
    exit_code = 4
    hint = '上游接口异常（微信端点可能已改版）。请把以下原始返回片段反馈给开发者：\n'
