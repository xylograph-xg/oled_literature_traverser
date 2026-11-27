from __future__ import annotations

import csv
import hashlib
import json
import re
import sys
import time
from collections import deque
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

BASE = Path(__file__).resolve().parent
INPUT = BASE / "input" / "seed_doi.csv"
CONFIG_PATH = BASE / "config.json"
PDF_DIR = BASE / "papers" / "pdf"
SUPP_DIR = BASE / "papers" / "supplements"
DATA_DIR = BASE / "data"
LOG_DIR = BASE / "logs"

VISITED_CSV = DATA_DIR / "visited.csv"
DISCOVERED_CSV = DATA_DIR / "discovered.csv"
INSTITUTION_QUEUE = DATA_DIR / "institution_queue.jsonl"
FAILED_QUEUE = DATA_DIR / "failed.jsonl"
LOG_FILE = LOG_DIR / "run.log"

for p in [PDF_DIR, SUPP_DIR, DATA_DIR, LOG_DIR]:
    p.mkdir(parents=True, exist_ok=True)

HEADERS = {
    "User-Agent": "OLED-Literature-Traverser/0.2 (personal academic research workflow)"
}

def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(line + "\n")

def load_config() -> dict[str, Any]:
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)

CFG = load_config()
MAX_DEPTH = int(CFG.get("max_depth", 3))
MAX_PAPERS = int(CFG.get("max_papers", 100))
MAX_RELATED = int(CFG.get("max_related_per_paper", 5))
TIMEOUT = int(CFG.get("request_timeout_seconds", 30))
SLEEP = float(CFG.get("sleep_between_requests_seconds", 0.8))
DOWNLOAD_OA = bool(CFG.get("download_open_access_pdf", True))
TRY_SUPP = bool(CFG.get("try_public_supplement_links", True))

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

def polite_get(url: str, **kwargs):
    time.sleep(SLEEP)
    return SESSION.get(url, timeout=TIMEOUT, allow_redirects=True, **kwargs)

def normalize_doi(value: str | None) -> str | None:
    if not value:
        return None
    s = value.strip()
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s, flags=re.I)
    s = re.sub(r"^doi:\s*", "", s, flags=re.I)
    return s.strip().lower() or None

def doi_url(doi: str) -> str:
    return "https://doi.org/" + doi

def safe_name(text: str, max_len: int = 150) -> str:
    text = re.sub(r'[\\/:*?"<>|]+', "_", text)
    text = re.sub(r"\s+", " ", text).strip(" ._")
    if len(text) > max_len:
        digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]
        text = text[: max_len - 9].rstrip() + "_" + digest
    return text or "paper"

def read_seed_dois() -> list[str]:
    if not INPUT.exists():
        raise FileNotFoundError(f"找不到种子 DOI 表：{INPUT}")
    out = []
    with INPUT.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            doi = normalize_doi(row.get("doi"))
            if doi:
                out.append(doi)
    return out

def openalex_by_doi(doi: str) -> dict[str, Any]:
    url = f"https://api.openalex.org/works/https://doi.org/{quote(doi, safe='')}"
    r = polite_get(url)
    if r.status_code == 404:
        raise RuntimeError("OpenAlex 中未找到 DOI")
    r.raise_for_status()
    return r.json()

def openalex_by_id(openalex_id: str) -> dict[str, Any] | None:
    r = polite_get(openalex_id)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()

def crossref_by_doi(doi: str) -> dict[str, Any] | None:
    url = f"https://api.crossref.org/works/{quote(doi, safe='')}"
    try:
        r = polite_get(url)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return (r.json() or {}).get("message")
    except Exception:
        return None

def metadata_from_openalex(work: dict[str, Any], doi: str) -> dict[str, Any]:
    primary = work.get("primary_location") or {}
    source = primary.get("source") or {}
    return {
        "doi": doi,
        "openalex_id": work.get("id"),
        "title": work.get("title") or "",
        "year": work.get("publication_year") or "",
        "type": work.get("type") or "",
        "journal": source.get("display_name") or "",
        "landing_page_url": primary.get("landing_page_url") or doi_url(doi),
        "is_oa": (work.get("open_access") or {}).get("is_oa", False),
        "cited_by_count": work.get("cited_by_count", 0),
    }

