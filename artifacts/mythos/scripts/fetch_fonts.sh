#!/bin/bash
# Fetch the OFL fonts the renderer uses into ./fonts (not committed).
set -e; cd "$(dirname "$0")/.."; mkdir -p fonts; cd fonts
B=https://github.com/google/fonts/raw/main
get() { [ -f "$2" ] || curl -sfL "$B/$1" -o "$2"; }
get "ofl/geistmono/GeistMono%5Bwght%5D.ttf" GeistMono.ttf; get ofl/anton/Anton-Regular.ttf Anton-Regular.ttf
get ofl/instrumentserif/InstrumentSerif-Regular.ttf InstrumentSerif-Regular.ttf; get ofl/instrumentserif/InstrumentSerif-Italic.ttf InstrumentSerif-Italic.ttf
get ofl/silkscreen/Silkscreen-Regular.ttf Silkscreen-Regular.ttf; get "ofl/doto/Doto%5BROND,wght%5D.ttf" Doto.ttf
get "ofl/bodonimoda/BodoniModa%5Bopsz,wght%5D.ttf" BodoniModa.ttf; get "ofl/intertight/InterTight%5Bwght%5D.ttf" InterTight.ttf
get ofl/vt323/VT323-Regular.ttf VT323-Regular.ttf; get ofl/librebarcode128/LibreBarcode128-Regular.ttf LibreBarcode128-Regular.ttf
get ofl/notosansrunic/NotoSansRunic-Regular.ttf NotoSansRunic-Regular.ttf; get ofl/unifrakturmaguntia/UnifrakturMaguntia-Book.ttf UnifrakturMaguntia-Book.ttf
get "ofl/notosanssymbols/NotoSansSymbols%5Bwght%5D.ttf" NotoSansSymbols.ttf; get ofl/notosansmath/NotoSansMath-Regular.ttf NotoSansMath-Regular.ttf
