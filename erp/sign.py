"""领星 OpenAPI 签名 —— 移植自 ecops/plugins/global-ecommerce-erp/src/client/lingxing-client.ts
（generateSign + encrypt）。★ 逐字节对照见 tests/test_erp_sign.py 的两条参考值。

签名 = base64(AES-ECB-PKCS7(key=app_id, MD5(sorted "k=v"&…).upper()))；
对象 / 数组按 JSON.stringify（无空格、不转义中文）。
"""
from __future__ import annotations

import base64
import hashlib
import json
from decimal import Decimal

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7


def normalize(obj):
    """把会让 JS 与 Python 打印不一致的数归一：整数值 float / Decimal → int，其余 Decimal → float。
    ★ 签名与请求体必须用同一份归一后的对象，否则签的不是发的。"""
    if isinstance(obj, dict):
        return {k: normalize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [normalize(v) for v in obj]
    if isinstance(obj, (float, Decimal)) and not isinstance(obj, bool):
        return int(obj) if obj == int(obj) else float(obj)
    return obj


def _text(v) -> str:
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, separators=(",", ":"))
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return "null"
    return str(v)


def sign(params: dict, app_id: str) -> str:
    plain = "&".join(f"{k}={_text(params[k])}" for k in sorted(params))
    digest = hashlib.md5(plain.encode("utf-8")).hexdigest().upper()
    padder = PKCS7(128).padder()
    data = padder.update(digest.strip().encode("utf-8")) + padder.finalize()
    enc = Cipher(algorithms.AES(app_id.encode("utf-8")), modes.ECB()).encryptor()
    return base64.b64encode(enc.update(data) + enc.finalize()).decode("ascii")
