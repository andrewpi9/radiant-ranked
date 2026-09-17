# Radiant Ranked — developer notes

Implementation detail behind the [README](README.md): module layout, the
findings that shaped the RateMyProfessors client, and the reasoning behind the
ranking and tier parameters. Read the README first for what the project is.

## Status

| Phase | What | State |
| --- | --- | --- |
| 1 | DB schema | **not built** — no database exists yet, everything is JSON on disk |
| 2 | RMP ingestion | **done** — `radiant_ingest/`, output in `data/professors_raw.json` |
| 3 | Simulated-Elo rating engine | **done** — `radiant_rank/`, output in `data/rankings.json` |
| 4 | Frontend / leaderboard | **done** — `web/`, VALORANT-themed, built by `make site` |

## Layout

```
radiant_ingest/            ingestion package
  config.py                schools, paths, timing, retry knobs (all env-overridable)
  client.py                GraphQL client: retry, backoff, fatal-on-errors
  persistence.py           append log, checkpoint, deduped final JSON
  pipeline.py              school resolution + teacher pagination
  validate.py              post-pull data-quality report
  cli.py                   argparse entry point
radiant_rank/              rating engine
  config.py                thresholds, prior, Elo knobs (all env-overridable)
  model.py                 Beta posterior per professor
  elo.py                   tournament + ensemble averaging + Spearman
  tiers.py                 VALORANT tier distribution + assignment
  pipeline.py              ranking, department/school sub-ranks, report
  cli.py                   argparse entry point
web/
  template.html            site source (hand-edited; __PAYLOAD__ placeholder)
  index.html               generated — standalone deployable single file
  artifact.html            generated — body-only, for the Artifact tool
  assets/ranks/*.png       vendored VALORANT rank badges
scripts/ingest_rmp.py      standalone ingestion runner
scripts/rank_professors.py standalone ranking runner
scripts/fetch_rank_icons.py refresh rank badges from valorant-api.com
scripts/build_site.py      compile rankings + template -> the two HTML outputs
data/                      all output (gitignored)
```

## Frontend (phase 4)

```bash
make site     # -> web/index.html + web/artifact.html
make serve    # http://localhost:8000/web/
```

Dark-only by deliberate choice: VALORANT's identity is dark and a light variant
would misrepresent it, so the page commits to one theme and paints every colour
explicitly rather than shipping a half-considered second palette.

**Everything is embedded, nothing is fetched.** The 2.6 MB `rankings.json`
packs to ~313 KB by interning department/school names into positional arrays,
and the nine rank badges inline as base64 data URIs (~201 KB). Two reasons this
is not optional:

1. `file://` blocks a fetch of a sibling `.json` under CORS, so a fetching page
   would only work behind a server.
2. **The Artifact host's CSP blocks images from every external origin, with no
   visible error.** Hotlinked rank badges render as nothing at all. Data URIs
   are the only way they appear.

The build emits two files from one template because the Artifact host supplies
its own `<!doctype>/<head>/<body>` wrapper, so the artifact copy must be a body
fragment while `index.html` needs the full document.

## Running the ingestion

```bash
make install                                  # pip install -r requirements.txt
make ingest                                   # pull (resumes if interrupted)
make fresh                                    # ignore checkpoint, full re-pull
make validate                                 # re-check existing output, no network

python scripts/ingest_rmp.py --school 1232    # one school by RMP legacy id
python scripts/ingest_rmp.py --max-pages 2    # smoke test
python scripts/ingest_rmp.py --strict         # exit 3 if validation finds problems
```

A full two-school pull is ~80 requests and takes about 90 seconds.

**A plain `make ingest` skips schools already marked complete in the
checkpoint.** To actually refresh the data, use `make fresh`.

### Files in `data/`

| File | Role |
| --- | --- |
| `professors_raw.json` | ingestion output — deduped, sorted, school-tagged |
| `professors_raw.jsonl` | append log written page-by-page; the resume source |
| `ingest_checkpoint.json` | per-school cursor / counts / done flag |
| `rankings.json` | ranking output — `{meta, professors[]}`, global rank order |
| `*.bak.<timestamp>` | what `--fresh` moved aside |

