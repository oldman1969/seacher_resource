"""知乎 x-zse-96 签名算法（zse96 v2）.

移植自 meurz/zhihu-mcp-server（MIT），原算法来自 zhihu-plus-plus。
生成搜索等接口所需的 x-zse-96 请求头签名值（"2.0_xxx"）.

签名流程：signSource = zse93 + pathname + d_c0 [+ body] → md5 → encryptZseV4
注意所有 32 位运算需 & 0xFFFFFFFF 保持无符号，与 JS >>> 0 等价.
"""

from __future__ import annotations

import hashlib
from urllib.parse import quote

ZK = [
    1170614578, 1024848638, 1413669199, 3951632832, 3528873006, 2921909214, 4151847688, 3997739139,
    1933479194, 3323781115, 3888513386, 460404854, 3747539722, 2403641034, 2615871395, 2119585428,
    2265697227, 2035090028, 2773447226, 4289380121, 4217216195, 2200601443, 3051914490, 1579901135,
    1321810770, 456816404, 2903323407, 4065664991, 330002838, 3506006750, 363569021, 2347096187,
]

ZB = [
    20, 223, 245, 7, 248, 2, 194, 209, 87, 6, 227, 253, 240, 128, 222, 91, 237, 9, 125, 157, 230,
    93, 252, 205, 90, 79, 144, 199, 159, 197, 186, 167, 39, 37, 156, 198, 38, 42, 43, 168, 217,
    153, 15, 103, 80, 189, 71, 191, 97, 84, 247, 95, 36, 69, 14, 35, 12, 171, 28, 114, 178, 148,
    86, 182, 32, 83, 158, 109, 22, 255, 94, 238, 151, 85, 77, 124, 254, 18, 4, 26, 123, 176, 232,
    193, 131, 172, 143, 142, 150, 30, 10, 146, 162, 62, 224, 218, 196, 229, 1, 192, 213, 27, 110,
    56, 231, 180, 138, 107, 242, 187, 54, 120, 19, 44, 117, 228, 215, 203, 53, 239, 251, 127, 81,
    11, 133, 96, 204, 132, 41, 115, 73, 55, 249, 147, 102, 48, 122, 145, 106, 118, 74, 190, 29, 16,
    174, 5, 177, 129, 63, 113, 99, 31, 161, 76, 246, 34, 211, 13, 60, 68, 207, 160, 65, 111, 82,
    165, 67, 169, 225, 57, 112, 244, 155, 51, 236, 200, 233, 58, 61, 47, 100, 137, 185, 64, 17, 70,
    234, 163, 219, 108, 170, 166, 59, 149, 52, 105, 24, 212, 78, 173, 45, 0, 116, 226, 119, 136,
    206, 135, 175, 195, 25, 92, 121, 208, 126, 139, 3, 75, 141, 21, 130, 98, 241, 40, 154, 66, 184,
    49, 181, 46, 243, 88, 101, 183, 8, 23, 72, 188, 104, 179, 210, 134, 250, 201, 164, 89, 216,
    202, 220, 50, 221, 152, 140, 33, 235, 214,
]

ALPHABET = "6fpLRqJO8M/c3jnYxFkUVC4ZIG12SiH=5v0mXDazWBTsuw7QetbKdoPyAl+hN9rgE"
KEY16 = b"059053f7d15e01d7"
ZSE93 = "101_3_3.0"


def _read_u32(b: bytes, off: int) -> int:
    return int.from_bytes(b[off : off + 4], "big")


def _write_u32(v: int, out: bytearray, off: int) -> None:
    out[off : off + 4] = (v & 0xFFFFFFFF).to_bytes(4, "big")


def _rotate_left(n: int, bits: int) -> int:
    n &= 0xFFFFFFFF
    return ((n << bits) | (n >> (32 - bits))) & 0xFFFFFFFF


def _g_transform(tt: int) -> int:
    tt &= 0xFFFFFFFF
    te0 = (tt >> 24) & 0xFF
    te1 = (tt >> 16) & 0xFF
    te2 = (tt >> 8) & 0xFF
    te3 = tt & 0xFF
    ti = (ZB[te0] << 24) | (ZB[te1] << 16) | (ZB[te2] << 8) | ZB[te3]
    return (
        ti
        ^ _rotate_left(ti, 2)
        ^ _rotate_left(ti, 10)
        ^ _rotate_left(ti, 18)
        ^ _rotate_left(ti, 24)
    ) & 0xFFFFFFFF


