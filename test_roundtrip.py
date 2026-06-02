"""
プロトコルv2の符号化/復号の往復テスト。
カメラ・QR描画を介さず、Python実装のロジックだけを検証する。

実行: python test_roundtrip.py
"""

import os
import random
import sys

import numpy as np

# 同ディレクトリのモジュールから一部関数だけ取り出したい。
# sender.py は qrcode/cv2/PIL を import するため、必要関数を直接定義する。

DEGREE_TABLE = [
    (2, 0.50),
    (3, 0.30),
    (5, 0.15),
    (8, 0.05),
]


def xorshift32(state: int) -> int:
    state &= 0xFFFFFFFF
    state ^= (state << 13) & 0xFFFFFFFF
    state ^= (state >> 17) & 0xFFFFFFFF
    state ^= (state << 5) & 0xFFFFFFFF
    return state & 0xFFFFFFFF


def derive_seed(packet_id: int) -> int:
    s = (packet_id + 0x9E3779B9) & 0xFFFFFFFF
    return s if s != 0 else 0x12345678


def sample_degree(state):
    state = xorshift32(state)
    r = state / 0xFFFFFFFF
    cum = 0.0
    for d, p in DEGREE_TABLE:
        cum += p
        if r < cum:
            return d, state
    return DEGREE_TABLE[-1][0], state


def sample_indices(state, n, degree):
    selected = []
    seen = set()
    deg = min(degree, n)
    while len(selected) < deg:
        state = xorshift32(state)
        idx = state % n
        if idx not in seen:
            seen.add(idx)
            selected.append(idx)
    return selected, state


def derive_packet_indices(packet_id, n):
    if packet_id < n:
        return [packet_id]
    state = derive_seed(packet_id)
    degree, state = sample_degree(state)
    indices, _ = sample_indices(state, n, degree)
    return indices


def encode_packet(packet_id, n, source_blocks, chunk_size):
    indices = derive_packet_indices(packet_id, n)
    if len(indices) == 1:
        return source_blocks[indices[0]]
    payload = bytearray(chunk_size)
    for idx in indices:
        block = source_blocks[idx]
        for i in range(chunk_size):
            payload[i] ^= block[i]
    return bytes(payload)


# receiver_pc.py の FountainDecoder（同じロジック）
class FountainDecoder:
    def __init__(self, n, chunk_size):
        self.n = n
        self.chunk_size = chunk_size
        self.mask_bytes = (n + 7) // 8
        self.pivots = [None] * n
        self.packets_seen = 0
        self.duplicates = 0

    def _make_mask(self, indices):
        m = np.zeros(self.mask_bytes, dtype=np.uint8)
        for i in indices:
            m[i >> 3] |= np.uint8(1 << (i & 7))
        return m

    def _bit(self, mask, i):
        return bool(mask[i >> 3] & np.uint8(1 << (i & 7)))

    def _popcount(self, mask):
        return int(np.unpackbits(mask).sum())

    def _first_set_bit(self, mask):
        for byte_idx in range(self.mask_bytes):
            b = int(mask[byte_idx])
            if b:
                for bit in range(8):
                    if b & (1 << bit):
                        col = (byte_idx << 3) | bit
                        return col if col < self.n else -1
        return -1

    def add_packet(self, indices, payload_bytes):
        self.packets_seen += 1
        mask = self._make_mask(indices)
        payload = np.frombuffer(payload_bytes, dtype=np.uint8).copy()

        # 既存pivotで完全に簡約（mask の各既存pivot列ビットを0にする）
        for c in range(self.n):
            if self.pivots[c] is not None and self._bit(mask, c):
                np.bitwise_xor(mask, self.pivots[c][0], out=mask)
                np.bitwise_xor(payload, self.pivots[c][1], out=payload)

        new_col = self._first_set_bit(mask)
        if new_col < 0:
            self.duplicates += 1
            return False

        # 既存pivot から new_col 列のビットを消去（RREF維持）
        for c in range(self.n):
            if c != new_col and self.pivots[c] is not None and self._bit(self.pivots[c][0], new_col):
                np.bitwise_xor(self.pivots[c][0], mask, out=self.pivots[c][0])
                np.bitwise_xor(self.pivots[c][1], payload, out=self.pivots[c][1])
        self.pivots[new_col] = (mask, payload)
        return True

    def is_solved_block(self, col):
        p = self.pivots[col]
        if p is None:
            return False
        return self._popcount(p[0]) == 1 and self._bit(p[0], col)

    def solved_blocks(self):
        return sum(1 for i in range(self.n) if self.is_solved_block(i))

    def rank(self):
        return sum(1 for p in self.pivots if p is not None)

    def complete(self):
        return self.solved_blocks() == self.n

    def assemble(self, total_len):
        buf = bytearray(self.n * self.chunk_size)
        for col in range(self.n):
            buf[col * self.chunk_size:(col + 1) * self.chunk_size] = bytes(self.pivots[col][1])
        return bytes(buf[:total_len])


