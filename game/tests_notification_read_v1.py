from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .models import Notification


class NotificationReadVisibilityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('notification-reader', password='pw')
        self.client.force_login(self.user)

    def test_opening_feed_marks_only_rendered_notifications_read(self):
        Notification.objects.bulk_create([
            Notification(
                user=self.user,
                kind=Notification.KIND_LEAGUE_STARTED,
                title=f'Evento {index}',
            )
            for index in range(101)
        ])

        response = self.client.get(reverse('notifications'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Notification.objects.filter(user=self.user, is_read=True).count(), 100)
        self.assertEqual(Notification.objects.filter(user=self.user, is_read=False).count(), 1)

    def test_unread_item_outside_first_page_stays_unread(self):
        notifications = [
            Notification.objects.create(
                user=self.user,
                kind=Notification.KIND_LEAGUE_STARTED,
                title=f'Evento {index}',
            )
            for index in range(101)
        ]
        oldest = notifications[0]

        self.client.get(reverse('notifications'))

        oldest.refresh_from_db()
        self.assertFalse(oldest.is_read)
