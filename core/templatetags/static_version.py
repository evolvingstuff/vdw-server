"""{% versioned_static 'path' %}: a static URL with ?v=<file modification time>.

Static files are cached by browsers for a day (nginx), and their names don't change
between deploys, so CSS/JS changes used to appear only after a hard refresh. The version
query string changes whenever the file changes, so browsers fetch the new copy at once.
"""

from functools import lru_cache

from django import template
from django.contrib.staticfiles import finders
from django.templatetags.static import static

register = template.Library()


@lru_cache(maxsize=None)  # one stat per file per process; a deploy restarts the processes
def _version(path: str) -> str:
    found = finders.find(path)
    if not found:
        return ''
    import os
    return str(int(os.path.getmtime(found)))


@register.simple_tag
def versioned_static(path: str) -> str:
    version = _version(path)
    url = static(path)
    return f"{url}?v={version}" if version else url
