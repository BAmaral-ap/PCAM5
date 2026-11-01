import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from monitor import main, instance_lock
from storage import Store


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.db = self.directory / 'db.sqlite3'
        self.purchase = self.directory / 'compra.json'
        self.purchase.write_text(json.dumps({'categoria':'RAM', 'modelo':'DDR5 1x16GB',
            'total_centavos':150001,'pagamento':'cartao','parcelas':3,'primeira_parcela':'2026-12'}))

    def run_command(self, args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            result = main(['--db', str(self.db)] + args)
        return result, output.getvalue()

    def test_purchase_needs_explicit_confirmation(self):
        with self.assertRaises(SystemExit) as result:
            self.run_command(['--purchase', str(self.purchase)])
        self.assertEqual(result.exception.code, 2)
        self.assertFalse(self.db.exists())

    def test_purchase_installment_and_no_duplicate_purchase(self):
        self.assertEqual(self.run_command(['--purchase', str(self.purchase), '--confirmar'])[0], 0)
        self.assertEqual(self.run_command(['--purchase', str(self.purchase), '--confirmar'])[0], 1)
        self.assertEqual(self.run_command(['--pay-installment', '1', '--confirmar'])[0], 0)
        self.assertEqual(self.run_command(['--purchase', str(self.purchase), '--confirmar'])[0], 1)
        result, output = self.run_command(['--budget'])
        self.assertEqual(result, 0)
        report = json.loads(output)
        self.assertEqual(len(report['compras']), 1)
        self.assertEqual(report['orcamento']['saldo_pix_centavos'], 1000000)
        self.assertEqual(report['orcamento']['limite_cartao_centavos'], 150000)
        self.assertEqual(report['orcamento']['compromissos_mensais'], {'2027-01':50000,'2027-02':50000})

    def test_dry_run_no_database_no_credentials(self):
        result, text = self.run_command(['--dry-run'])
        self.assertIsNone(result)
        self.assertIn('OFERTA FICTÍCIA', text)
        self.assertFalse(self.db.exists())

    def test_second_monitor_cannot_claim_same_database(self):
        with instance_lock(self.db):
            with self.assertRaises(ValueError):
                with instance_lock(self.db):
                    pass

    def test_import_export_preserve_original_and_no_alerts(self):
        original = self.directory / 'original.csv'
        original.write_text('peca,preco,texto,link\nRyzen 7 7800X3D,1500,oferta antiga,https://t.me/channel/1\n')
        initial = original.read_bytes()
        self.assertEqual(self.run_command(['--import-csv', str(original)])[0], 0)
        self.assertEqual(self.run_command(['--export-csv', str(original)])[0], 1)
        self.assertEqual(original.read_bytes(), initial)
        with Store(self.db) as store:
            self.assertEqual(store.notifications(), [])
