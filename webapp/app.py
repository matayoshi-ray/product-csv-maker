from __future__ import annotations

import csv
import html
import os
import re
import sys
import traceback
import urllib.parse
import urllib.request
import uuid
import zipfile
from dataclasses import dataclass
from html.parser import HTMLParser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    from PIL import Image
except ImportError as exc:
    raise SystemExit(
        "Pillow is required. Run this with the bundled workspace Python shown by Codex."
    ) from exc


ROOT = Path(__file__).resolve().parent
OUTPUT_ROOT = Path(os.environ.get("OUTPUT_DIR", str(ROOT / "runs")))
MAX_BODY_BYTES = 30 * 1024 * 1024


@dataclass
class ProductData:
    url: str
    product_name: str
    manufacturer_part_number: str
    vehicle_name: str
    colors: list[str]
    material: str
    description: str
    price_ex_tax: str
    price_in_tax: str
    image_urls: list[str]


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"br", "p", "div", "li", "dt", "dd", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")
        if tag == "li":
            self.parts.append("・")

    def handle_data(self, data: str) -> None:
        text = data.replace("\u3000", " ").strip()
        if text:
            self.parts.append(text)

    def get_text(self) -> str:
        text = "".join(self.parts)
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
        return "\n".join(line for line in lines if line)


