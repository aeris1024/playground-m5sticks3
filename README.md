# M5StickS3 PlatformIO playground

M5StickS3の小さなアプリを、IDEに依存せずCLIから再現可能にビルド・書き込みするための実験用リポジトリです。

Pythonツールはuv、組み込み開発環境はPlatformIO、デバイス制御はArduinoフレームワークとM5Unifiedを使用します。Pythonパッケージ、PlatformIOプラットフォーム、M5Unifiedのバージョンは固定しています。

## 必要なもの

- Windows 10または11
- PowerShell 7またはWindows PowerShell 5.1
- [uv](https://docs.astral.sh/uv/)
- M5StickS3とデータ通信対応USBケーブル

PlatformIOをグローバルにインストールする必要はありません。

## セットアップ

PowerShellでリポジトリのルートへ移動し、次を実行します。

```powershell
uv run m5pio setup hello-display
```

初回は必要に応じてPython本体、Pythonパッケージ、ESP32ツールチェーン、Arduinoフレームワーク、M5Unifiedをダウンロードします。Python仮想環境は `.venv`、ツールキャッシュは `.cache`、ビルド成果物は `.pio` に保存され、いずれもGitから除外されます。

## 基本操作

```powershell
# ビルド
uv run m5pio build hello-display

# 接続中のシリアルポートを確認
uv run m5pio devices

# 自動検出したポートへ書き込み
uv run m5pio upload hello-display

# ポートを明示して書き込み
uv run m5pio upload hello-display --port COM5

# シリアルモニター（終了は Ctrl+C）
uv run m5pio monitor hello-display

# ビルド成果物を削除
uv run m5pio clean hello-display

# 登録済みアプリを一覧表示
uv run m5pio apps
```

書き込み先が認識されない場合は、本体側面のリセットボタンを約2秒長押しし、内部の緑色LEDが点滅したら離してダウンロードモードへ入れます。その後、もう一度 `devices` と `upload` を実行してください。

## サンプル

### hello-display

起動メッセージを画面とシリアルへ出力します。AまたはBボタンを押すと、画面の状態と押下回数が更新され、シリアルにもイベントが出力されます。

## アプリの追加

例として `sensor-viewer` を追加する場合は、次のファイルを作成します。

```text
apps/
└── sensor-viewer/
    └── src/
        └── main.cpp
```

続いて `platformio.ini` に環境を追加します。

```ini
[env:sensor-viewer]
extends = sticks3
build_src_filter =
    +<sensor-viewer/src/>
```

これで次のように操作できます。

```powershell
uv run m5pio build sensor-viewer
uv run m5pio upload sensor-viewer
```

全アプリ共通のボード設定とライブラリは `config/sticks3.ini` に置き、アプリ固有の依存関係やビルドフラグだけを各 `[env:...]` に追加します。

## 構成

```text
apps/                 サンプルアプリ
config/sticks3.ini    M5StickS3共通設定
src/m5pio/            セットアップ・ビルド・書き込み用CLI
platformio.ini        アプリ環境の登録
pyproject.toml        Pythonツールの宣言
uv.lock               Python依存関係の固定
```
