# Changelog

## 0.7.0 (2026-08-29)

feat: `collect` and `people` — the `tools/photos-people/` scripts folded into the CLI, where
they can reuse the machinery that was already here (recursive name lookup, `media.capture_time`,
`sync_tree`). Concretely better than the standalone scripts: capture time now comes from HEIC
EXIF (pillow-heif) and from a video's container `creation_time` (ffprobe), where the old
PIL-only reader silently fell back to mtime for both.

```
slidershow-builder people --takeout-dir /tmp
slidershow-builder collect ./trip --search-dirs ~/Photos /mnt/backup --names "Jan Novák" --whole-day
```

* `collect --incompatible replace` (the default) writes a converted `IMG.heic.jpg` / `VID.mov.mp4`
  into the destination instead of the symlink, so the collected folder is displayable as it
  stands; `fallback` produces the `sli-fallback` layout via `sync_tree` instead; `link` neither.
* the people index moved to a documented, service-neutral `name,filename,taken` CSV in
  `~/.local/share/slidershow_builder/`; Google Takeout is one importer, not the format
* `jobs.yaml` is gone in favour of mininterface's own `--config <yaml>`, keyed by subcommand,
  which now works for every subcommand
* defaults no longer resolve next to the source file (unwritable once pip-installed) —
  `_lib/paths.py` centralizes the XDG cache/data dirs

fix: **the generated HTML used attribute names slidershow no longer reads.** Frames were
emitted as `data-src`/`data-step-points`/`data-video-points`/`data-datetime`/`data-device`/
`data-gps` and `<main data-start>`, but every released slidershow resolves properties as
`sli-<property>` only (`prop()` → `closest("[sli-…]")`, verified against the 1.2.0 bundle on
the CDN) — so a built presentation showed nothing at all. All of them are now `sli-*`.

feat: `build --dir FOLDER` builds a presentation straight from a folder of media, one frame
per file ordered by name, no spreadsheet needed — the other half of `collect`, whose output is
exactly such a folder. `--file` and `--dir` are mutually exclusive and one is required.

feat: `previews --fallback-kinds {both,photo,video}` restricts fallback generation to photos
or videos. Transcoding video costs orders of magnitude more than converting a photo (measured
on a 11.9k-file tree: 2222 HEIC → 4.6 GB in ~17 min, versus ~1160 HEVC videos → ~60 GB in
~7 h), so the cheap half must be doable on its own.

fix: `read_exif` no longer aborts the run on a file whose EXIF cannot be parsed — `piexif`
handles JPEG/TIFF only, so every HEIC raised, and a truncated JPEG could too.

feat: `build` fills in slidershow's `sli-thumb`/`sli-fallback` (`--slidershow.thumb`,
`--slidershow.fallback`), which it never did — the presentation can finally use the
thumbnails and fallback conversions `previews`/`collect` generate, the way
upload.edvard.cz's template does by hand. A bare prefix expands to that layout, a value
with `{` is passed through verbatim. Output without the new options is unchanged.

fix: **`fix_mtime` no longer writes through a symlink.** It stamps the link's own mtime
(`follow_symlinks=False`) and leaves the target alone, so `previews --fix-mtime` over a
collected tree can never rewrite the mtime of the originals it points at. Regular files are
unaffected, so upload.edvard.cz's cron behaves exactly as before.

## 0.6.2 (2026-07-27)

fix: `previews` (tree sync) now reaches parity with the `previews.php` cron it replaces,
verified against the live upload.edvard.cz tree (~3000 files, zero regenerated/orphaned):

* `MirrorLayout` **appends** the suffix (`a.heic` → `a.heic.webp`) instead of substituting it,
  so `a.jpg` and `a.png` no longer collide and existing caches stay valid
* HEIC/HEIF photos get their JPEG fallback again (`probe()` used to call every photo
  browser-compatible)
* the ffprobe codec parser paired `codec_name`/`codec_type` wrongly, so every video looked
  incompatible and got needlessly transcoded
* container `creation_time` (UTC) is converted to local time before being written as mtime
* idle runs are cheap: `.fail` markers, a shared `.compatible-videos.json` verdict cache and
  extension-only classification mean a fully cached tree costs 0.8 s and zero ffprobe calls
* conversions are atomic (`.part` + rename), leftovers of a crashed run are collected
* `.psd`/multi-frame images no longer fail on a needless `seek(0)`

## 0.6.1 (2026-07-26)

enh: `ezodf`/`lxml`/`jinja2`/`piexif` (needed only for `build`) and `pillow`/`pillow-heif`
(needed only for `previews`/`fix-mtime`/`probe` and HEIC/HEVC auto-conversion) are now
optional extras (`pip install slidershow_builder[build]` / `[media]` / `[all]`) instead of
hard dependencies. Missing an extra no longer breaks the whole CLI — it's only reported,
with the install command to run, at the point a command actually needs it.

## 0.6.0 (2026-07-26)

Working as expected.