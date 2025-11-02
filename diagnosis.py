#!/usr/bin/env python3
"""
Diagnosis: Audit tag assignment and page inclusion.

Usage examples:
  source .venv/bin/activate
  python diagnosis.py                   # human-readable output
  python diagnosis.py --json > audit.json  # JSON output
  python diagnosis.py --sample-limit 50

Notes:
- Read-only: does not modify the database.
- Runs inside Django context (uses vdw_server.settings).
"""

import os
import sys
import json
import argparse
from collections import defaultdict, Counter

# Django setup
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'vdw_server.settings')
import django  # noqa: E402

django.setup()

from django.db.models import Count, Q  # noqa: E402
from django.utils.text import slugify  # noqa: E402
from pages.models import Page  # noqa: E402
from tags.models import Tag  # noqa: E402


# Keep in sync with conversion_md_to_db.DISALLOWED_TAG_NAMES
DISALLOWED_TAG_NAMES = {
    'ai',
    'top news',
    'z',
    'z-section',
    'video page names',
    'old name',
}


def parse_front_matter_text(text):
    if not text:
        return None, None
    try:
        return json.loads(text), None
    except Exception as e:
        return None, f'front_matter JSON parse error: {e}'


def normalize_collection(value, key):
    """Normalize tags/categories from front_matter to a list of strings.

    Returns (names, anomaly_message_or_None).
    """
    names = []
    anomaly = None
    if value is None:
        return names, anomaly
    if isinstance(value, list):
        iterlist = value
    elif isinstance(value, str):
        anomaly = f"{key} is string (should be list)"
        iterlist = [value]
    else:
        anomaly = f"{key} is {type(value).__name__} (should be list)"
        iterlist = []
    for item in iterlist:
        if isinstance(item, str):
            s = item.strip()
            if s:
                names.append(s)
        else:
            anomaly = anomaly or f"{key} contains non-string item {type(item).__name__}"
    return names, anomaly


