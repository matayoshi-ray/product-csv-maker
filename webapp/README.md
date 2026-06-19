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
- `images/<品番>.png` 以降の800x800画像
- `source_images/` 配下の元画像
- 上記一式のZIP

CSVには以下を入れます。

- 元URL
- 車種名
- 車種型番
- 車種年式
- ブランク
- 商品名
- カラー
- メーカー品番
- 税込み価格
- 商品説明文

カラーが複数ある場合は、カラーごとに行を分けて出力します。

フォームの「画像リネーム用 品番」に入力した値を、画像ファイル名とZIP内フォルダー名に使います。画像名は「品番.png」「品番_1.png」「品番_2.png」の形式です。未入力の場合はメーカー品番を使います。

## 白抜き画像について

1枚目の商品画像は、`REMOVE_BG_API_KEY`が設定されている場合にremove.bg APIで自動白抜きしてから白背景の800x800 PNGに変換します。

`REMOVE_BG_API_KEY`が未設定の場合、またはAPIの結果に十分な透明部分がない場合は、処理停止を避けるため通常の800x800変換へ戻します。Render FreeではローカルAI白抜きがメモリ不足になりやすいため、外部API方式にしています。
