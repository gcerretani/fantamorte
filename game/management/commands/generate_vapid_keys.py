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

        # Private key: PEM (formato accettato da pywebpush)
        private_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode('ascii')

        self.stdout.write(self.style.SUCCESS('VAPID keys generate. Aggiungile al tuo .env:'))
        self.stdout.write('')
        self.stdout.write(f'VAPID_PUBLIC_KEY={public_b64}')
        self.stdout.write('VAPID_PRIVATE_KEY="""')
        self.stdout.write(private_pem.strip())
        self.stdout.write('"""')
        self.stdout.write('')
        self.stdout.write('Nota: la chiave pubblica è già in formato base64url, può essere usata direttamente dal client.')
