# Cavalry News Digest

A public, automatically-updating static website that monitors news across
five fixed areas — **AI Fraud**, **Pharma**, **Meta**, **Data Centers**, and
**China** (cross-cutting across the other four) — and publishes an
AI-generated one-sentence summary of each relevant item alongside a link to
the original reporting.

A scheduled pipeline does the work; a static site generator turns the
resulting database into plain HTML. There is no live backend — the published
site is just files, served by GitHub Pages.

---

## How it works

Every 2 hours, `pipeline/run.py`:

1. **Fetches** candidate articles from Google News RSS and (optionally)
   NewsAPI, per pillar topic list (`config/pillars/*.yaml`).
2. **Filters** by the global source block list (press-release wires) and the
   article domain allowlist (`config/allowed_sources.txt`).
3. **Deduplicates** against everything already stored (exact URL match +
   fuzzy title match).
4. **Scores** each new item 0–10 against the relevance definition of every
   pillar whose search query surfaced it, and assigns the item to whichever
   pillar scored it highest (see *Pillar overlap rule* below). Items below
   threshold for every candidate pillar are dropped.
5. **Summarizes** survivors with Claude — one original, declarative sentence
   per item. The model is explicitly instructed not to copy source phrasing.
6. **Stores** the result in `data/digest.db` (SQLite).
7. **Rebuilds the static site** (`site/generate.py`) from the current
   database state.

A GitHub Actions workflow (`.github/workflows/digest.yml`) runs this on a
cron schedule, commits the updated `data/digest.db` and the regenerated
`docs/` folder back to the repo, and GitHub Pages serves `docs/`.

---

## Pillar overlap rule

China-related items can legitimately match both a topic pillar (e.g. AI
Fraud) and the China pillar's own search queries. When that happens, the
item is scored against **both** pillars' relevance definitions and filed
under whichever one scores it higher — ties favor the more specific,
non-China pillar. This means every item appears exactly once, never twice.
The China pillar is held to the exact same factual relevance standard as the
other four; it does not weight, rank, or filter for tone or any
predetermined conclusion.

---

## Copyright discipline (this site is public)

The database has **no column for article body text** — article text is only
ever held in memory during scoring/summarization and is never persisted.
Templates render exactly four things per item: the headline (linked to the
original source), the source name, the publish timestamp, and our own
one-sentence summary. No body text, no images, ever. A persistent banner on
every page discloses that summaries are AI-generated and that a link does
not imply endorsement of that source.

---

## Local setup

```bash
git clone <your-repo-url>   # see "Deploying" below if this isn't a repo yet
cd news-digest
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # fill in ANTHROPIC_API_KEY at minimum
```

### Run the pipeline once

```bash
python pipeline/run.py                # fetch, score, summarize, store, rebuild site
python pipeline/run.py --reset-db      # clear data/digest.db
python pipeline/run.py --skip-site-build
```

### Rebuild just the site from the current database

```bash
python site/generate.py               # reads data/digest.db, writes site/dist/
python site/generate.py --sample      # uses fabricated sample data instead — writes nothing to the DB
```

Open `site/dist/index.html` directly in a browser to preview.

### Run tests

```bash
pytest tests/ -v
```

---

## Adding or editing a pillar

Each pillar is one YAML file in `config/pillars/`:

```yaml
slug: ai_fraud          # used in URLs (pillar/ai_fraud.html) and as a DB key — don't rename once live
name: "AI Fraud"        # display name
topics:                  # Google News / NewsAPI search queries
  - "deepfake scam fraud"
  - "AI-generated phishing attack"
definition: |
  <the relevance definition the scorer uses, verbatim>
```

To add a sixth pillar, drop a new file in `config/pillars/` — `pipeline/run.py`
and `site/generate.py` both discover pillars automatically by reading every
`*.yaml` file in that directory. No code changes needed. To retire a pillar,
remove its file (existing stored items for that slug stay in the database
but stop appearing in navigation).

`threshold` and `high_significance_threshold` default to the values in
`config/settings.yaml` but can be overridden per pillar by adding either key
to that pillar's YAML file.

---

## How the static build works

`site/generate.py` is a plain script (not a Python package — deliberately,
to avoid colliding with the standard library's own `site` module). It:

1. Reads `config/pillars/*.yaml` for pillar names/slugs.
2. Reads `data/digest.db` via `pipeline/storage.py`.
3. Renders Jinja2 templates from `site/templates/` (`base.html`, `index.html`,
   `pillar.html`, `about.html`).
4. Writes static HTML + `static/style.css` to `site/dist/`.

Each pillar is rendered independently inside a try/except — if one pillar's
data is somehow broken, the rest of the site still builds.

`site/dist/` is gitignored (it's a build artifact). The committed, published
copy lives in `docs/`, which GitHub Pages serves directly.

---

## Deploying to GitHub Pages

1. `git init`, create a GitHub repo, push.
2. In the repo's Settings → Pages, set source to the `main` branch, `/docs`
   folder.
3. Add repository secrets: `ANTHROPIC_API_KEY` (required), `NEWSAPI_KEY`
   (optional).
4. The workflow in `.github/workflows/digest.yml` runs every 2 hours,
   regenerates `docs/`, and commits it — Pages picks up the change
   automatically. You can also trigger it manually from the Actions tab
   (`workflow_dispatch`).

`data/digest.db` is committed back to the repo by the workflow so dedup
state and all stored content survive across runs — this is what makes the
"every 2 hours" schedule actually additive rather than starting from zero
each time.

---

## Re-enabling the email digest

The original email pipeline (`legacy_email/render.py`, `legacy_email/sender.py`)
is kept intact but disabled. To re-enable it:

```yaml
# config/settings.yaml
email:
  enabled: true     # was false
  mode: daily
  send_hour: 7
  max_items: 25
```

You'll also need `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD` in
`.env` and recipients in `config/recipients.txt`. No code changes are
required — `pipeline/run.py` checks this flag after every run.

---

## Project structure

```
news-digest/
├── pipeline/              # fetch → filter → dedup → score → summarize → store
│   ├── fetch.py            # Google News RSS + NewsAPI, pillar-aware
│   ├── source_filter.py    # article domain allowlist
│   ├── score.py            # pillar-assignment relevance scoring (Claude Haiku)
│   ├── summarize.py        # one-sentence-per-item summaries (Claude Sonnet)
│   ├── storage.py          # SQLite content store (also the dedup store)
│   └── run.py               # orchestrator / entry point
├── legacy_email/           # disabled-by-default email renderer + SMTP sender
├── site/                   # reads the DB, renders the static site
│   ├── generate.py          # entry point — run this to rebuild site/dist/
│   ├── sample_data.py        # fabricated data for --sample preview runs
│   ├── templates/            # Jinja2 templates
│   ├── static/style.css
│   └── dist/                 # build output (gitignored)
├── config/
│   ├── settings.yaml         # global thresholds, email toggle
│   ├── pillars/*.yaml        # one file per pillar
│   ├── source_block_list.yaml
│   └── allowed_sources.txt
├── data/digest.db           # canonical content store, committed by CI
└── .github/workflows/digest.yml
```
