import hashlib
import json
import math
import re
import secrets
import time
from urllib.parse import quote_plus, unquote


class ProtocolError(ValueError):
    pass


RSA_MODULUS = (
    '00C1E3934D1614465B33053E7F48EE4EC87B14B95EF88947713D25EECBFF7E74C7977'
    'D02DC1D9451F79DD5D1C10C29ACB6A9B4D6FB7D0A0279B6719E1772565F09AF627'
    '715919221AEF91899CAE08C0D686D748B20A3603BE2318CA6BC2B59706592A9219D0'
    'BF05C9F65023A21D2330807252AE0066D59CEEFA5F2748EA80BAB81'
)


def parse_script_config(script):
    if len(script) > 2 * 1024 * 1024:
        raise ProtocolError('极验客户端脚本过大')
    encoded = re.search(r'decodeURI\("([^"\n]+)"\)', script)
    key_match = re.search(r'\}\}\}\("([^"\n]+)"\)\}', script)
    if not encoded or not key_match:
        raise ProtocolError('极验客户端结构已变化，无法提取协议参数')
    encrypted = unquote(encoded.group(1))
    key = key_match.group(1)
    table = ''.join(chr(ord(char) ^ ord(key[index % len(key)])) for index, char in enumerate(encrypted)).split('^', 16384)
    if len(table) > 16384 or any(len(value) > 65536 for value in table):
        raise ProtocolError('极验客户端字符串表过大')
    expanded_size = len(script)

    def replace(match):
        nonlocal expanded_size
        index = int(match.group(1))
        if index >= len(table):
            raise ProtocolError('极验客户端字符串表不兼容')
        value = json.dumps(table[index])
        expanded_size += max(0, len(value) - len(match.group(0)))
        if expanded_size > 8 * 1024 * 1024:
            raise ProtocolError('极验客户端展开结果过大')
        return value

    decoded = re.sub(r'_.{4}\((\d+)\)', replace, script)
    values = []
    for name in ('_lib', '_abo'):
        match = re.search(r'\["' + name + r'"\]\s*=\s*(\{[^{}]+\})', decoded)
        if not match:
            raise ProtocolError('极验客户端缺少协议参数 ' + name)
        literal = re.sub(r'([{,])\s*([A-Za-z_][A-Za-z0-9_]*)\s*:', r'\1"\2":', match.group(1))
        value = json.loads(literal)
        if not value or not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
            raise ProtocolError('极验协议参数格式不兼容')
        values.append(value)
    return tuple(values)


def lot_value(expression, lot_number):
    parts = []
    for part in expression.split('+.+'):
        part = part.removeprefix('(').removesuffix(')')
        if not re.fullmatch(r'n\[\d{1,2}:\d{1,2}\](?:\+n\[\d{1,2}:\d{1,2}\])*', part):
            raise ProtocolError('不支持的 lot_number 映射表达式')
        result = ''
        for start, end in re.findall(r'n\[(\d+):(\d+)\]', part):
            start, end = int(start), int(end)
            if not 0 <= start <= end < len(lot_number):
                raise ProtocolError('lot_number 映射越界')
            result += lot_number[start:end + 1]
        parts.append(result)
    return '.'.join(parts)


def lot_fields(mapping, lot_number):
    result = {}
    for expression, value in mapping.items():
        parts = lot_value(expression, lot_number).split('.')
        current = result
        for part in parts[:-1]:
            current = current.setdefault(part, {})
        current[parts[-1]] = lot_value(value, lot_number)
    return result


def proof_of_work(detail, captcha_id, lot_number, deadline):
    algorithm = detail['hashfunc']
    bits = detail['bits']
    if algorithm not in ('md5', 'sha1', 'sha256') or type(bits) is not int or not 0 <= bits <= 24:
        raise ProtocolError('不支持的工作量证明参数')
    prefix = f"{detail['version']}|{bits}|{algorithm}|{detail['datetime']}|{captcha_id}|{lot_number}||"
    digest_bits = hashlib.new(algorithm).digest_size * 8
    while time.monotonic() < deadline:
        message = prefix + secrets.token_hex(8)
        digest = hashlib.new(algorithm, message.encode()).hexdigest()
        if int(digest, 16) >> (digest_bits - bits) == 0:
            return {'pow_msg': message, 'pow_sign': digest}
    raise TimeoutError('验证码工作量证明超时')


def normalize_points(points, image_size):
    width, height = image_size
    if width <= 0 or height <= 0:
        raise ProtocolError('验证码图片尺寸无效')
    result = []
    for x, y in points:
        if not math.isfinite(x) or not math.isfinite(y) or not (0 <= x < width and 0 <= y < height):
            raise ProtocolError('验证码识别坐标越界')
        result.append([round(x / width * 10000), round(y / height * 10000)])
    return result


def encrypt_payload(payload, pt):
    raw = json.dumps(payload, separators=(',', ':'))
    if str(pt) == '0':
        return quote_plus(raw)
    if str(pt) != '1':
        raise ProtocolError('不支持的极验加密协议版本')
    from Crypto.Cipher import AES, PKCS1_v1_5
    from Crypto.PublicKey import RSA
    from Crypto.Util.Padding import pad

    key = secrets.token_hex(8).encode('ascii')
    encrypted = AES.new(key, AES.MODE_CBC, b'0000000000000000').encrypt(pad(raw.encode(), AES.block_size))
    public_key = RSA.construct((int(RSA_MODULUS, 16), 65537))
    encrypted_key = PKCS1_v1_5.new(public_key).encrypt(key)
    return encrypted.hex() + encrypted_key.hex()


def make_w(data, captcha_id, points, image_size, elapsed_ms, config, deadline):
    library, mapping = config
    lot_number = data['lot_number']
    payload = {
        **library,
        **proof_of_work(data['pow_detail'], captcha_id, lot_number, deadline),
        **lot_fields(mapping, lot_number),
        'biht': '1426265548',
        'device_id': '',
        'em': {'cp': 0, 'ek': '11', 'nt': 0, 'ph': 0, 'sc': 0, 'si': 0, 'wd': 1},
        'gee_guard': {'roe': {key: '3' for key in ('auh', 'aup', 'cdc', 'egp', 'res', 'rew', 'sep', 'snh')}},
        'ep': '123',
        'geetest': 'captcha',
        'lang': 'zh',
        'lot_number': lot_number,
        'passtime': max(1, int(elapsed_ms)),
        'userresponse': normalize_points(points, image_size),
    }
    return encrypt_payload(payload, data['pt'])
