from django import template

from helper_functions.seo import meta_description_for

register = template.Library()


@register.filter
def meta_description(page) -> str:
    """Meta description for a Page or SitePage ("" when none can be generated)."""
    return meta_description_for(page)
