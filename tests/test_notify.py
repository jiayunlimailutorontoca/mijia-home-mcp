"""notify 模块测试:变化过滤、口播文案、音箱选择、三家推送格式(离线)。"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from mijia_home_mcp.client import HomeClient
from mijia_home_mcp.notify import (
    Pusher,
    SpeakerNotifier,
    filter_changes,
    format_changes_text,
    send_dingtalk,
    send_feishu,
    send_meow,
)

CHANGES = [
    {"type": "prop_changed", "device": "客厅台灯", "prop": "on", "from": True, "to": False},
    {"type": "prop_changed", "device": "洗碗机", "prop": "left-time", "from": 30, "to": 29},
    {"type": "went_offline", "device": "阳台传感器"},
]


def test_filter_only():
    out = filter_changes(CHANGES, only=["客厅*"])
    assert [c["device"] for c in out] == ["客厅台灯"]


def test_filter_ignore_by_prop_and_device():
    out = filter_changes(CHANGES, ignore=["left-time"])
    assert all(c.get("prop") != "left-time" for c in out)
    out2 = filter_changes(CHANGES, ignore=["阳台*"])
    assert all(c["device"] != "阳台传感器" for c in out2)


def test_filter_none_passthrough():
    assert filter_changes(CHANGES) == CHANGES


def test_format_changes_text_limit_and_suffix():
    text = format_changes_text(CHANGES, limit=2)
    assert "客厅台灯" in text and "洗碗机" in text
    assert "另有1项变化" in text
    assert "阳台传感器" not in text


def test_format_offline_text():
    text = format_changes_text([CHANGES[2]])
    assert text == "阳台传感器离线了"


def test_speaker_notifier_selects_and_announces(fake_api, settings):
    client = HomeClient(fake_api, settings)
    notifier = SpeakerNotifier(client)
    assert notifier.name == "小爱音箱"
    notifier.announce("测试播报")
    assert fake_api.action_calls[-1]["in"] == ["测试播报"]
    # play-text 的 siid/aiid 来自假 spec
    assert fake_api.action_calls[-1]["aiid"] == 5


def test_speaker_notifier_unknown_name(fake_api, settings):
    client = HomeClient(fake_api, settings)
    with pytest.raises(ValueError, match="未找到"):
        SpeakerNotifier(client, "不存在的音箱")


# ---------------- 推送 provider(本地 HTTP 接收端) ----------------


@pytest.fixture
def http_sink():
    """本地 HTTP 服务,记录收到的 (path, query, json_body)。"""
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            path, _, query = self.path.partition("?")
            received.append((path, query, json.loads(body) if body else None))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"errcode":0,"code":0,"status":200}')

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", received
    server.shutdown()


def test_send_dingtalk_format(http_sink):
    base, received = http_sink
    send_dingtalk(f"{base}/robot/send?access_token=tk", "米家提醒", "灯开了")
    path, query, body = received[0]
    assert body == {"msgtype": "text", "text": {"content": "米家提醒\n灯开了"}}


def test_send_dingtalk_signed(http_sink):
    base, received = http_sink
    send_dingtalk(
        f"{base}/robot/send?access_token=tk", "米家提醒", "灯开了", secret="SECxxx"
    )
    _, query, _ = received[0]
    assert "timestamp=" in query and "sign=" in query


def test_send_feishu_format(http_sink):
    base, received = http_sink
    send_feishu(f"{base}/open-apis/bot/v2/hook/xxx", "米家提醒", "灯开了")
    _, _, body = received[0]
    assert body == {"msg_type": "text", "content": {"text": "米家提醒\n灯开了"}}


def test_send_feishu_signed(http_sink):
    base, received = http_sink
    send_feishu(f"{base}/hook/xxx", "米家提醒", "灯开了", secret="sec123")
    _, _, body = received[0]
    assert "timestamp" in body and "sign" in body
    # 签名可复算:HMAC(key=ts\nsecret, msg=空串) 的 base64
    import base64 as b64
    import hashlib
    import hmac as hm

    key = f"{body['timestamp']}\nsec123".encode()
    expected = b64.b64encode(hm.new(key, b"", hashlib.sha256).digest()).decode()
    assert body["sign"] == expected


def test_send_meow_nickname_and_url(http_sink):
    base, received = http_sink
    # 完整 URL 直接用
    send_meow(f"{base}/mynick", "米家提醒", "灯开了")
    path, _, body = received[0]
    assert path == "/mynick"
    assert body == {"title": "米家提醒", "msg": "灯开了"}


def test_pusher_aggregates_and_isolates_failures(http_sink):
    base, received = http_sink
    pusher = Pusher(
        feishu=f"{base}/feishu",
        meow=f"{base}/meow",
        # 指向不存在的端口 → 该通道失败但不影响其他通道
        webhook="http://127.0.0.1:1/dead",
    )
    assert pusher.channels == ["飞书", "MeoW", "webhook"]
    errors = pusher.push("米家提醒", "灯开了", {"changes": []})
    assert len(errors) == 1 and errors[0].startswith("webhook")
    assert len(received) == 2, "飞书与 MeoW 应各收到一条"
