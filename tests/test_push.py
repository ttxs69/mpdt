"""push 模块的单元测试（不真的发信）。

原则：SMTP 不该在单元测试里被调用。所有网络行为用 stub 替身，
断言的是**契约**（凭据缺失时报错、重复推送跳过、失败显式抛错、HTML 内联）。
"""
import os
import sqlite3
import sys
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mpdt import push  # noqa: E402


class _FakeSMTP:
    """最小 SMTP 替身：记录 send_message 的调用。"""
    sent: list = []
    fail: str | None = None

    def __init__(self, host, port, context=None, timeout=None):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def login(self, u, p):
        if _FakeSMTP.fail == 'auth':
            import smtplib
            raise smtplib.SMTPAuthenticationError(535, b'bad creds')
        return (235, b'ok')

    def send_message(self, msg):
        if _FakeSMTP.fail == 'net':
            import smtplib
            raise smtplib.SMTPServerDisconnected('disconnected')
        _FakeSMTP.sent.append(msg)
        return {}


class PushTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.state = Path(self.tmp) / 'state'
        self.state.mkdir()
        # 用 monkeypatch 替换模块的路径函数，**不要**设 MPDT_STATE_DIR 环境变量：
        # 环境变量会泄漏到同进程后续调用（2026-10-21 事故：cron 脚本读到测试白名单
        # 并真的推送了测试地址）。生产代码已改为固定 ~/.mpdt，测试也要隔离。
        self._p1 = push._sent_db_path
        self._p2 = push._allowlist_path
        push._sent_db_path = lambda: self.state / 'pushed.sqlite3'
        push._allowlist_path = lambda: self.state / 'push_allowlist.json'
        # _log_unlisted 直接写 ~/.mpdt（审计日志不能被测试污染）→
        # 测试时改写它的落盘位置
        import mpdt.push as _pp
        _orig_log = _pp._log_unlisted
        def _patched_log(recipient, week):
            with (self.state / 'push_unlisted.log').open('a', encoding='utf-8') as f:
                f.write('%s\t%s\t%s\n' % ('test', week, recipient))
        push._log_unlisted = _patched_log
        self._p3 = _orig_log
        # 隔离凭据：用假的 MPDT_* 覆盖，_load_env 优先取 env
        os.environ['MPDT_SMTP_HOST'] = 'smtp.example.com'
        os.environ['MPDT_SMTP_PORT'] = '465'
        os.environ['MPDT_SMTP_USER'] = 'me@example.com'
        os.environ['MPDT_SMTP_PASSWORD'] = 'pw'
        os.environ['MPDT_MAIL_FROM'] = 'me@example.com'
        self.md = Path(self.tmp) / 'weekly-2026-W43.md'
        self.md.write_text('# 周报\n\n本周发表的文章：**3** 篇\n', encoding='utf-8')
        self.html = self.md.with_suffix('.html')
        self.html.write_text('<html><head><style>x{}</style></head><body><h1>周报</h1>'
                             '<p>推荐 86%</p></body></html>', encoding='utf-8')
        _FakeSMTP.sent = []
        _FakeSMTP.fail = None
        self._orig = push.smtplib.SMTP_SSL
        push.smtplib.SMTP_SSL = _FakeSMTP

    def tearDown(self):
        push.smtplib.SMTP_SSL = self._orig
        push._sent_db_path = self._p1
        push._allowlist_path = self._p2
        push._log_unlisted = self._p3
        for k in ('MPDT_SMTP_HOST', 'MPDT_SMTP_PORT',
                  'MPDT_SMTP_USER', 'MPDT_SMTP_PASSWORD', 'MPDT_MAIL_FROM'):
            os.environ.pop(k, None)


class TestSendReport(PushTestBase):
    """推送本身的行为。收件人门见 TestRecipientGate。

    这里默认把 a@b.com 登记进白名单 —— 模拟"客户已明确同意接收周报"，
    让本类只测发信行为，不被门拦住。
    """

    def setUp(self):
        super().setUp()
        (self.state / 'push_allowlist.json').write_text(
            '{"allow": ["a@b.com"]}', encoding='utf-8')

    def test_sends_with_subject_and_inline_html(self):
        res = push.send_report(str(self.md), 'a@b.com', week='2026-W43')
        self.assertTrue(res['ok'])
        self.assertEqual(len(_FakeSMTP.sent), 1)
        msg = _FakeSMTP.sent[0]
        self.assertEqual(msg['To'], 'a@b.com')
        self.assertIn('2026-W43', msg['Subject'])
        types = [p.get_content_type() for p in msg.get_payload()]
        self.assertIn('text/plain', types)
        self.assertIn('text/html', types)

    def test_html_body_excludes_head_style(self):
        push.send_report(str(self.md), 'a@b.com', week='2026-W43')
        msg = _FakeSMTP.sent[0]
        html = [p.get_payload(decode=True).decode() for p in msg.get_payload()
                if p.get_content_type() == 'text/html'][0]
        self.assertIn('<h1>周报</h1>', html)
        self.assertNotIn('<style>', html)   # 避免全站 CSS 打架

    def test_duplicate_is_skipped(self):
        push.send_report(str(self.md), 'a@b.com', week='2026-W43')
        res = push.send_report(str(self.md), 'a@b.com', week='2026-W43')
        self.assertTrue(res.get('skipped'))
        self.assertEqual(len(_FakeSMTP.sent), 1)   # 没有第二封

    def test_force_resends(self):
        push.send_report(str(self.md), 'a@b.com', week='2026-W43')
        res = push.send_report(str(self.md), 'a@b.com', week='2026-W43', force=True)
        self.assertFalse(res.get('skipped'))
        self.assertEqual(len(_FakeSMTP.sent), 2)

    def test_different_week_not_skipped(self):
        push.send_report(str(self.md), 'a@b.com', week='2026-W43')
        res = push.send_report(str(self.md), 'a@b.com', week='2026-W44')
        self.assertFalse(res.get('skipped'))

    def test_missing_file_raises_with_actionable_msg(self):
        with self.assertRaises(push.PushError) as cm:
            push.send_report(str(Path(self.tmp) / 'nope.md'), 'a@b.com', week='2026-W43')
        self.assertIn('不存在', str(cm.exception))

    def test_auth_error_raises_with_fix_hint(self):
        _FakeSMTP.fail = 'auth'
        with self.assertRaises(push.PushError) as cm:
            push.send_report(str(self.md), 'a@b.com', week='2026-W43')
        self.assertIn('认证失败', str(cm.exception))

    def test_network_error_raises_not_silent(self):
        _FakeSMTP.fail = 'net'
        with self.assertRaises(push.PushError):
            push.send_report(str(self.md), 'a@b.com', week='2026-W43')
        # 失败不写幂等记录 ⇒ 修好后可重试
        self.assertFalse(push.already_pushed('2026-W43', 'a@b.com'))

    def test_missing_credentials_raises_actionable(self):
        for k in ('MPDT_SMTP_HOST', 'MPDT_SMTP_PORT', 'MPDT_SMTP_USER', 'MPDT_SMTP_PASSWORD'):
            os.environ.pop(k, None)
        os.environ['MP_DOTENV'] = str(Path(self.tmp) / 'nonexistent.env')
        with self.assertRaises(push.PushError) as cm:
            push.send_report(str(self.md), 'a@b.com', week='2026-W43')
        self.assertIn('MPDT_SMTP_HOST', str(cm.exception))


