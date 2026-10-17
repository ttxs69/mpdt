"""配置读写：~/.mpdt/config.json（chmod 600）。凭据只存本机；打印时永不回显。"""
import json
import os
import stat

from .errors import ConfigError

CONFIG_DIR = os.path.expanduser('~/.mpdt')
CONFIG_PATH = os.path.join(CONFIG_DIR, 'config.json')

DEFAULTS = {
    # fingerprint 与 token 均可被 session 自动刷新；此处为初始值
    'fingerprint': '',
    'chrome_cdp': 'http://127.0.0.1:9222',
    'request_interval': 1.0,     # 礼貌限速（NFR：≥1s）
    'db_path': os.path.join(CONFIG_DIR, 'mpdt.sqlite3'),
}


def load() -> dict:
    if not os.path.exists(CONFIG_PATH):
        raise ConfigError(f'未找到配置 {CONFIG_PATH}')
    try:
        cfg = json.load(open(CONFIG_PATH))
    except (ValueError, OSError) as e:
        raise ConfigError(f'配置损坏（{e}）：删除后重跑 mpdt init')
    merged = dict(DEFAULTS)
    merged.update(cfg)
    return merged


def save(cfg: dict) -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    tmp = CONFIG_PATH + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)          # 600
    os.replace(tmp, CONFIG_PATH)
    # 写后读回（T2 断言）：cfg 未含 db_path 时文件里也没有该键，读回侧同样取默认值再比对
    saved = json.load(open(CONFIG_PATH))
    assert saved.get('db_path', DEFAULTS['db_path']) == cfg.get('db_path', DEFAULTS['db_path']), 'config write-then-readback failed'
