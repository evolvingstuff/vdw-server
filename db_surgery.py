#!/usr/bin/env python3
"""db_surgery.py

One-off scripts for "surgery" on the SQLite DB.

Currently implemented:
- diagnose: read-only audit that compares JSON `page_id` values against `posts_post`'s
  Tiki linkage field (auto-detected).

This script is designed to FAIL FAST AND LOUD.

Usage:
  .venv/bin/python db_surgery.py diagnose
  .venv/bin/python db_surgery.py diagnose --db db.sqlite3 --json ../vdw-external-data/tiki_pages_2025-10-03.json

Outputs:
  Writes TSV reports into `tmp/` by default.
"""

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any, Iterable


def iter_json_array_objects(path: Path, *, chunk_size: int = 2 * 1024 * 1024) -> Iterable[dict[str, Any]]:
    decoder = json.JSONDecoder()
    with path.open('r', encoding='utf-8') as f:
        buf = ''

        def refill() -> None:
            nonlocal buf
            chunk = f.read(chunk_size)
            if not chunk:
                return
            buf += chunk

        refill()
        buf = buf.lstrip()
        if not buf:
            raise ValueError(f"Empty JSON file: {path}")
        if buf[0] != '[':
            raise ValueError(f"Expected JSON array at top-level in {path}")
        buf = buf[1:]

        while True:
            buf = buf.lstrip()
            while not buf:
                refill()
                if not buf:
                    raise ValueError(f"Unexpected EOF while parsing {path}")
                buf = buf.lstrip()

            if buf[0] == ']':
                return
            if buf[0] == ',':
                buf = buf[1:]
                continue

            while True:
                try:
                    obj, end = decoder.raw_decode(buf)
                except json.JSONDecodeError:
                    before = len(buf)
                    refill()
                    if len(buf) == before:
                        raise
                    continue

                if not isinstance(obj, dict):
                    raise TypeError(f"Expected objects in JSON array; got {type(obj).__name__}")
                buf = buf[end:]
                yield obj
                break


def _require_file(path: Path, label: str) -> None:
    if not path.exists():
        raise FileNotFoundError(f"{label} not found: {path.resolve()}")
    if not path.is_file():
        raise ValueError(f"{label} is not a file: {path.resolve()}")


def _normalize_int_like(value: Any, *, label: str) -> int:
    if isinstance(value, bool):
        raise TypeError(f"{label} must be int-like; got bool")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    raise TypeError(f"{label} must be int-like; got {value!r} ({type(value).__name__})")


