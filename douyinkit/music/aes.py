"""纯 Python AES-128（ECB）——只给 NCM 解密用。

项目坚持「不装依赖」，而 NCM 头部用 AES-128-ECB 加密；这里按 FIPS-197
实现最小可用版本：128 位密钥、单块加解密。S 盒不是抄常量，而是按定义
（GF(2^8) 乘法逆元 + 仿射变换）现算，避免手抄 256 个常量抄错。

只用于几块头部数据，性能不是问题；音频部分走的是流式 XOR，不经过 AES。
"""

from __future__ import annotations


def _xtime(value: int) -> int:
    value <<= 1
    if value & 0x100:
        value ^= 0x11B
    return value & 0xFF


def _gmul(a: int, b: int) -> int:
    result = 0
    for _ in range(8):
        if b & 1:
            result ^= a
        high = a & 0x80
        a = (a << 1) & 0xFF
        if high:
            a ^= 0x1B
        b >>= 1
    return result


def _rotl8(value: int, shift: int) -> int:
    return ((value << shift) | (value >> (8 - shift))) & 0xFF


def _build_sbox() -> tuple[tuple[int, ...], tuple[int, ...]]:
    sbox = [0] * 256
    for value in range(256):
        inverse = 0
        if value:
            for candidate in range(1, 256):
                if _gmul(value, candidate) == 1:
                    inverse = candidate
                    break
        sbox[value] = (
            inverse
            ^ _rotl8(inverse, 1)
            ^ _rotl8(inverse, 2)
            ^ _rotl8(inverse, 3)
            ^ _rotl8(inverse, 4)
            ^ 0x63
        )
    inverse_sbox = [0] * 256
    for index, value in enumerate(sbox):
        inverse_sbox[value] = index
    return tuple(sbox), tuple(inverse_sbox)


SBOX, INV_SBOX = _build_sbox()
_RCON = (0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36)
_MUL = {
    2: lambda a: _xtime(a),
    3: lambda a: _xtime(a) ^ a,
    9: lambda a: _gmul(a, 9),
    11: lambda a: _gmul(a, 11),
    13: lambda a: _gmul(a, 13),
    14: lambda a: _gmul(a, 14),
}


def expand_key(key: bytes) -> list[list[int]]:
    """16 字节密钥 → 44 个 4 字节轮密钥字。"""
    if len(key) != 16:
        raise ValueError("AES-128 需要 16 字节密钥")
    words = [list(key[index:index + 4]) for index in range(0, 16, 4)]
    for index in range(4, 44):
        temp = list(words[index - 1])
        if index % 4 == 0:
            temp = temp[1:] + temp[:1]
            temp = [SBOX[byte] for byte in temp]
            temp[0] ^= _RCON[index // 4 - 1]
        words.append([words[index - 4][i] ^ temp[i] for i in range(4)])
    return words


def _add_round_key(state: list[list[int]], words: list[list[int]], rnd: int) -> None:
    for column in range(4):
        word = words[rnd * 4 + column]
        for row in range(4):
            state[row][column] ^= word[row]


def _sub_bytes(state: list[list[int]], box: tuple[int, ...]) -> None:
    for row in range(4):
        for column in range(4):
            state[row][column] = box[state[row][column]]


def _shift_rows(state: list[list[int]], inverse: bool = False) -> None:
    for row in range(1, 4):
        line = [state[row][column] for column in range(4)]
        shift = -row if inverse else row
        line = line[shift:] + line[:shift]
        for column in range(4):
            state[row][column] = line[column]


def _mix_columns(state: list[list[int]], inverse: bool = False) -> None:
    matrix = ((14, 11, 13, 9), (9, 14, 11, 13),
              (13, 9, 14, 11), (11, 13, 9, 14)) if inverse else \
             ((2, 3, 1, 1), (1, 2, 3, 1), (1, 1, 2, 3), (3, 1, 1, 2))
    for column in range(4):
        source = [state[row][column] for row in range(4)]
        for row in range(4):
            value = 0
            for index in range(4):
                factor = matrix[row][index]
                if factor == 1:
                    value ^= source[index]
                else:
                    value ^= _MUL[factor](source[index])
            state[row][column] = value


def _to_state(block: bytes) -> list[list[int]]:
    """AES 状态按列优先：state[r][c] = block[r + 4c]。"""
    return [[block[row + 4 * column] for column in range(4)] for row in range(4)]


def _from_state(state: list[list[int]]) -> bytes:
    return bytes(state[row][column] for column in range(4) for row in range(4))


def encrypt_block(block: bytes, key: bytes | list[list[int]]) -> bytes:
    words = key if isinstance(key, list) else expand_key(key)
    if len(block) != 16:
        raise ValueError("AES 分组必须是 16 字节")
    state = _to_state(block)
    _add_round_key(state, words, 0)
    for rnd in range(1, 10):
        _sub_bytes(state, SBOX)
        _shift_rows(state)
        _mix_columns(state)
        _add_round_key(state, words, rnd)
    _sub_bytes(state, SBOX)
    _shift_rows(state)
    _add_round_key(state, words, 10)
    return _from_state(state)


def decrypt_block(block: bytes, key: bytes | list[list[int]]) -> bytes:
    words = key if isinstance(key, list) else expand_key(key)
    if len(block) != 16:
        raise ValueError("AES 分组必须是 16 字节")
    state = _to_state(block)
    _add_round_key(state, words, 10)
    for rnd in range(9, 0, -1):
        _shift_rows(state, inverse=True)
        _sub_bytes(state, INV_SBOX)
        _add_round_key(state, words, rnd)
        _mix_columns(state, inverse=True)
    _shift_rows(state, inverse=True)
    _sub_bytes(state, INV_SBOX)
    _add_round_key(state, words, 0)
    return _from_state(state)


def decrypt_ecb(data: bytes, key: bytes) -> bytes:
    if len(data) % 16:
        raise ValueError("ECB 数据长度必须是 16 的整数倍")
    words = expand_key(key)
    return b"".join(
        decrypt_block(data[offset:offset + 16], words)
        for offset in range(0, len(data), 16)
    )


def encrypt_ecb(data: bytes, key: bytes) -> bytes:
    if len(data) % 16:
        raise ValueError("ECB 数据长度必须是 16 的整数倍")
    words = expand_key(key)
    return b"".join(
        encrypt_block(data[offset:offset + 16], words)
        for offset in range(0, len(data), 16)
    )


__all__ = ["decrypt_block", "encrypt_block", "decrypt_ecb", "encrypt_ecb",
           "expand_key", "SBOX", "INV_SBOX"]
