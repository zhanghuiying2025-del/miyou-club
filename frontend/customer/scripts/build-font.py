from pathlib import Path
from fontTools import subset

root = Path(__file__).resolve().parents[1]
text = ''.join(path.read_text(encoding='utf-8') for path in (root / 'src').glob('*.*'))
options = subset.Options()
options.flavor = 'woff2'
options.desubroutinize = True
font = subset.load_font('C:/Windows/Fonts/NotoSerifSC-VF.ttf', options)
subsetter = subset.Subsetter(options=options)
subsetter.populate(text=text)
subsetter.subset(font)
output = root / 'public/assets/miyo-serif.woff2'
subset.save_font(font, str(output), options)
print(f'Bundled display font: {output.stat().st_size // 1024} KB')
