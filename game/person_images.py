"""Foto delle persone: attribuzione Commons e cache locale (#103).

**Attribuzione.** Le foto P18 stanno su Wikimedia Commons, quasi sempre con
licenze CC BY / BY-SA che chiedono di citare autore e licenza. Il credito si
salva in `WikipediaPerson.image_credit` (`file`, `author`, `license`,
`license_url`, `page_url`) e si rinnova quando cambia la foto. Si recupera
a blocchi da 50 file per richiesta (`refresh_image_credits` command) e, per
la singola persona, alla visita della sua pagina se manca.

**Cache locale.** Le thumbnail passano da `/img/persona/<pk>/<w>/<hash>/`:
la prima richiesta le scarica da Commons e le salva su disco
(`PERSON_IMAGE_CACHE_DIR`), le successive le servono da lì. Così il browser
degli utenti non contatta Commons (privacy), le foto restano disponibili
offline nella PWA (il SW le tratta come asset) e non dipendiamo dagli
hotlink. L'hash nell'URL è quello di `image_url`: se la foto cambia cambia
l'URL, quindi la risposta può essere cachata a lungo senza invalidazioni.

Sicurezza: si scarica solo da `commons.wikimedia.org/wiki/Special:FilePath/`
(redirect ammessi solo verso `*.wikimedia.org`), solo raster (niente SVG, che
dal nostro dominio sarebbe XSS), con un tetto alla dimensione.
"""
import hashlib
import logging
import os
import tempfile
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from django.conf import settings
from django.core.cache import cache
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views import View

from .models import WikipediaPerson

logger = logging.getLogger(__name__)

PROXY_WIDTHS = (120, 250, 500)
MAX_BYTES = 3 * 1024 * 1024
FETCH_TIMEOUT = 8
FAILURE_TTL = 10 * 60
ALLOWED_TYPES = {
    'image/jpeg': '.jpg', 'image/png': '.png', 'image/webp': '.webp', 'image/gif': '.gif',
}
FILEPATH_PREFIX = '/wiki/Special:FilePath/'


def commons_filename(image_url):
    """`…/Special:FilePath/Foo%20bar.jpg?width=400` → `Foo bar.jpg` ('' se non Commons)."""
    if not image_url:
        return ''
    parts = urlsplit(image_url)
    if parts.netloc != 'commons.wikimedia.org' or not parts.path.startswith(FILEPATH_PREFIX):
        return ''
    return unquote(parts.path[len(FILEPATH_PREFIX):])


def image_hash(image_url):
    return hashlib.sha256(image_url.encode()).hexdigest()[:12]


def proxy_url(person, width):
    """URL locale della thumbnail, o '' se la foto non è proxabile."""
    if not getattr(settings, 'PERSON_IMAGE_PROXY', True):
        return ''
    if width not in PROXY_WIDTHS or not commons_filename(person.image_url):
        return ''
    return reverse('person_image', args=[person.pk, width, image_hash(person.image_url)])


# ---------------------------------------------------------------- crediti

def credit_is_stale(person):
    filename = commons_filename(person.image_url)
    return bool(filename) and (person.image_credit or {}).get('file') != filename


def refresh_image_credits(people, client=None):
    """Aggiorna `image_credit` delle persone passate con credito mancante o
    riferito a un'altra foto. Ritorna quante righe ha aggiornato. Un errore
    di rete lascia tutto com'è (si riprova al giro dopo)."""
    stale = [p for p in people if credit_is_stale(p)]
    if not stale:
        return 0
    if client is None:
        from wikidata_api.client import WikidataClient
        client = WikidataClient()
    try:
        found = client.get_image_credits([commons_filename(p.image_url) for p in stale])
    except Exception:
        logger.warning('Recupero crediti Commons fallito per %d file', len(stale), exc_info=True)
        return 0
    now = timezone.now().isoformat()
    updated = 0
    for person in stale:
        filename = commons_filename(person.image_url)
        credit = found.get(filename)
        # File sparito da Commons: si registra comunque (senza autore) per
        # non richiederlo a ogni giro; il link alla pagina resta costruibile.
        person.image_credit = {'file': filename, 'fetched_at': now, **(credit or {})}
        person.save(update_fields=['image_credit'])
        updated += 1
    return updated


