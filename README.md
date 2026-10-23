# M5StickS3 PlatformIO Playground

[M5StickS3](https://docs.m5stack.com/en/core/StickS3)向けのサンプルアプリを、IDEに依存せずCLIからビルド・書き込みするための実験用リポジトリです。

Python環境は[uv](https://docs.astral.sh/uv/)、組み込み開発環境は[PlatformIO](https://platformio.org/)、デバイス制御はArduinoフレームワークと[M5Unified](https://github.com/m5stack/M5Unified)を使用します。依存関係を固定しているため、別のPCやAgentからも同じ手順で開発環境を再現できます。

## 特徴

- PlatformIOをグローバルインストールせずに利用可能
- `uv run m5pio` にビルド、書き込み、シリアルモニター操作を集約
- 複数のサンプルアプリを1つのリポジトリで管理
- Python依存関係、ESP32プラットフォーム、M5Unified、M5GFXのバージョンを固定
- M5StickS3実機でビルド、書き込み、シリアル出力を確認できる構成

## サンプルアプリ

| アプリ | 内容 |
| --- | --- |
| `hello-display` | 画面表示、A/Bボタン入力、シリアル出力を確認する最小サンプル |
| `imu-monitor` | 内蔵IMUの加速度と角速度を水準器と3軸バーで表示し、CSVとしてシリアル出力 |
| `voice-memo` | 内蔵マイクで最大約30秒録音し、内蔵スピーカーで再生する。USB Audioは使わない |
| `head-tracker` | 頭の姿勢をWi-FiまたはUSBで送り、Windows確認アプリからIEM SceneRotatorへ中継 |

利用可能なアプリはCLIでも確認できます。

```console
uv run m5pio apps
```

## 必要なもの

- M5StickS3
- データ通信対応USB Type-Cケーブル
- [uv](https://docs.astral.sh/uv/getting-started/installation/) 0.11.24以降

PlatformIOとPython 3.14はプロジェクト環境として準備されるため、個別にインストールする必要はありません。

現在はWindows 11で動作確認しています。CLI自体にWindows固有処理はありませんが、macOSとLinuxでの実機動作は未検証です。

## クイックスタート

リポジトリを取得し、ルートディレクトリで以下を実行します。

```console
# 開発環境とhello-displayの依存パッケージを準備
uv run m5pio setup hello-display

# ファームウェアをビルド
uv run m5pio build hello-display

# 接続中のシリアルデバイスを確認
uv run m5pio devices
```

表示されたポートを指定してM5StickS3へ書き込みます。次の `COM7` はWindowsでの例です。macOSでは `/dev/cu.usbmodem...`、Linuxでは `/dev/ttyACM0` など、実際に表示されたポートへ置き換えてください。

```console
uv run m5pio upload hello-display --port COM7
```

書き込み後のシリアル出力を確認します。

```console
uv run m5pio monitor hello-display --port COM7
```

シリアルモニターは `Ctrl+C` で終了します。モニターがポートを使用している間は書き込めないため、再度書き込む前に終了してください。

## CLI

アプリ名を省略した場合は `hello-display` が選択されます。

| コマンド | 説明 |
| --- | --- |
| `uv run m5pio setup [APP]` | uv環境とPlatformIOパッケージを準備 |
| `uv run m5pio build [APP]` | ファームウェアをビルド |
| `uv run m5pio upload [APP]` | ビルドしてデバイスへ書き込み |
| `uv run m5pio monitor [APP]` | アプリ設定に従ってシリアルモニターを開始 |
| `uv run m5pio clean [APP]` | アプリのビルド成果物を削除 |
| `uv run m5pio devices` | 接続中のシリアルデバイスを一覧表示 |
| `uv run m5pio apps` | 利用可能なアプリを一覧表示 |

ポートは `--port` で指定できます。

```console
uv run m5pio upload imu-monitor --port COM7
uv run m5pio monitor imu-monitor --port COM7
```

`--port` を省略するとPlatformIOによる自動検出を使用します。

## hello-display

画面とシリアルへ起動メッセージを出力します。Aボタンでカウンターを増やし、Bボタンで減らします。

```console
uv run m5pio build hello-display
uv run m5pio upload hello-display --port COM7
uv run m5pio monitor hello-display --port COM7
```

シリアル出力例：

```text
hello-display started
button=A count=1
button=B count=0
```

## imu-monitor

内蔵IMUから3軸の加速度と角速度を取得し、10Hzで画面へ表示します。

- 左側の水準器：本体の傾き
- 右側のX/Y/Zバー：各軸まわりの回転方向と強さ
- 水準器のマーカー：画面が上向きなら緑、裏返すと赤
- Aボタン：表示とシリアル出力の一時停止・再開
- オフスクリーン描画：画面更新時のちらつきを抑制

```console
uv run m5pio build imu-monitor
uv run m5pio upload imu-monitor --port COM7
uv run m5pio monitor imu-monitor --port COM7
```

シリアルには分析や記録に利用できるCSVを出力します。加速度の単位はg、角速度の単位はdegree/secondです。

```text
millis,ax_g,ay_g,az_g,gx_dps,gy_dps,gz_dps
1234,0.0123,-0.0045,0.9981,0.125,-0.250,0.063
```

## voice-memo

内蔵マイクとスピーカーだけで録音と再生を確認します。USB Audioにはしません。録音はPSRAM上にだけ置き、リセットすると消えます。

- Aボタン：録音開始。録音中なら停止。再生中なら再生を止めて新規録音（上書き）
- Bボタン：再生。録音中なら停止してすぐ再生。再生中なら頭から再生し直し。データがなければEMPTY
- 16 kHz、16-bit、モノラル、上限約30秒
- 30秒に達すると自動停止（FULL）。自動では再生しない
- マイクとスピーカーは同時に使えないため、録音と再生を切り替えて使う
- 電池駆動時の再起動を避けるため、再生音量は約70%に固定

```console
uv run m5pio build voice-memo
uv run m5pio upload voice-memo --port COM7
uv run m5pio monitor voice-memo --port COM7
```

シリアルには状態遷移を出力します。PCMは流しません。

```text
voice-memo started
state=EMPTY samples=0
state=REC samples=0
state=IDLE samples=48000
state=PLAY samples=48000
```

## head-tracker

M5StickS3をヘッドフォン上へ「画面を上、USB端子を後ろ」の向きで固定して使います。
PCアプリで頭の向きを表示し、IEM SceneRotatorへ逆回転のクォータニオンを送ります。

Windowsでは、`tools/head-tracker-monitor/`内の[`start-head-tracker-monitor.cmd`](tools/head-tracker-monitor/start-head-tracker-monitor.cmd)を
ダブルクリックするとGUIを起動できます。デスクトップにこのファイルのショートカットを置いても使えます。
初回は依存パッケージを自動で準備します。uvは事前にインストールしてください。

```console
uv run m5pio build head-tracker
uv run m5pio upload head-tracker --port COM7
uv run --project tools/head-tracker-monitor --frozen head-tracker-monitor
```

PCアプリの「姿勢データの入力」でWi-FiとUSBを切り替えられます。
USBではポートを選ぶだけで連続受信でき、Wi-Fiの設定は不要です。
Wi-Fiで使う場合は、初回にUSB経由でWi-Fiと送信先を設定します。設定後は電池で使用できます。
Aボタンで正面を設定し直し、Bボタンを3秒長押しすると静止校正をやり直します。

PCアプリは独立したPythonプロジェクトとして、専用の依存関係とロックファイルを管理します。
設定方法、OSC仕様、SceneRotatorとの接続、検証状況は[ヘッドトラッカーの手順](docs/head-tracker.md)を参照してください。

## 新しいアプリの追加

例として `sensor-viewer` を追加する場合は、次のディレクトリを作成します。

```text
apps/
└── sensor-viewer/
    └── src/
        └── main.cpp
```

続いて、`platformio.ini` に環境を追加します。

```ini
[env:sensor-viewer]
extends = sticks3
build_src_filter =
    +<sensor-viewer/src/>
```

登録後は他のアプリと同様に操作できます。

```console
uv run m5pio build sensor-viewer
uv run m5pio upload sensor-viewer --port COM7
```

全アプリ共通のボード設定とライブラリは `config/sticks3.ini` に置き、アプリ固有の依存関係やビルドフラグだけを各 `[env:...]` に追加します。

## ディレクトリ構成

```text
.
├── apps/                  サンプルアプリ
├── config/sticks3.ini     M5StickS3共通設定
├── docs/                 通信仕様・アプリ別の操作手順
├── src/m5pio/             開発操作をまとめたPython CLI
├── tools/                PC側の確認アプリ（独立したuvプロジェクト）
├── platformio.ini         PlatformIO環境とアプリの登録
├── pyproject.toml         PythonプロジェクトとCLI定義
└── uv.lock                Python依存関係のロックファイル
```

ローカルで生成される以下のディレクトリはGit管理されません。

| ディレクトリ | 内容 |
| --- | --- |
| `.venv/` | Python仮想環境 |
| `.cache/` | uv、PlatformIO、ツールチェーンのキャッシュ |
| `.pio/` | ファームウェアなどのビルド成果物 |

## 固定している主要バージョン

| コンポーネント | バージョン |
| --- | --- |
| Python | 3.14 |
| PlatformIO Core | 6.1.19 |
| PlatformIO Espressif 32 | 6.12.0 |
| M5Unified | 0.2.19 |
| M5GFX | 0.2.26 |

Pythonパッケージの完全な解決結果は `uv.lock`、M5StickS3のPlatformIO設定は `config/sticks3.ini` を参照してください。

## トラブルシューティング

### シリアルポートが表示されない

1. USBケーブルがデータ通信対応か確認します。
2. M5StickS3側面のリセットボタンを約2秒長押しします。
3. 内部の緑色LEDが点滅したらボタンを離します。
4. `uv run m5pio devices` を再実行します。

### 書き込み先を自動検出できない

`devices` で確認したポートを明示します。

```console
uv run m5pio upload imu-monitor --port COM7
```

### ポートを開けない

別のシリアルモニターやターミナルが同じポートを使用していないか確認します。このリポジトリのモニターを実行中の場合は、`Ctrl+C` で終了してから書き込んでください。

## ライセンス

このプロジェクトは[MIT License](LICENSE)のもとで公開されています。
