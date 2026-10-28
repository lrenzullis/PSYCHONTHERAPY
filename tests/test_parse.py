from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from pubmed_phase_a.parse import parse_xml

FIXTURE = Path(__file__).parent / "fixtures" / "sample_pubmed.xml"


def test_parses_normalized_tables_and_links():
    publications, authors, affiliations, skipped_books = parse_xml(FIXTURE.read_bytes())
    assert (len(publications), len(authors), len(affiliations), skipped_books) == (2, 5, 4, 0)
    assert publications[0]["doi"] == "10.0000/example.001"
    assert publications[0]["pmcid"] == "PMC90000001"
    assert publications[1]["publication_year"] == "2019"
    assert authors[0]["orcid"] == "0000-0000-0000-0001"
    assert authors[0]["affiliation_count"] == 2
    assert authors[2]["affiliation_count"] == 0
    assert authors[3]["collective_name"] == "Demonstration Research Consortium"
    assert affiliations[0]["affiliation_ror"] == "https://ror.org/example01"
    assert affiliations[2]["author_id"] == "90000001:2"
    assert affiliations[3]["author_id"] == "90000002:2"


def test_never_assigns_affiliations_to_other_authors():
    xml = b"""<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>1234</PMID><Article>
      <AuthorList><Author><LastName>One</LastName><AffiliationInfo><Affiliation>University A</Affiliation></AffiliationInfo></Author>
      <Author><LastName>Two</LastName></Author></AuthorList>
      </Article></MedlineCitation></PubmedArticle></PubmedArticleSet>"""
    _, authors, affiliations, _ = parse_xml(xml)
    assert authors[1]["affiliation_count"] == 0
    assert [a["author_id"] for a in affiliations] == ["1234:1"]


def test_rejects_broken_xml():
    with pytest.raises(ET.ParseError):
        parse_xml(b"<PubmedArticleSet><PubmedArticle>")


def test_rejects_api_errors_in_xml():
    with pytest.raises(RuntimeError, match="EFetch returned errors"):
        parse_xml(b"<PubmedArticleSet><ERROR>Temporary error</ERROR></PubmedArticleSet>")
