"""Convert PubMed EFetch XML into article, article-author, and affiliation rows."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET

PUBLICATION_FIELDS = (
    "pmid", "title", "journal", "journal_iso", "publication_year", "publication_date_raw",
    "doi", "pmcid", "languages", "publication_types", "pubmed_url",
)
AUTHOR_FIELDS = (
    "author_id", "pmid", "author_position", "full_name", "last_name", "fore_name",
    "initials", "suffix", "collective_name", "orcid", "affiliation_count",
)
AFFILIATION_FIELDS = (
    "author_id", "pmid", "author_position", "affiliation_position", "affiliation_text", "affiliation_ror",
)


def node_text(element: ET.Element | None) -> str:
    return "" if element is None else " ".join("".join(element.itertext()).split())


def get_text(node: ET.Element, path: str) -> str:
    return node_text(node.find(path))


def get_article_id(record: ET.Element, id_type: str) -> str:
    for element in record.findall("./PubmedData/ArticleIdList/ArticleId"):
        if element.get("IdType", "").casefold() == id_type.casefold():
            return node_text(element)
    if id_type == "doi":
        for element in record.findall("./MedlineCitation/Article/ELocationID"):
            if element.get("EIdType", "").casefold() == "doi":
                return node_text(element)
    return ""


def publication_date(article: ET.Element) -> tuple[str, str]:
    node = article.find("./Journal/JournalIssue/PubDate")
    if node is not None:
        medline = get_text(node, "MedlineDate")
        if medline:
            year = re.search(r"\b(?:18|19|20)\d{2}\b", medline)
            return (year.group(0) if year else "", medline)
        parts = [get_text(node, name) for name in ("Year", "Month", "Day")]
        if any(parts):
            return parts[0], " ".join(part for part in parts if part)
    for node in article.findall("./ArticleDate"):
        year = get_text(node, "Year")
        if year:
            parts = [year, get_text(node, "Month"), get_text(node, "Day")]
            return year, "-".join(part for part in parts if part)
    return "", ""


def parse_article(record: ET.Element) -> tuple[dict, list[dict], list[dict]]:
    citation = record.find("./MedlineCitation")
    if citation is None:
        raise ValueError("PubmedArticle lacks MedlineCitation")
    pmid = get_text(citation, "PMID")
    article = citation.find("./Article")
    if not pmid or article is None:
        raise ValueError("PubmedArticle lacks PMID or Article")
    year, date_raw = publication_date(article)
    publication = {
        "pmid": pmid,
        "title": get_text(article, "ArticleTitle"),
        "journal": get_text(article, "./Journal/Title"),
        "journal_iso": get_text(article, "./Journal/ISOAbbreviation"),
        "publication_year": year,
        "publication_date_raw": date_raw,
        "doi": get_article_id(record, "doi"),
        "pmcid": get_article_id(record, "pmc"),
        "languages": "; ".join(filter(None, (node_text(n) for n in article.findall("./Language")))),
        "publication_types": "; ".join(filter(None, (node_text(n) for n in article.findall("./PublicationTypeList/PublicationType")))),
        "pubmed_url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
    }
    authors = []
    affiliations = []
    for position, author in enumerate(article.findall("./AuthorList/Author"), start=1):
        last, first = get_text(author, "LastName"), get_text(author, "ForeName")
        initials, suffix = get_text(author, "Initials"), get_text(author, "Suffix")
        collective = get_text(author, "CollectiveName")
        name = collective or " ".join(part for part in (first or initials, last, suffix) if part)
        orcid = ""
        for identifier in author.findall("./Identifier"):
            if identifier.get("Source", "").casefold() == "orcid":
                orcid = re.sub(r"^https?://orcid\.org/", "", node_text(identifier), flags=re.I)
                break
        author_id = f"{pmid}:{position}"
        seen_affiliations: set[tuple[str, str]] = set()
        affiliation_position = 0
        for info in author.findall("./AffiliationInfo"):
            raw = get_text(info, "Affiliation")
            if not raw:
                continue
            ror = next((node_text(identifier) for identifier in info.findall("./Identifier")
                        if identifier.get("Source", "").casefold() == "ror"), "")
            if (raw, ror) in seen_affiliations:
                continue
            seen_affiliations.add((raw, ror))
            affiliation_position += 1
            affiliations.append({
                "author_id": author_id, "pmid": pmid, "author_position": position,
                "affiliation_position": affiliation_position, "affiliation_text": raw,
                "affiliation_ror": ror,
            })
        authors.append({
            "author_id": author_id, "pmid": pmid, "author_position": position,
            "full_name": name, "last_name": last, "fore_name": first,
            "initials": initials, "suffix": suffix, "collective_name": collective,
            "orcid": orcid, "affiliation_count": affiliation_position,
        })
    return publication, authors, affiliations


def parse_xml(xml: bytes) -> tuple[list[dict], list[dict], list[dict], int]:
    root = ET.fromstring(xml)
    if root.tag != "PubmedArticleSet":
        raise ValueError(f"Unexpected EFetch root element: {root.tag}")
    errors = [node_text(e) for e in root.findall(".//ERROR")]
    if errors:
        raise RuntimeError("EFetch returned errors: " + "; ".join(errors))
    publications, authors, affiliations = [], [], []
    for article in root.findall("./PubmedArticle"):
        publication, article_authors, article_affiliations = parse_article(article)
        publications.append(publication)
        authors.extend(article_authors)
        affiliations.extend(article_affiliations)
    skipped_books = len(root.findall("./PubmedBookArticle"))
    return publications, authors, affiliations, skipped_books
