from django import forms
from django.contrib.auth import get_user_model
from django.utils import timezone

from .models import ServicePincode
import re

User = get_user_model()


class CustomerRegistrationForm(forms.Form):
    name = forms.CharField(max_length=150, widget=forms.TextInput(attrs={
        'placeholder': 'Full Name', 'class': 'form-input'
    }))
    email = forms.EmailField(widget=forms.EmailInput(attrs={
        'placeholder': 'Email Address', 'class': 'form-input'
    }))
    phone = forms.CharField(max_length=20, widget=forms.TextInput(attrs={
        'placeholder': 'Phone Number', 'class': 'form-input'
    }))
    password = forms.CharField(widget=forms.PasswordInput(attrs={
        'placeholder': 'Password', 'class': 'form-input'
    }))
    confirm_password = forms.CharField(widget=forms.PasswordInput(attrs={
        'placeholder': 'Confirm Password', 'class': 'form-input'
    }))

    def clean_email(self):
        email = self.cleaned_data.get('email')
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError('An account with this email already exists.')
        return email

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get('password')
        confirm_password = cleaned_data.get('confirm_password')
        if password and confirm_password and password != confirm_password:
            raise forms.ValidationError('Passwords do not match.')
        return cleaned_data


class ProviderRegistrationForm(forms.Form):
    name = forms.CharField(max_length=150, widget=forms.TextInput(attrs={
        'placeholder': 'Full Name', 'class': 'form-input'
    }))
    email = forms.EmailField(widget=forms.EmailInput(attrs={
        'placeholder': 'Email Address', 'class': 'form-input'
    }))
    phone = forms.CharField(max_length=20, widget=forms.TextInput(attrs={
        'placeholder': 'Phone Number', 'class': 'form-input'
    }))
    password = forms.CharField(widget=forms.PasswordInput(attrs={
        'placeholder': 'Password', 'class': 'form-input'
    }))
    confirm_password = forms.CharField(widget=forms.PasswordInput(attrs={
        'placeholder': 'Confirm Password', 'class': 'form-input'
    }))

    def clean_email(self):
        email = self.cleaned_data.get('email')
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError('An account with this email already exists.')
        return email

    def clean(self):
        cleaned_data = super().clean()
        password = cleaned_data.get('password')
        confirm_password = cleaned_data.get('confirm_password')
        if password and confirm_password and password != confirm_password:
            raise forms.ValidationError('Passwords do not match.')
        return cleaned_data


class LoginForm(forms.Form):
    email = forms.EmailField(widget=forms.EmailInput(attrs={
        'placeholder': 'Email Address', 'class': 'form-input'
    }))
    password = forms.CharField(widget=forms.PasswordInput(attrs={
        'placeholder': 'Password', 'class': 'form-input'
    }))


class BookingForm(forms.Form):
    """Validate the existing booking workflow before persistence."""

    scheduled_date = forms.DateField(input_formats=['%Y-%m-%d'])
    scheduled_time = forms.TimeField(input_formats=['%H:%M', '%H:%M:%S'])
    address = forms.CharField(strip=True, max_length=2000)
    pincode = forms.CharField(strip=True, max_length=10)
    description = forms.CharField(strip=True, max_length=5000)

    def __init__(self, *args, provider=None, **kwargs):
        self.provider = provider
        super().__init__(*args, **kwargs)

    def clean_scheduled_date(self):
        value = self.cleaned_data['scheduled_date']
        if value < timezone.localdate():
            raise forms.ValidationError('Please choose today or a future date.')
        return value

    def clean_pincode(self):
        value = self.cleaned_data['pincode']
        if not re.fullmatch(r'[1-9][0-9]{5}', value):
            raise forms.ValidationError('Enter a valid six-digit PIN code.')
        if not ServicePincode.objects.filter(pincode=value).exists():
            raise forms.ValidationError('That PIN code is not configured in FixNow.')
        if self.provider is not None and not self.provider.service_pincodes.filter(
            pincode=value
        ).exists():
            raise forms.ValidationError(
                'This provider does not currently serve that PIN code.'
            )
        return value
