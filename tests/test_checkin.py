import base64
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "auto_check_in_ikuuu.py"


def load_monitor():
    spec = importlib.util.spec_from_file_location("ikuuu_checkin_retry", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Response:
    def __init__(self, url, payload=None, text="", content_type="text/html"):
        self.url = url
        self.status_code = 200
        self._payload = payload
        self.text = text
        self.headers = {"Content-Type": content_type}

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class CheckinTests(unittest.TestCase):
    def test_checkin_retries_after_login_page_then_succeeds(self):
        monitor = load_monitor()
        session = mock.Mock()
        session.post.side_effect = [
            Response("https://ikuuu.example/auth/login", text="<html></html>"),
            Response(
                "https://ikuuu.example/user/checkin",
                payload={"ret": 1, "msg": "签到成功"},
            ),
        ]

        with mock.patch.object(
            monitor, "get_remaining_flow", return_value=("10", "GB")
        ) as get_remaining_flow, mock.patch.object(monitor.time, "sleep") as sleep:
            success, message, flow, unit = monitor.do_checkin_with_session(
                session, "https://ikuuu.example"
            )

        self.assertTrue(success)
        self.assertIn("签到成功", message)
        self.assertEqual((flow, unit), ("10", "GB"))
        self.assertEqual(session.post.call_count, 2)
        get_remaining_flow.assert_called_once_with(session, "https://ikuuu.example")
        sleep.assert_called_once_with(2)
        headers = session.post.call_args.kwargs["headers"]
        self.assertEqual(headers["Origin"], "https://ikuuu.example")
        self.assertEqual(headers["Referer"], "https://ikuuu.example/user")

    def test_remaining_flow_uses_authenticated_session_and_actual_domain(self):
        monitor = load_monitor()
        html = (
            '<div class="card card-statistic-2"><h4>剩余流量</h4>'
            '<span class="counter">12.34</span> GB</div>'
        )
        import base64

        wrapped = 'var originBody = "' + base64.b64encode(html.encode()).decode() + '"'
        response = Response("https://ikuuu.example/user", text=wrapped)
        session = mock.Mock()
        session.get.return_value = response

        flow, unit = monitor.get_remaining_flow(session, "https://ikuuu.example")

        self.assertEqual((flow, unit), ("12.34", "GB"))
        session.get.assert_called_once_with(
            "https://ikuuu.example/user",
            headers={"User-Agent": monitor.USER_AGENT},
            timeout=20,
            allow_redirects=True,
        )


class CookieCacheTests(unittest.TestCase):
    def setUp(self):
        self.monitor = load_monitor()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cookie_file = os.path.join(self.tmp.name, "cookies.json")
        self.cache_file = os.path.join(self.tmp.name, "cookie.enc")
        patcher = mock.patch.object(self.monitor, "COOKIE_FILE", self.cookie_file)
        patcher.start()
        self.addCleanup(patcher.stop)
        env = mock.patch.dict(os.environ, {
            "IKUUU_COOKIE_KEY": "test-passphrase",
            "IKUUU_COOKIE_CACHE_FILE": self.cache_file,
        })
        env.start()
        self.addCleanup(env.stop)

    def test_key_requires_passphrase_and_is_32_bytes(self):
        self.assertEqual(len(self.monitor.cookie_cache_key()), 32)
        with mock.patch.dict(os.environ, {"IKUUU_COOKIE_KEY": ""}):
            self.assertIsNone(self.monitor.cookie_cache_key())

    def test_encrypt_decrypt_roundtrip(self):
        store = {"a@@b": {"cookies": {"sid": "x"}, "saved_at": 1}}
        key = self.monitor.cookie_cache_key()
        blob = self.monitor.encrypt_cookie_store(store, key)
        self.assertNotIn("sid", blob)
        self.assertEqual(self.monitor.decrypt_cookie_store(blob, key), store)

    def test_wrong_key_or_tampered_blob_yields_empty(self):
        key = self.monitor.cookie_cache_key()
        blob = self.monitor.encrypt_cookie_store({"k": {"v": 1}}, key)
        self.assertEqual(self.monitor.decrypt_cookie_store(blob, b"\0" * 32), {})
        raw = bytearray(base64.b64decode(blob))
        raw[-1] ^= 0xFF
        self.assertEqual(self.monitor.decrypt_cookie_store(base64.b64encode(bytes(raw)).decode(), key), {})
        self.assertEqual(self.monitor.decrypt_cookie_store("not-base64!!", key), {})

    def test_restore_loads_cached_session_into_cookie_file(self):
        store = {"a@@b": {"cookies": {"sid": "x"}, "saved_at": 1}}
        with open(self.cache_file, "w", encoding="ascii") as handle:
            handle.write(self.monitor.encrypt_cookie_store(store, self.monitor.cookie_cache_key()))
        self.assertTrue(self.monitor.restore_encrypted_cookie_cache())
        self.assertEqual(self.monitor.load_cookie_store(), store)

    def test_restore_without_key_or_file_is_noop(self):
        self.assertFalse(self.monitor.restore_encrypted_cookie_cache())
        with open(self.cache_file, "w", encoding="ascii") as handle:
            handle.write(self.monitor.encrypt_cookie_store({"k": {}}, self.monitor.cookie_cache_key()))
        with mock.patch.dict(os.environ, {"IKUUU_COOKIE_KEY": ""}):
            self.assertFalse(self.monitor.restore_encrypted_cookie_cache())

    def test_persist_writes_decryptable_cache(self):
        store = {"a@@b": {"cookies": {"sid": "x"}, "saved_at": 1}}
        self.monitor.save_cookie_store(store)
        self.assertTrue(self.monitor.persist_encrypted_cookie_cache())
        with open(self.cache_file, "r", encoding="ascii") as handle:
            blob = handle.read()
        self.assertEqual(self.monitor.decrypt_cookie_store(blob, self.monitor.cookie_cache_key()), store)


class SessionCookieTests(unittest.TestCase):
    def setUp(self):
        self.monitor = load_monitor()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cookie_file = os.path.join(self.tmp.name, "cookies.json")
        patcher = mock.patch.object(self.monitor, "COOKIE_FILE", self.cookie_file)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_old_cookie_is_reused_without_age_cap(self):
        store = {"a@@b": {"email": "a", "base_url": "https://b",
                          "saved_at": 1, "cookies": {"sid": "x"}}}
        self.monitor.save_cookie_store(store)
        self.assertEqual(self.monitor.load_session_cookie("a", "https://b"), {"sid": "x"})

    def test_missing_or_empty_cookie_returns_none(self):
        self.assertIsNone(self.monitor.load_session_cookie("a", "https://b"))
        self.monitor.save_cookie_store({"a@@b": {"cookies": {}}})
        self.assertIsNone(self.monitor.load_session_cookie("a", "https://b"))


if __name__ == "__main__":
    unittest.main()
