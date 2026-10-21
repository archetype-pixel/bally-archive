"""Export local verification records for joining with the shared asset register.

Reads SQLite in read-only mode. Does not upload, rehash, or modify archive data.
"""
import argparse
import datetime
import json
import pathlib
import sqlite3


def export(database):
    database = pathlib.Path(database).resolve(strict=True)
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as connection:
        connection.row_factory = sqlite3.Row
        originals = {r['sha256']: dict(r) for r in connection.execute(
            'SELECT sha256, path, bytes, filename, source_guid FROM originals')}
        assets = list(connection.execute('SELECT guid, body FROM media_assets ORDER BY guid'))
    rows = []
    unresolved = []
    for asset in assets:
        body = json.loads(asset['body'])
        digest = body.get('sha256') or body.get('original_sha256')
        original = originals.get(digest)
        if original is None:
            unresolved.append(asset['guid'])
            continue
        rows.append({
            'source_guid': asset['guid'],
            'filename': body.get('filename') or original['filename'],
            'sha256': original['sha256'],
            'bytes': original['bytes'],
            'local_verification_record': True,
        })
    return {
        'exported_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'scope': 'Local verification register only; no new file or Azure verification performed.',
        'asset_count': len(rows),
        'unique_content_count': len({r['sha256'] for r in rows}),
        'unresolved_asset_guids': unresolved,
        'assets': rows,
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    result = export(args.database)
    output = pathlib.Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: result[k] for k in
                      ('asset_count', 'unique_content_count', 'unresolved_asset_guids')}))
