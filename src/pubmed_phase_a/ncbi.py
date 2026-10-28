"""Minimal and rate-limited NCBI E-utilities client (ESearch + EFetch)."""
from __future__ import annotations

import time
import xml.etree.ElementTree as ET

import requests

BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
TOOL = "PubMedPhaseAExperiment"
TRANSIENT_CODES = {429, 500, 502, 503, 504}


def text_at(node: ET.Element, path: str) -> str:
    child = node.find(path)
    return "" if child is None else " ".join("".join(child.itertext()).split())


class NcbiClient:
    """A small client, with conservative request pacing and transient-error retries."""

    def __init__(self, email: str, api_key: str | None = None):
        if not email or "@" not in email:
            raise ValueError("Set NCBI_EMAIL to a contact email address before fetching.")
        self.email = email
        self.api_key = api_key
        self.session = requests.Session()
        self.minimum_interval = 0.4 if not api_key else 0.12
        self.last_request = 0.0

    def _get_xml(self, endpoint: str, params: dict) -> bytes:
        request_params = dict(params)
        request_params.update({"tool": TOOL, "email": self.email})
        if self.api_key:
            request_params["api_key"] = self.api_key

        for attempt in range(4):
            wait = self.minimum_interval - (time.monotonic() - self.last_request)
            if wait > 0:
                time.sleep(wait)
            self.last_request = time.monotonic()
            try:
                response = self.session.get(
                    f"{BASE_URL}/{endpoint}",
                    params=request_params,
                    headers={"User-Agent": f"{TOOL}/0.1 ({self.email})"},
                    timeout=45,
                )
                if response.status_code in TRANSIENT_CODES:
                    if attempt == 3:
                        response.raise_for_status()
                    retry_after = response.headers.get("Retry-After", "")
                    try:
                        delay = max(float(retry_after), 2**attempt)
                    except ValueError:
                        delay = 2**attempt
                    time.sleep(min(delay, 30))
                    continue
                response.raise_for_status()
                if not response.content:
                    raise RuntimeError("NCBI returned an empty XML response")
                return response.content
            except (requests.Timeout, requests.ConnectionError) as exc:
                if attempt == 3:
                    raise RuntimeError("NCBI request failed after four attempts") from exc
                time.sleep(2**attempt)
        raise RuntimeError("NCBI request failed")

    def search(self, query: str) -> dict:
        """Return count and short-lived History server tokens for batch retrieval."""
        xml = self._get_xml(
            "esearch.fcgi",
            {"db": "pubmed", "term": query, "sort": "pub_date", "usehistory": "y", "retmax": 0, "retmode": "xml"},
        )
        root = ET.fromstring(xml)
        if root.tag != "eSearchResult":
            raise RuntimeError("Unexpected ESearch response format")
        error = text_at(root, "ERROR")
        if error:
            raise RuntimeError(f"NCBI ESearch error: {error}")
        try:
            count = int(text_at(root, "Count"))
        except ValueError as exc:
            raise RuntimeError("Could not determine PubMed search count") from exc
        webenv = text_at(root, "WebEnv")
        query_key = text_at(root, "QueryKey")
        if count and (not webenv or not query_key):
            raise RuntimeError("ESearch response missing History server tokens")
        return {"count": count, "webenv": webenv, "query_key": query_key,
                "translation": text_at(root, "QueryTranslation")}

    def fetch(self, webenv: str, query_key: str, start: int, size: int) -> bytes:
        """Fetch one batch from the exact ESearch result set."""
        return self._get_xml(
            "efetch.fcgi",
            {"db": "pubmed", "query_key": query_key, "WebEnv": webenv,
             "retstart": start, "retmax": size, "retmode": "xml"},
        )
