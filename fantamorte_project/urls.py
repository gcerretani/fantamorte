from django.contrib import admin
from django.urls import path, include

from game import views as game_views

urlpatterns = [
    path('admin/', admin.site.urls),
    # Auth: manteniamo le viste built-in per compatibilità (login/logout/password)
    path('accounts/', include('django.contrib.auth.urls')),
    # OAuth e signup self-service via django-allauth
    path('accounts/', include('allauth.urls')),
    # PWA: manifest e service worker servito dalla root per scope corretto
    path('manifest.webmanifest', game_views.ManifestView.as_view(), name='manifest'),
    path('sw.js', game_views.ServiceWorkerView.as_view(), name='service_worker'),
    path('offline/', game_views.OfflineView.as_view(), name='offline'),
    path('', include('game.urls')),
]
