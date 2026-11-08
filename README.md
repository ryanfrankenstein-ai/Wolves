# Wolves in College Basketball

Waukee Northwest basketball alumni dashboard for men's and women's college programs.

## Run locally

```sh
python -m pip install -r requirements.txt
python app.py
```

Open http://localhost:5000. Set `WOLVES_OFFLINE=1` to run with the bundled roster and without starting score requests.

## Render

Use a Python 3 Web Service on `main`. Build with `pip install -r requirements.txt` and start with `gunicorn app:app --workers 1 --threads 8 --timeout 180 --keep-alive 60`. The health endpoint is `/healthz`.

## Roster and scores

Update `roster.json` when an alumnus joins a new program, transfers, or finishes college. Each card links to an official college source. Confirm Wolves participation from high-school rosters or a college bio that names Waukee Northwest. Waukee High School is a separate program.

ESPN team schedules and box scores power the Division I panels. Official school schedules and published box scores provide Division II and NAIA coverage, with links to the school's own live stats when present. Season averages are derived from available box scores. Missing stats stay blank and incomplete box-score coverage is labeled. In-memory caching shares requests among visitors and retains the latest successful team response through transient failures.

Logo asset from [Waukee Northwest Football](https://www.waukeenwfb.com/). The [Waukee Community School District](https://www.waukeeschools.org/news/2019/10/board-approves-mascot-and-colors-for-northwest-high-school/) documents the school colors and mascot.

Player portraits are from the official college roster pages linked in `roster.json`. Aliyah Al-Hameed’s photo comes from the 2024–25 roster archive. Tracked college marks are from their athletics sites and the ESPN team feed. Store portraits and college marks in `static/media` and reference those files in `roster.json`. Opponent logos come from the relevant schedule feed.
