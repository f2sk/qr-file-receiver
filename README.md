# qr-file-receiver

QR動画でファイルを転送するツール。画面→カメラの片方向光チャネルだけで、ネットワークもアプリも使わずにファイルを送る。
現行版は **fountain符号（LT）** を採用し、各フレームを複数ブロックのXORにすることで「特定ブロック⇔特定フレーム」の鎖を断つ。
光の当たり方で読めないフレームがあっても、十分な枚数（約K×1.15）が集まれば順不同で復元できる。

## 公開URL

- **受信**: https://f2sk.github.io/qr-file-receiver/
- **送信**（ブラウザでmp4生成 / WebCodecs / 自己完結HTML）: https://f2sk.github.io/qr-file-receiver/tx.html
- 旧版（逐次チャンク方式・お蔵入り）: https://f2sk.github.io/qr-file-receiver/legacy/

## 使い方

1. PCのChrome / Edgeで **tx.html** を開き、ファイルを選んで「動画を生成」→ mp4を保存
2. その mp4 を全画面でループ再生
3. スマホで **受信ページ** を開き、カメラを動画に向ける。完了後「保存」でダウンロード

カメラ利用にはHTTPS（またはlocalhost）が必要。

## ローカルで受信を使う

受信はカメラを使うため、`file://`（HTMLを直接ダブルクリック）では動かない
（ブラウザはカメラを https / localhost でしか許可しない）。ローカルで使うには
`index.html` と同じフォルダに置いた **`serve-receiver.bat`** をダブルクリックすると、
localhost で配信してブラウザが開く（Python必須。使用後はコンソール窓を閉じる）。
送信 `tx.html` はカメラ不要なので `file://` 直開きでも動く。

## 構成

| ファイル | 役割 |
|---|---|
| `index.html` | 受信（単一HTML / デコードは zxing-wasm、CDN読込） |
| `tx.html` | 送信（単一HTML / WebCodecsでH.264オールイントラmp4生成。qrcode・mp4-muxerを埋め込み済みでCDN参照ゼロ→ローカル/オフライン動作可） |
| `legacy/index.html` | 旧・逐次チャンク方式の受信機（保管） |

送受信の開発一式（Node CLI送信・共有fountainコア・検証スクリプト）は別ワークスペース `qr-fountain` にある。

## 旧版について

旧「逐次チャンク方式」はペイロードが `index/total|filename|base64chunk` 形式で、全チャンクが揃うまで復元できず、
光の当たり方で読めないフレームがあると何周しても取りこぼす問題があった。fountain版はこれを構造的に解消したため、
旧版は `/legacy/` に保管し非推奨とする。

## ライセンス / Credits

本プロジェクトは MIT License（[LICENSE](LICENSE)）。

fountain符号（LT符号）とフレームプロトコルは
[decimen-optical-transfer](https://github.com/bashalarmistalt/decimen-optical-transfer)（MIT）の実装を移植・改変したもの。
送信ページは [node-qrcode](https://github.com/soldair/node-qrcode)（MIT）と
[mp4-muxer](https://github.com/Vanilagy/mp4-muxer)（MIT）を埋め込み、受信ページは
[zxing-wasm](https://github.com/Sec-ant/zxing-wasm)（MIT）を利用。
各ライセンス全文は [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md) を参照。