def fetch_bytes(url: str) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"
            )
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def decode_html(raw: bytes) -> str:
    for enc in ("utf-8", "cp932", "euc-jp"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def clean_text(value: str) -> str:
    return html.unescape(re.sub(r"\s+", " ", value).strip())


def strip_tags(fragment: str) -> str:
    parser = TextExtractor()
    parser.feed(fragment)
    return parser.get_text()


def first_match(pattern: str, text: str, flags: int = re.S) -> str:
    match = re.search(pattern, text, flags)
    return clean_text(match.group(1)) if match else ""


def extract_dd(label: str, fragment: str) -> str:
    pattern = rf"<dt>\s*{re.escape(label)}\s*</dt>\s*<br\s*/?>\s*<dd>(.*?)</dd>"
    return strip_tags(first_match(pattern, fragment))


def split_colors(raw: str) -> list[str]:
    text = raw
    text = re.sub(r"^(マット|カラー|色)\s*[:：]", "", text).strip()
    text = re.sub(r"^(フレーム|素材)\s*[:：].*?マット\s*[:：]", "", text).strip()
    text = text.replace("カラー", "カラー")
    parts = re.split(r"\s*(?:/|／|、|,|，|\||・)\s*", text)
    colors = []
    for part in parts:
        part = part.strip()
        if not part or any(skip in part for skip in ("PVC", "レザー", "高強度", "鋼材", "素材")):
            continue
        colors.append(part)
    return colors or ([raw.strip()] if raw.strip() else [])


def option_texts(select_html: str) -> list[str]:
    values: list[str] = []
    for option in re.findall(r"<option\b[^>]*>(.*?)</option>", select_html, re.I | re.S):
        text = strip_tags(option)
        if not text:
            continue
        if text in {"選択してください", "選択して下さい", "指定なし", "---", "--"}:
            continue
        if text.isdigit():
            continue
        if text not in values:
            values.append(text)
    return values


def extract_configured_colors(page_html: str) -> list[str]:
    colors: list[str] = []

    # Prefer a concrete "設定カラー" selector when the product page renders one.
    setting_color_blocks = re.findall(
        r"設定カラー.*?(<select\b.*?</select>)",
        page_html,
        re.I | re.S,
    )
    for block in setting_color_blocks:
        for value in option_texts(block):
            if value not in colors:
                colors.append(value)

    # Some pages expose color variation data in JavaScript rather than a rendered select.
    for label_match in re.finditer(r"(?:selectLabel|label|name)\s*[:=]\s*['\"]設定カラー['\"]", page_html):
        window = page_html[label_match.start() : label_match.start() + 6000]
        candidates = re.findall(
            r"(?:optionLabel|optionName|name|label|value)\s*[:=]\s*['\"]([^'\"]+)['\"]",
            window,
            re.I,
        )
        for candidate in candidates:
            value = clean_text(candidate)
            if value and value != "設定カラー" and not value.isdigit() and value not in colors:
                colors.append(value)

    return colors


def parse_lanbo_product(url: str, page_html: str) -> ProductData:
    desc_fragment = first_match(
        r'<div class="item_desc_text custom_desc">\s*(.*?)\s*</div>\s*</div>\s*</div>',
        page_html,
    )
    description = strip_tags(desc_fragment)

    product_name = first_match(r'<span class="title_text goods_name">(.*?)</span>', page_html)
    if not product_name:
        product_name = first_match(r'<span class="goods_name">(.*?)</span>', page_html)

    part_number = first_match(r'<span class="model_number_value">(.*?)</span>', page_html)
    vehicle_name = extract_dd("対応車種", desc_fragment)
    material_color = extract_dd("素材/カラー", desc_fragment)
    basic_product_name = extract_dd("商品名", desc_fragment)
    if not product_name and basic_product_name:
        product_name = basic_product_name

    colors = extract_configured_colors(page_html) or split_colors(material_color)
    material = material_color
    price_ex_tax = first_match(r'id="pricech">\s*([0-9,]+)\s*<span[^>]*>円</span>', page_html)
    price_in_tax = first_match(
        r'id="tax_included_price" class="figure">\s*([0-9,]+)\s*<span[^>]*>円</span>',
        page_html,
    )
    if price_ex_tax:
        price_ex_tax += "円"
    if price_in_tax:
        price_in_tax += "円"

    image_urls = []
    for image_url in re.findall(
        r'<a href="(https://www\.lanbo\.co\.jp/data/lanbo/product/[^"]+\.(?:jpg|jpeg|png|webp))"',
        page_html,
        re.I,
    ):
        if image_url not in image_urls:
            image_urls.append(image_url)

    return ProductData(
        url=url,
        product_name=product_name,
        manufacturer_part_number=part_number,
        vehicle_name=vehicle_name,
        colors=colors,
        material=material,
        description=description,
        price_ex_tax=price_ex_tax,
        price_in_tax=price_in_tax,
        image_urls=image_urls,
    )


def fit_to_square_800(input_path: Path, output_path: Path) -> None:
    with Image.open(input_path) as image:
        image = image.convert("RGB")
        image.thumbnail((800, 800), Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", (800, 800), "white")
        canvas.paste(image, ((800 - image.width) // 2, (800 - image.height) // 2))
        canvas.save(output_path, quality=95, subsampling=0)


def safe_slug(value: str, fallback: str = "product") -> str:
    slug = re.sub(r"[^0-9A-Za-z_-]+", "_", value).strip("_")
    return slug[:80] or fallback


def write_csv(product: ProductData, image_names: list[str], run_dir: Path) -> Path:
    csv_path = run_dir / "product.csv"
    row: dict[str, str] = {
        "商品URL": product.url,
        "メーカー品番": product.manufacturer_part_number,
        "車種名": product.vehicle_name,
        "商品名": product.product_name,
        "カラー（色）": " / ".join(product.colors),
        "カラー一覧": " / ".join(product.colors),
        "素材": product.material,
        "商品の説明文": product.description,
        "定価（税別）": product.price_ex_tax,
        "定価（税込）": product.price_in_tax,
        "画像フォルダー": "images",
    }
    for index, name in enumerate(image_names, 1):
        row[f"商品画像{index}"] = f"images/{name}"

    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(row.keys()))
        writer.writeheader()
        writer.writerow(row)
    return csv_path


def make_zip(run_dir: Path) -> Path:
    zip_path = run_dir.with_suffix(".zip")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(run_dir.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(run_dir.parent))
    return zip_path


def process_product(url: str, white_image: tuple[str, bytes] | None) -> tuple[ProductData, Path, Path]:
    raw = fetch_bytes(url)
    page_html = decode_html(raw)
    product = parse_lanbo_product(url, page_html)
    if not product.image_urls:
        raise ValueError("商品画像URLを抽出できませんでした。LANBOの商品ページURLか確認してください。")

    product_id = first_match(r"/product/([0-9]+)", urllib.parse.urlparse(url).path, flags=0)
    run_name = f"{safe_slug(product.manufacturer_part_number or product_id)}_{uuid.uuid4().hex[:8]}"
    run_dir = OUTPUT_ROOT / run_name
    source_dir = run_dir / "source_images"
    image_dir = run_dir / "images"
    source_dir.mkdir(parents=True, exist_ok=True)
    image_dir.mkdir(parents=True, exist_ok=True)

    image_names: list[str] = []
    if white_image and white_image[1]:
        original_name = safe_slug(Path(white_image[0]).stem, "white_image") + Path(white_image[0]).suffix
        white_source = source_dir / f"image_01_white_uploaded{Path(original_name).suffix or '.jpg'}"
        white_source.write_bytes(white_image[1])
        fit_to_square_800(white_source, image_dir / "image_01.jpg")
        image_names.append("image_01.jpg")
        start_index = 2
    else:
        start_index = 1

    for url_index, image_url in enumerate(product.image_urls, start_index):
        ext = Path(urllib.parse.urlparse(image_url).path).suffix.lower() or ".jpg"
        source_path = source_dir / f"image_{url_index:02d}_original{ext}"
        source_path.write_bytes(fetch_bytes(image_url))
        output_name = f"image_{url_index:02d}.jpg"
        fit_to_square_800(source_path, image_dir / output_name)
        image_names.append(output_name)

    write_csv(product, image_names, run_dir)
    zip_path = make_zip(run_dir)
    return product, run_dir, zip_path


def parse_multipart(body: bytes, content_type: str) -> tuple[str, tuple[str, bytes] | None]:
    match = re.search(r"boundary=(.+)", content_type)
    if not match:
        raise ValueError("フォームデータのboundaryが見つかりません。")
    boundary = ("--" + match.group(1).strip().strip('"')).encode()
    url = ""
    white_image: tuple[str, bytes] | None = None
    for part in body.split(boundary):
        if b"Content-Disposition:" not in part:
            continue
        header, _, data = part.partition(b"\r\n\r\n")
        data = data.rstrip(b"\r\n-")
        disposition = header.decode("utf-8", errors="replace")
        name_match = re.search(r'name="([^"]+)"', disposition)
        if not name_match:
            continue
        name = name_match.group(1)
        if name == "url":
            url = data.decode("utf-8", errors="replace").strip()
        elif name == "white_image" and data:
            filename = first_match(r'filename="([^"]*)"', disposition, flags=0) or "white_image.jpg"
            white_image = (filename, data)
    return url, white_image


def page_html(result: str = "", error: str = "") -> bytes:
    body = f"""<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>商品CSVメーカー</title>
  <style>
    :root {{
      color-scheme: light;
      --text: #1e2428;
      --muted: #66717a;
      --line: #d8dee4;
      --panel: #f7f9fb;
      --accent: #116149;
      --accent-dark: #0d4938;
      --danger: #9b1c1c;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: var(--text);
      background: #ffffff;
    }}
    main {{
      width: min(1040px, calc(100vw - 32px));
      margin: 0 auto;
      padding: 32px 0 48px;
    }}
    header {{
      display: flex;
      align-items: flex-end;
      justify-content: space-between;
      gap: 24px;
      padding-bottom: 20px;
      border-bottom: 1px solid var(--line);
    }}
    h1 {{
      margin: 0;
      font-size: 28px;
      line-height: 1.25;
      letter-spacing: 0;
    }}
    .sub {{
      margin: 8px 0 0;
      color: var(--muted);
      font-size: 14px;
      line-height: 1.6;
    }}
    form {{
      margin-top: 28px;
      padding: 24px;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
    }}
    label {{
      display: block;
      font-weight: 650;
      margin-bottom: 8px;
      font-size: 14px;
    }}
    input[type="url"], input[type="file"] {{
      width: 100%;
      min-height: 44px;
      border: 1px solid #b7c0c8;
      border-radius: 6px;
      background: white;
      padding: 10px 12px;
      font-size: 15px;
    }}
    .field {{ margin-bottom: 18px; }}
    .hint {{
      margin: 6px 0 0;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.5;
    }}
    button {{
      min-height: 44px;
      border: 0;
      border-radius: 6px;
      padding: 0 18px;
      background: var(--accent);
      color: white;
      font-weight: 700;
      font-size: 15px;
      cursor: pointer;
    }}
    button:hover {{ background: var(--accent-dark); }}
    .result, .error {{
      margin-top: 20px;
      padding: 16px;
      border-radius: 8px;
      line-height: 1.6;
      white-space: pre-wrap;
    }}
    .result {{ border: 1px solid #9ac7b2; background: #eef8f3; }}
    .error {{ border: 1px solid #efb3b3; background: #fff4f4; color: var(--danger); }}
    a {{ color: var(--accent-dark); font-weight: 700; }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 12px;
      margin-top: 24px;
    }}
    .metric {{
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
      min-height: 92px;
    }}
    .metric strong {{ display: block; font-size: 13px; margin-bottom: 6px; }}
    .metric span {{ color: var(--muted); font-size: 13px; line-height: 1.5; }}
    @media (max-width: 720px) {{
      header {{ display: block; }}
      .grid {{ grid-template-columns: 1fr; }}
      form {{ padding: 16px; }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>商品CSVメーカー</h1>
        <p class="sub">LANBOの商品URLから、CSVと800×800の商品画像入りZIPを生成します。</p>
      </div>
    </header>

    <form method="post" action="/process" enctype="multipart/form-data">
      <div class="field">
        <label for="url">商品URL</label>
        <input id="url" name="url" type="url" required placeholder="https://www.lanbo.co.jp/product/863">
      </div>
      <div class="field">
        <label for="white_image">1枚目の白抜き画像（任意）</label>
        <input id="white_image" name="white_image" type="file" accept="image/*">
        <p class="hint">未指定の場合、1枚目の元画像を白余白付き800×800にします。AI白抜き済み画像がある場合はここに入れると商品画像1として使います。</p>
      </div>
      <button type="submit">CSVと画像ZIPを作成</button>
    </form>

    {result}
    {error}

    <section class="grid" aria-label="処理内容">
      <div class="metric"><strong>抽出項目</strong><span>商品URL、メーカー品番、車種名、商品名、カラー一覧、説明文、税別/税込価格。</span></div>
      <div class="metric"><strong>画像処理</strong><span>全画像を白背景の800×800 JPGに変換。元画像はsource_imagesに保存。</span></div>
      <div class="metric"><strong>出力</strong><span>product.csv、imagesフォルダー、source_imagesフォルダーをZIPでダウンロード。</span></div>
    </section>
  </main>
</body>
</html>"""
    return body.encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path.startswith("/download/"):
            self.serve_download()
            return
        self.respond(HTTPStatus.OK, page_html())

    def do_POST(self) -> None:
        if self.path != "/process":
            self.respond(HTTPStatus.NOT_FOUND, b"Not found", "text/plain")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > MAX_BODY_BYTES:
                raise ValueError("アップロード容量が大きすぎます。")
            body = self.rfile.read(length)
            url, white_image = parse_multipart(body, self.headers.get("Content-Type", ""))
            if not url.startswith("https://www.lanbo.co.jp/product/"):
                raise ValueError("現在はLANBOの商品ページURLのみ対応しています。")
            product, run_dir, zip_path = process_product(url, white_image)
            link = f"/download/{urllib.parse.quote(zip_path.name)}"
            result = (
                '<div class="result">'
                f"作成完了\n"
                f"商品名: {html.escape(product.product_name)}\n"
                f"メーカー品番: {html.escape(product.manufacturer_part_number)}\n"
                f"カラー: {html.escape(' / '.join(product.colors))}\n"
                f"画像枚数: {len(product.image_urls)}\n"
                f'<a href="{link}">ZIPをダウンロード</a>'
                "</div>"
            )
            self.respond(HTTPStatus.OK, page_html(result=result))
        except Exception as exc:
            traceback.print_exc()
            error = f'<div class="error">エラー: {html.escape(str(exc))}</div>'
            self.respond(HTTPStatus.BAD_REQUEST, page_html(error=error))

    def serve_download(self) -> None:
        name = Path(urllib.parse.unquote(self.path.removeprefix("/download/"))).name
        path = OUTPUT_ROOT / name
        if not path.exists() or path.suffix != ".zip":
            self.respond(HTTPStatus.NOT_FOUND, b"Not found", "text/plain")
            return
        payload = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
        self.end_headers()
        self.wfile.write(payload)

    def respond(self, status: HTTPStatus, body: bytes, content_type: str = "text/html; charset=utf-8") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def main() -> None:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8765"))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Serving on http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
