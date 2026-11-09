#!/usr/bin/env bash
# Regenerate the translation template and merge it into every .po file.
# Run it after changing user-visible strings in src/.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
xgettext --language=Python --from-code=UTF-8 \
    --keyword=_ --keyword=N_ --keyword=ngettext:1,2 \
    --add-comments=TRANSLATORS --package-name=kbd-light \
    -o kbd-light.pot ../src/*.py
for lang in $(cat LINGUAS); do
    msgmerge --quiet --update --backup=none "$lang.po" kbd-light.pot
    printf '%s: ' "$lang"
    msgfmt --check --statistics -o /dev/null "$lang.po"
done