def diagnose(
    *,
    db_path: Path,
    json_path: Path,
    out_dir: Path,
    table: str,
    link_field: str,
    json_page_id_key: str,
    json_page_name_key: str,
) -> int:
    _require_file(db_path, 'DB')
    _require_file(json_path, 'JSON')

    out_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        conn.execute('PRAGMA query_only = ON')
        cur = conn.cursor()

        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
        if cur.fetchone() is None:
            raise RuntimeError(f"Expected table {table} in {db_path}")

        cur.execute(f"PRAGMA table_info({table})")
        columns = {row[1] for row in cur.fetchall()}
        if 'modified_date' not in columns:
            raise RuntimeError(f"Expected column {table}.modified_date")

        effective_link_field = link_field
        if not effective_link_field:
            effective_link_field = 'tiki_page_id' if 'tiki_page_id' in columns else 'original_page_id'

        if effective_link_field not in columns:
            raise RuntimeError(
                f"Expected link field in {table}: tried {effective_link_field!r} but it is missing. "
                f"Available columns include: {sorted(columns)}"
            )

        cur.execute(
            f"SELECT {effective_link_field} FROM {table} WHERE {effective_link_field} IS NOT NULL"
        )
        db_link_values_raw = [row[0] for row in cur.fetchall()]
        db_link_values = [_normalize_int_like(v, label=f"{table}.{effective_link_field}") for v in db_link_values_raw]
        db_link_set = set(db_link_values)

        missing_json_tsv = out_dir / 'tiki_modified_date_diagnosis.json_missing_in_posts_post.tsv'
        db_missing_in_json_tsv = out_dir / 'tiki_modified_date_diagnosis.db_missing_in_json.tsv'

        total_json_objects = 0
        missing_json_objects: list[tuple[int, str]] = []
        json_ids_set: set[int] = set()

        for obj in iter_json_array_objects(json_path):
            total_json_objects += 1
            if json_page_id_key not in obj:
                raise KeyError(
                    f"Missing required key {json_page_id_key!r} in JSON entry #{total_json_objects}"
                )
            page_id = _normalize_int_like(obj[json_page_id_key], label=f"JSON.{json_page_id_key}")
            json_ids_set.add(page_id)

            if page_id not in db_link_set:
                page_name_value = obj.get(json_page_name_key, '')
                if page_name_value is None:
                    page_name = ''
                elif isinstance(page_name_value, str):
                    page_name = page_name_value
                else:
                    page_name = str(page_name_value)
                missing_json_objects.append((page_id, page_name))

        intersection_distinct = len(db_link_set.intersection(json_ids_set))
        matched_post_rows = sum(1 for v in db_link_values if v in json_ids_set)

        db_missing_in_json = sorted(db_link_set.difference(json_ids_set))

        with missing_json_tsv.open('w', encoding='utf-8') as f:
            f.write('page_id\tpageName\n')
            for page_id, page_name in missing_json_objects:
                f.write(f"{page_id}\t{page_name}\n")

        with db_missing_in_json_tsv.open('w', encoding='utf-8') as f:
            f.write(f"{effective_link_field}\n")
            for value in db_missing_in_json:
                f.write(f"{value}\n")

        print('=== Tiki lastModif DB Update: DIAGNOSIS ONLY (no DB writes) ===')
        print(f'DB: {db_path.resolve()}')
        print(f'JSON: {json_path.resolve()} ({os.path.getsize(json_path):,} bytes)')
        print()

        if link_field:
            print(f'Link field used: {table}.{effective_link_field}')
        else:
            print(
                f'Link field auto-detected: {table}.{effective_link_field} '
                f"(note: {table}.tiki_page_id column {'present' if 'tiki_page_id' in columns else 'not present'} in this DB)"
            )
        print()

        print('1) Matches (JSON page_id -> DB link field):')
        print(f'  - Distinct DB link ids (non-null): {len(db_link_set):,}')
        print(f'  - JSON objects scanned: {total_json_objects:,}')
        print(f'  - Distinct matches (set intersection): {intersection_distinct:,}')
        print(f'  - Matched {table} rows (non-null link ids): {matched_post_rows:,}')
        print()

        print('2) JSON page_id entries NOT in DB table:')
        print(f'  - Count: {len(missing_json_objects):,}')
        if missing_json_objects:
            for page_id, page_name in missing_json_objects:
                print(f'  - {page_id}\t{page_name}')
        print(f'  - Full list (page_id, pageName): {missing_json_tsv}')
        print()

        print('3) DB link ids (non-null) NOT in JSON:')
        print(f'  - Count: {len(db_missing_in_json):,}')
        print(f'  - Full list: {db_missing_in_json_tsv}')

        if db_missing_in_json:
            print('\nWARNING: Found DB link ids not present in JSON. Inspect before doing updates.')

        return 0
    finally:
        conn.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='One-off DB surgery utilities (SQLite).')
    subparsers = parser.add_subparsers(dest='command', required=False)

    diagnose_parser = subparsers.add_parser(
        'diagnose',
        help='Read-only report comparing JSON page_id values vs posts_post link ids',
    )
    diagnose_parser.add_argument('--db', type=Path, default=Path('db.sqlite3'))
    diagnose_parser.add_argument(
        '--json',
        type=Path,
        default=Path('../vdw-external-data/tiki_pages_2025-10-03.json'),
    )
    diagnose_parser.add_argument('--out-dir', type=Path, default=Path('tmp'))
    diagnose_parser.add_argument('--table', type=str, default='posts_post')
    diagnose_parser.add_argument(
        '--link-field',
        type=str,
        default='',
        help='DB field to match against JSON page_id; empty means auto-detect',
    )
    diagnose_parser.add_argument('--json-page-id-key', type=str, default='page_id')
    diagnose_parser.add_argument('--json-page-name-key', type=str, default='pageName')

    return parser


def main(argv: list[str]) -> int:
    if len(argv) == 1:
        argv = [argv[0], 'diagnose']

    parser = build_parser()
    args = parser.parse_args(argv[1:])

    if args.command == 'diagnose':
        return diagnose(
            db_path=args.db,
            json_path=args.json,
            out_dir=args.out_dir,
            table=args.table,
            link_field=args.link_field,
            json_page_id_key=args.json_page_id_key,
            json_page_name_key=args.json_page_name_key,
        )

    raise RuntimeError(f"Unknown command: {args.command}")


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))

