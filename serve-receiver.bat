@echo off
rem ============================================================
rem  Receiver をローカルで使うための簡易サーバ起動ツール
rem ------------------------------------------------------------
rem  カメラは https / localhost でしか使えないため、受信HTMLは
rem  file:// 直開きでは動かない。このバッチを受信HTML
rem  (index.html または receiver.html) と同じフォルダに置いて
rem  ダブルクリックすると、localhost で配信してブラウザを開く。
rem  依存: Python（python もしくは py）。使用後はこの黒い窓を閉じる。
rem ============================================================
setlocal
cd /d "%~dp0"
set "PORT=8000"
set "PAGE="
if not exist index.html if exist receiver.html set "PAGE=receiver.html"

echo(
echo   Receiver をローカル起動します
echo   URL: http://localhost:%PORT%/%PAGE%
echo   使い終わったらこのウィンドウを閉じてください
echo(

start "" "http://localhost:%PORT%/%PAGE%"

where python >nul 2>nul
if %errorlevel%==0 (
  python -m http.server %PORT%
) else (
  py -m http.server %PORT%
)
