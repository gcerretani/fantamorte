"""Genera una coppia di chiavi VAPID per le notifiche Web Push.

Uso:
    python manage.py generate_vapid_keys

Le chiavi vengono stampate a stdout. Copiale in `.env` come
VAPID_PUBLIC_KEY e VAPID_PRIVATE_KEY.
"""
import base64
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Genera una coppia di chiavi VAPID per notifiche Web Push'

    def handle(self, *args, **options):
        try:
            from cryptography.hazmat.primitives.asymmetric import ec
            from cryptography.hazmat.primitives import serialization
        except ImportError:
            self.stderr.write('Manca la libreria `cryptography`. Installa: pip install cryptography')
            return

        private_key = ec.generate_private_key(ec.SECP256R1())
        public_key = private_key.public_key()

        # Public key: 65 byte uncompressed, base64url
        public_numbers = public_key.public_numbers()
        x = public_numbers.x.to_bytes(32, 'big')
        y = public_numbers.y.to_bytes(32, 'big')
        public_bytes = b'\x04' + x + y
        public_b64 = base64.urlsafe_b64encode(public_bytes).rstrip(b'=').decode('ascii')

        # Private key: DER in base64url su una riga sola. NON PEM: quando
        # vapid_private_key non è un path di file, pywebpush chiama
        # Vapid.from_string(), che si aspetta il DER "nudo" in base64url e si
        # limita a togliere i newline dall'input (py_vapid.Vapid.from_string).
        # Un PEM con gli header/footer -----BEGIN/END PRIVATE KEY----- finisce
        # quindi incollato al base64 e la decodifica fallisce con "ASN.1
        # parsing error: invalid length".
        private_der = private_key.private_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        private_b64 = base64.urlsafe_b64encode(private_der).rstrip(b'=').decode('ascii')

        self.stdout.write(self.style.SUCCESS('VAPID keys generate. Aggiungile al tuo .env:'))
        self.stdout.write('')
        self.stdout.write(f'VAPID_PUBLIC_KEY={public_b64}')
        self.stdout.write(f'VAPID_PRIVATE_KEY={private_b64}')
        self.stdout.write('')
        self.stdout.write('Nota: entrambe le chiavi sono in base64url su una riga sola, pronte per pywebpush.')
