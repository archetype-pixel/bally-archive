import importlib.util
import json
import pathlib
import sqlite3
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('verified_export', ROOT / 'tools/export_verified_originals.py')
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)


class ReconciliationExportTests(unittest.TestCase):
    def test_shared_bytes_preserve_distinct_asset_identities_and_report_missing_content(self):
        with tempfile.TemporaryDirectory() as directory:
            database = pathlib.Path(directory) / 'archive.sqlite3'
            with sqlite3.connect(database) as c:
                c.execute('CREATE TABLE originals(sha256 TEXT, path TEXT, bytes INT, filename TEXT, source_guid TEXT)')
                c.execute('CREATE TABLE media_assets(guid TEXT, body TEXT)')
                c.execute('INSERT INTO originals VALUES(?,?,?,?,?)', ('abc', 'originals/abc', 123, 'shoe.jpg', 'A'))
                for guid, digest in [('A', 'abc'), ('B', 'abc'), ('C', 'missing')]:
                    c.execute('INSERT INTO media_assets VALUES(?,?)', (guid, json.dumps({'sha256': digest, 'filename': 'shoe.jpg'})))
            before = database.read_bytes()
            result = exporter.export(database)
            self.assertEqual(result['asset_count'], 2)
            self.assertEqual(result['unique_content_count'], 1)
            self.assertEqual(result['unresolved_asset_guids'], ['C'])
            self.assertEqual([x['source_guid'] for x in result['assets']], ['A', 'B'])
            self.assertNotIn('local_path', result['assets'][0])
            self.assertEqual(database.read_bytes(), before)

    def test_missing_database_does_not_create_an_empty_database(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = pathlib.Path(directory) / 'missing.sqlite3'
            with self.assertRaises(FileNotFoundError):
                exporter.export(missing)
            self.assertFalse(missing.exists())


class ReferenceStartupTests(unittest.TestCase):
    def test_empty_checkout_initializes_without_private_seed_data(self):
        sys.path.insert(0, str(ROOT / 'reference/local-archive'))
        import server
        old_root, old_db = server.ROOT, server.DB
        try:
            with tempfile.TemporaryDirectory() as directory:
                server.ROOT = pathlib.Path(directory)
                (server.ROOT / 'data').mkdir()
                (server.ROOT / 'data/pilot.json').write_text('[]')
                server.DB = server.ROOT / 'data/archive.sqlite3'
                server.init()
                with server.connect() as c:
                    self.assertEqual(c.execute('SELECT count(*) FROM records').fetchone()[0], 0)
                    self.assertEqual(c.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        finally:
            server.ROOT, server.DB = old_root, old_db


if __name__ == '__main__':
    unittest.main()
