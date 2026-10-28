"""CLI: fetch PubMed XML once, then parse/re-parse it offline."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

from . import __version__
from .ncbi import NcbiClient
from .parse import AFFILIATION_FIELDS, AUTHOR_FIELDS, PUBLICATION_FIELDS, parse_xml

DEFAULT_QUERY_FILE = Path("config/query.txt")
DEFAULT_RUNS_DIR = Path("data/runs")


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build_query(base_query: str, start_year: int, end_year: int) -> str:
    if not base_query.strip():
        raise ValueError("The query file is empty")
    if start_year > end_year or start_year < 1800:
        raise ValueError("Invalid publication-year range")
    return (
        f"({base_query.strip()}) AND "
        f'(\"{start_year}/01/01\"[dp] : \"{end_year}/12/31\"[dp])'
    )


def new_run_dir() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return DEFAULT_RUNS_DIR / stamp


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_csv(path: Path, fields: tuple[str, ...], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def fetch(args: argparse.Namespace) -> Path:
    if not 1 <= args.max_results <= 10000:
        raise ValueError("--max-results must be between 1 and 10000 for this experiment")
    if not 1 <= args.batch_size <= 500:
        raise ValueError("--batch-size must be between 1 and 500")
    if args.start_year > args.end_year:
        raise ValueError("--start-year cannot exceed --end-year")
    query_file = Path(args.query_file)
    base_query = query_file.read_text(encoding="utf-8")
    query = build_query(base_query, args.start_year, args.end_year)
    run_dir = Path(args.run_dir) if args.run_dir else new_run_dir()
    if run_dir.exists():
        raise FileExistsError(f"Run directory already exists: {run_dir}; choose a new --run-dir")
    email = args.email or os.environ.get("NCBI_EMAIL", "")
    api_key = os.environ.get("NCBI_API_KEY", "")
    client = NcbiClient(email=email, api_key=api_key)

    # Search before creating any on-disk output; no data is written on a failed search.
    results = client.search(query)
    matched, requested = results["count"], min(results["count"], args.max_results)
    raw_dir = run_dir / "raw"
    raw_dir.mkdir(parents=True)
    manifest = {
        "experiment": "pubmed_phase_a",
        "project_version": __version__,
        "fetched_at_utc": now_utc(),
        "source": "NCBI PubMed ESearch + EFetch",
        "query_file": str(query_file),
        "query": query,
        "pubmed_query_translation": results["translation"],
        "start_year": args.start_year,
        "end_year": args.end_year,
        "sort": "pub_date",
        "total_pubmed_matches": matched,
        "max_results": args.max_results,
        "expected_records": requested,
        "batch_size": args.batch_size,
        "batches": [],
    }
    for start in range(0, requested, args.batch_size):
        size = min(args.batch_size, requested - start)
        payload = client.fetch(results["webenv"], results["query_key"], start, size)
        # Reject HTTP 200 error pages / invalid EFetch XML before saving them as raw data.
        parse_xml(payload)
        filename = f"batch_{start // args.batch_size + 1:04d}.xml"
        (raw_dir / filename).write_bytes(payload)
        manifest["batches"].append({
            "file": f"raw/{filename}", "retstart": start, "retmax": size,
            "sha256": hashlib.sha256(payload).hexdigest(),
        })
    write_json(run_dir / "manifest.json", manifest)
    return run_dir


def extract(run_dir: Path) -> dict:
    manifest_file = run_dir / "manifest.json"
    if not manifest_file.is_file():
        raise FileNotFoundError(f"Missing {manifest_file}; run 'fetch' first")
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    publications: list[dict] = []
    authors: list[dict] = []
    affiliations: list[dict] = []
    skipped_books = 0
    for batch in manifest["batches"]:
        xml_path = run_dir / batch["file"]
        payload = xml_path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != batch["sha256"]:
            raise ValueError(f"Raw XML integrity check failed: {xml_path}")
        batch_pubs, batch_authors, batch_affs, batch_books = parse_xml(payload)
        publications.extend(batch_pubs)
        authors.extend(batch_authors)
        affiliations.extend(batch_affs)
        skipped_books += batch_books
    # Guard against accidentally including duplicates across batches.
    pmids = [row["pmid"] for row in publications]
    if len(pmids) != len(set(pmids)):
        raise ValueError("Duplicate PMIDs across downloaded batches")
    processed = run_dir / "processed"
    processed.mkdir(exist_ok=True)
    write_csv(processed / "publications.csv", PUBLICATION_FIELDS, publications)
    write_csv(processed / "authors.csv", AUTHOR_FIELDS, authors)
    write_csv(processed / "affiliations.csv", AFFILIATION_FIELDS, affiliations)
    authors_with_affiliation = sum(bool(a["affiliation_count"]) for a in authors)
    summary = {
        "extracted_at_utc": now_utc(),
        "expected_records": manifest["expected_records"],
        "publications": len(publications),
        "authors": len(authors),
        "affiliations": len(affiliations),
        "authors_with_affiliation": authors_with_affiliation,
        "author_affiliation_coverage_pct": round(100 * authors_with_affiliation / len(authors), 2) if authors else 0,
        "skipped_book_records": skipped_books,
        "record_count_matches_search": len(publications) + skipped_books == manifest["expected_records"],
    }
    write_json(processed / "summary.json", summary)
    return summary


def parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pubmed-phase-a", description="PubMed Phase A: fetch raw article XML and extract normalized CSVs."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("fetch", "run"):
        cmd = sub.add_parser(name, help="Fetch raw XML" + (" and extract CSVs" if name == "run" else ""))
        cmd.add_argument("--query-file", default=str(DEFAULT_QUERY_FILE))
        cmd.add_argument("--start-year", type=int, default=2015)
        cmd.add_argument("--end-year", type=int, default=datetime.now(timezone.utc).year)
        cmd.add_argument("--max-results", type=int, default=200)
        cmd.add_argument("--batch-size", type=int, default=100)
        cmd.add_argument("--email", help="Contact email (alternative: NCBI_EMAIL env variable)")
        cmd.add_argument("--run-dir", help="New, non-existing run directory (default: timestamped data/runs/...)")
    cmd = sub.add_parser("extract", help="Re-extract CSVs from saved XML without contacting PubMed")
    cmd.add_argument("--run-dir", required=True, help="Existing run directory containing manifest.json")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "extract":
            run_dir = Path(args.run_dir)
            summary = extract(run_dir)
        else:
            run_dir = fetch(args)
            summary = extract(run_dir) if args.command == "run" else None
        print(f"Run directory: {run_dir}")
        if summary is not None:
            print(json.dumps(summary, indent=2))
        else:
            print("Raw XML saved. Run 'pubmed-phase-a extract --run-dir <path>' to produce CSVs.")
    except (OSError, ValueError, RuntimeError, KeyError, ET.ParseError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0
