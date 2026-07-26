# Changelog

## 0.6.1 (2026-07-26)

enh: `ezodf`/`lxml`/`jinja2`/`piexif` (needed only for `build`) and `pillow`/`pillow-heif`
(needed only for `previews`/`fix-mtime`/`probe` and HEIC/HEVC auto-conversion) are now
optional extras (`pip install slidershow_builder[build]` / `[media]` / `[all]`) instead of
hard dependencies. Missing an extra no longer breaks the whole CLI — it's only reported,
with the install command to run, at the point a command actually needs it.

## 0.6.0 (2026-07-26)

Working as expected.