def oa_pdf_candidates(work: dict[str, Any]) -> list[str]:
    candidates = []
    for key in ("best_oa_location", "primary_location"):
        loc = work.get(key) or {}
        if loc.get("pdf_url"):
            candidates.append(loc["pdf_url"])
    for loc in work.get("locations") or []:
        if loc and loc.get("pdf_url"):
            candidates.append(loc["pdf_url"])
    seen, out = set(), []
    for u in candidates:
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out

def download_pdf(url: str, out: Path) -> bool:
    try:
        r = polite_get(url, stream=True)
        r.raise_for_status()
        ctype = (r.headers.get("Content-Type") or "").lower()
        first = b""
        with out.open("wb") as f:
            for i, chunk in enumerate(r.iter_content(256 * 1024)):
                if not chunk:
                    continue
                if i == 0:
                    first = chunk[:8]
                f.write(chunk)
        if "pdf" not in ctype and not first.startswith(b"%PDF"):
            out.unlink(missing_ok=True)
            return False
        return True
    except Exception:
        out.unlink(missing_ok=True)
        return False

def download_any_public_file(url: str, out: Path) -> bool:
    """Best-effort public supplementary-file download. No authentication bypass."""
    try:
        r = polite_get(url, stream=True)
        r.raise_for_status()
        ctype = (r.headers.get("Content-Type") or "").lower()
        if "text/html" in ctype:
            return False
        with out.open("wb") as f:
            for chunk in r.iter_content(256 * 1024):
                if chunk:
                    f.write(chunk)
        return out.exists() and out.stat().st_size > 0
    except Exception:
        out.unlink(missing_ok=True)
        return False

def extension_from_url(url: str) -> str:
    path = urlparse(url).path
    ext = Path(path).suffix
    if ext and len(ext) <= 8:
        return ext
    return ".bin"

def supplementary_from_crossref(cr: dict[str, Any] | None) -> list[str]:
    """Crossref relation metadata is inconsistent; collect URL-like supplement relations if present."""
    if not cr:
        return []
    rel = cr.get("relation") or {}
    urls = []
    for key, items in rel.items():
        if "supp" not in key.lower():
            continue
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            val = item.get("id") or item.get("url")
            if isinstance(val, str) and val.startswith("http"):
                urls.append(val)
    return urls

def supplementary_from_public_landing_page(url: str) -> list[str]:
    """
    Fetch only a normally accessible public landing page and find explicit
    supplementary/supporting-information links. Does not log in, solve CAPTCHA,
    alter cookies, or bypass access controls.
    """
    if not url or not TRY_SUPP:
        return []
    try:
        r = polite_get(url)
        if r.status_code != 200:
            return []
        ctype = (r.headers.get("Content-Type") or "").lower()
        if "html" not in ctype:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        keys = ("supplement", "supporting information", "supporting-info", "supp info", "supp.")
        out, seen = [], set()
        for a in soup.find_all("a", href=True):
            text = " ".join(a.stripped_strings).lower()
            href = a.get("href", "")
            hay = (text + " " + href.lower())
            if not any(k in hay for k in keys):
                continue
            abs_url = urljoin(r.url, href)
            if abs_url.startswith("http") and abs_url not in seen:
                seen.add(abs_url)
                out.append(abs_url)
        return out[:10]
    except Exception:
        return []

def related_dois(work: dict[str, Any]) -> list[tuple[str, str]]:
    out = []
    for openalex_id in (work.get("related_works") or [])[: max(MAX_RELATED * 3, 10)]:
        try:
            rw = openalex_by_id(openalex_id)
            if not rw:
                continue
            doi = normalize_doi(rw.get("doi"))
            if doi:
                out.append((doi, openalex_id))
                if len(out) >= MAX_RELATED:
                    break
        except Exception:
            continue
    return out

def append_jsonl(path: Path, obj: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")

def append_csv(path: Path, row: dict[str, Any], fields: list[str]) -> None:
    exists = path.exists()
    with path.open("a", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if not exists:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in fields})

VISITED_FIELDS = [
    "doi", "title", "year", "journal", "depth", "parent_doi",
    "is_oa", "pdf_status", "supplement_count", "related_count",
    "cited_by_count", "landing_page_url", "openalex_id"
]
DISCOVERED_FIELDS = [
    "doi", "parent_doi", "depth", "source"
]

