"""周报推送：把生成的周报发给客户。

设计要点（都是交付里踩出来的）：
1. **正文是 Markdown 源 + HTML 版本双挂载**：多数邮件客户端不渲染 Markdown，
   只发 .md 文件客户还得自己看编辑器。HTML 版直接可读。
2. **失败必须显式**：SMTP 认证失败 / 网络超时 → 抛异常，不静默吞掉。
   "发了但客户没收到"是最贵的失败模式（我们不知道，用户也不知道）。
3. **凭据不落代码**：从环境变量读，缺失时给可执行的修复指引。
4. **幂等**：同一 (week, recipient) 默认不重复发，除非 force=True。
   定时任务重跑不该给客户发两封同样的信。
"""

from __future__ import annotations

import json
import os
import smtplib
import sqlite3
import ssl
from email.message import EmailMessage
from pathlib import Path

# 必填项（缺任何一个都不能发信）
ENV_KEYS = {
    "host": "MPDT_SMTP_HOST",
    "port": "MPDT_SMTP_PORT",
    "user": "MPDT_SMTP_USER",
    "password": "MPDT_SMTP_PASSWORD",
}
# 可选项：from 缺省用 user
ENV_OPTIONAL = {"from": "MPDT_MAIL_FROM"}


class PushError(RuntimeError):
    """推送失败。消息里必须带「下一步怎么做」，不能只说失败了。"""


def _load_env() -> dict:
    """从 MP_DOTENV 指向的文件（默认 ~/.hermes/.env）补齐缺失的凭据。

    允许复用现有邮件凭据（EMAIL_SMTP_*），避免客户多配一套。
    """
    env = dict(os.environ)
    dotenv = os.environ.get("MP_DOTENV") or os.path.expanduser("~/.hermes/.env")
    p = Path(dotenv)
    if p.exists():
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    # 复用通用邮件凭据
    alias = {
        "MPDT_SMTP_HOST": "EMAIL_SMTP_HOST",
        "MPDT_SMTP_PORT": "EMAIL_SMTP_PORT",
        "MPDT_SMTP_USER": "EMAIL_ADDRESS",
        "MPDT_SMTP_PASSWORD": "EMAIL_PASSWORD",
    }
    for a, b in alias.items():
        if not env.get(a) and env.get(b):
            env[a] = env[b]
    return env


def _cfg() -> dict:
    env = _load_env()
    missing = [k for k in ENV_KEYS.values() if not env.get(k)]
    if missing:
        raise PushError(
            "缺少邮件凭据：{}。修复：export {}=...（或在 ~/.hermes/.env 里写，"
            "也可直接复用 EMAIL_SMTP_HOST/EMAIL_SMTP_PORT/EMAIL_ADDRESS/EMAIL_PASSWORD）。".format(
                "、".join(missing), "、".join(sorted(set(missing)))
            )
        )
    return {
        "host": env[ENV_KEYS["host"]],
        "port": int(env.get(ENV_KEYS["port"]) or 465),
        "user": env[ENV_KEYS["user"]],
        "password": env[ENV_KEYS["password"]],
        "from": env.get(ENV_OPTIONAL["from"]) or env[ENV_KEYS["user"]],
    }


def _sent_db_path() -> Path:
    # 不信任继承的 MPDT_STATE_DIR（测试会污染真实环境；2026-10-21 事故）
    d = os.path.expanduser("~/.mpdt")
    Path(d).mkdir(parents=True, exist_ok=True)
    return Path(d) / "pushed.sqlite3"


