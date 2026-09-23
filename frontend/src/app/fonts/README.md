# Studio fonts

These assets are self-hosted through `next/font/local`; builds and page loads do not
request fonts from Google or another external font service.

Source: the official [Google Fonts repository](https://github.com/google/fonts),
pinned at commit `e44c4b011a820c2cbe2fd2cfa8052037d7edb571`.

| Local asset | Official upstream file | Weight | Bytes |
| --- | --- | --- | ---: |
| BebasNeue-Regular.woff2 | `ofl/bebasneue/BebasNeue-Regular.ttf` | 400 | 21,548 |
| Barlow-Regular.woff2 | `ofl/barlow/Barlow-Regular.ttf` | 400 | 38,456 |
| Barlow-Medium.woff2 | `ofl/barlow/Barlow-Medium.ttf` | 500 | 38,556 |
| Barlow-SemiBold.woff2 | `ofl/barlow/Barlow-SemiBold.ttf` | 600 | 39,788 |
| Barlow-Bold.woff2 | `ofl/barlow/Barlow-Bold.ttf` | 700 | 39,700 |

The TTF files were converted to WOFF2 with `fontTools.ttLib.TTFont` and Brotli
(`font.flavor = "woff2"`; `recalcTimestamp=False`). All source glyphs, font names,
and copyright metadata were retained. No subsetting or outline editing was used.
The five font assets total 178,048 bytes (approximately 174 KiB).

## Language support

Barlow's four weights were verified with their Unicode `cmap` tables against
Vietnamese letters, the `U+1EA0–U+1EF9` tone-letter range, and combining marks
`U+0300`, `U+0301`, `U+0302`, `U+0303`, `U+0306`, `U+0309`, `U+031B`, and
`U+0323`. All tested characters are present.

Bebas Neue does **not** provide complete Vietnamese support: it lacks `Ơ`, `ơ`,
`Ư`, `ư`, and most Vietnamese tone letters. Use Bebas Neue for supported English
display text, and Barlow for complete Vietnamese headings and body copy. Keeping
whole Vietnamese headings in Barlow avoids mixing different typefaces within a
word. Put Barlow after Bebas Neue in any display fallback stack.

## Licenses and provenance

Both families use the SIL Open Font License 1.1. Their original copyright notices
and licenses are retained in `bebasneue-OFL.txt` and `barlow-OFL.txt`.

Original upstream TTF SHA-256 checksums:

```text
08e4623805102d819f58601e46e345648846075e363b2ceb23313c2d1c83ec73  BebasNeue-Regular.ttf
95aa02c7c43096e0dd44d787ba6216864a67157e402adab59b35572e0c1577ea  Barlow-Regular.ttf
f8906f762cb73dca441da034bc363b2d8e2e68bc10d5c05e58717646c20cc4b4  Barlow-Medium.ttf
86577cb32f8abe3673db53ca0f4221e6856751a4f6730c867e00f720f8bb1fc5  Barlow-SemiBold.ttf
84e6a4d61e7c3e21f3c50ea6a4f7e5303a3467864c038be6ea3759bab8d547f9  Barlow-Bold.ttf
```