`professors_raw.json` is written **only when every school completes**, so it is
never a partial dataset. A crashed run leaves the JSONL and checkpoint holding
the progress and leaves the previous JSON untouched.

## Record shape

```json
{
  "id": "VGVhY2hlci00ODg5NzU=",
  "legacy_id": 488975,
  "first_name": "Elizabeth",
  "last_name": "McLaughlin",
  "department": "Mathematics",
  "avg_rating": 3.3,
  "avg_difficulty": 3.6,
  "num_ratings": 457,
  "would_take_again_percent": 60.8592,
  "school_id": "U2Nob29sLTEyMzI=",
  "school_legacy_id": 1232,
  "school_name": "The University of North Carolina at Chapel Hill"
}
```

## Ranking (phase 3)

```bash
make rank          # -> data/rankings.json  (~55s)
make rank-check    # same, plus a seed-stability report (~2x slower)
python scripts/rank_professors.py --min-ratings 10 --tournaments 20
```

### How it works

RMP never gives head-to-head results, only aggregates, so matches are
simulated:

1. **Posterior.** `avg_rating` is rescaled to [0,1] and treated as a success
   proportion over `num_ratings` trials, giving a Beta posterior with a weak
   prior (5 pseudo-ratings) centred on the rating-weighted field mean
   (0.699 ≈ 3.79/5). The prior is what stops a lone 5.0 outranking an
   established 4.8, and it must be > 0 — at 0, a perfect 5.0 average yields
   `beta = 0`, which is not a samplable distribution.
2. **Matches.** Each round shuffles the field, pairs it off, and resolves each
   match by drawing one sample from each professor's posterior. Higher draw
   wins; standard Elo update; K decays 32 → 6 across rounds.
3. **Ensemble.** 10 independent tournaments of 500 rounds are averaged.

### Why the ensemble is not optional

A single tournament is **not reproducible enough to publish**. Measured, one
200-round tournament retains only **32/50** of its top 50 when the seed
changes. Longer runs barely help — 4000 rounds still only reaches 45/50 —
because the noise floor comes from the sampling itself, not from too few
rounds. At the top of the board many professors have near-identical posteriors,
and some of that churn is honest: they are genuinely tied within the data's
resolution.

Averaging independent tournaments fixes what *can* be fixed, and beats one long
run at equal compute (5×500 strictly better than 1×2500). Current defaults
score **Spearman 0.99902, top-10 9/10, top-50 46/50** against a disjoint seed
block. `--tournaments 20` tightens it further at 2x runtime.

Every row carries `elo_stddev` — the spread across the ensemble. It is the
honest width of a rank: a heavily-rated professor sits around ±10, a thinly
rated one ±35. **The frontend should surface it rather than presenting bare
ranks as exact**, since adjacent ranks are frequently within one standard
deviation of each other.

### Ranking record shape

Global rank order, with `department_rank`/`school_rank` precomputed so
department and school boards are a filter rather than a re-run (Elo scores are
comparable across them because everyone competed in one global pool).
`naive_rank` and `rank_delta` compare against a plain `avg_rating` sort — handy
for showing what the model changed, and a fast sanity check that it is doing
anything at all.

### VALORANT divisions

`radiant_rank/tiers.py` places each professor into one of VALORANT's **25
competitive divisions by board position, not by score**, reproducing the game's
published population distribution. The project name is the top division.

The published shares total **100.02%**, not 100 (rounding in the source), so
assignment normalises by the actual total rather than assuming 100. Cumulative
share crosses 50% inside Gold 2, so Gold straddles the median professor as it
does in game.

| Tier | Share | Professors | Ranks |
| --- | --- | --- | --- |
| Radiant | 0.05% | 2 | 1–2 |
| Immortal | 1.37% | 55 | 3–57 |
| Ascendant | 6.32% | 255 | 58–312 |
| Diamond | 11.55% | 466 | 313–778 |
| Platinum | 17.56% | 708 | 779–1486 |
| Gold | 22.12% | 892 | 1487–2378 |
| Silver | 20.66% | 833 | 2379–3211 |
| Bronze | 15.72% | 634 | 3212–3845 |
| Iron | 4.67% | 188 | 3846–4033 |