class TestRecipientGate(PushTestBase):
    """R239 硬门：只有白名单里的地址（或发件人自己）能收到本账号数据。

    事故：拿真实冷线索邮箱做投递自测，把账号内容结构/推荐命中率发给了未同意的人。
    数据出了手就收不回 —— 必须是机制拦截，不是"我下次记得"。
    """

    def _allow(self, *emails):
        (self.state / 'push_allowlist.json').write_text(
            '{"allow": [%s]}' % ', '.join('"%s"' % e for e in emails), encoding='utf-8')

    def test_unlisted_recipient_is_refused(self):
        with self.assertRaises(push.PushError) as cm:
            push.send_report(str(self.md), 'stranger@example.com', week='2026-W43')
        self.assertIn('白名单', str(cm.exception))
        self.assertEqual(len(_FakeSMTP.sent), 0)   # 一封都没发出去

    def test_allowlisted_recipient_passes(self):
        self._allow('client@example.com')
        res = push.send_report(str(self.md), 'client@example.com', week='2026-W43')
        self.assertTrue(res['ok'])
        self.assertEqual(len(_FakeSMTP.sent), 1)

    def test_own_address_is_allowed(self):
        # 发件人自己的邮箱（自测收件箱）不需要登记
        res = push.send_report(str(self.md), 'me@example.com', week='2026-W43')
        self.assertTrue(res['ok'])

    def test_allow_unlisted_sends_but_leaves_audit_trail(self):
        res = push.send_report(str(self.md), 'stranger@example.com', week='2026-W43',
                               allow_unlisted=True)
        self.assertTrue(res['ok'])
        self.assertEqual(len(_FakeSMTP.sent), 1)
        log = self.state / 'push_unlisted.log'
        self.assertTrue(log.exists())
        self.assertIn('stranger@example.com', log.read_text(encoding='utf-8'))

    def test_refusal_message_explains_how_to_authorize(self):
        with self.assertRaises(push.PushError) as cm:
            push.send_report(str(self.md), 'stranger@example.com', week='2026-W43')
        msg = str(cm.exception)
        self.assertIn('push_allowlist.json', msg)   # 告诉人怎么放行
        self.assertIn('allow', msg)

    def test_invalid_address_refused(self):
        for bad in ('', 'not-an-email', 'a@'):
            with self.assertRaises(push.PushError):
                push.send_report(str(self.md), bad, week='2026-W43')


class TestDeliveryVerification(PushTestBase):
    """R239：SMTP 接受 ≠ 收件方收到。必须有独立的送达验证通道。

    事故：把信发给 example.com（不存在的域名），SMTP 照样返回成功、
    照样写进幂等表、照样报"已推送" —— 如果当真，客户永远收不到而我们不知道。
    """

    def test_verify_reports_not_delivered_when_imap_finds_nothing(self):
        v = push.verify_delivery('a@b.com', subject_contains='2026-W43',
                                 imap_host='imap.invalid', imap_user='u', imap_password='p')
        self.assertFalse(v['delivered'])
        self.assertIn('error', v)

    def test_verify_without_credentials_returns_not_verified(self):
        v = push.verify_delivery('a@b.com', subject_contains='x')
        self.assertFalse(v['delivered'])
        self.assertIn('凭据', v.get('error', ''))

    def test_send_result_flags_smtp_accepted_not_delivered(self):
        (self.state / 'push_allowlist.json').write_text(
            '{"allow": ["a@b.com"]}', encoding='utf-8')
        res = push.send_report(str(self.md), 'a@b.com', week='2026-W43')
        self.assertTrue(res['ok'])
        self.assertFalse(res['verified'])          # 明确：SMTP 接受 ≠ 已送达
        self.assertIn('未验证收件方实际收到', res['note'])


if __name__ == '__main__':


    unittest.main()
