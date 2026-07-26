# Changelog

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