Per-division figures are in `tiers.py` and printed by `make rank`. All three
divisions of a tier share one colour, as in game — the badge artwork is what
distinguishes them.

Division colours are Riot's official values, asserted against the live API by
`scripts/fetch_rank_icons.py`, which reports drift rather than silently
disagreeing. That script also downscales the badges 256px → 96px; at full size
all 25 would add ~860 KB of base64 to every page load for no visible gain.

A division boundary is **not** meaningful when two adjacent professors' Elo
bands overlap, which at these band widths is most of them. The Gold 2 / Gold 1
line in particular falls between professors the data does not separate.

### Scoring caveats

- `avg_difficulty` and `would_take_again_percent` are carried through but **not
  used** in scoring. Only `avg_rating` and `num_ratings` drive the result.
- Cross-department comparison is unweighted: a Nursing lecturer and an Organic
  Chemistry professor compete as if their rating scales were identical.
- The `>=5 ratings` threshold excludes **3,821 of 7,854** professors (49%).
  `>=1` would keep 6,719, `>=10` keeps 2,534.

## RMP API notes (verified 2026-09-04)

Endpoint `https://www.ratemyprofessors.com/graphql`, auth header
`Basic dGVzdDp0ZXN0` (base64 `test:test`) — public, shipped in RMP's own
frontend bundle, not a secret.

Things that cost time to discover; check these first if a future run breaks:

- **`autocomplete` no longer exists** on the root Query type. The commonly
  circulated `AutocompleteSearchQuery` for school lookup is dead. Schools are
  resolved instead by base64-encoding `School-<legacyId>` and verifying with
  `node(id:)`, which still works and returns name/city/state.
- **Global IDs are base64 `<Type>-<legacyId>`** — `School-1232`, `Teacher-488975`.
- **There is no 1000-result cap** on `newSearch.teachers`. Cursor pagination
  walks the entire school (verified 4915/4915 for UNC). The widely repeated
  "RMP caps at 1000" advice refers to an older endpoint. No letter/department
  sharding is needed. `departmentID` *is* a valid filter input if it's ever
  wanted — it expects a base64 `Department-<id>`, not a bare int.
- **Cursors are offset-based** (`arrayconnection:N` in base64), which makes
  checkpoints stable. But an **invalid or stale cursor silently resets to page
  0** rather than erroring — no exception, just duplicate data. Dedup by `id`
  on write is what protects against this.
- **RMP serves duplicate teacher rows** inside a single result set (37 across
  the two schools, all `num_ratings: 0`, byte-identical). So `resultCount`
  counts *rows*, not distinct professors: UNC's 4915 rows are 4883 people.
  Validation therefore compares **rows fetched** against `resultCount` (must
  match) and reports the dedup separately. Comparing the deduped count against
  `resultCount` makes a correct pull look truncated.
- **Sentinel values, not nulls.** An unrated professor comes back as
  `num_ratings: 0`, `avg_rating: 0`, `avg_difficulty: 0`,
  `would_take_again_percent: -1`. These are preserved verbatim in the raw
  layer. **The rating engine must exclude or floor them** — averaging a `-1`
  percent or treating a `0` rating as a genuine 0/5 will corrupt rankings.
  ~14.5% of records are unrated.
- `firstName` / `lastName` / `department` arrive with **stray whitespace**;
  stripped during normalization.
- Schema drift is treated as fatal: any GraphQL `errors` field is logged in
  full and stops the run rather than writing partial data.

## Current data (as of 2026-09-04)

| School | Professors | Rows | Total ratings |
| --- | --- | --- | --- |
| UNC Chapel Hill (1232) | 4,883 | 4,915 | — |
| Duke (1350) | 2,971 | 2,976 | — |
| **Total** | **7,854** | **7,891** | **96,569** |

6,719 professors (85.5%) have at least one rating; 141 distinct departments.

## Adding a school

Append to `TARGET_SCHOOLS` in `radiant_ingest/config.py` with its RMP legacy id
(the number in a school's ratemyprofessors.com URL). `expected_name` is only a
drift warning. Or pull an unlisted one ad hoc with `--school <legacy_id>`.
