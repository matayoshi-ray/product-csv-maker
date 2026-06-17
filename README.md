# Product CSV Maker

LANBOの商品URLから、商品CSVと800x800の商品画像をまとめたZIPを作るWebアプリです。

## Features

- LANBO商品ページURLを入力して商品情報を抽出
- メーカー品番、車種名、商品名、設定カラー、説明文、税別/税込価格をCSV化
- 設定カラーがある商品では「設定カラー」を優先してカラー一覧に反映
- 1枚目の商品画像を自動白抜きして白背景の800x800 JPGに変換
- 2枚目以降の商品画像を白背景の800x800 JPGに変換
- CSV、変換済み画像、元画像をZIPでダウンロード
- 1枚目の白抜き済み画像を任意アップロードして自動白抜きの代わりに使用可能

## Requirements

- Python 3.12+
- Pillow

CodexのバンドルPythonを使う場合は、そのままPillow入りで起動できます。

## Run locally

```bash
python3 -m pip install -r requirements.txt
python3 webapp/app.py
```

Then open:

```text
http://127.0.0.1:8765
```

## Deploy on Render

This repository includes `render.yaml`.

1. Push this repository to GitHub.
2. In Render, create a new Blueprint from this repository.
3. Render will use:
   - Build command: `pip install -r requirements.txt`
   - Start command: `python webapp/app.py`
   - `HOST=0.0.0.0`
   - `OUTPUT_DIR=/tmp/product-csv-maker-runs`

For automatic background removal, add this environment variable in Render:

```text
REMOVE_BG_API_KEY=your_remove_bg_api_key
```

The app also works on other Python web hosts that support a `Procfile`:

```text
web: python webapp/app.py
```

## Output

Each run creates a ZIP containing:

- `product.csv`
- `images/image_01.jpg`, `image_02.jpg`, ...
- `source_images/`

The generated files are stored under `webapp/runs/` locally. On hosted environments, set `OUTPUT_DIR` to a writable temporary directory such as `/tmp/product-csv-maker-runs`.

## Note

The first image is automatically sent to remove.bg when `REMOVE_BG_API_KEY` is set, then placed on an 800x800 white background. If no API key is set, the app falls back to normal 800x800 conversion. Render Free does not have enough memory to run local AI background removal reliably.
