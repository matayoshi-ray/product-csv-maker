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
    from PIL import Image, ImageDraw, ImageStat
except ImportError as exc:
    raise SystemExit(
        "Pillow is required. Run this with the bundled workspace Python shown by Codex."
    ) from exc

ROOT = Path(__file__).resolve().parent
OUTPUT_ROOT = Path(os.environ.get("OUTPUT_DIR", str(ROOT / "runs")))
MAX_BODY_BYTES = 30 * 1024 * 1024
REMOVE_BG_ENDPOINT = "https://api.remove.bg/v1.0/removebg"
IMAGE_SIZE = 900
TEMPLATE_PATH = ROOT / "assets" / "yp_listing_template.png"
TEMPLATE_BAND_TOP = 835


@dataclass
class ProductData:
    url: str
    product_name: str
    manufacturer_part_number: str
    vehicle_name: str
    vehicle_model: str
    vehicle_year: str
    colors: list[str]
    material: str
    description: str
    price_ex_tax: str
    price_in_tax: str
    image_urls: list[str]
    variants: list[tuple[str, str]]


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
    pattern = rf"<dt>\s*{re.escape(label)}\s*</dt>\s*(?:<br\s*/?>\s*)*<dd>\s*(.*?)(?=<dt\b|</dl>)"
    return strip_tags(first_match(pattern, fragment))


def extract_dd_any(labels: list[str], fragment: str) -> str:
    for label in labels:
        value = extract_dd(label, fragment)
        if value:
            return value
    return ""


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


def unique_values(values: list[str]) -> list[str]:
    unique: list[str] = []
    for value in values:
        value = clean_text(value)
        if value and value not in unique:
            unique.append(value)
    return unique


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


def extract_variants(page_html: str) -> list[tuple[str, str]]:
    variants: list[tuple[str, str]] = []
    for label_html in re.findall(r'<span class="variation_label">(.*?)</span>', page_html, re.I | re.S):
        label = strip_tags(label_html)
        match = re.match(r"(.+?)\s+([A-Z0-9][A-Z0-9_-]*[0-9])$", label, re.I)
        if not match:
            continue
        color = clean_text(match.group(1))
        part_number = clean_text(match.group(2))
        if color and part_number and (color, part_number) not in variants:
            variants.append((color, part_number))
    return variants


def extract_color_setting(desc_fragment: str) -> list[str]:
    color_text = extract_dd_any(["カラー設定", "設定カラー", "素材/カラー", "カラー"], desc_fragment)
    return split_colors(color_text)


def split_vehicle_lines(vehicle_text: str) -> tuple[str, str, str]:
    raw_lines = [line.strip(" ・\t") for line in vehicle_text.splitlines()]
    lines = [line for line in raw_lines if line]
    vehicle_names: list[str] = []
    vehicle_models: list[str] = []
    vehicle_years: list[str] = []
    for line in lines:
        bracketed_vehicles = re.findall(r"([^/［\[]+?)\s*[［\[]([^］\]]+)[］\]]", line)
        if len(bracketed_vehicles) > 1:
            for name, model in bracketed_vehicles:
                vehicle_names.append(clean_text(name))
                vehicle_models.append(clean_text(model))
                vehicle_years.append("")
            inferred_year = infer_vehicle_year(line)
            if inferred_year:
                vehicle_years.append(inferred_year)
            continue

        name = line
        model = ""
        year = ""
        match = re.match(r"(.+?)\s*[［\[]([^］\]]+)[］\]]\s*(.*)", line)
        if match:
            name = clean_text(match.group(1))
            model = clean_text(match.group(2))
            year = infer_vehicle_year(match.group(3))
        else:
            inferred_model = infer_vehicle_model(line)
            inferred_year = infer_vehicle_year(line)
            if inferred_model:
                model = inferred_model
                name = clean_text(line.replace(inferred_model, ""))
            if inferred_year:
                year = inferred_year
                name = clean_text(name.replace(inferred_year, ""))
        vehicle_names.append(name)
        vehicle_models.append(model)
        vehicle_years.append(year)
    return (
        " / ".join(unique_values(vehicle_names)),
        " / ".join(unique_values(vehicle_models)),
        " / ".join(unique_values(vehicle_years)),
    )


