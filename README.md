# garmin-boostcamp-sync

Copies the exercises, sets, reps and weights from your Boostcamp workouts onto
the matching Garmin Connect strength activity. Runs once a day in Docker.

- Boostcamp is read only. Garmin's exercise sets on the matched activity are **overwritten**.
- The matched activity is renamed to the Boostcamp program and workout title,
  e.g. `GZCLP · Week 1 · Day 1`. A name you change by hand in Garmin is
  overwritten again while the workout is within `lookback_days`; set
  `rename_activities = false` to turn this off.
- Matching: closest Garmin `strength_training` activity in time (within
  `match_window_minutes`) when Boostcamp gives a start time, otherwise the
  strength activity on the same local date.
- Already-synced workouts are recorded in `data/state.json` and only re-sent if
  the Boostcamp workout changes (or with `sync --force`).

Uses [python-garminconnect](https://github.com/cyberjunky/python-garminconnect)
and [boostcamp-api](https://github.com/Alex-Keyes/boostcamp-api), both
unofficial, reverse-engineered clients.

## Setup

```sh
cp credentials.example.toml credentials.toml   # gitignored — fill in both accounts
python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"
```

If your Boostcamp account uses Google/Apple sign-in, set a password first via
Boostcamp's "Forgot password" flow.

## Usage

```sh
gbsync login                    # interactive: prompts for the Garmin MFA code, saves tokens
gbsync dump                     # save raw API JSON to data/dumps/ (for checking field names)
gbsync sync --dry-run --since 14  # show matches and payloads, write nothing
gbsync sync                     # one real sync
gbsync daemon                   # sync now, then every interval_hours
```

## Garmin MFA

Tokens live in `data/garmin_tokens/` and refresh automatically, so MFA is
normally only needed once. Two ways to provide it:

1. **Interactive** (local / first deploy): `gbsync login`, or in Docker
   `docker compose run --rm sync login`.
2. **File-based** (unattended): if the daemon finds the tokens missing or
   rejected, it starts a login, logs
   `Garmin MFA required: write the code to data/mfa_code ...` and waits up to
   `mfa_wait_minutes`. Write the code on the host:

   ```sh
   echo 123456 > data/mfa_code
   ```

   A wrong code is logged and it waits for a new one. If nothing arrives in
   time, the cycle is skipped and it tries again next interval.

## Deploy (Docker)

CI (`.github/workflows/build-and-push.yml`) builds the image on every PR and
pushes `ghcr.io/umizoom/garmin-boostcamp-sync` (tags `latest` and the short
commit SHA) on merge to `main`. The package is public, so pulling needs no
login. The image contains no secrets; `credentials.toml` and `data/` are
mounted at runtime.

```sh
cp credentials.example.toml credentials.toml   # fill in
mkdir -p data && sudo chown 1000:1000 data      # container runs as uid 1000
docker compose pull                            # or `docker compose build` to build locally
docker compose run --rm sync login             # one-time MFA
docker compose up -d
docker compose logs -f
```

To update: `docker compose pull && docker compose up -d`.

## Exercise mapping

Garmin only accepts exercise names from its own catalog. Names are resolved in
this order:

1. `exercise_map.toml` overrides
2. exact or normalized match against the Garmin catalog
3. keyword → category (exercise name left blank)
4. `TOTAL_BODY`

Unmapped names are logged at the end of each sync. Add them to
`exercise_map.toml`.

## Tests

```sh
.venv/bin/pytest
```
