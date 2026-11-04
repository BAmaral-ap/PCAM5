import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from parsing import parse_offers
from runtime import make_notifications
from settings import load_config
from storage import Store

class FiltrosTests(unittest.TestCase):
    def setUp(self):
        self.cfg = load_config()
        self.now = datetime.now(timezone.utc)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / 'state.db')
        self.addCleanup(self.store.close)
        self.store.set_meta('alerts_started_at', self.now.isoformat())
    def alert(self, text):
        offers = parse_offers('tecm anual'.replace(' ', ''), 123, self.now, text, self.cfg)
        return [a for offer in offers for a in make_notifications(self.store, offer, self.cfg, [], self.now)]
    def test_placas_am4_nao_notificam(self):
        for title in ('Placa-mãe Am4 Msi B550m A Pro Msi', 'Placa mãe B450M Gaming', 'Placa mãe X570S AM4', 'Placa mãe A520M'):
            with self.subTest(title=title): self.assertEqual(self.alert(title + '\nPreço: R$ 450'), [])
    def test_am5_continua_notificando(self):
        self.assertTrue(self.alert('Placa mãe MSI B650M DDR5 AM5\nPreço: R$ 650'))
    def test_apenas_fontes_850w(self):
        for power in ('650W', '750W', '1000W', ''):
            with self.subTest(power=power): self.assertEqual(self.alert('Fonte Cooler Master ' + power + '\nPreço: R$ 450'), [])
        for power in ('850W', '850 W', '850 watts'):
            with self.subTest(power=power): self.assertTrue(self.alert('Fonte Gamer ' + power + '\nPreço: R$ 450'))
    def test_potencia_conflitante_nao_notifica(self):
        self.assertEqual(self.alert('Fonte 750W ou 850W\nPreço: R$ 450'), [])
    def test_outras_categorias_preservadas(self):
        self.assertTrue(self.alert('Ryzen 7 7800X3D\nPreço: R$ 1600'))
