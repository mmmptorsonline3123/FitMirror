"""Extract product title, images and a size chart from a product page's HTML (stdlib parser, no JS execution)."""
import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin
from pydantic import ValidationError
from .sizing import SizeChart

MEASURE_WORDS = [("inseam", ("inseam", "inside leg", "leg length")), ("shoulder_width", ("shoulder",)),
                 ("chest", ("chest", "bust")), ("waist", ("waist",)), ("hip", ("hip",))]
NUM = re.compile(r"\d+(?:[.,]\d+)?")


@dataclass
class ProductPage:
    title: str | None = None
    images: list[str] = field(default_factory=list)  # absolute URLs, best first
    size_chart: SizeChart | None = None


class _Collector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.title = ""
        self.ld: list[str] = []
        self.tables: list[list[list[str]]] = []
        self._in = None  # "title" | "ld"
        self._buf = ""
        self._stack: list[list[list[str]]] = []  # nested tables
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag == "meta":
            key = (a.get("property") or a.get("name") or "").lower()
            if key and "content" in a:
                self.meta.setdefault(key, a["content"])
        elif tag == "title":
            self._in, self._buf = "title", ""
        elif tag == "script" and a.get("type", "").lower() == "application/ld+json":
            self._in, self._buf = "ld", ""
        elif tag == "table":
            self._stack.append([])
        elif tag == "tr" and self._stack:
            self._stack[-1].append([])
        elif tag in ("td", "th") and self._stack:
            self._cell = []

    def handle_endtag(self, tag):
        if tag == "title" and self._in == "title":
            self.title, self._in = self._buf.strip(), None
        elif tag == "script" and self._in == "ld":
            self.ld.append(self._buf)
            self._in = None
        elif tag in ("td", "th") and self._cell is not None and self._stack and self._stack[-1]:
            self._stack[-1][-1].append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "table" and self._stack:
            self.tables.append([r for r in self._stack.pop() if r])

    def handle_data(self, data):
        if self._in:
            self._buf += data
        elif self._cell is not None:
            self._cell.append(data)


def _walk(o):
    if isinstance(o, dict):
        yield o
        for v in o.values():
            yield from _walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from _walk(v)


def _ld_products(blobs: list[str]):
    for blob in blobs:
        try:
            data = json.loads(blob)
        except ValueError:
            continue
        for o in _walk(data):
            t = o.get("@type")
            if t == "Product" or (isinstance(t, list) and "Product" in t):
                yield o


def _image_urls(v) -> list[str]:
    if isinstance(v, str):
        return [v]
    if isinstance(v, dict):
        return [u for u in (v.get("url") or v.get("contentUrl"),) if isinstance(u, str)]
    if isinstance(v, list):
        return [u for x in v for u in _image_urls(x)]
    return []


def _measure(header: str) -> str | None:
    h = header.lower()
    return next((m for m, words in MEASURE_WORDS if any(w in h for w in words)), None)


def _unit(text: str) -> str | None:
    t = text.lower()
    if re.search(r"\bcm\b|centimet", t):
        return "cm"
    if re.search(r"\binch|\bin\b|\(in\)|\"", t):
        return "in"
    return None


def _value(cell: str):
    nums = [float(n.replace(",", ".")) for n in NUM.findall(cell)]
    return (min(nums), max(nums)) if len(nums) == 2 else nums[0] if len(nums) == 1 else None


def _chart_from_grid(grid: list[list[str]], table_text: str) -> SizeChart | None:
    """grid[0] = header row: first cell = size column, others = measures (possibly with units)."""
    if len(grid) < 3 or len(grid[0]) < 2:
        return None
    cols: dict[str, tuple[int, str | None]] = {}
    for i, h in enumerate(grid[0][1:], start=1):
        m = _measure(h)
        if m and (m not in cols or (_unit(h) == "cm" and cols[m][1] != "cm")):  # prefer the cm column
            cols[m] = (i, _unit(h))
    if not cols:
        return None
    rows = [(r[0].strip()[:12], {m: _value(r[i]) for m, (i, _) in cols.items() if i < len(r)}) for r in grid[1:] if r]
    default_unit = _unit(table_text)
    if not default_unit:  # no unit anywhere: adult chest/waist/hip girths are > 60 in cm and < 60 in inches
        girths = [v if isinstance(v, float) else v[1] for _, vals in rows for m, v in vals.items() if v and m in ("chest", "waist", "hip")]
        default_unit = "in" if girths and max(girths) < 60 else "cm"
    sizes = []
    for label, vals in rows:
        ranges = {}
        for m, v in vals.items():
            if v is None:
                continue
            f = 2.54 if (cols[m][1] or default_unit) == "in" else 1.0
            ranges[m] = v * f if isinstance(v, float) else [v[0] * f, v[1] * f]  # a point is read as +-2 cm by SizeChart
        if label and ranges:
            sizes.append({"label": label, "ranges": ranges})
    if len(sizes) < 2:
        return None
    try:
        return SizeChart.model_validate({"unit": "cm", "sizes": sizes})
    except ValidationError:
        return None


def find_size_chart(tables: list[list[list[str]]]) -> SizeChart | None:
    for t in tables:
        text = " ".join(" ".join(r) for r in t)
        if not any(_measure(c) for r in t for c in r):
            continue
        width = max(len(r) for r in t)
        t = [r + [""] * (width - len(r)) for r in t]
        transposed = [list(c) for c in zip(*t)]
        # Measures across the top (sizes down the side) or measures down the side (sizes across the top).
        for grid in (t, transposed):
            if chart := _chart_from_grid(grid, text):
                return chart
    return None


def parse_product_page(html: str, base_url: str) -> ProductPage:
    c = _Collector()
    c.feed(html)
    c.close()
    images, title = [], None
    for p in _ld_products(c.ld):
        images += _image_urls(p.get("image"))
        title = title or (p.get("name") if isinstance(p.get("name"), str) else None)
    images += [c.meta[k] for k in ("og:image", "og:image:url", "og:image:secure_url", "twitter:image") if c.meta.get(k)]
    seen, absolute = set(), []
    for u in images:
        full = urljoin(base_url, u.strip())
        if full.startswith(("http://", "https://")) and full not in seen:
            seen.add(full)
            absolute.append(full)
    title = title or c.meta.get("og:title") or c.title or None
    return ProductPage(title=(title or "")[:200] or None, images=absolute[:8], size_chart=find_size_chart(c.tables))
