"""更新检查。网络请求 mock 掉,不真打 GitHub。"""

from unittest.mock import patch

from mijia_home_mcp.update_check import _parse, check_latest


class FakeResp:
    def __init__(self, tag):
        self._tag = tag

    def raise_for_status(self):
        pass

    def json(self):
        return {"tag_name": self._tag}


def test_parse():
    assert _parse("v0.10.0") == (0, 10, 0)
    assert _parse("0.9.0") == (0, 9, 0)
    assert _parse("垃圾") is None


def test_outdated():
    with patch("mijia_home_mcp.update_check.requests.get", return_value=FakeResp("v0.11.0")):
        r = check_latest("0.10.0")
    assert r["status"] == "outdated"
    assert "--refresh" in r["hint"]


def test_up_to_date():
    with patch("mijia_home_mcp.update_check.requests.get", return_value=FakeResp("v0.10.0")):
        r = check_latest("0.10.0")
    assert r["status"] == "up_to_date"
    # v0.10 和 v0.9 比,数值比较而非字符串比较(字符串比 "0.9" > "0.10")
    with patch("mijia_home_mcp.update_check.requests.get", return_value=FakeResp("v0.9.0")):
        r = check_latest("0.10.0")
    assert r["status"] == "up_to_date"


def test_network_failure_is_unknown():
    with patch(
        "mijia_home_mcp.update_check.requests.get", side_effect=OSError("offline")
    ):
        r = check_latest("0.10.0")
    assert r["status"] == "unknown"
