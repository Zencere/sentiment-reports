"""
酷安 V3 X-App-Token 生成器。

基于 qiuyurs/coolApkAPI 的实现，从 APK 中提取 libauth.so 来动态计算
X-App-Token 签名。算法流程详见 docs/TOKEN_ALGORITHM.md。

依赖: pip install bcrypt
"""
import base64
import hashlib
import re
import time
import zipfile

import bcrypt

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
STD_B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"

# APK 信息（从 APK 文件名解析）
APK_PATH = r"C:\Users\25757\Downloads\CoolApk-16.6.1-2608212-coolapk-arm64-sign.apk"
VERSION_CODE = 2608212
APP_VERSION = "16.6.1"
PACKAGE = "com.coolapk.market"

# 酷安设备指纹（固定值，避免每次运行变化触发风控）
DEFAULT_DEVICE = (
    "AZmV2N4UzN0UmZ3kDOzEzYgsjMwAjL2IjMwUjMuE0MRFEI7MkMxITM4AjMyAyOp1GZlJFI7kWbvFWaYByO"
    "gsDI7AyOzYGO3okVq1GWOlEez8WYLlkWKVWbllzX3pUTjFTcjx2aPVFR"
)
DDID = "21b52710-79a8-43a9-a9b0-a773b106d08f"

DEFAULT_UA = (
    "Dalvik/2.1.0 (Linux; U; Android 14; SM-S9080 Build/UP1A.231005.007) "
    "CoolMarket/16.6.1"
)


def _find_blob(libauth_bytes: bytes) -> bytes:
    """在 libauth.so 中找到用于 token 生成的 base64 blob。

    选择解码后 XOR 0x5A 可打印字符比例最高的候选。
    """
    candidates = re.findall(rb"[A-Za-z0-9+/]{1000,}", libauth_bytes)
    best = None
    best_score = -1.0
    for cand in candidates:
        try:
            dec = base64.b64decode(cand, validate=True)
        except Exception:
            continue
        if not dec:
            continue
        x = bytes(b ^ 0x5A for b in dec)
        printable = sum(1 for c in x if 32 <= c < 127)
        score = printable / len(x)
        if score > best_score:
            best_score = score
            best = cand
    if best is None:
        raise RuntimeError("Could not locate token blob in libauth.so")
    return best


def _shift_last_char_minus_5(s: str) -> str:
    """将 salt 最后一位在标准 Base64 字典中向前偏移 5 位。"""
    idx = STD_B64.index(s[-1])
    return s[:-1] + STD_B64[(idx - 5) % 64]


def generate_token(
    apk_path: str = APK_PATH,
    device: str = DEFAULT_DEVICE,
    ts: int = 0,
    version_code: int = VERSION_CODE,
    package: str = PACKAGE,
) -> str:
    """生成酷安 V3 X-App-Token。

    Args:
        apk_path: 酷安 APK 文件路径。
        device: X-App-Device 值（酷安设备指纹）。
        ts: Unix 时间戳（秒），默认当前时间。
        version_code: 应用版本号。
        package: 应用包名。

    Returns:
        v3 开头的 X-App-Token 字符串。
    """
    if not ts:
        ts = int(time.time())

    with zipfile.ZipFile(apk_path, "r") as zf:
        libauth = zf.read("lib/arm64-v8a/libauth.so")

    blob = _find_blob(libauth)
    phase1 = base64.b64decode(blob)
    phase2 = bytes(b ^ 0x5A for b in phase1)

    idx = ((ts + version_code) % 100) * 4 + 0x80
    if idx >= len(phase2):
        raise RuntimeError(
            f"index out of range: idx={idx}, blob_len={len(phase2)}"
        )

    chunk = phase2[idx : idx + 0x80]
    segment = base64.b64decode(chunk)

    md5_device = hashlib.md5(device.encode("utf-8")).hexdigest().encode("ascii")

    plain = (
        package.encode("utf-8")
        + b"&"
        + segment
        + b"&"
        + md5_device
        + b"&"
        + str(ts).encode("ascii")
        + b"&"
        + str(version_code).encode("ascii")
    )

    pw = hashlib.md5(base64.b64encode(plain)).hexdigest().encode("ascii")
    salt_src = (
        base64.b64encode(
            f"{ts:x}/{hashlib.md5(plain).hexdigest()}".encode("ascii")
        )
        .decode("ascii")
        .rstrip("=")
    )

    salt22 = _shift_last_char_minus_5(salt_src[:22])
    setting = f"$2y$10${salt22}".encode("ascii")
    hashed = bcrypt.hashpw(pw, setting)
    token = "v3" + base64.b64encode(hashed).decode("ascii").rstrip("=")
    return token


def generate_token_compatible(
    device: str = DEFAULT_DEVICE,
    ts: int = 0,
    max_ahead: int = 20,
    **kwargs,
) -> tuple:
    """带时间戳前探的 token 生成，规避 Python bcrypt 的 Invalid salt。

    Python bcrypt 对部分由 app 算法生成的盐校验更严格，会抛 Invalid salt。
    通过向前探测时间戳 ts, ts+1, ... 直到找到可用盐。

    Returns:
        (token, actual_ts, offset) 三元组。
    """
    start_ts = ts or int(time.time())
    last_err = None
    for offset in range(max_ahead + 1):
        cur_ts = start_ts + offset
        try:
            token = generate_token(device=device, ts=cur_ts, **kwargs)
            return token, cur_ts, offset
        except ValueError as exc:
            if "Invalid salt" in str(exc):
                last_err = exc
                continue
            raise
    raise RuntimeError(
        f"could not generate token in +0..+{max_ahead}s window: {last_err}"
    )


def build_headers(device: str = DEFAULT_DEVICE, ts: int = 0) -> dict:
    """构建完整的酷安 API 请求头。

    Args:
        device: X-App-Device 值。
        ts: Unix 时间戳。

    Returns:
        包含所有认证头的字典。
    """
    token, actual_ts, _offset = generate_token_compatible(device=device, ts=ts)

    return {
        "User-Agent": DEFAULT_UA,
        "Connection": "Keep-Alive",
        "Accept-Encoding": "gzip",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://www.coolapk.com/",
        "X-Requested-With": "XMLHttpRequest",
        "X-Sdk-Int": "35",
        "X-Sdk-Locale": "zh-CN",
        "X-App-Id": PACKAGE,
        "X-App-Token": token,
        "X-App-Version": APP_VERSION,
        "X-App-Code": str(VERSION_CODE),
        "X-Api-Version": "16",
        "X-App-Device": device,
        "X-Dark-Mode": "0",
        "X-App-Channel": "coolapk",
        "X-App-Mode": "universal",
        "X-App-Supported": str(VERSION_CODE),
        "Cookie": f"ddid={DDID}",
    }


# ---------------------------------------------------------------------------
# CLI 测试
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description="生成酷安 X-App-Token")
    parser.add_argument("--apk", default=APK_PATH, help="酷安 APK 路径")
    parser.add_argument("--device", default=DEFAULT_DEVICE, help="X-App-Device")
    parser.add_argument("--ts", type=int, default=0, help="时间戳")
    parser.add_argument("--version-code", type=int, default=VERSION_CODE)
    parser.add_argument("--package", default=PACKAGE)
    parser.add_argument("--headers", action="store_true", help="输出完整请求头 JSON")
    args = parser.parse_args()

    args.ts = args.ts or int(time.time())

    headers = build_headers(device=args.device, ts=args.ts)
    print(json.dumps(headers, indent=2, ensure_ascii=False))