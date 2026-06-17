# Product CSV Maker

LANBOの商品URLから、商品CSVと800x800の商品画像をまとめたZIPを作るローカルWebアプリです。

## Features

- LANBO商品ページURLを入力して商品情報を抽出
- メーカー品番、車種名、商品名、設定カラー、説明文、税別/税込価格をCSV化
- 設定カラーがある商品では「設定カラー」を優先してカラー一覧に反映
- 商品画像を白背景の800x800 JPGに変換
- CSV、変換済み画像、元画像をZIPでダウンロード
- 1枚目の白抜き済み画像を任意アップロード可能

## Requirements

- Python 3.12+
- Pillow

CodexのバンドルPythonを使う場合は、そのままPillow入りで起動できます。

## Run

```bash
/Users/user/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 webapp/app.py
```

Then open:

```text
http://127.0.0.1:8765
```

If using your own Python:

```bash
python3 -m pip install Pillow
python3 webapp/app.py
```

## Output

Each run creates a ZIP containing:

- `product.csv`
- `images/image_01.jpg`, `image_02.jpg`, ...
- `source_images/`

The generated files are stored under `webapp/runs/`, which is ignored by Git.

## Note

Fully automatic product-only background removal requires an AI/API or a local background-removal model. This app currently accepts an optional pre-cut white-background image for image 1. If no white-background image is uploaded, the first source image is converted to 800x800 like the other images.