def extract_sales_description(desc_fragment: str) -> str:
    match = re.search(r"(<h4>\s*セット内容\s*</h4>.*)", desc_fragment, re.I | re.S)
    if match:
        return strip_tags(match.group(1))
    return strip_tags(desc_fragment)


def normalize_price(value: str) -> str:
    return re.sub(r"\D", "", value)


def clean_product_name(title_name: str, basic_name: str) -> str:
    if basic_name:
        return clean_text(basic_name)
    return clean_text(re.sub(r"\s*【.*?】\s*$", "", title_name))


def infer_vehicle_model(vehicle_text: str) -> str:
    text = clean_text(vehicle_text)
    for pattern in (
        r"([0-9０-９]+\s*系)",
        r"([A-Z]{1,5}[0-9]{1,4}[A-Z]?(?:/[A-Z]{1,5}[0-9]{1,4}[A-Z]?)*系?)",
    ):
        match = re.search(pattern, text, re.I)
        if match:
            return clean_text(match.group(1))
    return ""


def infer_vehicle_year(text: str) -> str:
    text = clean_text(text)
    patterns = [
        r"((?:平成|令和|H|R)\s*[0-9０-９]+年?\s*[0-9０-９]*月?\s*(?:以降|～|〜|-|－|から)?\s*(?:(?:平成|令和|H|R)\s*[0-9０-９]+年?\s*[0-9０-９]*月?)?)",
        r"((?:19|20)[0-9]{2}年\s*[0-9０-９]*月?\s*(?:以降|～|〜|-|－|から)?\s*(?:(?:19|20)[0-9]{2}年\s*[0-9０-９]*月?)?)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return clean_text(match.group(1))
    return ""


def parse_lanbo_product(url: str, page_html: str) -> ProductData:
    desc_fragment = first_match(
        r'<div class="item_desc_text custom_desc">\s*(.*?)\s*</div>\s*</div>\s*</div>',
        page_html,
    )
    description = extract_sales_description(desc_fragment)

    product_name = first_match(r'<span class="title_text goods_name">(.*?)</span>', page_html)
    if not product_name:
        product_name = first_match(r'<span class="goods_name">(.*?)</span>', page_html)

    part_number = first_match(r'<span class="model_number_value">(.*?)</span>', page_html)
    vehicle_name = extract_dd("対応車種", desc_fragment)
    vehicle_model = extract_dd_any(["型式", "車輌型式", "車両型式", "対応型式"], desc_fragment)
    vehicle_year = extract_dd_any(["年式", "対応年式"], desc_fragment)
    parsed_vehicle_name, parsed_vehicle_model, parsed_vehicle_year = split_vehicle_lines(vehicle_name)
    if parsed_vehicle_name:
        vehicle_name = parsed_vehicle_name
    if parsed_vehicle_model:
        vehicle_model = parsed_vehicle_model
    if parsed_vehicle_year:
        vehicle_year = parsed_vehicle_year
    material_color = extract_dd_any(["素材/カラー", "カラー設定", "設定カラー", "カラー"], desc_fragment)
    basic_product_name = extract_dd("商品名", desc_fragment)
    product_name = clean_product_name(product_name, basic_product_name)

    variants = extract_variants(page_html)
    colors = [color for color, _part_number in variants] or extract_configured_colors(page_html) or extract_color_setting(desc_fragment) or split_colors(material_color)
    if not vehicle_model:
        vehicle_model = infer_vehicle_model(vehicle_name)
    if not vehicle_year:
        vehicle_year = infer_vehicle_year(description)
    material = material_color
    price_ex_tax = first_match(r'id="pricech">\s*([0-9,]+)\s*<span[^>]*>円</span>', page_html)
    price_in_tax = first_match(
        r'id="tax_included_price" class="figure">\s*([0-9,]+)\s*<span[^>]*>円</span>',
        page_html,
    )
    price_ex_tax = normalize_price(price_ex_tax)
    price_in_tax = normalize_price(price_in_tax)

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
        vehicle_model=vehicle_model,
        vehicle_year=vehicle_year,
        colors=colors,
        material=material,
        description=description,
        price_ex_tax=price_ex_tax,
        price_in_tax=price_in_tax,
        image_urls=image_urls,
        variants=variants,
    )


