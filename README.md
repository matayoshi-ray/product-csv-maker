# Product CSV Maker

LANBOの商品URLから、商品CSVと900x900の商品画像をまとめたZIPを作るWebアプリです。

## Features

- LANBO商品ページURLを入力して商品情報を抽出
- 元URL、車種名、車種型番、車種年式、商品名、カラー、メーカー品番、税込み価格、商品説明文をCSV化
- 設定カラーがある商品では「設定カラー」を優先してカラー一覧に反映
- カラーが複数ある場合はカラーごとにCSV行を分けて出力
- 1枚目の商品画像を自動白抜きして白背景の900x900 PNGに変換
- 2枚目以降の商品画像を下帯テンプレート付きの900x900 PNGに変換
- フォーム入力の品番で画像ファイル名をリネーム
- CSV、変換済み画像、元画像をZIPでダウンロード

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
- `images/<品番>.png`, `<品番>_1.png`, ...
- `source_images/`

The generated files are stored under `webapp/runs/` locally. On hosted environments, set `OUTPUT_DIR` to a writable temporary directory such as `/tmp/product-csv-maker-runs`.

## Note

The first image is automatically sent to remove.bg when `REMOVE_BG_API_KEY` is set, then placed on a 900x900 white background. If no API key is set, or if the API result does not contain meaningful transparency, the app falls back to normal 900x900 conversion. Render Free does not have enough memory to run local AI background removal reliably.
