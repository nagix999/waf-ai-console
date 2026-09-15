"""Reproducible web font conversion; all glyphs kept, OFL reserved names changed.

Requires fonttools[woff] only when regenerating these checked-in assets.
Original fonts and OFL-Nanum.txt are retained unchanged.
"""
from pathlib import Path
from fontTools.ttLib import TTFont

root = Path(__file__).resolve().parents[1] / "backend/app/assets/fonts"
for source, style in (("NanumGothic.ttf", "Regular"), ("NanumGothicBold.ttf", "Bold")):
    font = TTFont(root / source)
    names = {1: "Waf Report", 2: style, 3: f"WafReport-{style}-1.0", 4: f"Waf Report {style}",
             6: f"WafReport-{style}", 16: "Waf Report", 17: style}
    for record in font["name"].names:
        if record.nameID in names:
            record.string = names[record.nameID].encode(record.getEncoding())
    font.flavor = "woff2"
    font.save(root / f"WafReport-{style}.woff2")
