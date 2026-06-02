"""
QRファイル受信ツール v2 (ファウンテン符号化対応)

機能:
    PCのWebカメラからQR動画を読み取り、ファウンテン符号を逐次復号して
    元のファイルを保存する。プロトコルv2:
        v2|packet_id|N|total_len|filename_urlenc|base64payload
    の各QRをカメラから読み取り、GF(2)上のGauss-Jordan消去でソースブロックを解く。

実行方法:
    python receiver_pc.py [-o OUTPUT_DIR] [-c CAMERA_INDEX] [--mirror]

引数:
    -o, --output-dir    保存先ディレクトリ（既定: カレントディレクトリ）
    -c, --camera-index  使用するカメラのインデックス（既定: 0）
    --mirror            プレビューを左右反転表示
    --no-preview        カメラプレビューを表示しない

操作:
    ESC または q   中断
    r              受信状態をリセット

依存:
    Python 3.8+
    opencv-python, pyzbar, numpy
"""

import argparse
import base64
import os
import sys
import urllib.parse
from pathlib import Path

import cv2
import numpy as np
from pyzbar.pyzbar import decode as zbar_decode


PROTOCOL_VERSION = "v2"

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


def sample_degree(state: int):
    state = xorshift32(state)
    r = state / 0xFFFFFFFF
    cum = 0.0
    for d, p in DEGREE_TABLE:
        cum += p
        if r < cum:
            return d, state
    return DEGREE_TABLE[-1][0], state


def sample_indices(state: int, n: int, degree: int):
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


def derive_packet_indices(packet_id: int, n: int):
    if packet_id < n:
        return [packet_id]
    state = derive_seed(packet_id)
    degree, state = sample_degree(state)
    indices, _ = sample_indices(state, n, degree)
    return indices


class FountainDecoder:
    """GF(2)上のGauss-Jordan消去によるオンライン復号器。RREFを毎パケット維持する。"""

    def __init__(self, n: int, chunk_size: int):
        self.n = n
        self.chunk_size = chunk_size
        self.mask_bytes = (n + 7) // 8
        # pivots[col] = (mask: np.uint8 array, payload: np.uint8 array) or None
        self.pivots: list = [None] * n
        self.packets_seen = 0
        self.duplicates = 0

    def _make_mask(self, indices) -> np.ndarray:
        m = np.zeros(self.mask_bytes, dtype=np.uint8)
        for i in indices:
            m[i >> 3] |= np.uint8(1 << (i & 7))
        return m

    def _bit(self, mask: np.ndarray, i: int) -> bool:
        return bool(mask[i >> 3] & np.uint8(1 << (i & 7)))

    def _mask_popcount(self, mask: np.ndarray) -> int:
        return int(np.unpackbits(mask).sum())

    def _first_set_bit(self, mask: np.ndarray) -> int:
        for byte_idx in range(self.mask_bytes):
            b = int(mask[byte_idx])
            if b:
                for bit in range(8):
                    if b & (1 << bit):
                        col = (byte_idx << 3) | bit
                        return col if col < self.n else -1
        return -1

    def add_packet(self, indices, payload_bytes: bytes) -> bool:
        """新パケットを取り込み。新しい独立情報なら True。冗長なら False。"""
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

    def solved_blocks(self) -> int:
        """個別に解けたソースブロック数（pivotのmaskが該当ビットのみのもの）"""
        cnt = 0
        for col in range(self.n):
            p = self.pivots[col]
            if p is None:
                continue
            if self._mask_popcount(p[0]) == 1 and self._bit(p[0], col):
                cnt += 1
        return cnt

    def rank(self) -> int:
        return sum(1 for p in self.pivots if p is not None)

    def is_solved_block(self, col: int) -> bool:
        p = self.pivots[col]
        if p is None:
            return False
        return self._mask_popcount(p[0]) == 1 and self._bit(p[0], col)

    def complete(self) -> bool:
        return self.solved_blocks() == self.n

    def assemble(self, total_len: int) -> bytes:
        buf = bytearray(self.n * self.chunk_size)
        for col in range(self.n):
            p = self.pivots[col]
            buf[col * self.chunk_size:(col + 1) * self.chunk_size] = bytes(p[1])
        return bytes(buf[:total_len])


def parse_payload(text: str):
    """戻り値: dict or None"""
    if not text.startswith(PROTOCOL_VERSION + "|"):
        return None
    parts = text.split("|", 5)
    if len(parts) != 6:
        return None
    try:
        _, pid_s, n_s, tl_s, fn_enc, b64 = parts
        packet_id = int(pid_s)
        n = int(n_s)
        total_len = int(tl_s)
        filename = urllib.parse.unquote(fn_enc)
        payload = base64.b64decode(b64)
    except Exception:
        return None
    return {
        "packet_id": packet_id,
        "n": n,
        "total_len": total_len,
        "filename": filename,
        "payload": payload,
    }