def process_one(doi: str, depth: int, parent_doi: str | None) -> list[str]:
    log(f"处理 depth={depth}: {doi}")
    try:
        work = openalex_by_doi(doi)
    except Exception as e:
        append_jsonl(FAILED_QUEUE, {
            "doi": doi, "depth": depth, "parent_doi": parent_doi,
            "stage": "openalex", "error": str(e)
        })
        log(f"  失败：{e}")
        return []

    meta = metadata_from_openalex(work, doi)
    title = meta["title"]
    stem = safe_name(f"{meta['year']} - {title}")

    pdf_status = "not_found"
    if DOWNLOAD_OA:
        for pdf_url in oa_pdf_candidates(work):
            out = PDF_DIR / f"{stem}.pdf"
            if out.exists() and out.stat().st_size > 1024:
                pdf_status = "already_exists"
                break
            log(f"  尝试 OA PDF: {pdf_url}")
            if download_pdf(pdf_url, out):
                pdf_status = "downloaded"
                log(f"  PDF 已保存：{out.relative_to(BASE)}")
                break

    if pdf_status == "not_found":
        append_jsonl(INSTITUTION_QUEUE, {
            "doi": doi,
            "title": title,
            "year": meta["year"],
            "landing_page_url": meta["landing_page_url"],
            "status": "institution_or_manual_required"
        })

    # Supplements: Crossref relation + explicit links on public landing page.
    supp_urls = []
    cr = crossref_by_doi(doi)
    supp_urls.extend(supplementary_from_crossref(cr))
    supp_urls.extend(supplementary_from_public_landing_page(meta["landing_page_url"]))
    # dedupe
    supp_urls = list(dict.fromkeys(supp_urls))

    supp_count = 0
    if supp_urls:
        paper_supp_dir = SUPP_DIR / safe_name(doi.replace("/", "_"), 80)
        paper_supp_dir.mkdir(parents=True, exist_ok=True)
        for i, url in enumerate(supp_urls, 1):
            ext = extension_from_url(url)
            out = paper_supp_dir / f"supp_{i:02d}{ext}"
            if out.exists() and out.stat().st_size > 0:
                supp_count += 1
                continue
            log(f"  尝试公开补充材料：{url}")
            if download_any_public_file(url, out):
                supp_count += 1
                log(f"  补充材料已保存：{out.relative_to(BASE)}")

    rels = related_dois(work) if depth < MAX_DEPTH else []
    for rdoi, _ in rels:
        append_csv(DISCOVERED_CSV, {
            "doi": rdoi,
            "parent_doi": doi,
            "depth": depth + 1,
            "source": "OpenAlex related_works"
        }, DISCOVERED_FIELDS)

    row = {
        **meta,
        "depth": depth,
        "parent_doi": parent_doi or "",
        "pdf_status": pdf_status,
        "supplement_count": supp_count,
        "related_count": len(rels),
    }
    append_csv(VISITED_CSV, row, VISITED_FIELDS)
    return [d for d, _ in rels]

def main() -> int:
    seeds = read_seed_dois()
    if not seeds:
        print("seed_doi.csv 中没有 DOI。")
        return 1

    log("=" * 70)
    log(f"启动：seeds={len(seeds)} max_depth={MAX_DEPTH} max_papers={MAX_PAPERS} max_related={MAX_RELATED}")

    q = deque((d, 0, None) for d in seeds)
    seen: set[str] = set()
    processed = 0

    while q and processed < MAX_PAPERS:
        doi, depth, parent = q.popleft()
        doi = normalize_doi(doi)
        if not doi or doi in seen or depth > MAX_DEPTH:
            continue
        seen.add(doi)
        processed += 1

        children = process_one(doi, depth, parent)
        if depth < MAX_DEPTH:
            for child in children:
                child = normalize_doi(child)
                if child and child not in seen:
                    q.append((child, depth + 1, doi))

    log(f"完成：实际处理 {processed} 篇；已见 DOI {len(seen)} 个；队列剩余 {len(q)}")
    log("不会自动绕过 CARSI、CAPTCHA、付费墙或出版社反爬。")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
