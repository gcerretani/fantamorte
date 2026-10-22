"""Card «lutto» al primo accesso dopo un decesso nella tua rosa (#106)."""
import json
from datetime import date

from django.urls import reverse
from django.utils import timezone

from ._views_test_cases import ViewsBaseTestCase
from .models import Death, Notification


class MourningOverlayTest(ViewsBaseTestCase):

    def setUp(self):
        super().setUp()
        person = self.person
        person.is_dead = True
        person.death_date = date(2023, 6, 12)
        person.save()
        # La conferma passa dal signal: crea le notifiche del feed.
        self.death = Death.objects.create(person=person, death_date=person.death_date,
                                          is_confirmed=True, confirmed_at=timezone.now())
        self.client.login(username='member', password='x')

    def notification(self):
        return Notification.objects.get(
            user=self.member, kind=Notification.KIND_DEATH_TEAM, death=self.death)

    def test_compare_con_i_punti(self):
        resp = self.client.get(reverse('home'))
        self.assertContains(resp, 'id="fmMourning"')
        self.assertContains(resp, f'data-fm-notification-id="{self.notification().pk}"')
        self.assertContains(resp, 'È morto un tuo personaggio')
        self.assertContains(resp, 'Squadra Privata')
        self.assertContains(resp, 'data-fm-count-to="')

    def test_una_volta_sola(self):
        n = self.notification()
        self.client.post(reverse('notifications_mark_read'), json.dumps({'ids': [n.pk]}),
                         content_type='application/json')
        self.assertNotContains(self.client.get(reverse('home')), 'id="fmMourning"')

    def test_non_nella_pagina_notifiche(self):
        self.assertNotContains(self.client.get(reverse('notifications')), 'id="fmMourning"')

    def test_non_per_chi_non_ha_la_persona_in_rosa(self):
        self.client.login(username='owner', password='x')
        self.assertNotContains(self.client.get(reverse('home')), 'id="fmMourning"')

    def test_decesso_revocato_non_compare(self):
        Death.objects.filter(pk=self.death.pk).update(is_confirmed=False)
        self.assertNotContains(self.client.get(reverse('home')), 'id="fmMourning"')

    def test_anonimo(self):
        self.client.logout()
        resp = self.client.get(reverse('account_login'))
        self.assertNotContains(resp, 'id="fmMourning"')