def load_listing_template() -> Image.Image | None:
    if not TEMPLATE_PATH.exists():
        return None
    with Image.open(TEMPLATE_PATH) as template:
        image = template.convert("RGB")
    if image.size != (IMAGE_SIZE, IMAGE_SIZE):
        image = image.resize((IMAGE_SIZE, IMAGE_SIZE), Image.Resampling.LANCZOS)
    return image


def sample_background_color(image: Image.Image, box: tuple[int, int, int, int]) -> tuple[int, int, int]:
    crop = image.crop(box)
    stat = ImageStat.Stat(crop)
    return tuple(int(value) for value in stat.median[:3])


def has_upper_left_maker_logo(image: Image.Image) -> bool:
    width, height = image.size
    crop = image.crop((0, 0, min(width, int(width * 0.34)), min(height, int(height * 0.18))))
    pixels = list(crop.getdata())
    if not pixels:
        return False
    red_pixels = 0
    bright_pixels = 0
    for red, green, blue in pixels:
        if red > 140 and green < 110 and blue < 110:
            red_pixels += 1
        if red > 210 and green > 210 and blue > 210:
            bright_pixels += 1
    total = len(pixels)
    return red_pixels / total > 0.002 and bright_pixels / total > 0.01


def remove_upper_left_logo(image: Image.Image) -> Image.Image:
    image = image.convert("RGB")
    if not has_upper_left_maker_logo(image):
        return image

    width, height = image.size
    logo_w = min(int(width * 0.5), 620)
    logo_h = min(int(height * 0.16), 150)
    sample_left = min(width - 1, logo_w + max(8, width // 40))
    sample_right = min(width, sample_left + max(16, width // 12))
    sample_bottom = min(height, max(12, logo_h // 2))
    fill = sample_background_color(image, (sample_left, 0, sample_right, sample_bottom))
    ImageDraw.Draw(image).rectangle((0, 0, logo_w, logo_h), fill=fill)
    return image


def fit_to_square(input_path: Path, output_path: Path, with_template_band: bool = False) -> None:
    with Image.open(input_path) as image:
        image = image.convert("RGB")
        if with_template_band:
            image = remove_upper_left_logo(image)
            max_height = TEMPLATE_BAND_TOP
            canvas = load_listing_template() or Image.new("RGB", (IMAGE_SIZE, IMAGE_SIZE), "white")
        else:
            max_height = IMAGE_SIZE
            canvas = Image.new("RGB", (IMAGE_SIZE, IMAGE_SIZE), "white")
        image.thumbnail((IMAGE_SIZE, max_height), Image.Resampling.LANCZOS)
        y_space = max_height if with_template_band else IMAGE_SIZE
        canvas.paste(image, ((IMAGE_SIZE - image.width) // 2, (y_space - image.height) // 2))
        canvas.save(output_path)


def remove_background_with_api(input_path: Path, cutout_path: Path) -> bool:
    api_key = os.environ.get("REMOVE_BG_API_KEY", "").strip()
    if not api_key:
        return False

    boundary = f"----product-csv-maker-{uuid.uuid4().hex}"
    image_bytes = input_path.read_bytes()
    filename = input_path.name
    parts = [
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="image_file"; filename="{filename}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode("utf-8"),
        image_bytes,
        f"\r\n--{boundary}\r\n".encode("utf-8"),
        b'Content-Disposition: form-data; name="size"\r\n\r\nauto',
        f"\r\n--{boundary}\r\n".encode("utf-8"),
        b'Content-Disposition: form-data; name="type"\r\n\r\nproduct',
        f"\r\n--{boundary}\r\n".encode("utf-8"),
        b'Content-Disposition: form-data; name="format"\r\n\r\npng',
        f"\r\n--{boundary}--\r\n".encode("utf-8"),
    ]
    body = b"".join(parts)
    req = urllib.request.Request(
        REMOVE_BG_ENDPOINT,
        data=body,
        headers={
            "X-Api-Key": api_key,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        if resp.status != 200:
            return False
        cutout_path.write_bytes(resp.read())
    return True


def has_meaningful_transparency(image: Image.Image) -> bool:
    if image.mode != "RGBA":
        return False
    alpha = image.getchannel("A")
    extrema = alpha.getextrema()
    if extrema == (255, 255):
        return False
    bbox = alpha.getbbox()
    if not bbox:
        return False
    width, height = image.size
    left, top, right, bottom = bbox
    foreground_area = (right - left) * (bottom - top)
    return foreground_area < width * height * 0.92


def remove_background_to_square(input_path: Path, output_path: Path) -> bool:
    cutout_path = output_path.with_name(output_path.stem + "_cutout.png")
    try:
        if not remove_background_with_api(input_path, cutout_path):
            fit_to_square(input_path, output_path)
            return False

        with Image.open(cutout_path) as cutout:
            cutout_image = cutout.convert("RGBA")
        if not has_meaningful_transparency(cutout_image):
            fit_to_square(input_path, output_path)
            return False

        bbox = cutout_image.getbbox()
        if bbox:
            cutout_image = cutout_image.crop(bbox)
        cutout_image.thumbnail((860, 860), Image.Resampling.LANCZOS)

        canvas = Image.new("RGBA", (IMAGE_SIZE, IMAGE_SIZE), "white")
        canvas.alpha_composite(
            cutout_image,
            ((IMAGE_SIZE - cutout_image.width) // 2, (IMAGE_SIZE - cutout_image.height) // 2),
        )
        canvas.convert("RGB").save(output_path)
        return True
    except Exception:
        traceback.print_exc()
        fit_to_square(input_path, output_path)
        return False
    finally:
        if cutout_path.exists():
            cutout_path.unlink()


def safe_slug(value: str, fallback: str = "product") -> str:
    slug = re.sub(r"[^0-9A-Za-z_-]+", "_", value).strip("_")
    return slug[:80] or fallback


def write_csv(product: ProductData, image_names: list[str], run_dir: Path) -> Path:
    csv_path = run_dir / "product.csv"
    fieldnames = [
        "元URL",
        "車種名",
        "車種型番",
        "車種年式",
        "ブランク",
        "商品名",
        "カラー",
        "メーカー品番",
        "税込み価格",
        "商品説明文",
    ]
    variants = product.variants or [(color, product.manufacturer_part_number) for color in (product.colors or [""])]
    rows = [
        {
            "元URL": product.url,
            "車種名": product.vehicle_name,
            "車種型番": product.vehicle_model,
            "車種年式": product.vehicle_year,
            "ブランク": "",
            "商品名": product.product_name,
            "カラー": color,
            "メーカー品番": part_number,
            "税込み価格": product.price_in_tax,
            "商品説明文": product.description,
        }
        for color, part_number in variants
    ]

    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return csv_path


def make_zip(run_dir: Path) -> Path:
    zip_path = run_dir.with_suffix(".zip")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(run_dir.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(run_dir.parent))
    return zip_path


def process_product(url: str, image_rename_code: str = "") -> tuple[ProductData, Path, Path, bool]:
    raw = fetch_bytes(url)
    page_html = decode_html(raw)
    product = parse_lanbo_product(url, page_html)
    if not product.image_urls:
        raise ValueError("商品画像URLを抽出できませんでした。LANBOの商品ページURLか確認してください。")

    product_id = first_match(r"/product/([0-9]+)", urllib.parse.urlparse(url).path, flags=0)
    image_name_base = safe_slug(image_rename_code or product.manufacturer_part_number or product_id, "image")
    run_name = f"{image_name_base}_{uuid.uuid4().hex[:8]}"
    run_dir = OUTPUT_ROOT / run_name
    source_dir = run_dir / "source_images"
    image_dir = run_dir / "images"
    source_dir.mkdir(parents=True, exist_ok=True)
    image_dir.mkdir(parents=True, exist_ok=True)

    image_names: list[str] = []
    auto_white_background = False
    for url_index, image_url in enumerate(product.image_urls, 1):
        ext = Path(urllib.parse.urlparse(image_url).path).suffix.lower() or ".jpg"
        source_path = source_dir / f"image_{url_index:02d}_original{ext}"
        source_path.write_bytes(fetch_bytes(image_url))
        suffix = "" if url_index == 1 else f"_{url_index - 1}"
        output_name = f"{image_name_base}{suffix}.png"
        if url_index == 1:
            auto_white_background = remove_background_to_square(source_path, image_dir / output_name)
        else:
            fit_to_square(source_path, image_dir / output_name, with_template_band=True)
        image_names.append(output_name)

    write_csv(product, image_names, run_dir)
    zip_path = make_zip(run_dir)
    return product, run_dir, zip_path, auto_white_background


def parse_multipart(body: bytes, content_type: str) -> tuple[str, str]:
    match = re.search(r"boundary=(.+)", content_type)
    if not match:
        raise ValueError("フォームデータのboundaryが見つかりません。")
    boundary = ("--" + match.group(1).strip().strip('"')).encode()
    url = ""
    image_rename_code = ""
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
        elif name == "image_rename_code":
            image_rename_code = data.decode("utf-8", errors="replace").strip()
    return url, image_rename_code


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
    input[type="url"], input[type="text"] {{
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
        <p class="sub">LANBOの商品URLから、CSVと900×900の商品画像入りZIPを生成します。</p>
      </div>
    </header>

    <form method="post" action="/process" enctype="multipart/form-data">
      <div class="field">
        <label for="url">商品URL</label>
        <input id="url" name="url" type="url" required placeholder="https://www.lanbo.co.jp/product/863">
      </div>
      <div class="field">
        <label for="image_rename_code">画像リネーム用 品番</label>
        <input id="image_rename_code" name="image_rename_code" type="text" placeholder="例: BED07-BR">
        <p class="hint">入力すると画像名を「品番.png」「品番_1.png」「品番_2.png」の形式にします。未入力の場合はメーカー品番を使います。</p>
      </div>
      <button type="submit">CSVと画像ZIPを作成</button>
    </form>

    {result}
    {error}

    <section class="grid" aria-label="処理内容">
      <div class="metric"><strong>抽出項目</strong><span>元URL、車種名、車種型番、車種年式、商品名、カラー、メーカー品番、税込み価格、商品説明文。</span></div>
      <div class="metric"><strong>画像処理</strong><span>1枚目は自動白抜き、2枚目以降は下帯付きの900×900 PNGに変換。元画像はsource_imagesに保存。</span></div>
      <div class="metric"><strong>出力</strong><span>product.csv、imagesフォルダー、source_imagesフォルダーをZIPでダウンロード。</span></div>
    </section>
  </main>
</body>
</html>"""
    return body.encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    def do_HEAD(self) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()

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
            url, image_rename_code = parse_multipart(body, self.headers.get("Content-Type", ""))
            if not url.startswith("https://www.lanbo.co.jp/product/"):
                raise ValueError("現在はLANBOの商品ページURLのみ対応しています。")
            product, run_dir, zip_path, auto_white_background = process_product(url, image_rename_code)
            link = f"/download/{urllib.parse.quote(zip_path.name)}"
            white_status = "自動白抜き済み" if auto_white_background else "通常変換"
            result = (
                '<div class="result">'
                f"作成完了\n"
                f"商品名: {html.escape(product.product_name)}\n"
                f"メーカー品番: {html.escape(product.manufacturer_part_number)}\n"
                f"カラー: {html.escape(' / '.join(product.colors))}\n"
                f"画像枚数: {len(product.image_urls)}\n"
                f"画像品番: {html.escape(image_rename_code or product.manufacturer_part_number)}\n"
                f"1枚目: {html.escape(white_status)}\n"
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
