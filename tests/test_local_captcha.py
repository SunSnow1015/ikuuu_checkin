import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import types
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch
from urllib.parse import quote, unquote_plus


ROOT = Path(__file__).resolve().parents[1]
CAPTCHA_ID = "f" * 32
BASE_URL = "https://ikuuu.example"
LOT_NUMBER = "0123456789abcdef0123456789abcdef"
RESULT_FIELDS = ("lot_number", "captcha_output", "pass_token", "gen_time")


def load_module(filename, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    with patch.object(sys, "dont_write_bytecode", True):
        spec.loader.exec_module(module)
    return module


def credentials(**changes):
    result = {
        "lot_number": LOT_NUMBER,
        "captcha_output": "synthetic-output",
        "pass_token": "synthetic-token",
        "gen_time": "1700000000",
    }
    result.update(changes)
    return result


def challenge(captcha_type="icon"):
    return {
        "captcha_type": captcha_type,
        "lot_number": LOT_NUMBER,
        "imgs": "/fixtures/background.png",
        "ques": ["/fixtures/prompt.png"],
        "static_path": "/fixtures/client",
        "payload": "synthetic-payload",
        "process_token": "synthetic-process-token",
        "payload_protocol": 1,
        "pt": "0",
    }


def script_fixture(body=None, table=None):
    # A synthetic XOR string table, not a captured script or an executable fixture.
    if table is None:
        table = ["_lib", "synthetic-seed", "_abo", "n[0:1]", "n[2:3]"]
    key = "K3"
    encrypted = "".join(
        chr(ord(char) ^ ord(key[index % len(key)]))
        for index, char in enumerate("^".join(table))
    )
    if body is None:
        body = (
            "client[_abcd(0)] = {seed: _abcd(1)};\n"
            "client[_abcd(2)] = {_abcd(3): _abcd(4)};\n"
        )
    return (
        'decodeURI("' + quote(encrypted, safe="") + '");\n'
        + '}}}("' + key + '")}\n'
        + body
        + '\nthrow new Error("This fixture must never execute");'
    )


class OfflineTestCase(unittest.TestCase):
    def setUp(self):
        self.contexts = ExitStack()
        self.addCleanup(self.contexts.close)
        # Even a regression must not reach a real captcha/login API or worker.
        self.contexts.enter_context(patch.dict(os.environ, {"IKUUU_DATA_DIR": str(ROOT)}, clear=True))
        self.contexts.enter_context(patch.object(sys, "path", list(sys.path)))
        self.request = self.contexts.enter_context(patch(
            "requests.sessions.Session.request",
            side_effect=AssertionError("Real HTTP requests are forbidden in these tests"),
        ))
        self.connect = self.contexts.enter_context(patch(
            "socket.socket.connect",
            side_effect=AssertionError("Real sockets are forbidden in these tests"),
        ))
        self.popen = self.contexts.enter_context(patch(
            "subprocess.Popen",
            side_effect=AssertionError("Real subprocesses are forbidden in these tests"),
        ))
        for blocked in (self.request, self.connect, self.popen):
            self.addCleanup(blocked.assert_not_called)


class LoginBackendTests(OfflineTestCase):
    def setUp(self):
        super().setUp()
        with patch.dict(sys.modules, {"notify": types.SimpleNamespace(send=Mock())}):
            with patch("os.makedirs"):
                self.module = load_module("auto_check_in_ikuuu.py", "ikuuu_checkin_local_tests")

    def test_get_login_opts_defaults_to_local_without_cloud_fallback(self):
        solver = self.module.get_login_opts()["captcha_solver"]

        self.assertTrue(solver["enabled"])
        self.assertEqual(solver["provider"], "local")
        self.assertFalse(solver["fallback_provider"])

    def test_local_never_calls_cloud_even_with_keys_and_fallback(self):
        for fallback in ("capsolver", "anticaptcha"):
            for outcome in ((credentials(), None), (None, "local failed")):
                with self.subTest(fallback=fallback, success=outcome[0] is not None):
                    env = {
                        "IKUUU_CAPTCHA_PROVIDER": "local",
                        "IKUUU_CAPTCHA_FALLBACK_PROVIDER": fallback,
                        "IKUUU_CAPSOLVER_API_KEY": "dummy-capsolver-key",
                        "IKUUU_ANTICAPTCHA_API_KEY": "dummy-anticaptcha-key",
                        "CAPSOLVER_API_KEY": "dummy-legacy-key",
                        "ANTICAPTCHA_API_KEY": "dummy-legacy-key",
                    }
                    with patch.dict(os.environ, env), patch("builtins.print"):
                        cfg = self.module.get_login_opts()["captcha_solver"]
                        with patch.object(self.module, "solve_captcha_local", return_value=outcome) as local:
                            with patch.object(self.module, "solve_captcha_capsolver") as capsolver:
                                with patch.object(self.module, "solve_captcha_anticaptcha") as anticaptcha:
                                    with patch.object(self.module, "get_captcha_api_key") as api_key:
                                        result = self.module.solve_geetest_v4(
                                            BASE_URL, CAPTCHA_ID, {"riskType": "word"}, cfg
                                        )
                    self.assertEqual(result, outcome)
                    local.assert_called_once_with(BASE_URL, CAPTCHA_ID, {"riskType": "word"}, cfg)
                    capsolver.assert_not_called()
                    anticaptcha.assert_not_called()
                    api_key.assert_not_called()

    def test_local_notification_info_does_not_query_cloud_balance(self):
        with patch.dict(os.environ, {
            "IKUUU_CAPSOLVER_API_KEY": "dummy-key",
            "IKUUU_CAPTCHA_FALLBACK_PROVIDER": "capsolver",
        }):
            self.assertIn("本地", self.module.get_captcha_info())
            self.assertIn("local", self.module.get_captcha_provider_only())

    def test_disabled_notification_info_is_empty_and_uses_no_network(self):
        for provider in ("local", "capsolver", "anticaptcha"):
            for flag in ("IKUUU_CAPTCHA_SOLVER_ENABLED", "CAPTCHA_SOLVER_ENABLED"):
                with self.subTest(provider=provider, flag=flag):
                    with patch.dict(os.environ, {
                        flag: "false",
                        "IKUUU_CAPTCHA_PROVIDER": provider,
                        "IKUUU_CAPSOLVER_API_KEY": "dummy-key",
                        "IKUUU_ANTICAPTCHA_API_KEY": "dummy-key",
                    }):
                        self.assertFalse(self.module.get_login_opts()["captcha_solver"]["enabled"])
                        self.assertIsNone(self.module.get_captcha_info())
                        self.assertIsNone(self.module.get_captcha_provider_only())

    def run_worker_result(self, stdout, returncode=0):
        completed = subprocess.CompletedProcess(["mock-worker"], returncode, stdout, "diagnostics")
        with patch.object(self.module.subprocess, "run", return_value=completed) as run:
            result = self.module.solve_captcha_local(
                BASE_URL, CAPTCHA_ID, {"riskType": "word"},
                {"timeout_seconds": 7, "max_attempts": 2},
            )
        return result, run

    def test_local_worker_success_extracts_only_credentials_and_passes_config(self):
        (solution, error), run = self.run_worker_result(json.dumps({
            "solution": credentials(captcha_id=CAPTCHA_ID, debug="not-a-credential"),
            "debug": "not-a-credential",
        }))

        self.assertEqual(solution, credentials())
        self.assertIsNone(error)
        run.assert_called_once()
        args, kwargs = run.call_args
        self.assertEqual(args, ([sys.executable, str(ROOT / "local_captcha.py")],))
        self.assertEqual(json.loads(kwargs["input"]), {
            "captcha_id": CAPTCHA_ID,
            "base_url": BASE_URL,
            "timeout_seconds": 7,
            "max_attempts": 2,
            "risk_type": "word",
        })
        self.assertEqual(kwargs["timeout"], 7)
        self.assertTrue(kwargs["capture_output"])
        self.assertEqual(kwargs["encoding"], "utf-8")
        self.assertFalse(kwargs["check"])

    def test_local_worker_timeout_returns_no_credentials(self):
        with patch.object(self.module.subprocess, "run", side_effect=subprocess.TimeoutExpired("mock-worker", 7)):
            solution, error = self.module.solve_captcha_local(
                BASE_URL, CAPTCHA_ID, {}, {"timeout_seconds": 7}
            )
        self.assertIsNone(solution)
        self.assertIn("超时", error)

    def test_local_worker_non_json_and_non_object_results_are_rejected(self):
        for stdout in ("", "not JSON", '<html>error</html>', 'log\n{"solution": {}}', "null", "[]", '"text"'):
            with self.subTest(stdout=stdout):
                (solution, error), _ = self.run_worker_result(stdout)
                self.assertIsNone(solution)
                self.assertTrue(error)

    def test_local_worker_failure_does_not_return_credentials(self):
        for payload in ({"error": "synthetic failure"}, {"solution": credentials()}):
            with self.subTest(payload=payload):
                (solution, error), _ = self.run_worker_result(json.dumps(payload), returncode=1)
                self.assertIsNone(solution)
                self.assertTrue(error)
                if "error" in payload:
                    self.assertEqual(error, payload["error"])

    def test_local_worker_error_without_solution_is_reported(self):
        (solution, error), _ = self.run_worker_result(json.dumps({"error": "synthetic failure"}))
        self.assertIsNone(solution)
        self.assertEqual(error, "synthetic failure")

    def test_local_worker_incomplete_or_invalid_credentials_are_rejected(self):
        invalid = [None, [], {}, "not a solution"]
        for field in RESULT_FIELDS:
            missing = credentials()
            missing.pop(field)
            invalid.append(missing)
            invalid.extend(credentials(**{field: value}) for value in ("", None, 123, False))
        for value in invalid:
            with self.subTest(solution=value):
                (solution, error), _ = self.run_worker_result(json.dumps({"solution": value}))
                self.assertIsNone(solution)
                self.assertTrue(error)


class ProtocolTests(OfflineTestCase):
    def setUp(self):
        super().setUp()
        self.protocol = load_module("geetest_protocol.py", "geetest_protocol_tests")

    def test_normalize_points_uses_real_non_square_dimensions_without_mutation(self):
        points = [(0, 0), (80, 30), (160, 60), (319, 119)]
        original = list(points)
        self.assertEqual(
            self.protocol.normalize_points(points, (320, 120)),
            [[0, 0], [2500, 2500], [5000, 5000], [9969, 9917]],
        )
        self.assertEqual(points, original)
        self.assertEqual(self.protocol.normalize_points([(0.5, 0.25)], (2, 1)), [[2500, 2500]])

    def test_normalize_points_rejects_out_of_bounds_and_non_finite_values(self):
        for point in ((-1, 0), (0, -1), (320, 1), (1, 120), (5000, 5000),
                      (float("nan"), 0), (0, float("nan")), (float("inf"), 0), (0, -float("inf"))):
            with self.subTest(point=point):
                with self.assertRaises(self.protocol.ProtocolError):
                    self.protocol.normalize_points([point], (320, 120))

    def test_normalize_points_rejects_nonpositive_dimensions(self):
        for size in ((0, 120), (320, 0), (-1, 120), (320, -1)):
            with self.subTest(size=size):
                with self.assertRaises(self.protocol.ProtocolError):
                    self.protocol.normalize_points([(0, 0)], size)

    def test_make_w_normalizes_pixel_coordinates_exactly_once(self):
        data = {**challenge(), "pow_detail": {}}
        points = [(80, 30)]
        with patch.object(self.protocol, "proof_of_work", return_value={"pow_msg": "msg", "pow_sign": "sign"}):
            with patch.object(self.protocol, "normalize_points", wraps=self.protocol.normalize_points) as normalize:
                encoded = self.protocol.make_w(data, CAPTCHA_ID, points, (320, 120), 42, ({}, {}), 100)
        payload = json.loads(unquote_plus(encoded))
        self.assertEqual(payload["userresponse"], [[2500, 2500]])
        self.assertEqual(points, [(80, 30)])
        normalize.assert_called_once_with(points, (320, 120))

    def pow_detail(self, bits=5, algorithm="sha256"):
        return {"hashfunc": algorithm, "bits": bits, "version": "1", "datetime": "synthetic-time"}

    def test_proof_of_work_checks_partial_byte_leading_bits_for_each_hash(self):
        for algorithm in ("md5", "sha1", "sha256"):
            with self.subTest(algorithm=algorithm):
                detail = self.pow_detail(5, algorithm)
                prefix = f"1|5|{algorithm}|synthetic-time|{CAPTCHA_ID}|{LOT_NUMBER}||"
                # Deterministically offer a non-solution before a genuine 5-bit solution.
                nonces = [f"{number:016x}" for number in range(4096)]
                digest = lambda nonce: hashlib.new(algorithm, (prefix + nonce).encode()).digest()
                invalid = next(nonce for nonce in nonces if digest(nonce)[0] >= 8)
                valid = next(nonce for nonce in nonces if 0 < digest(nonce)[0] < 8)
                with patch.object(self.protocol.time, "monotonic", side_effect=[0, 0, 10]):
                    with patch.object(self.protocol.secrets, "token_hex", side_effect=[invalid, valid]) as token:
                        result = self.protocol.proof_of_work(detail, CAPTCHA_ID, LOT_NUMBER, 10)
                self.assertEqual(token.call_count, 2)
                self.assertEqual(result["pow_msg"], prefix + valid)
                self.assertEqual(result["pow_sign"], hashlib.new(algorithm, result["pow_msg"].encode()).hexdigest())
                self.assertEqual(bytes.fromhex(result["pow_sign"])[0] >> 3, 0)
                self.assertNotEqual(bytes.fromhex(result["pow_sign"])[0], 0)

    def test_proof_of_work_zero_bits_accepts_first_digest(self):
        with patch.object(self.protocol.time, "monotonic", side_effect=[0, 10]):
            with patch.object(self.protocol.secrets, "token_hex", return_value="0" * 16) as token:
                result = self.protocol.proof_of_work(self.pow_detail(0), CAPTCHA_ID, LOT_NUMBER, 10)
        token.assert_called_once()
        self.assertEqual(result["pow_sign"], hashlib.sha256(result["pow_msg"].encode()).hexdigest())

    def test_proof_of_work_rejects_invalid_bit_counts_and_hashes(self):
        for bits in (-1, 25, "4", 1.5, None, True, False):
            with self.subTest(bits=bits):
                # Parameter validation must be deterministic, not depend on a random nonce.
                with patch.object(self.protocol.time, "monotonic", return_value=10):
                    with self.assertRaises(self.protocol.ProtocolError):
                        self.protocol.proof_of_work(self.pow_detail(bits), CAPTCHA_ID, LOT_NUMBER, 10)
        for algorithm in ("sha512", "SHA256", "unknown"):
            with self.subTest(algorithm=algorithm):
                with self.assertRaises(self.protocol.ProtocolError):
                    self.protocol.proof_of_work(self.pow_detail(algorithm=algorithm), CAPTCHA_ID, LOT_NUMBER, 10)

    def test_proof_of_work_deadline_inclusive_and_max_bits_allowed(self):
        for bits in (0, 24):
            with self.subTest(bits=bits):
                with patch.object(self.protocol.time, "monotonic", return_value=10):
                    with patch.object(self.protocol.secrets, "token_hex") as token:
                        with self.assertRaises(TimeoutError):
                            self.protocol.proof_of_work(self.pow_detail(bits), CAPTCHA_ID, LOT_NUMBER, 10)
                token.assert_not_called()

    def test_proof_of_work_times_out_after_unsuccessful_attempt(self):
        prefix = f"1|24|sha256|synthetic-time|{CAPTCHA_ID}|{LOT_NUMBER}||"
        nonce = "0" * 16
        self.assertNotEqual(hashlib.sha256((prefix + nonce).encode()).digest()[:3], b"\0\0\0")
        with patch.object(self.protocol.time, "monotonic", side_effect=[0, 10]):
            with patch.object(self.protocol.secrets, "token_hex", return_value=nonce) as token:
                with self.assertRaises(TimeoutError):
                    self.protocol.proof_of_work(self.pow_detail(24), CAPTCHA_ID, LOT_NUMBER, 10)
        token.assert_called_once()

    def test_lot_value_includes_slice_right_endpoint_and_joins_parts(self):
        self.assertEqual(self.protocol.lot_value("n[0:0]", LOT_NUMBER), "0")
        self.assertEqual(self.protocol.lot_value("n[30:31]", LOT_NUMBER), "ef")
        self.assertEqual(self.protocol.lot_value("(n[0:1]+n[4:5])+.+(n[8:9])", LOT_NUMBER), "0145.89")

    def test_lot_value_rejects_bounds_and_does_not_execute_expressions(self):
        expressions = (
            "", "n[-1:2]", "n[3:2]", "n[0:32]", "n[0:999]", "n[0]", "n[0:1:2]",
            "n[0:1]+__import__('os').system('should-not-run')",
            "__import__('os').system('should-not-run')", "n[0:1];alert(1)",
        )
        with patch("builtins.eval", side_effect=AssertionError("Expressions must not be evaluated")) as evaluate:
            with patch("os.system", side_effect=AssertionError("Commands must not execute")) as system:
                for expression in expressions:
                    with self.subTest(expression=expression):
                        with self.assertRaises(self.protocol.ProtocolError):
                            self.protocol.lot_value(expression, LOT_NUMBER)
        evaluate.assert_not_called()
        system.assert_not_called()

    def test_parse_script_config_decodes_synthetic_xor_without_execution(self):
        script = script_fixture()
        with patch("builtins.eval", side_effect=AssertionError("No eval")) as evaluate:
            with patch("builtins.exec", side_effect=AssertionError("No exec")) as execute:
                result = self.protocol.parse_script_config(script)
        self.assertEqual(result, ({"seed": "synthetic-seed"}, {"n[0:1]": "n[2:3]"}))
        evaluate.assert_not_called()
        execute.assert_not_called()

    def test_parse_script_config_rejects_oversized_input_and_tables(self):
        scripts = (
            'x' * (2 * 1024 * 1024 + 1),
            script_fixture(table=['x'] * 16385),
            script_fixture(table=['x' * 65537]),
        )
        for script in scripts:
            with self.subTest(size=len(script)):
                with self.assertRaisesRegex(self.protocol.ProtocolError, '过大'):
                    self.protocol.parse_script_config(script)

    def test_parse_script_config_bounds_cumulative_expansion(self):
        script = script_fixture(body='_abcd(0);' * 2050, table=['x' * 4096])
        self.assertLess(len(script), 100_000)
        with self.assertRaisesRegex(self.protocol.ProtocolError, '展开结果过大'):
            self.protocol.parse_script_config(script)

    def test_parse_script_config_rejects_changed_structure_and_string_table(self):
        scripts = (
            "", "var newProtocol = {};", script_fixture().replace("decodeURI", "decodeURIComponent"),
            script_fixture().replace("_abcd(4)", "_abcd(99)"),
            script_fixture('client[_abcd(0)] = {seed: _abcd(1)};'),
            script_fixture('client[_abcd(0)] = {seed: 123}; client[_abcd(2)] = {_abcd(3): _abcd(4)};'),
            script_fixture('client[_abcd(0)] = {seed: runJavaScript()}; client[_abcd(2)] = {_abcd(3): _abcd(4)};'),
        )
        for script in scripts:
            with self.subTest(script=script):
                # JSONDecodeError is also an explicit parse failure, not executable JS.
                with self.assertRaises(ValueError):
                    self.protocol.parse_script_config(script)


class LocalCaptchaTests(OfflineTestCase):
    def setUp(self):
        super().setUp()
        self.protocol = load_module("geetest_protocol.py", "geetest_protocol_local_tests")
        with patch.dict(sys.modules, {"geetest_protocol": self.protocol}):
            self.module = load_module("local_captcha.py", "local_captcha_tests")
        self.session = Mock()
        self.session.headers = {}
        with patch.object(self.module.requests, "Session", return_value=self.session):
            self.solver = self.module.LocalCaptcha(CAPTCHA_ID, BASE_URL, timeout_seconds=120)
        self.addCleanup(self.solver.close)

    def test_asset_url_allows_only_official_https_hosts(self):
        cases = {
            "fixtures/image.png": "https://static.geetest.com/fixtures/image.png",
            "/fixtures/image.png": "https://static.geetest.com/fixtures/image.png",
            "https://static.geetest.com:443/a.png": "https://static.geetest.com:443/a.png",
            "https://static.geevisit.com/a.png": "https://static.geevisit.com/a.png",
            "//static.geevisit.com/a.png": "https://static.geevisit.com/a.png",
        }
        for path, expected in cases.items():
            with self.subTest(path=path):
                self.assertEqual(self.module.asset_url(path), expected)

    def test_asset_url_rejects_untrusted_hosts_schemes_and_ports(self):
        for path in (None, "", 123, "https://evil.example/image.png", "//evil.example/image.png",
                     "http://static.geetest.com/image.png", "ftp://static.geetest.com/image.png",
                     "https://static.geetest.com.evil.example/image.png",
                     "https://static.geetest.com@evil.example/image.png",
                     "https://static.geetest.com:444/image.png", "javascript:alert(1)"):
            with self.subTest(path=path):
                with self.assertRaises(self.protocol.ProtocolError):
                    self.module.asset_url(path)

    def test_asset_url_rejects_all_userinfo_including_empty_userinfo(self):
        for userinfo in ("user@", "user:password@", ":password@", "@", ":@"):
            with self.subTest(userinfo=userinfo):
                with self.assertRaises(self.protocol.ProtocolError):
                    self.module.asset_url("https://" + userinfo + "static.geetest.com/a.png")

    def test_fetch_rejects_evil_redirect_without_following_it(self):
        for status in (301, 302, 303, 307, 308):
            with self.subTest(status=status):
                response = MagicMock()
                response.__enter__.return_value = response
                response.status_code = status
                response.headers = {"Location": "https://evil.example/stolen-resource"}
                self.session.get.reset_mock()
                self.session.get.return_value = response
                url = self.module.asset_url("/fixtures/image.png")
                with self.assertRaises(self.protocol.ProtocolError):
                    self.solver.fetch(url)
                self.session.get.assert_called_once()
                self.assertEqual(self.session.get.call_args.args, (url,))
                self.assertFalse(self.session.get.call_args.kwargs["allow_redirects"])
                response.iter_content.assert_not_called()

    def test_parse_response_accepts_json_and_matching_jsonp(self):
        data = {"captcha_type": "icon", "label": "synthetic"}
        text = json.dumps({"status": "success", "data": data})
        for content in (text, " \n" + text + "\n", "geetest_123(" + text + ")", "geetest_123(" + text + ");"):
            with self.subTest(content=content):
                self.assertEqual(self.module.parse_response(content.encode(), "geetest_123"), data)

    def test_parse_response_rejects_wrong_callback_and_trailing_code(self):
        text = '{"status":"success","data":{}}'
        for content in ("evil(" + text + ");", "prefix_geetest_123(" + text + ");",
                        "geetest_123(" + text + ");alert(1)", "geetest_123(" + text + ");;",
                        "geetest_123(" + text, "<html>not JSON</html>"):
            with self.subTest(content=content):
                # Both protocol rejection and JSONDecodeError reject appended script text.
                with self.assertRaises(ValueError):
                    self.module.parse_response(content.encode(), "geetest_123")
        with self.assertRaises(self.protocol.ProtocolError):
            self.module.parse_response(("geetestX123(" + text + ");").encode(), "geetest.123")

    def test_parse_response_rejects_failure_and_invalid_envelopes(self):
        for envelope in ({"status": "fail", "data": {}}, {"data": {}},
                         {"status": "success", "data": []}, {"status": "success", "data": None},
                         {"status": "success"}, []):
            with self.subTest(envelope=envelope):
                with self.assertRaises(self.protocol.ProtocolError):
                    self.module.parse_response(("geetest_123(" + json.dumps(envelope) + ");").encode(), "geetest_123")
        with self.assertRaises(ValueError):
            self.module.parse_response(b'{"status":', "geetest_123")

    def test_call_api_uses_the_same_callback_for_request_and_response(self):
        def respond(url, params, limit):
            self.assertEqual(url, self.module.API_BASE + "/load")
            self.assertEqual(params["captcha_id"], CAPTCHA_ID)
            self.assertEqual(limit, 1_000_000)
            return (params["callback"] + '({"status":"success","data":{"value":42}});').encode()

        with patch.object(self.solver, "fetch", side_effect=respond) as fetch:
            self.assertEqual(self.solver.call_api("/load", {"captcha_id": CAPTCHA_ID}), {"value": 42})
        fetch.assert_called_once()

    def test_load_and_recognize_dispatch_on_actual_type_not_requested_risk(self):
        for actual, requested in (("icon", "word"), ("word", "icon")):
            with self.subTest(actual=actual, requested=requested):
                self.solver.risk_type = requested
                self.solver.word_recognizer = None
                images = types.ModuleType("captcha_images")
                images.match_icons = Mock(return_value=([(11, 22)], (320, 120)))
                images.WordRecognizer = Mock()
                images.WordRecognizer.return_value.match.return_value = ([(33, 44)], (320, 120))
                with patch.object(self.solver, "call_api", return_value=challenge(actual)) as api:
                    loaded = self.solver.load()
                self.assertEqual(loaded["captcha_type"], actual)
                self.assertEqual(api.call_args.args[0], "/load")
                self.assertEqual(api.call_args.args[1]["risk_type"], requested)
                self.assertEqual(api.call_args.args[1]["captcha_id"], CAPTCHA_ID)
                with patch.dict(sys.modules, {"captcha_images": images}):
                    # Recognition internals are deliberately outside this protocol test.
                    with patch.object(self.solver, "fetch", side_effect=[b"synthetic-background", b"synthetic-prompt"]):
                        result = self.solver.recognize(loaded)
                if actual == "icon":
                    self.assertEqual(result, ([(11, 22)], (320, 120)))
                    images.match_icons.assert_called_once_with(b"synthetic-background", [b"synthetic-prompt"])
                    images.WordRecognizer.assert_not_called()
                else:
                    self.assertEqual(result, ([(33, 44)], (320, 120)))
                    images.match_icons.assert_not_called()
                    images.WordRecognizer.assert_called_once_with()
                    images.WordRecognizer.return_value.match.assert_called_once_with(b"synthetic-background", [b"synthetic-prompt"])

    def test_load_does_not_force_a_risk_type_by_default(self):
        with patch.object(self.solver, "call_api", return_value=challenge()) as api:
            self.solver.load()
        self.assertNotIn("risk_type", api.call_args.args[1])

    def test_load_rejects_unknown_types_before_fetching_assets(self):
        for kind in (None, "", "slide", "ai", "ICON"):
            with self.subTest(kind=kind):
                with patch.object(self.solver, "call_api", return_value=challenge(kind)):
                    with patch.object(self.solver, "fetch") as fetch:
                        with self.assertRaises(self.protocol.ProtocolError):
                            self.solver.load()
                fetch.assert_not_called()

    def test_load_rejects_missing_or_invalid_prompt_lists(self):
        for prompts in (None, [], {}, "image.png", ["image.png"] * 6):
            with self.subTest(prompts=prompts):
                with patch.object(self.solver, "call_api", return_value={**challenge(), "ques": prompts}):
                    with self.assertRaises(self.protocol.ProtocolError):
                        self.solver.load()

    def prepare_verify(self, response):
        fetch = self.contexts.enter_context(patch.object(self.solver, "fetch", return_value=script_fixture().encode()))
        make_w = self.contexts.enter_context(patch.object(self.module, "make_w", return_value="synthetic-w"))
        api = self.contexts.enter_context(patch.object(self.solver, "call_api", return_value=response))
        return fetch, make_w, api

    def test_verify_returns_only_server_seccode_and_uses_actual_challenge(self):
        data = challenge("word")
        fetch, make_w, api = self.prepare_verify({
            "result": "success",
            "seccode": credentials(captcha_id=CAPTCHA_ID, extra="discard"),
            **credentials(captcha_output="untrusted-top-level-output"),
        })
        result = self.solver.verify(data, [(80, 30)], (320, 120), 250)

        self.assertEqual(result, credentials())
        fetch.assert_called_once_with("https://static.geetest.com/fixtures/client/js/gcaptcha4.js")
        make_w.assert_called_once_with(
            data, CAPTCHA_ID, [(80, 30)], (320, 120), 250,
            ({"seed": "synthetic-seed"}, {"n[0:1]": "n[2:3]"}), self.solver.deadline,
        )
        api.assert_called_once_with("/verify", {
            "captcha_id": CAPTCHA_ID,
            "client_type": "web",
            "lot_number": LOT_NUMBER,
            "risk_type": "word",
            "payload": data["payload"],
            "process_token": data["process_token"],
            "payload_protocol": data["payload_protocol"],
            "pt": "0",
            "w": "synthetic-w",
        })

    def test_verify_reuses_parsed_script_config(self):
        fetch, _, api = self.prepare_verify({"result": "success", "seccode": credentials()})
        with patch.object(self.module, "parse_script_config", wraps=self.module.parse_script_config) as parse:
            for _ in range(2):
                self.assertEqual(self.solver.verify(challenge(), [(80, 30)], (320, 120), 250), credentials())
        fetch.assert_called_once()
        parse.assert_called_once()
        self.assertEqual(api.call_count, 2)

    def test_verify_rejects_success_or_credentials_outside_seccode(self):
        _, _, api = self.prepare_verify({})
        for result in ({"result": "success"}, {"result": "success", **credentials()},
                       {"result": "fail"}, {"seccode": "not-an-object"}):
            with self.subTest(result=result):
                api.return_value = result
                with self.assertRaises(self.module.VerificationError):
                    self.solver.verify(challenge(), [(80, 30)], (320, 120), 250)

    def test_verify_rejects_incomplete_or_invalid_seccode(self):
        _, _, api = self.prepare_verify({})
        for field in RESULT_FIELDS:
            missing = credentials()
            missing.pop(field)
            invalid = [missing] + [credentials(**{field: value}) for value in (None, "", 123, False)]
            for seccode in invalid:
                with self.subTest(field=field, seccode=seccode):
                    api.return_value = {"result": "success", "seccode": seccode}
                    with self.assertRaises(self.module.VerificationError):
                        self.solver.verify(challenge(), [(80, 30)], (320, 120), 250)

    def test_verify_rejects_mismatched_lot_or_captcha_id(self):
        _, _, api = self.prepare_verify({})
        for seccode in (credentials(lot_number="different-lot"), credentials(captcha_id="a" * 32)):
            with self.subTest(seccode=seccode):
                api.return_value = {"result": "success", "seccode": seccode}
                with self.assertRaises(self.protocol.ProtocolError):
                    self.solver.verify(challenge(), [(80, 30)], (320, 120), 250)

    def test_verify_explicit_failure_must_not_return_credentials(self):
        self.prepare_verify({"result": "fail", "seccode": credentials()})
        with self.assertRaises(self.module.VerificationError):
            self.solver.verify(challenge(), [(80, 30)], (320, 120), 250)

    def test_verify_point_count_mismatch_is_not_submitted(self):
        _, make_w, api = self.prepare_verify({"result": "success", "seccode": credentials()})
        with self.assertRaises(self.protocol.ProtocolError):
            self.solver.verify(challenge(), [], (320, 120), 250)
        make_w.assert_not_called()
        api.assert_not_called()


if __name__ == "__main__":
    unittest.main()