def configure_camera(cap):
    """v1と同様の露出固定設定。環境により無視される。"""
    try:
        cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 0.25)
        cap.set(cv2.CAP_PROP_EXPOSURE, -6)
        cap.set(cv2.CAP_PROP_AUTO_WB, 0)
        cap.set(cv2.CAP_PROP_BRIGHTNESS, 128)
    except Exception:
        pass
    # 解像度はできるだけ高めに
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)


def draw_status(img, lines, blink: bool):
    """カメラ画像上にステータスをオーバーレイ描画"""
    h, w = img.shape[:2]
    overlay = img.copy()
    pad = 10
    line_h = 22
    box_h = pad * 2 + line_h * len(lines)
    cv2.rectangle(overlay, (0, 0), (w, box_h), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.55, img, 0.45, 0, img)
    for i, line in enumerate(lines):
        y = pad + (i + 1) * line_h - 4
        cv2.putText(img, line, (pad, y), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
    # 認識インジケータ（右上）
    color = (0, 255, 0) if blink else (0, 0, 200)
    cv2.circle(img, (w - 24, 24), 10, color, -1)


def main():
    parser = argparse.ArgumentParser(description="QRファイル受信ツール v2")
    parser.add_argument("-o", "--output-dir", default=".", help="保存先ディレクトリ")
    parser.add_argument("-c", "--camera-index", type=int, default=0)
    parser.add_argument("--mirror", action="store_true")
    parser.add_argument("--no-preview", action="store_true")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    cap = cv2.VideoCapture(args.camera_index)
    if not cap.isOpened():
        print(f"エラー: カメラ {args.camera_index} を開けません", file=sys.stderr)
        sys.exit(1)
    configure_camera(cap)
    print("カメラ起動。ESCまたはqで中断、rでリセット。")

    decoder: FountainDecoder | None = None
    expected = {"n": None, "total_len": None, "filename": None}
    blink_frames = 0
    seen_packet_ids: set[int] = set()

    while True:
        ret, frame = cap.read()
        if not ret:
            continue

        if args.mirror:
            frame = cv2.flip(frame, 1)

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        results = zbar_decode(gray)

        for r in results:
            try:
                text = r.data.decode("utf-8")
            except UnicodeDecodeError:
                continue
            info = parse_payload(text)
            if info is None:
                continue

            blink_frames = 4

            if expected["n"] is None:
                expected["n"] = info["n"]
                expected["total_len"] = info["total_len"]
                expected["filename"] = info["filename"]
                chunk_size = len(info["payload"])
                decoder = FountainDecoder(info["n"], chunk_size)
                print(f"プロトコル開始: N={info['n']}, file='{info['filename']}', total={info['total_len']:,} bytes")
            elif info["n"] != expected["n"] or info["total_len"] != expected["total_len"]:
                # 別ファイルのQRが混入
                continue

            if info["packet_id"] in seen_packet_ids:
                continue
            seen_packet_ids.add(info["packet_id"])

            indices = derive_packet_indices(info["packet_id"], info["n"])
            if len(info["payload"]) != decoder.chunk_size:
                # 想定外サイズ。スキップ
                continue
            decoder.add_packet(indices, info["payload"])

        # プレビュー
        if not args.no_preview:
            preview = frame.copy()
            if decoder is not None:
                lines = [
                    f"file: {expected['filename']}",
                    f"packets: {decoder.packets_seen}  rank: {decoder.rank()}/{decoder.n}  solved: {decoder.solved_blocks()}/{decoder.n}",
                    "ESC/q: quit  r: reset",
                ]
            else:
                lines = [
                    "Waiting for QR (protocol v2)...",
                    "ESC/q: quit",
                ]
            draw_status(preview, lines, blink_frames > 0)
            # 縮小表示
            ph, pw = preview.shape[:2]
            target_w = 720
            if pw > target_w:
                scale = target_w / pw
                preview = cv2.resize(preview, (target_w, int(ph * scale)))
            cv2.imshow("QR Receiver v2", preview)
            blink_frames = max(0, blink_frames - 1)

        key = cv2.waitKey(1) & 0xFF
        if key in (27, ord("q")):
            print("中断しました。")
            break
        if key == ord("r"):
            decoder = None
            expected = {"n": None, "total_len": None, "filename": None}
            seen_packet_ids.clear()
            print("リセットしました。")
            continue

        if decoder is not None and decoder.complete():
            save_name = expected["filename"] or "received_file.bin"
            # ファイル名のサニタイズ（パストラバーサル防止）
            save_name = os.path.basename(save_name)
            save_path = output_dir / save_name
            data = decoder.assemble(expected["total_len"])
            save_path.write_bytes(data)
            print(f"受信完了: {save_path} ({len(data):,} bytes)")
            print(f"  受信パケット: {decoder.packets_seen}, 重複: {decoder.duplicates}")
            break

    cap.release()
    if not args.no_preview:
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
