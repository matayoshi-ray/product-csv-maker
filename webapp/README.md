# 商品CSVメーカー

LANBOの商品URLを入力すると、商品情報CSVと800x800の商品画像をZIPで出力するWebアプリです。

## 起動

```bash
python3 -m pip install -r requirements.txt
python3 webapp/app.py
```

ブラウザで以下を開きます。

```text
http://127.0.0.1:8765
```

## Web公開

Renderなどのホスティング環境では、以下の環境変数を設定してください。

```text
HOST=0.0.0.0
OUTPUT_DIR=/tmp/product-csv-maker-runs
```

リポジトリ直下の`render.yaml`と`Procfile`を使ってデプロイできます。

## 出力内容

- `product.csv`
- `images/image_01.jpg` 以降の800x800画像
- `source_images/` 配下の元画像
- 上記一式のZIP

CSVには以下を入れます。

- 商品URL
- メーカー品番
- 車種名
- 商品名
- カラー（色）
- カラー一覧
- 素材
- 商品の説明文
- 定価（税別）
- 定価（税込）
- 商品画像パス

## 白抜き画像について

ローカルだけで商品部分を完全自動切り抜きするには、AI/APIまたは背景除去モデルが必要です。

このアプリでは、1枚目の白抜き画像を任意でアップロードできます。アップロードした場合は、それを`image_01.jpg`として800x800化します。未指定の場合は、商品ページ1枚目の元画像を白余白付き800x800にします。
