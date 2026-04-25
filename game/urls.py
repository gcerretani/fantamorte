from django.urls import path
from . import views

urlpatterns = [
    path('', views.HomeView.as_view(), name='home'),
    path('classifica/', views.RankingsView.as_view(), name='rankings'),
    path('classifica/<int:year>/', views.RankingsView.as_view(), name='rankings_year'),
    path('decessi/', views.DeathsTimelineView.as_view(), name='deaths_timeline'),
    path('regolamento/', views.RulesView.as_view(), name='rules'),
    path('profilo/', views.ProfileView.as_view(), name='profile'),
    path('squadra/<int:pk>/', views.TeamDetailView.as_view(), name='team_detail'),
    path('morte/<int:pk>/', views.DeathDetailView.as_view(), name='death_detail'),
    path('squadra/nuova/', views.TeamCreateView.as_view(), name='team_create'),
    path('squadra/<int:pk>/modifica/', views.TeamEditView.as_view(), name='team_edit'),
    path('squadra/<int:pk>/aggiungi/', views.AddPersonView.as_view(), name='add_person'),
    path('squadra/<int:pk>/sostituisci/<int:member_pk>/', views.SubstituteMemberView.as_view(), name='substitute_member'),
    path('api/search-person/', views.PersonSearchView.as_view(), name='person_search'),
    path('api/push/subscribe/', views.PushSubscribeView.as_view(), name='push_subscribe'),
    path('api/push/unsubscribe/', views.PushUnsubscribeView.as_view(), name='push_unsubscribe'),
    path('api/push/test/', views.PushTestView.as_view(), name='push_test'),
]
