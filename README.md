# Radiant Ranked

Every rated professor at UNC Chapel Hill and Duke, ranked by a simulated-Elo
tournament over their RateMyProfessors ratings and sorted into VALORANT's 25
competitive divisions.

**[Live site →](https://andrewpi9.github.io/radiant-ranked/)**

4,033 ranked professors · 7,854 pulled · 96,569 ratings · 117 departments

---

## The interesting part: the first version wasn't reproducible

The ranking works by simulating matches. Each professor is a probability
distribution over their true quality, every match draws a sample from each of
two professors, and Elo updates on who drew higher.

The first working version looked fine — until I changed the random seed and
**18 of the top 50 professors changed.** A leaderboard whose podium depends on
an RNG seed is not a leaderboard.

The obvious fix is more rounds. I measured it instead:

| Rounds | Spearman ρ | top-10 | top-50 |
|---|---|---|---|
| 200 | 0.9857 | 5/10 | 32/50 |
| 500 | 0.9912 | 7/10 | 40/50 |
| 1,000 | 0.9932 | 8/10 | 42/50 |
| 4,000 | 0.9973 | 8/10 | 45/50 |

Twenty times the compute bought 13 places out of 50. The noise wasn't coming
from too few rounds — it was a floor in the estimator itself. At the top of the
board many professors have nearly identical distributions, and *some of that
churn is honest*: they are genuinely tied within what the data can resolve.

So I fixed what could be fixed, by averaging independent tournaments rather than
running one longer. At equal compute this is strictly better:

| Configuration | Spearman ρ | top-10 | top-50 | top-100 |
|---|---|---|---|---|
| 1 × 2,500 rounds | 0.9939 | 6/10 | 42/50 | 82/100 |
| 5 × 500 rounds | 0.9982 | 8/10 | 42/50 | 93/100 |
| **10 × 500 rounds** | **0.9991** | **10/10** | **47/50** | **96/100** |

Ten averaged tournaments is what ships. Every professor also carries the
standard deviation of their Elo across those runs, and the site draws it as a
band under each row — **where two bands overlap, the ranks between them are not
real**, and the page says so rather than presenting a rank as exact.

### Checking the simulation against the maths

A simulation is only worth trusting if something independent agrees with it. The
tournament is really a Monte Carlo estimate of a quantity you can compute
directly: each professor's probability of beating a uniformly random opponent.
`radiant_rank/verify.py` computes that exactly on a 1,000-bin grid — no RNG, no
sampling — and compares the orderings:

```
Spearman rho ................ 0.99948
top-10 agreement ............ 9/10
top-50 agreement ............ 47/50
median rank shift ........... 20
```

Run it yourself with `make rank-verify`.

---

## How a professor gets ranked

RateMyProfessors publishes an average and a rating count, never head-to-head
results, so the matches have to be invented.

1. **Posterior.** A professor's average is rescaled to [0,1] and treated as a
   success rate over `num_ratings` trials, giving a Beta posterior with a weak
   prior (5 pseudo-ratings) centred on the rating-weighted field mean of 3.79/5.
2. **Matches.** Each round shuffles the field, pairs it off, and draws one
   sample per professor. Higher draw wins. Standard Elo update, K decaying 32→6.
3. **Ensemble.** Ten independent tournaments, averaged.

The posterior is what makes this more than a sort. A 5.0 held up by five ratings
has a wide distribution and regresses toward the field; a 4.7 backed by two
hundred has a narrow one and holds. Concretely, against a naive sort by raw
average:

| | Rating | Naive rank | Elo rank |
|---|---|---|---|
| Michelle Sheran-Andrews | 4.6 over 221 ratings | #969 | **#266** |
| Thomas Nechyba | 4.7 over 194 ratings | #786 | **#118** |
| *(thin 5.0s)* | 5.0 over 5 ratings | ~#280 | ~#960 |

### Tiers

The 25 VALORANT divisions are assigned **by board position, not by score**,
reproducing the game's published population distribution. Those published shares
total 100.02%, not 100, so assignment normalises by the actual total. Cumulative
share crosses 50% inside Gold 2 — so Gold straddles the median professor, as it
does in game.

| Tier | Share | Professors |
|---|---|---|
| Radiant | 0.05% | 2 |
| Immortal | 1.37% | 55 |
| Ascendant | 6.32% | 255 |
| Diamond | 11.55% | 466 |
| Platinum | 17.56% | 708 |
| Gold | 22.12% | 892 |
| Silver | 20.66% | 833 |
| Bronze | 15.72% | 634 |
| Iron | 4.67% | 188 |

---

## Getting the data out of RateMyProfessors

There is no public API. The site runs on an undocumented GraphQL endpoint, and
most of the community knowledge about it is out of date. Things that cost real
time to work out, all verified against the live API:

- **The documented school-lookup query no longer exists.** Every guide uses an
  `autocomplete` root field; it now returns *"Cannot query field autocomplete on
  type Query"*. Schools are resolved instead by base64-encoding
  `School-<legacyId>` and verifying with `node(id:)`.
- **The widely repeated "1,000 result cap" does not apply here.** Cursor
  pagination walks an entire school — verified 4,915/4,915 for UNC. No
  letter-sharding or department-sharding needed.
- **The API serves duplicate professors.** Duke returned 5 duplicate teacher IDs
  in a single uninterrupted pass, UNC 32. So `resultCount` counts *rows*, not
  people: UNC's 4,915 rows are 4,883 professors. Validation compares rows
  fetched against `resultCount` and reports the dedup separately — comparing the
  deduped count instead makes a complete pull look truncated.
- **A stale cursor silently resets to page 0** instead of erroring, so a bad
  resume returns plausible wrong data. Deduping by ID on write is what protects
  against it.
- **Unrated professors use sentinels, not nulls**: `num_ratings: 0`,
  `avg_rating: 0`, `would_take_again_percent: -1`. Averaging a `-1` percentage
  would quietly corrupt the board, so the raw layer preserves them verbatim and
  the ranking excludes anyone under 5 ratings.

The ingest is resumable. Progress is checkpointed after every page, and the
final JSON is only written once every school completes, so a crash never leaves
a half-populated dataset behind. Any GraphQL `errors` field is treated as fatal
and logged in full rather than continuing on partial data.

---

## Running it

```bash
pip install -r requirements.txt

make ingest    # pull from RateMyProfessors (resumes if interrupted)
make rank      # simulated-Elo tournament -> data/rankings.json
make site      # build the page -> web/index.html
make all       # all three
```

The pulled data is committed, so `make rank` and `make site` work from a clean
clone without touching RateMyProfessors.

```bash
make test         # 49 tests
make rank-verify  # check the simulation against the closed form
make serve        # http://localhost:8000/web/
```

### Layout

```
radiant_ingest/   GraphQL client, retry/backoff, checkpointing, validation
radiant_rank/     Beta model, Elo ensemble, tier assignment, verification
web/              single-file site; data + rank badges embedded as data URIs
tests/            49 tests
```

The site is one self-contained HTML file. The data is embedded rather than
fetched because `file://` blocks a sibling `fetch` under CORS, and the 2.7 MB
ranking packs to ~313 KB by interning names into positional arrays. The 25 rank
badges are inlined as base64, downscaled 256px → 96px by
`scripts/fetch_rank_icons.py` (at full size they would add ~860 KB per page load
for no visible gain).

---

## Known limits

- **Only average rating and rating count drive the score.** Difficulty and
  would-take-again are displayed but unused.
- **No adjustment for grading culture.** A language lecturer and an organic
  chemist compete on the same scale, which is not really fair to either.
- **The ≥5-ratings threshold excludes 3,821 of 7,854 professors** (49%). Below
  that the posterior is so wide a rank would be noise with a number on it.
- **Self-selection.** RateMyProfessors is voluntary, so the underlying ratings
  over-represent students with strong opinions. Nothing here corrects for that,
  and no ranking built on this data can.

---

Unofficial fan project. Rank artwork and tier colours are property of Riot
Games; not endorsed by Riot Games, UNC Chapel Hill, or Duke University. Rating
data belongs to RateMyProfessors and is used here for a non-commercial student
project.
