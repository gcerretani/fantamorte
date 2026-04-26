from django.urls import path
from . import views

urlpatterns = [
    path('', views.HomeView.as_view(), name='home'),

    # Leghe
    path('leghe/', views.LeagueListView.as_view(), name='league_list'),
    path('leghe/nuova/', views.LeagueCreateView.as_view(), name='league_create'),
    path('leghe/<slug:slug>/', views.LeagueDetailView.as_view(), name='league_detail'),
    path('leghe/<slug:slug>/iscriviti/', views.LeagueJoinView.as_view(), name='league_join'),
    path('leghe/<slug:slug>/abbandona/', views.LeagueLeaveView.as_view(), name='league_leave'),
    path('leghe/<slug:slug>/admin/', views.LeagueAdminView.as_view(), name='league_admin'),
    path('leghe/<slug:slug>/classifica/', views.LeagueRankingsView.as_view(), name='league_rankings'),
    path('leghe/<slug:slug>/decessi/', views.LeagueDeathsView.as_view(), name='league_deaths'),
    path('leghe/<slug:slug>/squadra/nuova/', views.TeamCreateView.as_view(), name='team_create'),

    # Generiche (richiedono login dal middleware)
    path('classifica/', views.RankingsView.as_view(), name='rankings'),
    path('classifica/<int:year>/', views.RankingsView.as_view(), name='rankings_year'),
    path('decessi/', views.DeathsTimelineView.as_view(), name='deaths_timeline'),
    path('regolamento/', views.RulesView.as_view(), name='rules'),
    path('profilo/', views.ProfileView.as_view(), name='profile'),

    # Squadre
    path('squadra/<int:pk>/', views.TeamDetailView.as_view(), name='team_detail'),
    path('squadra/<int:pk>/modifica/', views.TeamEditView.as_view(), name='team_edit'),
    path('squadra/<int:pk>/aggiungi/', views.AddPersonView.as_view(), name='add_person'),
    path('squadra/<int:pk>/sostituisci/<int:member_pk>/', views.SubstituteMemberView.as_view(), name='substitute_member'),

    # Persone & decessi
    path('persona/<int:pk>/', views.PersonDetailView.as_view(), name='person_detail'),
    path('morte/<int:pk>/', views.DeathDetailView.as_view(), name='death_detail'),
    path('api/persona/<int:pk>/', views.PersonInfoView.as_view(), name='person_info'),
    path('api/search-person/', views.PersonSearchView.as_view(), name='person_search'),

    # Push
    path('api/push/subscribe/', views.PushSubscribeView.as_view(), name='push_subscribe'),
    path('api/push/unsubscribe/', views.PushUnsubscribeView.as_view(), name='push_unsubscribe'),
    path('api/push/test/', views.PushTestView.as_view(), name='push_test'),
]
