# qr-file-receiver

公開URL (HTML受信側): https://f2sk.github.io/qr-file-receiver/

QRコード動画でファイルを片方向転送するシステム。**ファウンテン符号化** によって、
動画の任意のフレームを十分な数読み取れば復元できる。受信側は「特定のチャンクが取れるまで
動画をループ再生」する必要がない。

## 構成

- **送信側** [sender.py](sender.py) — PCで動作。任意ファイルからQR動画(mp4)を生成
- **受信側1** [receiver_pc.py](receiver_pc.py) — PCで動作。Webカメラ経由で受信・復号
- **受信側2** [index.html](index.html) — スマホブラウザで動作（GitHub Pages配信）

## プロトコル v2

各QRペイロード:

```
v2|packet_id|N|total_len|filename(URLencoded)|base64payload
```

- `packet_id` (uint32): パケット識別子。PRNGシードとして利用
- `N`: ソースブロック総数
- `total_len`: 原本ファイルのバイト数
- `filename`: URLエンコードされたファイル名（**全パケットに含まれる**）
- `payload`: base64エンコードされた符号化バイト列

全パケットにメタデータを含むため、**任意のパケットから復号を開始できる**（v1の
「先頭フレームを必ず捕まえる必要あり」問題を解消）。

## 符号化方式

- **Systematic + ランダムXORパリティ**
  - `packet_id < N`: ソースブロック `packet_id` をそのまま送信（systematic）
  - `packet_id >= N`: PRNGで抽選した次数 d∈{2,3,5,8}（重み{0.50,0.30,0.15,0.05}）と
    インデックス集合を導出し、対応するソースブロックのXORを送信
- PRNG: **xorshift32**。Python/JS で同一の系列を生成する
- 送信パケット数: 既定 `N × 1.5`（redundancy）

## 復号方式

- GF(2) 上の **Gauss-Jordan消去**（オンラインRREF維持）
- 各パケット到着時に既存pivotで簡約 → 新pivotがあれば挿入＆既存pivotから新列を消去
- `rank == N` かつ全pivotが単一ビットになった時点で完了
- 性能: パケット毎 O(N × mask_bytes) + O(N × chunk_size)

## QRパラメータ（既定）

| 項目 | v1 | v2 |
|---|---|---|
| QRバージョン | 40 | **20** |
| 誤り訂正レベル | L (7%) | **M (15%)** |
| box_size | 6 | **8** |
| chunk_size | 1700 B | **400 B** |
| FPS | 任意 | 5 |

低密度・高誤り訂正化により、レンズ収差・モーションブラー・フォーカスずれへの
耐性が大幅向上する。

## 使い方

### 送信（PC）

```sh
pip install qrcode opencv-python Pillow
python sender.py path/to/file.bin
# → path/to/file.mp4 が生成される
```

主なオプション:
- `--chunk-size N` ソースブロックサイズ（既定400。QR容量と相談）
- `--redundancy 1.5` パケット数倍率（既定1.5）
- `--fps 5` 動画フレームレート
- `--qr-version 20` QRバージョン（1〜40）
- `--ecc M` 誤り訂正レベル（L/M/Q/H）

### 受信（PC）

```sh
pip install opencv-python pyzbar numpy
python receiver_pc.py -o ./recv_dir
```

ESCまたはqで中断、rでリセット。Windowsで `pyzbar` を使う場合は
Visual C++ 再頒布パッケージが必要なことがある。

### 受信（スマホ）

1. https://f2sk.github.io/qr-file-receiver/ を Android Chrome 等で開く
2. 「スキャン開始」→ カメラ権限を許可
3. PC で再生中の `*.mp4` をスマホでスキャン
4. 完了後「保存」でダウンロード

カメラ起動には HTTPS が必要（GitHub Pages は自動でHTTPS提供）。
BarcodeDetector API が使える環境ではネイティブQR復号、未対応時は jsQR にフォールバック。

## テスト

```sh
python test_roundtrip.py
```

PRNG系列の表示と、符号化→復号の往復テスト（loss 0〜70%）を実行。
