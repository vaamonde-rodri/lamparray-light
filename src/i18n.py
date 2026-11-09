"""Translation helpers (gettext).

Compiled catalogs live next to this module in ``locale/<lang>/LC_MESSAGES``;
``install.sh`` builds them from ``po/*.po``. The language follows the usual
environment variables (LANGUAGE, LC_ALL, LC_MESSAGES, LANG) and falls back to
English when no catalog matches.
"""
import gettext
import os

DOMAIN = "kbd-light"
LOCALE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "locale")

_translation = gettext.translation(DOMAIN, LOCALE_DIR, fallback=True)
_ = _translation.gettext
ngettext = _translation.ngettext


def N_(message):
    """Mark a string for extraction without translating it yet."""
    return message
