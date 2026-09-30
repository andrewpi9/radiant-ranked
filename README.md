# Radiant Ranked

It ranks every professor at UNC and Duke with VALORANT ranks so you can
actually compare them to each other.

**[Live site →](https://andrewpi9.github.io/radiant-ranked/)**

4,033 professors · 7,854 pulled · 96,569 ratings · 117 departments

---

## Why I made this

RateMyProfessors is annoying to use. If you want to compare two professors you
have to open them in separate tabs and go back and forth — check the rating,
check how many reviews it's actually based on, read the reviews, go back. The
search is bad. The whole interface is bad for the one thing people use it for,
which is deciding between a couple of options during registration.

So I put every professor at UNC and Duke on one page, in one order, and made it
searchable and filterable.

## Why VALORANT

I've played since it came out, mostly with friends. I'm Radiant.

Putting professors on the same ladder started as a joke, but it ended up being
the clearest way to show the ranking. Everyone already knows roughly what Gold
means. Nobody knows what 4.3 out of 5 means — especially when that 4.3 is from
six people and the 4.1 next to it is from three hundred.

Out of 4,033 professors, **two** are Radiant.

## What a rank actually means

The tiers follow VALORANT's real population distribution, so they mean the same
thing they do in game. Being Diamond here means you're in roughly the top 12% of
professors, same as Diamond means in your competitive queue.

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

All 25 divisions are in there, Iron 1 through Radiant.

I had William Davis for MATH 381 and he was one of my favorite professors at
UNC. He came out Ascendant 1, rank 206. That's about where I'd have put him.

---

## How the ranking works

The hard part is that a 5.0 from 5 students is not better than a 4.7 from 200,
but sorting by average says it is. Every "best professors" list has this problem
and it puts noise at the top.

So professors don't get sorted — they play each other.

1. **Each professor becomes a range instead of a number.** Their average and
   their review count together describe how good they probably are. Five reviews
   gives a wide range, three hundred gives a narrow one.
2. **Matches are simulated.** Two professors are picked, a value is drawn from
   each of their ranges, higher value wins, and both get an Elo update like a
   chess ladder.
3. **This runs a lot.** Ten separate tournaments of 500 rounds each, averaged.

A professor with five reviews swings wildly between matches and loses a lot of
them. A professor with three hundred reviews lands in nearly the same place every
time. Confidence has to be earned, not assumed.

What it does to the board, compared to just sorting by average:

| | Rating | Sorted by average | Actual rank |
|---|---|---|---|
| Michelle Sheran-Andrews | 4.6 from 221 reviews | #969 | **#266** |
| Thomas Nechyba | 4.7 from 194 reviews | #786 | **#118** |
| *typical thin 5.0* | 5.0 from 5 reviews | ~#280 | ~#960 |

### Two ranks next to each other often don't mean anything

This is the part I'd want someone to know before trusting it.

Because the matches are random, the same professor doesn't land on exactly the
same Elo every time. The site draws that spread as a bar under each rating. Where
two bars overlap, the data genuinely can't tell those professors apart, and the
order between them is arbitrary — including when a tier boundary falls between
them.

A single tournament wasn't good enough to publish. Changing the random seed moved
18 of the top 50. Running longer barely helped:

| Rounds | top-50 stays the same |
|---|---|
| 200 | 32/50 |
| 1,000 | 42/50 |
| 4,000 | 45/50 |

Twenty times the work bought 13 places. The randomness wasn't from too few
rounds — at the top, professors are genuinely close enough that no amount of
simulating separates them. Averaging ten independent tournaments instead of
running one long one gets it to 47/50 and a top 10 that doesn't move.

### Checking it against math instead of more simulation

Simulating matches is really just a slow way of asking "what are the odds this
professor beats a random other professor." That number can be calculated
directly, without any randomness at all. `radiant_rank/verify.py` does that and
compares the two orderings:

```
Spearman rho ................ 0.99948
top-10 agreement ............ 9/10
top-50 agreement ............ 47/50
```

They agree, so the simulation isn't doing anything weird. `make rank-verify`.

---

## Getting the data

RateMyProfessors has no public API. The site runs on a hidden GraphQL endpoint,
and most of what's written online about it is out of date. Things I had to work
out against the live API:

- **The school lookup every guide uses doesn't exist anymore.** They all use an
  `autocomplete` field that now returns *"Cannot query field autocomplete on type
  Query."* Schools have to be looked up by ID instead.
- **The "you can only get 1,000 results" thing isn't true here.** Paging through
  gets the whole school — 4,915 of 4,915 for UNC.
- **The API sends some professors twice.** Duke returned 5 duplicates in one
  clean run, UNC 32. So the count it reports is rows, not people: UNC's 4,915
  rows are 4,883 actual professors.
- **A bad page cursor silently starts over from the beginning** instead of
  erroring, so a failed resume returns data that looks fine and isn't.
- **Professors with no reviews aren't blank, they're zeros** — and
  would-take-again is `-1`. Averaging that in would quietly wreck everything.

The scraper can be stopped and restarted without losing progress, and it refuses
to write a half-finished dataset.

---

## Running it

```bash
pip install -r requirements.txt

make ingest    # pull from RateMyProfessors (can resume)
make rank      # run the tournaments
make site      # build the page
make all       # all three
```

The data is included, so `make rank` and `make site` work on a fresh clone
without scraping anything.

```bash
make test         # 49 tests
make rank-verify  # check the simulation against the direct calculation
make serve        # http://localhost:8000/web/
```

```
radiant_ingest/   scraper, retries, resume, validation
radiant_rank/     the ranking, tiers, verification
web/              the site — one HTML file, data and rank icons built in
tests/
```

---

## What I'd do next

- Every school, not just UNC and Duke
- Other rank systems — other games, or chess titles
- Keep the data current instead of a one-time pull
- Eventually let people review professors here directly instead of pulling from
  RateMyProfessors at all

## What's wrong with it right now

- Only the rating and the review count affect the rank. Difficulty and
  would-take-again are shown but not used.
- Nothing accounts for departments grading differently. A language lecturer and
  an organic chemistry professor are on the same ladder, which isn't really fair
  to either of them.
- Professors with under 5 reviews aren't ranked at all. That's 3,821 of the
  7,854 I pulled, which is a lot of people left out.
- RateMyProfessors reviews are voluntary, so they lean toward people with strong
  feelings either way. Nothing here fixes that, and nothing built on this data
  could.

---

Made this for me and my friends. Unofficial fan project — rank icons and tier
colors belong to Riot Games, and it isn't endorsed by Riot, UNC, or Duke. Review
data is RateMyProfessors', used here for a non-commercial student project.
