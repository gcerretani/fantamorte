"""Form allauth con classi Bootstrap applicate server-side.

I template account/* renderizzano i campi con {{ field }}: senza queste
subclass i widget arriverebbero senza classi Bootstrap e lo stile andrebbe
iniettato via JS a pagina caricata (flash di form non stilato, niente
stile con JS disabilitato). Registrate in settings.ACCOUNT_FORMS.
"""
from allauth.account import forms as allauth_forms
from django.forms.widgets import CheckboxInput


class BootstrapFormMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            widget = field.widget
            base = 'form-check-input' if isinstance(widget, CheckboxInput) else 'form-control'
            classes = widget.attrs.get('class', '').split()
            if base not in classes:
                classes.append(base)
            if self.is_bound and name in self.errors:
                classes.append('is-invalid')
            widget.attrs['class'] = ' '.join(classes)


class LoginForm(BootstrapFormMixin, allauth_forms.LoginForm):
    pass


class SignupForm(BootstrapFormMixin, allauth_forms.SignupForm):
    pass


class ResetPasswordForm(BootstrapFormMixin, allauth_forms.ResetPasswordForm):
    pass


class ResetPasswordKeyForm(BootstrapFormMixin, allauth_forms.ResetPasswordKeyForm):
    pass


class ChangePasswordForm(BootstrapFormMixin, allauth_forms.ChangePasswordForm):
    pass