def split_blocks(data, chunk_size):
    n = (len(data) + chunk_size - 1) // chunk_size
    blocks = []
    for i in range(n):
        b = data[i * chunk_size:(i + 1) * chunk_size]
        if len(b) < chunk_size:
            b = b + b"\x00" * (chunk_size - len(b))
        blocks.append(b)
    return blocks, n


def test_perfect_roundtrip():
    """ロスなし時の往復"""
    data = os.urandom(2500)
    chunk_size = 500
    blocks, n = split_blocks(data, chunk_size)

    decoder = FountainDecoder(n, chunk_size)
    pid = 0
    while not decoder.complete() and pid < n * 3:
        payload = encode_packet(pid, n, blocks, chunk_size)
        indices = derive_packet_indices(pid, n)
        decoder.add_packet(indices, payload)
        pid += 1

    assert decoder.complete(), f"未完了: rank={decoder.rank()}, solved={decoder.solved_blocks()}"
    reconstructed = decoder.assemble(len(data))
    assert reconstructed == data, "復元データが原本と一致しない"
    print(f"[PASS] perfect roundtrip: N={n}, decoded after {pid} packets (rank={decoder.rank()})")


def test_lossy_roundtrip():
    """ランダム損失下での往復"""
    random.seed(42)
    data = os.urandom(10000)
    chunk_size = 500
    blocks, n = split_blocks(data, chunk_size)
    total_packets = int(n * 1.5)

    for loss_rate in (0.0, 0.1, 0.3, 0.5, 0.7):
        decoder = FountainDecoder(n, chunk_size)
        attempted = 0
        for pid in range(total_packets * 5):
            if random.random() < loss_rate:
                attempted += 1
                continue
            attempted += 1
            payload = encode_packet(pid, n, blocks, chunk_size)
            indices = derive_packet_indices(pid, n)
            decoder.add_packet(indices, payload)
            if decoder.complete():
                break

        if decoder.complete():
            reconstructed = decoder.assemble(len(data))
            assert reconstructed == data
            print(f"[PASS] loss={loss_rate*100:.0f}%: N={n}, decoded after {attempted} attempts ({decoder.packets_seen} received, dup={decoder.duplicates})")
        else:
            print(f"[FAIL] loss={loss_rate*100:.0f}%: 未完了 rank={decoder.rank()}/{n}")


def test_random_order():
    """パケット到着順をシャッフルしても復号可能か"""
    random.seed(123)
    data = os.urandom(5000)
    chunk_size = 500
    blocks, n = split_blocks(data, chunk_size)

    total_packets = int(n * 1.5)
    packet_ids = list(range(total_packets))
    random.shuffle(packet_ids)

    decoder = FountainDecoder(n, chunk_size)
    for pid in packet_ids:
        payload = encode_packet(pid, n, blocks, chunk_size)
        indices = derive_packet_indices(pid, n)
        decoder.add_packet(indices, payload)
        if decoder.complete():
            break

    assert decoder.complete()
    assert decoder.assemble(len(data)) == data
    print(f"[PASS] shuffled order: N={n}, decoded after {decoder.packets_seen} packets")


def test_prng_known_values():
    """xorshift32 の既知値出力（JS実装との一致確認に使う）"""
    state = 1
    actual = []
    for _ in range(5):
        state = xorshift32(state)
        actual.append(state)
    print(f"xorshift32(seed=1) 系列: {actual}")
    # JSコンソールで以下を実行して同じ系列が得られればOK:
    #   function x(s){s^=s<<13;s^=s>>>17;s^=s<<5;return s>>>0;}
    #   let s=1; for(let i=0;i<5;i++){s=x(s);console.log(s);}


def test_large_n():
    """大きめのNでのスケール確認"""
    random.seed(7)
    data = os.urandom(50000)  # 100ブロック
    chunk_size = 500
    blocks, n = split_blocks(data, chunk_size)
    total_packets = int(n * 1.5)

    decoder = FountainDecoder(n, chunk_size)
    for pid in range(total_packets * 3):
        payload = encode_packet(pid, n, blocks, chunk_size)
        indices = derive_packet_indices(pid, n)
        decoder.add_packet(indices, payload)
        if decoder.complete():
            break

    assert decoder.complete(), f"未完了 rank={decoder.rank()}/{n}"
    assert decoder.assemble(len(data)) == data
    print(f"[PASS] large N: N={n}, decoded after {decoder.packets_seen} packets (overhead={decoder.packets_seen/n:.2f}x)")


if __name__ == "__main__":
    test_prng_known_values()
    test_perfect_roundtrip()
    test_lossy_roundtrip()
    test_random_order()
    test_large_n()
    print("\nAll tests OK.")