def _r_block(input16: bytes) -> bytes:
    tr = [0] * 36
    tr[0] = _read_u32(input16, 0)
    tr[1] = _read_u32(input16, 4)
    tr[2] = _read_u32(input16, 8)
    tr[3] = _read_u32(input16, 12)
    for i in range(32):
        ta = _g_transform((tr[i + 1] ^ tr[i + 2] ^ tr[i + 3] ^ ZK[i]) & 0xFFFFFFFF)
        tr[i + 4] = (tr[i] ^ ta) & 0xFFFFFFFF
    out = bytearray(16)
    _write_u32(tr[35], out, 0)
    _write_u32(tr[34], out, 4)
    _write_u32(tr[33], out, 8)
    _write_u32(tr[32], out, 12)
    return bytes(out)


def _x_blocks(data: bytes, iv0: bytes) -> bytes:
    iv = bytearray(iv0)
    out = bytearray(len(data))
    off = 0
    while off < len(data):
        mixed = bytearray(16)
        for i in range(16):
            mixed[i] = data[off + i] ^ iv[i]
        iv = bytearray(_r_block(bytes(mixed)))
        out[off : off + 16] = iv
        off += 16
    return bytes(out)


def _custom_encode(bytes_in: bytes) -> str:
    data = bytearray(bytes_in)
    rem = len(data) % 3
    if rem != 0:
        data += bytes(3 - rem)

    out: list[str] = []
    i = 0
    p = len(data) - 1
    while p >= 0:
        v = 0

        b0 = data[p] & 0xFF
        m0 = (58 >> (8 * (i % 4))) & 0xFF
        i += 1
        v |= (b0 ^ m0) & 0xFF

        b1 = data[p - 1] & 0xFF
        m1 = (58 >> (8 * (i % 4))) & 0xFF
        i += 1
        v |= ((b1 ^ m1) & 0xFF) << 8

        b2 = data[p - 2] & 0xFF
        m2 = (58 >> (8 * (i % 4))) & 0xFF
        i += 1
        v |= ((b2 ^ m2) & 0xFF) << 16

        out.append(ALPHABET[v & 63])
        out.append(ALPHABET[(v >> 6) & 63])
        out.append(ALPHABET[(v >> 12) & 63])
        out.append(ALPHABET[(v >> 18) & 63])

        p -= 3
    return "".join(out)


def _encrypt_zse_v4(input_str: str) -> str:
    """对 md5 hex 字符串做加密（等价 JS encryptZseV4）."""
    plain = bytearray()
    plain.append(210)  # seed
    plain.append(0)
    # encodeURIComponent(input) —— md5 hex 全为安全字符，原样
    plain.extend(quote(input_str, safe="").encode("utf-8"))
    pad = 16 - (len(plain) % 16)
    plain.extend([pad] * pad)

    first = bytearray(16)
    for i in range(16):
        first[i] = plain[i] ^ KEY16[i] ^ 42

    c0 = _r_block(bytes(first))
    cipher = bytearray(len(plain))
    cipher[0:16] = c0
    if len(plain) > 16:
        cipher[16:] = _x_blocks(bytes(plain[16:]), c0)
    return _custom_encode(bytes(cipher))


def _extract_pathname(url: str) -> str:
    """等价 JS：'/' + url.split('//')[1].split('/').slice(1).join('/')
    结果包含 query string（如 /api/v4/search_v3?t=general&q=x）."""
    after_proto = url.split("//", 1)[1]
    parts = after_proto.split("/")
    return "/" + "/".join(parts[1:])


def sign_request(url: str, d_c0: str = "", body: str | None = None, zse93: str = ZSE93) -> str:
    """生成 x-zse-96 签名值（"2.0_xxx"）.

    url: 完整 URL（含 query）
    d_c0: cookie 中的 d_c0 值（签名核心输入）
    body: POST 请求的 JSON 字符串；GET 请求为 None
    """
    pathname = _extract_pathname(url)
    parts = [zse93, pathname, d_c0, body]
    parts = [x for x in parts if x is not None]
    sign_source = "+".join(parts)
    md5 = hashlib.md5(sign_source.encode("utf-8")).hexdigest()
    signature = _encrypt_zse_v4(md5)
    return f"2.0_{signature}"
