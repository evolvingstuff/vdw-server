from django.core.paginator import InvalidPage, Page, Paginator
from django.http import Http404


def paginate_or_404(object_list, per_page: int, page_number) -> Page:
    """Like Paginator.get_page, but 404 instead of silently showing another page.

    Stops out-of-range or non-numeric ?page= values from creating endless duplicate URLs.
    """
    paginator = Paginator(object_list, per_page)
    try:
        return paginator.page(page_number or 1)
    except InvalidPage as exc:
        raise Http404(f"Invalid page {page_number!r}") from exc
