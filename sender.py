"""
QRファイル送信ツール v2 (ファウンテン符号化対応)

機能:
    任意のファイルを「ファウンテン符号化したQRコード動画」として生成する。
    受信側は、生成された動画から任意のフレームを十分な数だけ読み取れば、
    元のファイルを復元できる（特定フレームの順序や全フレーム取得を必要としない）。

    プロトコルv2: 各QRペイロードは
        v2|packet_id|N|total_len|filename_urlenc|base64payload
    の形式。全パケットにメタデータを含めることで、受信側はどのパケットからでも
    復号を開始できる。

実行方法:
    python sender.py FILE [-o OUTPUT.mp4] [--chunk-size 500]
                          [--redundancy 1.5] [--fps 5]
                          [--qr-version 20] [--ecc M]
                          [--qr-size 720] [--box-size 8]

引数:
    FILE                送信したいファイルのパス
    -o, --output        出力動画ファイル名（既定: <FILE>.mp4）
    --chunk-size        1ソースブロックのバイト数（既定: 500）
    --redundancy        ソースブロック数Nに対する送信パケット数の倍率（既定: 1.5）
    --fps               動画フレームレート（既定: 5）
    --qr-version        QRコードのバージョン 1〜40（既定: 20、低いほど誤り耐性↑）
    --ecc               誤り訂正レベル L/M/Q/H（既定: M）
    --qr-size           QR画像の出力ピクセルサイズ（既定: 720）
    --box-size          QRモジュール1個のピクセルサイズ（既定: 8）

依存:
    Python 3.8+
    qrcode, opencv-python, Pillow
"""

import argparse
import base64
import os
import sys
import tempfile
import urllib.parse
from pathlib import Path

import cv2
import qrcode
from qrcode.constants import (
    ERROR_CORRECT_L,
    ERROR_CORRECT_M,
    ERROR_CORRECT_Q,
    ERROR_CORRECT_H,
)
from PIL import Image, ImageDraw, ImageFont


PROTOCOL_VERSION = "v2"

ECC_MAP = {
    "L": ERROR_CORRECT_L,
    "M": ERROR_CORRECT_M,
    "Q": ERROR_CORRECT_Q,
    "H": ERROR_CORRECT_H,
}

# 次数分布。経験的にデコード確率と冗長性のバランスが良い組み合わせ
DEGREE_TABLE = [
    (2, 0.50),
    (3, 0.30),
    (5, 0.15),
    (8, 0.05),
]


def xorshift32(state: int) -> int:
    """32bit xorshift PRNG。Python/JSで同一の系列を再現可能。"""
    state &= 0xFFFFFFFF
    state ^= (state << 13) & 0xFFFFFFFF
    state ^= (state >> 17) & 0xFFFFFFFF
    state ^= (state << 5) & 0xFFFFFFFF
    return state & 0xFFFFFFFF


def derive_seed(packet_id: int) -> int:
    """packet_idからPRNG初期状態を導出。0回避と弱seed対策。"""
    s = (packet_id + 0x9E3779B9) & 0xFFFFFFFF
    return s if s != 0 else 0x12345678


def sample_degree(state: int) -> tuple[int, int]:
    """次数を引く。戻り値: (degree, 更新後state)。"""
    state = xorshift32(state)
    r = state / 0xFFFFFFFF
    cum = 0.0
    for d, p in DEGREE_TABLE:
        cum += p
        if r < cum:
            return d, state
    return DEGREE_TABLE[-1][0], state


def sample_indices(state: int, n: int, degree: int) -> tuple[list[int], int]:
    """重複なしで n 個から degree 個のインデックスをサンプリング。"""
    selected: list[int] = []
    seen = set()
    deg = min(degree, n)
    while len(selected) < deg:
        state = xorshift32(state)
        idx = state % n
        if idx not in seen:
            seen.add(idx)
            selected.append(idx)
    return selected, state


def derive_packet_indices(packet_id: int, n: int) -> list[int]:
    """packet_id から含まれるソースブロックのインデックス集合を導出。"""
    if packet_id < n:
        return [packet_id]
    state = derive_seed(packet_id)
    degree, state = sample_degree(state)
    indices, _ = sample_indices(state, n, degree)
    return indices


def encode_packet(packet_id: int, n: int, source_blocks: list[bytes], chunk_size: int) -> bytes:
    """packet_id に対応するパケットを XOR で生成。"""
    indices = derive_packet_indices(packet_id, n)
    if len(indices) == 1:
        return source_blocks[indices[0]]
    payload = bytearray(chunk_size)
    for idx in indices:
        block = source_blocks[idx]
        for i in range(chunk_size):
            payload[i] ^= block[i]
    return bytes(payload)


def make_qr_payload(packet_id: int, n: int, m: int, total_len: int, filename: str, chunk_data: bytes) -> str:
    fn_enc = urllib.parse.quote(filename, safe="")
    b64 = base64.b64encode(chunk_data).decode("ascii")
    return f"{PROTOCOL_VERSION}|{packet_id}|{n}|{m}|{total_len}|{fn_enc}|{b64}"


