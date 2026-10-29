# Shoebox

A curation workbench for scanned family photo albums. It imports scans organized
one folder per physical album, pairs back scans with their fronts, and lets you
date and tag photos quickly (bulk year/event entry, undo). It then exports copies
with standard metadata that Apple Photos, Immich and Google Photos read in
chronological order.

Status: **milestones 1–3** (dating workbench, back reading, faces and people, inference and review).

## Setup

1. Install Docker Desktop. In *Settings → Resources*, give it about 8 CPUs (the face
   detector runs on CPU inside the container).
2. `cp .env.example private/.env` and set:
   - `PHOTO_ROOT`: the folder holding one subfolder per album. It's mounted
     **read-only**, so originals are never modified.
   - `BACKUP_ROOT`: the permanent layer (see below). Put it somewhere Time Machine
     or iCloud covers.
3. Optional: `cp config/config.example.toml private/config.toml` and adjust
   `back_pattern` if your back scans aren't named like `IMG_0042_back.jpg`,
   `IMG_0042-b.jpg` or `IMG_0042 reverse.jpg`.
4. `docker compose up -d --build`, then open http://localhost:8000.
5. On the **Jobs** page, click **Import**. It queues *Read backs* (OCR),
   *Estimate dates* and *Auto-rotate* to run after it.

## Personal data

Anything that identifies your family stays out of git:

- **`private/`** holds your settings (`.env`, `config.toml`) and `denylist.txt`.
  Git ignores everything in it except its README, and the Docker build skips it.
- Scans, the database and backups live outside the repository (`PHOTO_ROOT`, the
  Docker volume, `BACKUP_ROOT`). `.gitignore` also blocks image, sidecar and
  database files anywhere in the tree.
- A pre-commit hook refuses commits that add those files or contain any word
  listed in `private/denylist.txt` (family names, places, real paths). Turn it
  on once per clone:
  ```bash
  git config core.hooksPath .githooks
  ```

## Using it

- **Grid:** pick an album. Click to select, Shift-click for a range, ⌘-click to
  add. Press `Y` (or use the bar at the bottom) and type a date:
  `1987`, `1987-06`, `1987-06-14`, `1980s`, `1985-1988`, `~1987`.
  Pick an event type. A year plus a fixed-date holiday becomes that day
  ("1985" + Christmas → 1985-12-25), and a dated event instance dates any
  undated photo you later tag with it.
- **Estimated dates** (amber, shown as `≈1978-10` with a confidence). The app
  combines several kinds of evidence, and photo detail's *Why* line shows which
  ones it used:
  1. the photo's own back (printed lab date read by local OCR, or a date you typed);
  2. the nearest dated loose back within 3 files;
  3. **album neighbours**, interpolated between the nearest dated photos before and
     after it in the same album. Measured on a real collection: exact year 92% of the
     time, within one year 96%. Dates you confirm anchor it fully; OCR-read back
     dates anchor it at 70% weight;
  4. **ages**: a named person with a birth year, and how old their face looks;
  5. years in the file name (`Album3_1984_1986_0161` → ≈1984-1986).

  When pieces of evidence agree, confidence goes up; disagreements are shown
  ("… but file name says 1990"). Estimates never overwrite a date you entered and
  are never used as evidence for other estimates. *Accept* confirms an estimate,
  which then anchors its neighbours. **Order → Least sure** is the review queue.
  Inference re-runs on its own after your edits.
- **Backs page** (two tabs). *Loose* shows each back with no front of its own,
  next to the fronts it dates. *Paired* shows each back next to its own photo.
  OCR reads stamps that are faint, rotated, upside down or mirrored, plus
  dot-matrix lab lines. It only uses a reading automatically when the reading has
  a 4-digit year, or several passes agree within 1940–2015, and it fits the
  album's filename years. Other readings appear as chips you can click. Type the
  date (`1978-10`) and press Enter to save and move on, or click *No date*.
  Handwriting is left to you.
- **People:** faces are found after auto-rotate (about 0.25 s per photo on
  CPU). *People → To name* groups faces that look alike, oldest photo first.
  Click any face that doesn't belong, then type a name and press Enter, or click
  the suggestion chip once you've named that person before. Young siblings can
  share a group, so check the row before naming it. *Ignore* is for strangers and
  background faces. Children usually split into several groups by age; give each
  group the same name. A person's page shows their faces in date order, so one
  that doesn't belong stands out; that page is also where you set a birth year
  and merge duplicates. In photo detail, click a face box to name it, or use
  *＋ Draw face* for a face the detector missed.
- **Name suggestions:** once you've named people, unnamed faces are compared
  with the faces you named or confirmed, weighted toward photos from the same
  era. When the app is very sure, it names the face itself; these show as
  "Name?" with a dashed box. Otherwise it suggests a name, and faces that look
  under 6 are only ever suggested. Answer them under *People → Check* (click the
  wrong ones, then Confirm) or with Yes/No on a face box. "Not them" is
  remembered. Automatic names are never exported until you confirm them. The
  Jobs page can revert the names one run gave automatically; those faces then
  only get suggestions.
