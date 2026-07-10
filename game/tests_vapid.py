"""Regressione: la chiave privata generata da `generate_vapid_keys` deve
essere nel formato che pywebpush si aspetta davvero (DER in base64url su una
riga sola), non un PEM con header/footer -----BEGIN/END-----. Quando
vapid_private_key non e' un path di file, pywebpush chiama
py_vapid.Vapid.from_string(), che si limita a togliere i newline
dall'input: un PEM finisce con gli header incollati al base64 e la
decodifica fallisce con "ASN.1 parsing error: invalid length" (visto in
produzione sull'endpoint /api/push/test/)."""
import io

from django.core.management import call_command
from django.test import SimpleTestCase


class GenerateVapidKeysTest(SimpleTestCase):

    def _generate(self):
        out = io.StringIO()
        call_command('generate_vapid_keys', stdout=out)
        lines = out.getvalue().splitlines()
        public_line = next(l for l in lines if l.startswith('VAPID_PUBLIC_KEY='))
        private_line = next(l for l in lines if l.startswith('VAPID_PRIVATE_KEY='))
        return public_line.split('=', 1)[1], private_line.split('=', 1)[1]

    def test_output_e_su_una_riga_sola_senza_pem(self):
        _, private_key = self._generate()
        self.assertNotIn('\n', private_key)
        self.assertNotIn('BEGIN', private_key)
        self.assertNotIn('END', private_key)

    def test_chiave_privata_e_consumabile_da_pywebpush(self):
        """Stesso percorso di game.push.send_push: Vapid.from_string() sulla
        stringa cosi' com'e', senza alcun trattamento."""
        from py_vapid import Vapid
        _, private_key = self._generate()
        Vapid.from_string(private_key=private_key)  # non deve sollevare

    def test_chiave_pubblica_e_65_byte_uncompressed(self):
        import base64
        public_key, _ = self._generate()
        raw = base64.urlsafe_b64decode(public_key + '=' * (-len(public_key) % 4))
        self.assertEqual(len(raw), 65)
        self.assertEqual(raw[0:1], b'\x04')
