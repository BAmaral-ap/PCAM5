import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from datetime import datetime,timezone
from parsing import parse_offers
from storage import Store

class OccurrenceTests(unittest.TestCase):
    def test_roundup_repeating_model_is_saved_without_stalling_cursor(self):
        now=datetime.now(timezone.utc)
        text='Ryzen 7 7800X3D R$ 1700 no PIX\nRX 9070 XT R$ 4999\nRyzen 7 7800X3D R$ 1600 no PIX'
        offers=parse_offers('pcbuildwizard',1,now,text)
        self.assertEqual(len(offers),3)
        with tempfile.TemporaryDirectory() as d, Store(Path(d)/'db') as store:
            self.assertTrue(store.record_message('pcbuildwizard',1,now,text,offers))
            self.assertEqual(len(store.offers()),3)
            self.assertEqual(store.get_cursor('pcbuildwizard'),1)
            self.assertFalse(store.record_message('pcbuildwizard',1,now,text,offers))
            self.assertEqual(len(store.offers()),3)
    def test_legacy_database_migration_preserves_old_offers(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'db'
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE messages(channel TEXT,message_id INTEGER,date TEXT,text TEXT,PRIMARY KEY(channel,message_id))')
                db.execute('CREATE TABLE offers(channel TEXT,message_id INTEGER,model TEXT,payload TEXT,PRIMARY KEY(channel,message_id,model))')
                db.execute("INSERT INTO messages VALUES ('canal',1,'2026-11-13T00:00:00+00:00','antiga')")
                db.execute('INSERT INTO offers VALUES (?,?,?,?)',('canal',1,'CPU',json.dumps({'peca':'CPU','preco':1500})))
            for _ in range(2):
                with Store(path) as store:
                    self.assertEqual(store.offers(),[{'peca':'CPU','preco':1500}])
                    self.assertEqual(store.db.execute('PRAGMA foreign_key_check').fetchall(),[])