- **Measure accuracy** (Jobs) hides 20% of your confirmed dates and names,
  predicts them, and reports how close the app gets. It needs at least 5
  confirmed dates and 10 named faces.
- `R` / `Shift+R` rotates. `⌘Z` (or the Undo button) undoes the last action,
  including a bulk one, a drawn or deleted face box, and a person added by
  naming. If everything an action changed has been changed again since, Undo
  says so; press it again to skip that action.
- Double-click a photo to see the front and back side by side; `←` `→` move
  through the album.
- Possible duplicates (rescans) are flagged **D**. Tags are shared across a
  duplicate group, and *Keep this one* picks which file is exported.
- **Export** writes to `BACKUP_ROOT/export/<year>/`.

## Where data lives

| Layer | Location | Contents |
|---|---|---|
| Live | Docker volume `shoebox-data` | SQLite DB, thumbnails |
| Permanent | `BACKUP_ROOT/db/` | `VACUUM INTO` snapshots after every job (the newest 10, then one per hour, day, week and month going back a year) and before schema upgrades (never deleted automatically) |
| Permanent | `BACKUP_ROOT/truth/truth.json` | Everything you entered, keyed by file hash; `history/` keeps older versions |
| Permanent | `BACKUP_ROOT/manifest-sha256.txt` | Checksums of every original |
| Output | `BACKUP_ROOT/export/` | Dated copies with embedded metadata |
| Output | `BACKUP_ROOT/sidecars/` | `file.jpg.xmp` sidecars mirroring the photo tree |

- `docker compose down -v` **deletes the live volume**, and so does Docker
  Desktop's *Clean / Purge data*. To rebuild: start the stack, run **Import**,
  wait for it to finish, then click **Restore from truth.json** on the Jobs page. Until you
  restore, the new empty database leaves `truth.json` alone. Restore reads
  `truth.json`, or the newest copy in `truth/history/` that has entries, and
  stops with an error if nothing in it matches your imported scans. To start
  over on purpose instead, move `truth.json` out of the way first.
- Or copy a snapshot from `BACKUP_ROOT/db/` into the volume as `shoebox.db`
  (with the stack stopped) to get back the whole database as it was then.
- Verify originals without the app:
  ```bash
  cd "$PHOTO_ROOT" && shasum -a 256 -c "$BACKUP_ROOT/manifest-sha256.txt"
  ```
  The app also re-checks them weekly and before every export, and won't export a
  changed original.
- **The DB backups are not a backup of your scans.** Keep the originals under
  their own 3-2-1 backup.

## Viewing the exports

- **Apple Photos / Google Photos:** import the `export/` folder. Dates, ordering
  within a day, descriptions (and keywords in Apple Photos) carry over. Neither
  app imports face names from metadata.
- **Immich:** add `export/` as an external library; named faces are written as MWG regions
  (with `PersonInImage` and `People|Name` keywords), which Immich reads. Alternatively, keep the
  originals as the library and copy the sidecars next to them:
  ```bash
  rsync -a --include='*/' --include='*.xmp' --exclude='*' "$BACKUP_ROOT/sidecars/" "$PHOTO_ROOT/"
  ```
  (This writes into your photo folder, so only do it if you want that.)
- Year-only dates are written as July 1 (decades as Jan 1), and the true precision
  is kept in `XMP-photoshop:DateCreated` (e.g. `1987`). Photos from the same date
  get consecutive seconds from 09:00:00, so apps keep album order. Estimated
  dates also get the keyword `date:estimated`.

## Development

```bash
# tests (run in the container; exiftool and the face models live there)
docker build -t shoebox:latest . && docker run --rm -v "$PWD/backend":/app/backend -v "$PWD/tests":/app/tests shoebox:latest python -m pytest -q

# schema change: edit models.py, then
docker run --rm -v "$PWD":/app -w /app -e SHOEBOX_DATA_DIR=/tmp/d shoebox:latest alembic revision --autogenerate -m "describe change"

# API types for the UI after changing api.py
docker run --rm shoebox:latest python -m shoebox.cli openapi > openapi.json && (cd frontend && npm run types)
```

Test images are synthetic, and test file names are made up. Never commit real
family photos, names or paths.

The server only answers to `localhost` / `127.0.0.1`, and only accepts changes
(anything but GET) that carry the `X-Shoebox: 1` header, which the UI sends. A web
page open in another tab can't add that header, so it can't drive the app.

## Licensing note

The InsightFace `buffalo_l` models baked into the image are licensed for
**non-commercial use only**. That's fine for personal use, but don't push this
image to a public registry.