def credit_for_display(person):
    """Dict per il partial _image_credit.html, o None se la foto non è Commons."""
    filename = commons_filename(person.image_url)
    if not filename:
        return None
    credit = dict(person.image_credit or {}) if not credit_is_stale(person) else {}
    if not credit.get('page_url'):
        credit['page_url'] = 'https://commons.wikimedia.org/wiki/File:' + filename.replace(' ', '_')
    return credit


# ------------------------------------------------------------ cache locale

def _cache_dir():
    return Path(getattr(settings, 'PERSON_IMAGE_CACHE_DIR', Path(settings.BASE_DIR) / 'media' / 'person_images'))


def _cached_file(pk, width, digest):
    directory = _cache_dir()
    for ext in ALLOWED_TYPES.values():
        path = directory / f'{pk}-{width}-{digest}{ext}'
        if path.exists():
            return path
    return None


def _download(image_url, width):
    """Scarica la thumbnail. Ritorna (bytes, content_type) o solleva."""
    filename = commons_filename(image_url)
    if not filename:
        raise ValueError('URL non Commons')
    from wikidata_api.client import _shared_session
    resp = _shared_session().get(
        'https://commons.wikimedia.org/wiki/Special:FilePath/' + quote(filename),
        params={'width': width}, timeout=FETCH_TIMEOUT, stream=True,
        headers={'Accept': 'image/*'},
    )
    try:
        resp.raise_for_status()
        host = urlsplit(resp.url).hostname or ''
        if not (host == 'wikimedia.org' or host.endswith('.wikimedia.org')):
            raise ValueError(f'redirect inatteso verso {host}')
        content_type = resp.headers.get('Content-Type', '').split(';')[0].strip().lower()
        if content_type not in ALLOWED_TYPES:
            raise ValueError(f'tipo non ammesso: {content_type}')
        chunks, size = [], 0
        for chunk in resp.iter_content(64 * 1024):
            size += len(chunk)
            if size > MAX_BYTES:
                raise ValueError('immagine troppo grande')
            chunks.append(chunk)
        return b''.join(chunks), content_type
    finally:
        resp.close()


class PersonImageView(View):
    """Thumbnail della foto di una persona, servita dal nostro dominio."""

    def get(self, request, pk, width, digest):
        if width not in PROXY_WIDTHS:
            raise Http404()
        person = get_object_or_404(WikipediaPerson.objects.only('pk', 'image_url'), pk=pk)
        if not commons_filename(person.image_url):
            raise Http404()
        current = image_hash(person.image_url)
        if digest != current:
            # Foto cambiata dopo il render della pagina: si punta alla nuova.
            return redirect('person_image', pk=pk, width=width, digest=current)

        path = _cached_file(pk, width, digest)
        if path is None:
            fail_key = f'person_img_fail:{pk}:{width}:{digest}'
            if cache.get(fail_key):
                return _not_found()
            try:
                data, content_type = _download(person.image_url, width)
            except Exception:
                logger.warning('Download foto fallito per persona %s', pk, exc_info=True)
                cache.set(fail_key, True, FAILURE_TTL)
                return _not_found()
            path = _store(pk, width, digest, data, ALLOWED_TYPES[content_type])
        response = FileResponse(open(path, 'rb'), content_type=_content_type_of(path))
        # URL immutabile (l'hash cambia con la foto): cache lunga nel browser
        # e nel SW. `private`: la rotta richiede il login.
        response['Cache-Control'] = 'private, max-age=31536000, immutable'
        return response


def _not_found():
    resp = HttpResponse(status=404)
    resp['Cache-Control'] = 'no-store'
    return resp


def _store(pk, width, digest, data, ext):
    directory = _cache_dir()
    directory.mkdir(parents=True, exist_ok=True)
    final = directory / f'{pk}-{width}-{digest}{ext}'
    # Scrittura atomica: due worker che scaricano la stessa foto in parallelo
    # non lasciano mai un file a metà.
    fd, tmp = tempfile.mkstemp(dir=directory, suffix='.part')
    with os.fdopen(fd, 'wb') as f:
        f.write(data)
    os.replace(tmp, final)
    return final


def _content_type_of(path):
    ext = path.suffix
    return next((ct for ct, e in ALLOWED_TYPES.items() if e == ext), 'application/octet-stream')