def audit(sample_limit=50, top_limit=20, as_json=False):
    summary = {}

    total_pages = Page.objects.count()
    total_published = Page.objects.filter(status='published').count()
    total_drafts = Page.objects.filter(status='draft').count()
    total_tags = Tag.objects.count()

    tags_unused_qs = Tag.objects.annotate(page_count=Count('pages')).filter(page_count=0)
    tags_zero_published_qs = Tag.objects.annotate(
        pub_count=Count('pages', filter=Q(pages__status='published'))
    ).filter(pub_count=0)

    summary['counts'] = {
        'total_pages': total_pages,
        'published_pages': total_published,
        'draft_pages': total_drafts,
        'total_tags': total_tags,
        'unused_tags_total': tags_unused_qs.count(),
        'tags_with_zero_published_total': tags_zero_published_qs.count(),
    }

    # Admin-only impact
    admin_tag = Tag.objects.filter(name__iexact='admin only').first()
    admin_info = {'tag_exists': bool(admin_tag)}
    if admin_tag:
        admin_pages_all = Page.objects.filter(tags=admin_tag)
        admin_info['pages_with_admin_only_tag'] = admin_pages_all.count()
        admin_info['drafts_with_admin_only_tag'] = admin_pages_all.filter(status='draft').count()
        admin_info['published_with_admin_only_tag'] = admin_pages_all.filter(status='published').count()
        admin_info['anomalies'] = {
            'published_but_admin_only_tag': list(
                admin_pages_all.filter(status='published').values_list('slug', flat=True)[:sample_limit]
            ),
            'draft_without_admin_only_tag': list(
                Page.objects.filter(status='draft')
                .exclude(tags=admin_tag)
                .values_list('slug', flat=True)[:sample_limit]
            ),
        }
    summary['admin_only'] = admin_info

    # Tag usage distribution
    top_tags = (
        Tag.objects.annotate(pub_count=Count('pages', filter=Q(pages__status='published')))
        .filter(pub_count__gt=0)
        .order_by('-pub_count')[:top_limit]
        .values('name', 'slug', 'pub_count')
    )
    summary['tags'] = {
        'top_tags_by_published': list(top_tags),
        'unused_tags_sample': list(tags_unused_qs.values('name', 'slug')[:sample_limit]),
        'tags_with_zero_published_sample': list(tags_zero_published_qs.values('name', 'slug')[:sample_limit]),
    }

    # Inclusion: pages currently excluded from All Pages due to status
    summary['inclusion'] = {
        'draft_pages_sample': list(
            Page.objects.filter(status='draft').values_list('slug', flat=True)[:sample_limit]
        )
    }

    # Per-page frontmatter and assignment checks
    fm_anomalies = {
        'invalid_json': [],
        'tags_is_string': [],
        'categories_is_string': [],
        'non_string_items': [],
    }

    pages_with_fm_tags_but_zero_assigned = []
    missing_reasons_counter = Counter()
    missing_samples = []
    extra_assignments_samples = []
    collapsed_slug_variants_samples = []

    pages = Page.objects.all().prefetch_related('tags')
    for page in pages:
        assigned = list(page.tags.all())
        assigned_slugs = {t.slug for t in assigned}

        fm, fm_err = parse_front_matter_text(page.front_matter)
        if fm_err:
            if len(fm_anomalies['invalid_json']) < sample_limit:
                fm_anomalies['invalid_json'].append({'slug': page.slug, 'error': fm_err})
            continue
        if not fm:
            # No frontmatter stored (e.g., admin-created). Skip per-FM checks.
            continue

        fm_tags_raw = fm.get('tags')
        fm_cats_raw = fm.get('categories')

        fm_tags, anomaly_t = normalize_collection(fm_tags_raw, 'tags')
        fm_cats, anomaly_c = normalize_collection(fm_cats_raw, 'categories')

        if anomaly_t and 'string' in anomaly_t and len(fm_anomalies['tags_is_string']) < sample_limit:
            fm_anomalies['tags_is_string'].append({'slug': page.slug, 'detail': anomaly_t})
        if anomaly_c and 'string' in anomaly_c and len(fm_anomalies['categories_is_string']) < sample_limit:
            fm_anomalies['categories_is_string'].append({'slug': page.slug, 'detail': anomaly_c})
        non_string_issue = (anomaly_t and 'non-string' in anomaly_t) or (anomaly_c and 'non-string' in anomaly_c)
        if non_string_issue and len(fm_anomalies['non_string_items']) < sample_limit:
            fm_anomalies['non_string_items'].append({'slug': page.slug})

        fm_names = [*fm_tags, *fm_cats]
        if not fm_names:
            # Nothing to compare
            continue

        # Map FM names to slugs to detect collapsed variants
        fm_slug_map = defaultdict(list)
        for name in fm_names:
            fm_slug_map[slugify(name)].append(name)

        # Collapsed names (distinct names mapping to same slug)
        collapsed = {s: names for s, names in fm_slug_map.items() if len(set(names)) > 1}
        if collapsed and len(collapsed_slug_variants_samples) < sample_limit:
            collapsed_slug_variants_samples.append({'page': page.slug, 'collapsed': collapsed})

        fm_slugs = set(fm_slug_map.keys())
        missing_slugs = fm_slugs - assigned_slugs
        extra_slugs = assigned_slugs - fm_slugs

        if missing_slugs and len(assigned) == 0:
            if len(pages_with_fm_tags_but_zero_assigned) < sample_limit:
                pages_with_fm_tags_but_zero_assigned.append(page.slug)

        page_missing_records = []
        for mslug in missing_slugs:
            names_for_slug = fm_slug_map[mslug]
            reasons = []
            if any(n.lower() in DISALLOWED_TAG_NAMES for n in names_for_slug):
                reasons.append('disallowed')
            elif Tag.objects.filter(slug=mslug).exists():
                reasons.append('exists_in_db_but_not_assigned')
            else:
                reasons.append('no_tag_created')
            reason_key = ','.join(reasons)
            missing_reasons_counter[reason_key] += 1
            if len(missing_samples) < sample_limit:
                page_missing_records.append({'slug': mslug, 'names': names_for_slug, 'reason': reason_key})

        if page_missing_records and len(missing_samples) < sample_limit:
            missing_samples.append({'page': page.slug, 'missing': page_missing_records})

        if extra_slugs and len(extra_assignments_samples) < sample_limit:
            extra_assignments_samples.append({
                'page': page.slug,
                'extra_assigned_slugs_not_in_frontmatter': sorted(extra_slugs),
            })

    summary['frontmatter_anomalies'] = fm_anomalies
    summary['assignment_anomalies'] = {
        'pages_with_frontmatter_tags_but_zero_assigned_sample': pages_with_fm_tags_but_zero_assigned,
        'missing_assignments_by_reason': dict(missing_reasons_counter),
        'missing_samples': missing_samples,
        'extra_assigned_not_in_frontmatter_samples': extra_assignments_samples,
        'collapsed_frontmatter_names_by_slug_samples': collapsed_slug_variants_samples,
    }

    # Output
    if as_json:
        print(json.dumps(summary, indent=2, default=str))
        return

    # Human-readable summary
    print("=== COUNTS ===")
    for k, v in summary['counts'].items():
        print(f"{k}: {v}")

    print("\n=== ADMIN ONLY ===")
    print(json.dumps(summary['admin_only'], indent=2, default=str))

    print("\n=== TAGS ===")
    print("Top tags by published count:")
    print(json.dumps(summary['tags']['top_tags_by_published'], indent=2))
    print("Unused tags sample:")
    print(json.dumps(summary['tags']['unused_tags_sample'], indent=2))
    print("Tags with zero published sample:")
    print(json.dumps(summary['tags']['tags_with_zero_published_sample'], indent=2))

    print("\n=== INCLUSION (DRAFTS) ===")
    print(json.dumps(summary['inclusion'], indent=2))

    print("\n=== FRONTMATTER ANOMALIES ===")
    for k, v in summary['frontmatter_anomalies'].items():
        print(f"{k}: {json.dumps(v, indent=2)}")

    print("\n=== ASSIGNMENT ANOMALIES ===")
    print("Pages with frontmatter tags/categories but zero assigned tags (sample):")
    print(json.dumps(summary['assignment_anomalies']['pages_with_frontmatter_tags_but_zero_assigned_sample'], indent=2))
    print("Missing assignments by reason:")
    print(json.dumps(summary['assignment_anomalies']['missing_assignments_by_reason'], indent=2))
    print("Missing samples:")
    print(json.dumps(summary['assignment_anomalies']['missing_samples'], indent=2))
    print("Extra assigned tags not in frontmatter (sample):")
    print(json.dumps(summary['assignment_anomalies']['extra_assigned_not_in_frontmatter_samples'], indent=2))
    print("Collapsed frontmatter names mapping to same slug (sample):")
    print(json.dumps(summary['assignment_anomalies']['collapsed_frontmatter_names_by_slug_samples'], indent=2))


def main():
    parser = argparse.ArgumentParser(description="Audit tag assignment and page inclusion (read-only).")
    parser.add_argument('--sample-limit', type=int, default=50, help='Max sample items per anomaly list')
    parser.add_argument('--top-limit', type=int, default=20, help='How many top tags to list')
    parser.add_argument('--json', action='store_true', help='Output JSON instead of text')
    args = parser.parse_args()
    audit(sample_limit=args.sample_limit, top_limit=args.top_limit, as_json=args.json)


if __name__ == '__main__':
    main()

