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

1枚目の商品画像は、`rembg`で自動白抜きしてから白背景の800x800 JPGに変換します。

1枚目の白抜き画像を任意でアップロードすることもできます。アップロードした場合は、自動白抜きの代わりにそれを`image_01.jpg`として800x800化します。

Render Freeでは、初回処理時に背景除去モデルの読み込みで時間がかかることがあります。
