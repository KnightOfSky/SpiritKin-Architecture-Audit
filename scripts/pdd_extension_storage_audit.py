#!/usr/bin/env python3
"""Extract and summarize PDD extension productData from Chromium LevelDB files."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def extract_json_objects(blob: bytes) -> list[dict[str, Any]]:
    markers = [b'{"data":[', b'{"source":"pdd-batch-extractor"']
    objects: list[dict[str, Any]] = []

    for marker in markers:
        start = 0
        while True:
            idx = blob.find(marker, start)
            if idx < 0:
                break

            depth = 0
            in_string = False
            escaped = False
            end = None

            for i in range(idx, len(blob)):
                byte = blob[i]
                if in_string:
                    if escaped:
                        escaped = False
                    elif byte == 92:
                        escaped = True
                    elif byte == 34:
                        in_string = False
                else:
                    if byte == 34:
                        in_string = True
                    elif byte == 123:
                        depth += 1
                    elif byte == 125:
                        depth -= 1
                        if depth == 0:
                            end = i + 1
                            break

            if end:
                try:
                    text = blob[idx:end].decode("utf-8")
                    objects.append(json.loads(text))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    pass

            start = idx + 1

    return objects


def timestamp_of(package: dict[str, Any]) -> float:
    candidates: list[Any] = [package.get("timestamp")]
    data = package.get("data")
    if isinstance(data, list):
        candidates.extend(item.get("timestamp") for item in data if isinstance(item, dict))
    for value in candidates:
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return 0.0


def product_of(package: dict[str, Any]) -> dict[str, Any]:
    data = package.get("data")
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return data[0]
    return package


def goods_id_from_url(url: str | None) -> str | None:
    if not url:
        return None
    try:
        params = parse_qs(urlparse(url).query)
    except ValueError:
        return None
    for key in ("goods_id", "goodsId", "goodsID", "goods_id_str"):
        values = params.get(key)
        if values:
            value = values[0]
            return value if value.isdigit() else None
    return None


def summarize(package: dict[str, Any], source_file: str | None = None) -> dict[str, Any]:
    product = product_of(package)
    sku_info = product.get("skuInfo") if isinstance(product.get("skuInfo"), dict) else {}
    sku_list = sku_info.get("skuList") if isinstance(sku_info, dict) else None
    skus = product.get("skus")
    goods_id = str(product.get("goodsId") or product.get("goods_id") or "")
    url_goods_id = goods_id_from_url(product.get("url"))
    has_real_goods_id = bool(url_goods_id and goods_id == url_goods_id)

    return {
        "sourceFile": source_file,
        "timestamp": package.get("timestamp"),
        "goodsId": goods_id,
        "urlGoodsId": url_goods_id,
        "goodsIdMatchesUrl": has_real_goods_id,
        "title": product.get("title") or product.get("goodsName") or product.get("goods_name"),
        "price": product.get("price"),
        "url": product.get("url"),
        "images": len(product.get("images") or []),
        "mainImages": len(product.get("mainImages") or []),
        "detailImages": len(product.get("detailImages") or []),
        "skuInfoHasValidSku": sku_info.get("hasValidSku") if isinstance(sku_info, dict) else None,
        "skuInfoSkuList": len(sku_list or []) if isinstance(sku_list, list) else None,
        "skus": len(skus or []) if isinstance(skus, list) else None,
        "hasRealGoodsId": has_real_goods_id,
        "listingReady": bool(
            has_real_goods_id
            and product.get("title")
            and len(product.get("images") or []) > 0
            and len(product.get("detailImages") or []) > 0
            and (
                (isinstance(sku_list, list) and len(sku_list) > 0)
                or (isinstance(skus, list) and len(skus) > 0)
            )
        ),
    }


def read_packages(extension_dir: Path) -> list[tuple[str, dict[str, Any]]]:
    packages: list[tuple[str, dict[str, Any]]] = []
    for path in extension_dir.glob("*"):
        if path.is_dir() or path.name == "LOCK":
            continue
        try:
            blob = path.read_bytes()
        except OSError:
            continue
        packages.extend((path.name, obj) for obj in extract_json_objects(blob))

    dedup: dict[str, tuple[str, dict[str, Any]]] = {}
    for source_file, package in packages:
        key = json.dumps(package, ensure_ascii=False, sort_keys=True)
        dedup[key] = (source_file, package)
    return list(dedup.values())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--extension-dir",
        default=os.path.expandvars(
            r"%LOCALAPPDATA%\Microsoft\Edge\User Data\Default\Local Extension Settings\hfnifjcojalkhohnnejkfniemjopakcb"
        ),
        help="Chromium Local Extension Settings directory for PDD Batch Data Extractor.",
    )
    parser.add_argument("--goods-id", help="Prefer a package whose product URL or goodsId contains this id.")
    parser.add_argument("--out-dir", default=r"state\pdd_logged_edge_probe")
    args = parser.parse_args()

    extension_dir = Path(args.extension_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    packages = read_packages(extension_dir)
    packages.sort(key=lambda item: timestamp_of(item[1]), reverse=True)

    if args.goods_id:
        preferred = [
            item
            for item in packages
            if args.goods_id in json.dumps(product_of(item[1]), ensure_ascii=False)
        ]
        if preferred:
            packages = preferred + [item for item in packages if item not in preferred]

    summaries = [summarize(package, source_file) for source_file, package in packages]
    (out_dir / "extension_storage_audit_summary.json").write_text(
        json.dumps(summaries, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if packages:
        source_file, latest = packages[0]
        (out_dir / "extension_latest_package.json").write_text(
            json.dumps(latest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (out_dir / "extension_latest_product.json").write_text(
            json.dumps(product_of(latest), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(json.dumps(summarize(latest, source_file), ensure_ascii=False, indent=2))
        return 0

    print(json.dumps({"error": "no productData package found", "extensionDir": str(extension_dir)}, ensure_ascii=False))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
