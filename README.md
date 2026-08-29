Build a [slidershow](https://github.com/CZ-NIC/slidershow/) media presentation from a sheet.

Just put file names into the sheet and we generate the HTML file.

```mermaid
graph LR;
sheet --> slidershow-builder --> presentation.html
```


# Features

* Find files on disk, evade hassling with the relative paths.

Ex. You just use the file name from Google Photos, slidershow-builder will find an original file within the given directories scope.

* auto-conversion

Some formats cannot be played in the browser; slidershow-builder will automatically creates a cache folder with the mp4 files, playable everywhere.

* gather the originals first

Photos are usually scattered over several disks and Google Photos only ever gives you a
file *name*. `collect` turns a list of people and/or days into one folder of symlinks —
no data duplicated, nothing written into the originals — with HEIC and unplayable video
converted on the way in, so the folder is displayable as it stands.

```bash
slidershow-builder people --takeout-dir /tmp          # who is on which photo
slidershow-builder collect ./trip --search-dirs ~/Photos /mnt/backup \
    --names "Jan Novák" --whole-day --date-ranges 2026-08-05:2026-08-07
```

* or skip straight to a presentation

`build` accepts the same `--names`/`--date-ranges`/`--search-dirs`/`--whole-day` selection as
`collect`, so a slideshow for a few friends is one command — no symlink folder, no sheet to
write by hand. Photos land chronologically, split into a `<section>` per day with a date
title frame.

```bash
slidershow-builder build --search-dirs ~/Photos /mnt/backup \
    --names "Jan Novák" "Petra Malá" --whole-day --output trip.html
```

Pass `--people-mode intersection` to keep only photos where *all* the named people appear
together (default `union`: anyone of them is enough). `--dump-sheet trip.ods` writes the
resolved, day-sectioned list as a sheet instead of rendering HTML, so it can be hand-tuned
(zoom points, video commands, wording) before a normal `build --file trip.ods`.

# Installation

```bash
pip install slidershow-builder
```
# Sheet

Format of the sheet

## Columns

`comment   filename	start	commands`

Comment is an inserted HTML comment, displayed at the presenter's notes.

## Text frame
* filename: <empty>
* start: header
* commands: subtitle

## Image frame
start: point
`[left = 0, top = 0, scale = 1, transition_duration = 0, duration = 0, data-rotate = 0]`

Ex: `[[], [100,100,5]]` will begin unzoomed, on the next frame we zoom to 5.

See: https://github.com/CZ-NIC/slidershow/?tab=readme-ov-file#data-step-points

How to get the point? Go to the slidershow in the browser, open properties Alt+p and click on the new point.

## Video frame

start
* video start time, empty = 0:00

commands

* number is a timestamp, jehož akce určují následující buňky
* posouvací šipka, ex: `→60.5` skočí z momentu na 60.5
* rate a číslo změní rate momentu `rate 2`
* mute, unmute
* R+number(M|U): rate. Ex: `R2` = rate 2, `R4M` = rate 4 + mute
* P = rate 1, unmute
* F+number: faster rate. Ex `F2` = rate 1.2
* comma character behaves like a cell-separator, these are independent commands `rate 2, unmute` → `rate 2` a `unmute`
* poslední osamělé číslo je end
* point command zooms, ex: `point:[0,0,2,null,null,270]` zoom and rotate. (Point musí být v buňce zvlášť.)

Ex: `15, → 4, 1:10`: At 0:15, jump to 0:04, then end at 1:10.

## Section break
comment: SECTION

If the row starts with the word "SECTION", a new `<section>` is inserted. (And the row is skipped.)

## Rows

Parsing ends on the first empty row.

# People index

Which people are on which photo — the thing `collect --names` selects by. Kept in
`~/.local/share/slidershow_builder/people.csv` (`--index` for another path).

## Columns

`name,filename,taken`

| column | | |
|---|---|---|
| `name` | person's name | Exactly as `--names` will spell it. Free text; `slidershow-builder people` lists what the index holds. |
| `filename` | base name of the original | No path — the whole point is that Google Photos does not tell you where the file lives; `collect` finds it under `--search-dirs`. Matched case-insensitively. |
| `taken` | ISO capture time, or empty | Only `--whole-day` reads it (to know which day to pull the rest of). Empty is fine otherwise. |

```csv
name,filename,taken
Jan Novák,IMG_0001.jpg,2026-08-05T10:00:00+00:00
Jan Novák,IMG_0004.heic,2026-08-09T09:00:00+00:00
Petra Malá,IMG_0001.jpg,2026-08-05T10:00:00+00:00
```

One row per person **per photo**: a photo with three people tagged is three rows. Rows are
sorted and deduplicated on write; order in the file carries no meaning.

## Rows

The file is read whole — unlike the sheet, an empty line does not stop parsing.

## Where it comes from

Nothing above is Google-specific, so write the file by hand or export it from another photo
manager if you like. Google Takeout is currently the only *importer*, because the Google
Photos API does not expose face tags at all and Takeout has no metadata-only export:
`--takeout-dir` reads the `.json` sidecars straight out of the downloaded zip(s), never
extracting a photo. Re-running merges into the existing index instead of overwriting it, so
several Takeout exports can be accumulated.

Note a photo that also sits in an album is exported twice by Takeout (once under
`Photos from <year>/`, once under the album), so one person can be counted twice under two
different file names.

# Subcommands

```
slidershow-builder [build] --file x.ods   # sheet -> presentation HTML (implicit default)
slidershow-builder build --dir folder     # ... or straight from a folder of media
slidershow-builder build --names "..."    # ... or straight from a list of people
slidershow-builder people                 # who is on which photo (index for `collect --names`)
slidershow-builder collect DEST           # symlink the matching originals into one folder
slidershow-builder previews SOURCE        # thumbnails + fallback conversions for a media tree
slidershow-builder fix-mtime SOURCE       # set mtime from EXIF/ffprobe capture time
slidershow-builder probe FILE             # debug: codec, browser compatibility, capture time
```

Any of them takes `--config <yaml>` instead of a long command line; put the options under
the subcommand's name. `collect` and `build --names` share the same person/date-selection
fields (`search_dirs`, `names`, `date_ranges`, `whole_day`, `index`), so one file can hold
both — handy for a recurring event: `collect` once for a symlink folder to poke through by
hand, `build` straight to HTML for a quick re-render once someone gets added or dropped from
`names`:

```yaml
collect:
  dest: ./trip/symlinks
  index: ./trip/people.csv
  incompatible: link
  search_dirs: [/home/user/Photos, /mnt/backup]
  names: ["Jan Novák", "Petra Malá"]
  date_ranges: ["2026-08-05:2026-08-07"]

build:
  index: ./trip/people.csv
  search_dirs: [/home/user/Photos, /mnt/backup]
  names: ["Jan Novák", "Petra Malá"]
  whole_day: true
  output: trip.html
```

```bash
slidershow-builder collect --config trip.yaml
slidershow-builder build --config trip.yaml
```

## Browser-incompatible files

`collect --incompatible` decides what happens to HEIC/HEIF photos and videos in codecs no
browser plays:

| | |
|---|---|
| `replace` (default) | a converted `IMG_1234.heic.jpg` / `VID.mov.mp4` goes into DEST **instead of** the symlink — drop the folder into slidershow and it just works |
| `fallback` | the original is symlinked and the conversion goes to `--fallback-dir` (default `DEST/.fallback`), the layout slidershow's `sli-fallback` attribute expects |
| `link` | symlink only |

## Thumbnails and fallbacks in the generated HTML

`build` can point the presentation at what `collect`/`previews` produced, so a big photo
shows a thumbnail while it downloads and a HEIC has something to fall back to:

```bash
slidershow-builder previews ./trip --preview-dir ./thumbs --fallback-dir ./fb
slidershow-builder build --file trip.ods --output trip.html \
    --slidershow.thumb thumbs --slidershow.fallback fb
```

```html
<main data-start sli-thumb="thumbs/{file}.webp" sli-fallback="fb/{file}.jpg fb/{file}.mp4">
```

A bare prefix is expanded to that layout; pass anything containing `{` to write the template
yourself (placeholders `{dir} {file} {name} {ext}`). Both layouts key on the file's *name*,
so they line up when the media sit in one folder — which is what `collect` gives you.

# Usage

Run `slidershow-builder <subcommand> --help`; every option is a field of a dataclass in
`slidershow_builder/_lib/env.py` with its docstring as the help text.
