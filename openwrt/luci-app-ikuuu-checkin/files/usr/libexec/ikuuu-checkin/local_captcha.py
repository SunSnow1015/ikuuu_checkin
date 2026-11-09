import contextlib
import json
import re
import sys
import time
import uuid
from urllib.parse import urljoin, urlparse

import requests

from geetest_protocol import ProtocolError, make_w, parse_script_config


API_BASE = 'https://gcaptcha4.geetest.com'
STATIC_BASE = 'https://static.geetest.com/'
STATIC_HOSTS = {'static.geetest.com', 'static.geevisit.com'}
RESULT_FIELDS = ('lot_number', 'captcha_output', 'pass_token', 'gen_time')


class VerificationError(RuntimeError):
    pass


def asset_url(path):
    if not isinstance(path, str) or not path:
        raise ProtocolError('极验图片资源地址为空')
    url = urljoin(STATIC_BASE, path)
    parsed = urlparse(url)
    if parsed.scheme != 'https' or parsed.hostname not in STATIC_HOSTS or parsed.username is not None or parsed.password is not None or parsed.port not in (None, 443):
        raise ProtocolError('拒绝非官方极验资源地址')
    return url


def parse_response(content, callback):
    text = content.decode('utf-8').strip()
    if not text.startswith('{'):
        match = re.fullmatch(re.escape(callback) + r'\((.*)\);?', text, re.S)
        if not match:
            raise ProtocolError('极验响应不是预期的 JSON/JSONP')
        text = match.group(1)
    envelope = json.loads(text)
    if not isinstance(envelope, dict) or envelope.get('status') != 'success' or not isinstance(envelope.get('data'), dict):
        raise ProtocolError('极验接口未返回成功响应')
    return envelope['data']


class LocalCaptcha:
    def __init__(self, captcha_id, base_url, timeout_seconds=120, max_attempts=3, risk_type=None):
        if not re.fullmatch(r'[a-fA-F0-9]{32}', captcha_id):
            raise ValueError('captcha_id 格式无效')
        if not 1 <= timeout_seconds <= 600 or not 1 <= max_attempts <= 5:
            raise ValueError('验证码超时须为 1–600 秒，尝试次数须为 1–5')
        self.captcha_id = captcha_id
        self.deadline = time.monotonic() + timeout_seconds
        self.max_attempts = max_attempts
        self.risk_type = risk_type
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36',
            'Referer': base_url.rstrip('/') + '/auth/login',
        })
        self.script_configs = {}
        self.word_recognizer = None

    def close(self):
        self.session.close()

    def fetch(self, url, params=None, limit=2_000_000):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('本地验证码处理超时')
        with self.session.get(url, params=params, timeout=min(20, remaining), allow_redirects=False, stream=True) as response:
            if response.status_code != 200:
                raise ProtocolError(f'极验资源请求失败（HTTP {response.status_code}）')
            chunks = []
            size = 0
            for chunk in response.iter_content(65536):
                if time.monotonic() >= self.deadline:
                    raise TimeoutError('本地验证码资源下载超时')
                size += len(chunk)
                if size > limit:
                    raise ProtocolError('极验资源超过大小限制')
                chunks.append(chunk)
            return b''.join(chunks)

    def call_api(self, path, params):
        callback = 'geetest_' + str(int(time.time() * 1000))
        content = self.fetch(API_BASE + path, {**params, 'callback': callback}, limit=1_000_000)
        return parse_response(content, callback)

    def load(self):
        params = {
            'captcha_id': self.captcha_id,
            'challenge': str(uuid.uuid4()),
            'client_type': 'web',
            'lang': 'zho',
        }
        if self.risk_type:
            params['risk_type'] = self.risk_type
        data = self.call_api('/load', params)
        if data.get('captcha_type') not in ('icon', 'word'):
            raise ProtocolError('本地后端暂不支持题型：' + str(data.get('captcha_type')))
        if not isinstance(data.get('ques'), list) or not 1 <= len(data['ques']) <= 5:
            raise ProtocolError('极验提示图片数量异常')
        return data

    def recognize(self, data):
        from captcha_images import WordRecognizer, match_icons

        background = self.fetch(asset_url(data['imgs']))
        prompts = [self.fetch(asset_url(path)) for path in data['ques']]
        if data['captcha_type'] == 'icon':
            return match_icons(background, prompts)
        if self.word_recognizer is None:
            self.word_recognizer = WordRecognizer()
        return self.word_recognizer.match(background, prompts)

    def verify(self, data, points, image_size, elapsed_ms):
        static_path = data['static_path']
        script_url = asset_url(static_path.rstrip('/') + '/js/gcaptcha4.js')
        if script_url not in self.script_configs:
            script = self.fetch(script_url).decode('utf-8')
            self.script_configs[script_url] = parse_script_config(script)
        if len(points) != len(data['ques']):
            raise ProtocolError('识别坐标数量与提示数量不一致')
        w = make_w(data, self.captcha_id, points, image_size, elapsed_ms, self.script_configs[script_url], self.deadline)
        result = self.call_api('/verify', {
            'captcha_id': self.captcha_id,
            'client_type': 'web',
            'lot_number': data['lot_number'],
            'risk_type': data['captcha_type'],
            'payload': data['payload'],
            'process_token': data['process_token'],
            'payload_protocol': data['payload_protocol'],
            'pt': data['pt'],
            'w': w,
        })
        seccode = result.get('seccode')
        if result.get('result') != 'success' or not isinstance(seccode, dict) or any(not isinstance(seccode.get(key), str) or not seccode[key] for key in RESULT_FIELDS):
            raise VerificationError('极验未接受识别结果，未生成验证凭据')
        if seccode['lot_number'] != data['lot_number'] or seccode.get('captcha_id', self.captcha_id) != self.captcha_id:
            raise ProtocolError('极验返回凭据与当前挑战不匹配')
        return {key: seccode[key] for key in RESULT_FIELDS}

    def solve(self):
        from captcha_images import RecognitionError

        last_error = None
        for attempt in range(self.max_attempts):
            if attempt:
                remaining = self.deadline - time.monotonic()
                if remaining <= 1:
                    raise TimeoutError('本地验证码处理超时')
                time.sleep(1)
            data = self.load()
            started = time.monotonic()
            try:
                points, size = self.recognize(data)
                return self.verify(data, points, size, (time.monotonic() - started) * 1000)
            except (RecognitionError, VerificationError) as error:
                last_error = error
                print(f'[本地验证码] 第 {attempt + 1}/{self.max_attempts} 次：{error}', file=sys.stderr, flush=True)
        raise last_error


def worker():
    solver = None
    try:
        config = json.load(sys.stdin)
        solver = LocalCaptcha(**config)
        with contextlib.redirect_stdout(sys.stderr):
            solution = solver.solve()
        return {'solution': solution}
    except ImportError:
        return {'error': '缺少本地识别依赖，请按 README 安装 requirements-local.txt 与 rapidocr_onnxruntime'}
    except requests.RequestException as error:
        return {'error': f'极验网络请求失败（{type(error).__name__}）'}
    except (ValueError, KeyError, TypeError, RuntimeError, TimeoutError) as error:
        if isinstance(error, (KeyError, TypeError, json.JSONDecodeError)):
            return {'error': '极验响应字段不兼容，未提交登录'}
        return {'error': str(error)}
    except Exception as error:
        return {'error': f'本地验证码处理异常（{type(error).__name__}）'}
    finally:
        if solver is not None:
            solver.close()


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    result = worker()
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if 'solution' in result else 1)
