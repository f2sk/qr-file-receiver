# qr-file-receiver

公開URL:
- 受信（逐次チャンク版・従来）: https://f2sk.github.io/qr-file-receiver/
- 受信（fountain版・新, LT符号＋zxing-wasm）: https://f2sk.github.io/qr-file-receiver/fountain/
- 送信（fountain版・ブラウザでmp4生成, WebCodecs）: https://f2sk.github.io/qr-file-receiver/fountain/send.html
  （自己完結HTML。Rawを保存すればChromeでローカル・オフライン動作も可）

QR動画として送信されたファイルをブラウザで受信・保存するWebアプリ。
スマートフォンのカメラでQR動画を撮影し、復元したバイナリをダウンロードする。

送信側ツール（PCでQR動画を生成）と組み合わせて使う。
プロトコル仕様: 各QRペイロードは `index/total|filename|base64chunk` 形式。
先頭チャンク（index=0）のみ filename が入る。

## 使い方

1. 任意のWebサーバ（GitHub Pages等のHTTPS配信）に `index.html` を配置する
2. スマートフォン（Android Chrome等）でアクセス
3. 「スキャン開始」を押してカメラ権限を許可
4. 送信側PCで再生中のQR動画にカメラを向ける
5. 全チャンク受信完了後、「保存」ボタンでファイルがダウンロードフォルダに保存される

カメラの起動には HTTPS（または localhost）が必要。

## 機能

- カメラからのリアルタイムQR読み取り
- BarcodeDetector API優先（対応環境のみ）、未対応時はjsQRにフォールバック
- チャンク格子状の受信進捗表示（緑=受信済み / 灰=未受信）
- 認識インジケーター（QR検出時に緑点滅）
- 重複チャンク自動排除・任意順での受信
- Blobダウンロードによるファイル保存（送信時のファイル名を維持）

## 依存

- jsQR (CDN: jsdelivr) — フォールバック用
- BarcodeDetector API（ブラウザ内蔵）

## ファイル構成

- `index.html` — 全機能を含む単一ファイル
