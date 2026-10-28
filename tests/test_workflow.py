import csv
import hashlib
import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from pubmed_phase_a.cli import build_query, extract, main
from pubmed_phase_a.ncbi import NcbiClient

FIXTURE = Path(__file__).parent / "fixtures" / "sample_pubmed.xml"


def test_query_includes_bounded_publication_dates():
    query = build_query("psychoanal*[tiab]", 2015, 2026)
    assert '"2015/01/01"[dp]' in query
    assert '"2026/12/31"[dp]' in query


def test_esearch_uses_history_and_correct_sort():
    response = Mock(status_code=200, content=(
        b"<eSearchResult><Count>220</Count><WebEnv>token</WebEnv>"
        b"<QueryKey>1</QueryKey><QueryTranslation>q</QueryTranslation></eSearchResult>"
    ))
    response.raise_for_status.return_value = None
    client = NcbiClient(email="test@example.org")
    with patch.object(client.session, "get", return_value=response) as get:
        meta = client.search("psychoanal*[tiab]")
    assert meta == {"count": 220, "webenv": "token", "query_key": "1", "translation": "q"}
    params = get.call_args.kwargs["params"]
    assert params["usehistory"] == "y"
    assert params["sort"] == "pub_date"
    assert params["retmax"] == 0


def test_mocked_fetch_and_offline_reextract(tmp_path):
    run_dir = tmp_path / "my-run"
    q = tmp_path / "query.txt"
    q.write_text("psychoanal*[tiab]", encoding="utf-8")
    fake_search = {"count": 2, "webenv": "mock-web-env", "query_key": "1", "translation": "q"}
    with patch.object(NcbiClient, "search", return_value=fake_search) as search:
        with patch.object(NcbiClient, "fetch", return_value=FIXTURE.read_bytes()) as fetch:
            status = main(["run", "--email", "test@example.org", "--query-file", str(q),
                           "--run-dir", str(run_dir), "--max-results", "2"])
    assert status == 0
    search.assert_called_once()
    fetch.assert_called_once_with("mock-web-env", "1", 0, 2)
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["total_pubmed_matches"] == 2
    assert manifest["sort"] == "pub_date"
    assert manifest["batches"][0]["sha256"] == hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    with (run_dir / "processed" / "publications.csv").open(encoding="utf-8", newline="") as stream:
        pubs = list(csv.DictReader(stream))
    assert len(pubs) == 2
    summary = extract(run_dir)  # offline, no requests and same results
    assert (summary["publications"], summary["authors"], summary["affiliations"]) == (2, 5, 4)
    assert summary["authors_with_affiliation"] == 3
    assert summary["author_affiliation_coverage_pct"] == 60.0
    assert summary["record_count_matches_search"]


def test_integrity_check_detects_raw_xml_edits(tmp_path):
    run_dir = tmp_path / "run"
    (run_dir / "raw").mkdir(parents=True)
    payload = FIXTURE.read_bytes()
    (run_dir / "raw" / "batch_0001.xml").write_bytes(payload + b"changed")
    (run_dir / "manifest.json").write_text(json.dumps({"batches": [
        {"file": "raw/batch_0001.xml", "sha256": hashlib.sha256(payload).hexdigest()}
    ]}), encoding="utf-8")
    with pytest.raises(ValueError, match="integrity"):
        extract(run_dir)


def test_does_not_overwrite_existing_run_directory(tmp_path):
    query = tmp_path / "query.txt"
    query.write_text("psychoanal*[tiab]", encoding="utf-8")
    existing = tmp_path / "already-exists"
    existing.mkdir()
    with patch.object(NcbiClient, "search", side_effect=AssertionError("should not query PubMed")):
        status = main(["fetch", "--email", "test@example.org", "--query-file", str(query),
                       "--run-dir", str(existing)])
    assert status == 1