def _init_sent(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS pushed ("
        " week TEXT NOT NULL, recipient TEXT NOT NULL, md_path TEXT,"
        " sent_at TEXT, PRIMARY KEY (week, recipient))"
    )


def already_pushed(week: str, recipient: str) -> bool:
    conn = sqlite3.connect(str(_sent_db_path()))
    try:
        _init_sent(conn)
        row = conn.execute(
            "SELECT 1 FROM pushed WHERE week=? AND recipient=?", (week, recipient)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def _mark_pushed(conn, week, recipient, md_path):
    from datetime import datetime

    conn.execute(
        "INSERT OR REPLACE INTO pushed (week, recipient, md_path, sent_at) VALUES (?,?,?,?)",
        (week, recipient, md_path, datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()


def _html_inline(html_path: Path) -> str:
    """把 HTML 报告内联进邮件正文（客户不想收附件也要能直接看）。"""
    if not html_path.exists():
        return ""
    raw = html_path.read_text(encoding="utf-8", errors="replace")
    # 只取 <body>…</body>，避免 <style> 里的全站 CSS 打架
    low = raw.lower()
    i = low.find("<body")
    j = low.find("</body>")
    if i >= 0 and j > i:
        raw = raw[i : j + 7]
    return raw


def verify_delivery(
    recipient: str,
    subject_contains: str = "",
    imap_host: str = "",
    imap_port: int = 993,
    imap_user: str = "",
    imap_password: str = "",
    timeout_s: int = 60,
) -> dict:
    """用 IMAP 回查收件箱，确认邮件**真的到了对方**。

    为什么需要：SMTP 返回成功只代表"服务器收下了这封信"——
    实测把信发给 example.com（不存在的域名）SMTP 同样返回成功。
    若把它当"已送达"，就会出现"我们以为客户收到了，客户根本没收到"的静默失败。
    这类失败是最贵的：客户以为没收到就放弃，我们还以为在正常服务。

    返回 {'delivered': bool, 'subject': str|None, 'checked': n}
    收件箱里没有 ⇒ delivered=False（可能仍在投递队列中，调用方决定是否重试）。
    """
    import email as _email
    import imaplib
    from email.header import decode_header, make_header

    if not imap_host or not imap_user or not imap_password:
        return {
            "delivered": False,
            "subject": None,
            "checked": 0,
            "error": "缺少 IMAP 凭据，无法验证投递",
        }
    try:
        M = imaplib.IMAP4_SSL(imap_host, imap_port, timeout=timeout_s)
        M.login(imap_user, imap_password)
        M.select("INBOX")
        typ, data = M.search(None, "ALL")
        ids = data[0].split()
        checked = 0
        for i in reversed(ids[-25:]):
            _typ, d = M.fetch(i, "(BODY.PEEK[HEADER.FIELDS (SUBJECT FROM TO)])")
            raw = d[0][1]
            if isinstance(raw, str):
                raw = raw.encode()
            hdr = _email.message_from_bytes(raw)
            subj = str(make_header(decode_header(hdr["Subject"] or "")))
            checked += 1
            if subject_contains and subject_contains in subj:
                M.logout()
                return {"delivered": True, "subject": subj, "checked": checked}
        M.logout()
        return {
            "delivered": False,
            "subject": None,
            "checked": checked,
            "error": "收件箱最近 %d 封中未找到主题含 %r 的邮件" % (checked, subject_contains),
        }
    except Exception as e:
        return {
            "delivered": False,
            "subject": None,
            "checked": 0,
            "error": f"IMAP 校验失败：{type(e).__name__}: {e}",
        }


def _allowlist_path() -> Path:
    # 不信任继承的 MPDT_STATE_DIR（同上）
    d = os.path.expanduser("~/.mpdt")
    return Path(d) / "push_allowlist.json"


def _load_allowlist() -> list:
    """允许接收**本账号数据**的收件人白名单。

    R239 事故：拿真实冷线索的邮箱做投递自测，把我们号的内容结构、
    推荐命中率、分发数据发给了从未同意的人。对方是真人开的邮箱，
    数据已经出了我们的手，收不回。
    ⇒ 机制上必须：默认**没有**人能收到本账号数据；
    只有显式登记在白名单里的地址才能收到。
    白名单文件：~/.mpdt/push_allowlist.json，形如
        {"allow": ["a@example.com"], "note": "客户邮箱，2026-10-21 客户明确同意接收周报"}
    """
    p = _allowlist_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    allow = data.get("allow") or []
    return [str(x).strip().lower() for x in allow if str(x).strip()]


def _self_addresses(cfg: dict) -> set:
    """发件人自己 / 账号所有者本人的地址——这些永远允许（自测收件箱）。"""
    addrs = set()
    for k in ("EMAIL_ADDRESS", "MPDT_SMTP_USER", "MPDT_MAIL_FROM"):
        v = os.environ.get(k)
        if v:
            addrs.add(v.strip().lower())
    try:
        for line in (
            Path(os.environ.get("MP_DOTENV") or os.path.expanduser("~/.hermes/.env"))
            .read_text(encoding="utf-8", errors="replace")
            .splitlines()
        ):
            line = line.strip()
            if "=" not in line or line.startswith("#"):
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            if k in ("EMAIL_ADDRESS", "MPDT_SMTP_USER", "MPDT_MAIL_FROM"):
                addrs.add(v.strip().strip('"').strip("'").lower())
    except OSError:
        pass
    if cfg.get("user"):
        addrs.add(cfg["user"].strip().lower())
    return addrs


def check_recipient(recipient: str, cfg: dict) -> None:
    """收件人未授权 ⇒ 抛 PushError。

    这是**硬门**：不在白名单、也不是发件人自己，就不能收到本账号数据。
    想要临时放行必须显式写白名单文件（或加 --allow-unlisted），
    后者会在日志里留痕，便于事后审计。
    """
    r = (recipient or "").strip().lower()
    if not r or "@" not in r:
        raise PushError(f"收件人邮箱不合法：{recipient!r}")
    if r in _self_addresses(cfg):
        return
    allow = _load_allowlist()
    if r in allow:
        return
    raise PushError(
        f"收件人 {recipient} 不在推送白名单，拒绝发送本账号数据。\n"
        "原因：把账号数据发给未同意的第三方无法撤回（R239 事故：曾误发给冷线索）。\n"
        "放行方式（二选一）：\n"
        f"  1) 客户明确同意接收周报后，把地址写进 {_allowlist_path()}：\n"
        f'     {{"allow": ["{r}"], "note": "同意接收周报，2026-10-21 客户确认"}}\n'
        "  2) 临时自测：加 --allow-unlisted（仅限发件人自己的邮箱或你确认过的地址，"
        "调用会被记录到推送日志）"
    )


def _log_unlisted(recipient: str, week: str) -> None:
    """放行未登记收件人时留痕（append-only，便于事后审计）。"""
    from datetime import datetime

    d = os.path.expanduser("~/.mpdt")  # 不信任继承的 MPDT_STATE_DIR
    Path(d).mkdir(parents=True, exist_ok=True)
    with (Path(d) / "push_unlisted.log").open("a", encoding="utf-8") as f:
        f.write(
            "{}\t{}\t{}\n".format(datetime.now().isoformat(timespec="seconds"), week, recipient)
        )


def send_report(
    md_path: str,
    recipient: str,
    subject: str = "",
    week: str = "",
    body_md: str = "",
    force: bool = False,
    allow_unlisted: bool = False,
) -> dict:
    """把周报发给 recipient。返回 {'ok':True,'recipient':...,'bytes':n}。

    失败抛 PushError（带修复指引），不静默。
    收件人必须已授权（白名单或发件人本人），见 check_recipient。
    """
    md = Path(md_path)
    if not md.exists():
        raise PushError(f"周报文件不存在：{md_path}（先生成再推：mpdt report --week <W>）")
    week = week or md.stem.replace("weekly-", "")
    cfg = _cfg()
    if not allow_unlisted:
        check_recipient(recipient, cfg)
    elif (recipient or "").strip().lower() not in _self_addresses(cfg) and (
        recipient or ""
    ).strip().lower() not in _load_allowlist():
        # --allow-unlisted 只放宽一次，且必须留痕
        _log_unlisted(recipient, week)
    if not force and already_pushed(week, recipient):
        return {
            "ok": True,
            "skipped": True,
            "recipient": recipient,
            "week": week,
            "reason": "该周已推送给此收件人（force=True 可重发）",
        }

    html_path = md.with_suffix(".html")
    msg = EmailMessage()
    msg["From"] = cfg["from"]
    msg["To"] = recipient
    msg["Subject"] = subject or (f"公众号数据周报 {week}")
    intro = body_md or (
        f"您好，\n\n这是您 {week} 的公众号数据周报（数据口径：文章发表日落在该周内）。\n"
        "回复本邮件即可把问题直接发给我。\n\n"
    )
    msg.set_content(intro + "\n" + md.read_text(encoding="utf-8", errors="replace"))
    inline = _html_inline(html_path)
    if inline:
        msg.add_alternative(inline, subtype="html")

    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(cfg["host"], cfg["port"], context=ctx, timeout=30) as s:
            s.login(cfg["user"], cfg["password"])
            s.send_message(msg)
    except smtplib.SMTPAuthenticationError as e:
        raise PushError(
            "SMTP 认证失败：{}。请检查 {} / {} 是否为有效授权码。".format(
                e, ENV_KEYS["user"], ENV_KEYS["password"]
            )
        ) from e
    except (smtplib.SMTPException, OSError) as e:
        raise PushError(
            f"推送失败：{type(e).__name__}: {e}（网络或 SMTP 服务端问题，稍后重试；"
            "本工具不会静默吞错）"
        ) from e

    conn = sqlite3.connect(str(_sent_db_path()))
    try:
        _init_sent(conn)
        _mark_pushed(conn, week, recipient, str(md))
    finally:
        conn.close()
    return {
        "ok": True,
        "recipient": recipient,
        "week": week,
        "md": str(md),
        "bytes": len(msg.as_bytes()),
        "verified": False,
        "note": "SMTP 服务器已接受（未验证收件方实际收到）",
    }
