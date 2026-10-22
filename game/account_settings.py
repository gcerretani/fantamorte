"""Impostazioni dell'account dal profilo (#121): nome utente ed eliminazione.

Email, password e account collegati restano le view di allauth (stilate dai
template in templates/allauth/): qui solo ciò che allauth non offre.

L'eliminazione è un'**anonimizzazione**: dati personali via (nome, email,
password, social, dispositivi push, notifiche), squadre e piazzamenti
restano. Cancellare l'utente cancellerebbe a cascata le sue squadre e
cambierebbe a posteriori classifiche, statistiche e albo d'oro di tutti gli
altri. Chi possiede leghe deve prima trasferirne la proprietà
(`League.owner` è PROTECT e una lega senza owner non ha più nessuno che la
amministri).
"""
from allauth.account.adapter import get_adapter
from allauth.account.models import EmailAddress
from django import forms
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.shortcuts import redirect, render
from django.views import View

from .models import League, Notification, PushSubscription


class UsernameForm(forms.Form):
    username = forms.CharField(label='Nuovo nome utente', max_length=150)

    def __init__(self, *args, user, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_username(self):
        username = self.cleaned_data['username'].strip()
        if username == self.user.username:
            raise forms.ValidationError('È già il tuo nome utente.')
        # Stesse regole della registrazione (caratteri ammessi, lunghezza,
        # unicità case-insensitive, nomi riservati): le applica allauth.
        return get_adapter().clean_username(username)


class UsernameChangeView(LoginRequiredMixin, View):
    template_name = 'game/account_username.html'

    def get(self, request):
        return render(request, self.template_name, {'form': UsernameForm(user=request.user)})

    def post(self, request):
        form = UsernameForm(request.POST, user=request.user)
        if not form.is_valid():
            return render(request, self.template_name, {'form': form})
        request.user.username = form.cleaned_data['username']
        request.user.save(update_fields=['username'])
        messages.success(request, f'Ora sei «{request.user.username}».')
        return redirect('profile')


def anonymize_user(user):
    """Toglie ogni dato personale lasciando intatto lo storico di gioco."""
    from allauth.socialaccount.models import SocialAccount

    with transaction.atomic():
        EmailAddress.objects.filter(user=user).delete()
        SocialAccount.objects.filter(user=user).delete()
        PushSubscription.objects.filter(user=user).delete()
        Notification.objects.filter(user=user).delete()
        user.username = f'utente-eliminato-{user.pk}'
        user.email = ''
        user.first_name = ''
        user.last_name = ''
        user.is_active = False
        user.is_staff = False
        user.is_superuser = False
        user.set_unusable_password()
        user.save()


class AccountDeleteView(LoginRequiredMixin, View):
    template_name = 'game/account_delete.html'

    def _context(self, request, **extra):
        return {
            'owned_leagues': League.objects.filter(owner=request.user).order_by('name'),
            **extra,
        }

    def get(self, request):
        return render(request, self.template_name, self._context(request))

    def post(self, request):
        ctx = self._context(request)
        if ctx['owned_leagues']:
            messages.error(request, 'Prima trasferisci la proprietà delle tue leghe.')
            return render(request, self.template_name, ctx)
        if request.POST.get('confirm_username', '').strip() != request.user.username:
            messages.error(request, 'Il nome utente digitato non corrisponde.')
            return render(request, self.template_name, ctx)
        user = request.user
        logout(request)
        anonymize_user(user)
        messages.success(request, 'Il tuo account è stato eliminato.')
        return redirect('account_login')
