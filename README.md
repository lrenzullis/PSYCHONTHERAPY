# PubMed Phase A — reproducible extraction experiment

A **minimal, Git-ready Python project** for discovering psychoanalysis-related publications in PubMed and extracting the publication, author, and affiliation fields present in PubMed XML. This phase does **not** filter countries, infer institutions, collect emails, or conduct outreach.

**What is in Git:** code, query, configuration, dependency specification, small synthetic XML test fixture, offline tests, and a GitHub Actions test workflow.  
**What is not in Git:** downloaded XML, real publication/author data, generated CSVs, API keys, virtual environments, and other local outputs.

## Repository layout

```text
pubmed-phase-a/
├── .github/workflows/tests.yml    # offline CI checks on GitHub
├── .gitignore                      # generated datasets and secrets ignored
├── .env.example                    # environment-variable names (not auto-loaded)
├── pyproject.toml                  # Python requirements + CLI entry point
├── README.md
├── config/
│   └── query.txt                   # editable PubMed topic search
├── data/
│   └── .gitkeep                    # outputs created under data/runs/
├── src/pubmed_phase_a/
│   ├── __init__.py
│   ├── __main__.py
│   ├── cli.py                      # fetch / extract / run
│   ├── ncbi.py                     # ESearch + EFetch client
│   └── parse.py                    # XML to normalized tables
└── tests/
    ├── fixtures/sample_pubmed.xml # entirely synthetic, not PubMed results
    ├── test_parse.py
    └── test_workflow.py
```

## 1. Local setup

Requires **Python 3.11+** and an internet connection when fetching real data.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m pytest -q             # tests are OFFLINE; no NCBI contact
```

Only one runtime dependency: `requests`. Developer dependency: `pytest`. Packaging uses setuptools.

## 2. Configure

Edit [`config/query.txt`](config/query.txt) to change search terms. The command appends the publication-year range, so **do not** include a date filter in the query file unless you intend an additional restriction.

Set your contact email (recommended by NCBI) and optionally an API key in the shell:

```bash
export NCBI_EMAIL="you@university.edu"
# Optional:
# export NCBI_API_KEY="your-key"
```

On Windows PowerShell: `$env:NCBI_EMAIL="you@university.edu"`.

`.env.example` lists variable names; the CLI does not automatically load `.env` files. Never commit secrets.

## 3. Run your experiment

From the repository root:

```bash
pubmed-phase-a run \
  --query-file config/query.txt \
  --start-year 2015 \
  --end-year 2026 \
  --max-results 200 \
  --batch-size 100
```

The CLI prints the generated run directory, such as `data/runs/20261028T110000123456Z/`.

If you prefer to **separate collection from processing**, run:

```bash
pubmed-phase-a fetch --max-results 200 --run-dir data/runs/pilot-001
pubmed-phase-a extract --run-dir data/runs/pilot-001
```

The `extract` command is **fully offline**; it re-parses the saved raw XML and does not need NCBI credentials. It can be re-run when you modify the parsing code.

You can also run the commands as `python -m pubmed_phase_a ...` after installation.

### Per-run output (created locally; NOT included in this repository)

```text
data/runs/<run-id>/
├── manifest.json               # exact query, years, sort, counts, batch hashes
├── raw/
│   ├── batch_0001.xml          # original NCBI XML, unchanged
│   └── batch_0002.xml
└── processed/
    ├── publications.csv        # article-level metadata, PMID key
    ├── authors.csv             # article-author rows, article-specific author_id
    ├── affiliations.csv        # author-affiliation rows, original text
    └── summary.json           # coverage and extraction counts
```

**Keys:** `publications.pmid` joins to `authors.pmid`. `authors.author_id` joins to `affiliations.author_id`. An `author_id` is `PMID:position`, **not** a globally disambiguated person identifier. Affiliations are associated only with the authors whose PubMed XML includes them; do not assign the corresponding author's affiliation to every coauthor.

The manifest retains the exact query and full raw XML batches with SHA-256 digests, so you can re-run parsing on the same snapshot. A **new fetch is not guaranteed to give the same articles**: PubMed content and indexing change over time. `sort=pub_date` selects the most recently published matches first.

## 4. Version control / GitHub

```bash
git init
git add .
git commit -m "Initialize reproducible PubMed Phase A experiment"
git branch -M main
# After creating an empty GitHub repository:
git remote add origin https://github.com/YOUR-ACCOUNT/pubmed-phase-a.git
git push -u origin main
```

The included GitHub Actions workflow runs the **offline tests** on pushes and pull requests. It never downloads real PubMed data or requires an API key.

## Experiment boundaries and caveats

- **Not country-filtered.** Latin American institution identification is a separate phase.
- **Not contact extraction.** It reads the raw affiliation text, which may contain an email, but it does not extract or normalize addresses.
- **Limited author coverage.** Older records and some PubMed records have no author-linked affiliation; missing values remain missing.
- **Article vs person.** The same researcher appearing in two articles currently produces two author rows. Author disambiguation is out of scope.
- **PubMed ESearch restriction.** This starter deliberately supports at most 10,000 records in a run. For larger projects, partition the query into smaller publication-date windows rather than silently dropping records.
- **NCBI usage guidance.** The client keeps under 3 requests per second without an API key (under 10 with one), uses batch requests, and retries transient errors. For large runs NCBI recommends off-peak hours. See official [E-utilities guidelines](https://eutilities.github.io/site/API_Key/usageandkey/) and [ESearch/EFetch documentation](https://www.ncbi.nlm.nih.gov/books/NBK25499/).
- **Validation.** The tests run against a synthetic fixture. A real PubMed API run is deliberately **not** included and must be executed by you.