def render_qr(payload: str, version: int, ecc, box_size: int) -> Image.Image:
    qr = qrcode.QRCode(
        version=version,
        error_correction=ecc,
        box_size=box_size,
        border=4,
    )
    qr.add_data(payload)
    qr.make(fit=False)
    return qr.make_image(fill_color="black", back_color="white").convert("RGB")


def main():
    parser = argparse.ArgumentParser(description="QRファイル送信ツール v2（ファウンテン符号化）")
    parser.add_argument("file", help="送信するファイル")
    parser.add_argument("-o", "--output", help="出力動画ファイル名（既定: <FILE>.mp4）")
    parser.add_argument("--chunk-size", type=int, default=400)
    parser.add_argument("--redundancy", type=float, default=1.5)
    parser.add_argument("--fps", type=int, default=5)
    parser.add_argument("--qr-version", type=int, default=20)
    parser.add_argument("--ecc", choices=["L", "M", "Q", "H"], default="M")
    parser.add_argument("--qr-size", type=int, default=720)
    parser.add_argument("--box-size", type=int, default=8)
    args = parser.parse_args()

    file_path = Path(args.file)
    if not file_path.is_file():
        print(f"エラー: ファイルが見つかりません: {file_path}", file=sys.stderr)
        sys.exit(1)

    output_path = Path(args.output) if args.output else file_path.with_suffix(".mp4")

    data = file_path.read_bytes()
    total_len = len(data)
    chunk_size = args.chunk_size
    n = (total_len + chunk_size - 1) // chunk_size

    print(f"ファイル: {file_path.name} ({total_len:,} bytes)")
    print(f"ソースブロック数 N: {n} (chunk_size={chunk_size})")

    source_blocks: list[bytes] = []
    for i in range(n):
        block = data[i * chunk_size:(i + 1) * chunk_size]
        if len(block) < chunk_size:
            block = block + b"\x00" * (chunk_size - len(block))
        source_blocks.append(block)

    total_packets = max(n + 1, int(round(n * args.redundancy)))
    print(f"送信パケット数: {total_packets}（redundancy={args.redundancy}x, parity={total_packets - n}）")

    ecc = ECC_MAP[args.ecc]

    # 事前検証: 最も長くなる最終パケットのペイロード長でQRに収まるか確認
    # （packet_id桁数が最大の末尾パケットを基準にする）
    worst_pid = total_packets - 1
    worst_payload = make_qr_payload(worst_pid, n, total_packets, total_len, file_path.name, source_blocks[0])
    try:
        sample_img = render_qr(worst_payload, args.qr_version, ecc, args.box_size)
    except Exception as e:
        msg = str(e)
        print(f"\nエラー: QRコードにペイロードが収まりません: {msg}", file=sys.stderr)
        print(f"  ペイロード長: {len(worst_payload)} chars", file=sys.stderr)
        print(f"  対応策（いずれかを試す）:", file=sys.stderr)
        print(f"    1) --chunk-size を下げる（現在 {args.chunk_size}）", file=sys.stderr)
        print(f"    2) --qr-version を上げる（現在 {args.qr_version}、最大 40）", file=sys.stderr)
        print(f"    3) --ecc を下げる（現在 {args.ecc}、L < M < Q < H の順で容量が減る）", file=sys.stderr)
        sys.exit(2)

    sample_img = sample_img.resize((args.qr_size, args.qr_size), Image.NEAREST)
    margin = 40
    frame_w, frame_h = args.qr_size, args.qr_size + margin

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(output_path), fourcc, args.fps, (frame_w, frame_h))
    if not writer.isOpened():
        print(f"エラー: VideoWriterを開けません: {output_path}", file=sys.stderr)
        sys.exit(1)

    try:
        font = ImageFont.truetype("arial.ttf", 24)
    except OSError:
        font = ImageFont.load_default()

    import numpy as np

    for pid in range(total_packets):
        payload_bytes = encode_packet(pid, n, source_blocks, chunk_size)
        qr_payload = make_qr_payload(pid, n, total_len, file_path.name, payload_bytes)

        qr_img = render_qr(qr_payload, args.qr_version, ecc, args.box_size)
        qr_img = qr_img.resize((args.qr_size, args.qr_size), Image.NEAREST)

        frame_img = Image.new("RGB", (frame_w, frame_h), "white")
        frame_img.paste(qr_img, (0, 0))
        draw = ImageDraw.Draw(frame_img)
        text = f"Packet {pid+1}/{total_packets} (N={n})"
        bbox = draw.textbbox((0, 0), text, font=font)
        tx = (frame_w - (bbox[2] - bbox[0])) // 2
        ty = args.qr_size + (margin - (bbox[3] - bbox[1])) // 2
        draw.text((tx, ty), text, fill="black", font=font)

        frame_bgr = cv2.cvtColor(np.array(frame_img), cv2.COLOR_RGB2BGR)
        writer.write(frame_bgr)

        if (pid + 1) % 20 == 0 or pid + 1 == total_packets:
            print(f"  生成中: {pid+1}/{total_packets}")

    writer.release()
    duration = total_packets / args.fps
    print(f"完了: {output_path}")
    print(f"動画長: {duration:.1f} 秒 ({args.fps} fps, {total_packets} frames)")


if __name__ == "__main__":
    main()
