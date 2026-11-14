from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.core.validators import MaxLengthValidator
from django.db import models


def get_ktp_fernet():
    key = getattr(settings, 'C2C_KTP_ENCRYPTION_KEY', '')
    if not key:
        raise ImproperlyConfigured(
            'C2C_KTP_ENCRYPTION_KEY must be configured before reading or saving KTP data.'
        )
    try:
        return Fernet(key.encode('ascii'))
    except (AttributeError, UnicodeEncodeError, TypeError, ValueError) as error:
        raise ImproperlyConfigured(
            'C2C_KTP_ENCRYPTION_KEY must be a valid Fernet key.'
        ) from error


class EncryptedCharField(models.CharField):
    def __init__(self, *args, max_plaintext_length=None, **kwargs):
        self.max_plaintext_length = max_plaintext_length
        super().__init__(*args, **kwargs)
        if max_plaintext_length is not None:
            self.validators.append(MaxLengthValidator(max_plaintext_length))

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        if self.max_plaintext_length is not None:
            kwargs['max_plaintext_length'] = self.max_plaintext_length
        return name, path, args, kwargs

    def formfield(self, **kwargs):
        if self.max_plaintext_length is not None:
            kwargs.setdefault('max_length', self.max_plaintext_length)
        return super().formfield(**kwargs)

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if value in (None, ''):
            return value
        value = str(value)
        if self.max_plaintext_length is not None and len(value) > self.max_plaintext_length:
            raise ValidationError(
                f'Ensure this value has at most {self.max_plaintext_length} characters.'
            )
        return get_ktp_fernet().encrypt(value.encode('utf-8')).decode('ascii')

    def from_db_value(self, value, expression, connection):
        if value in (None, ''):
            return value
        try:
            return get_ktp_fernet().decrypt(value.encode('ascii')).decode('utf-8')
        except (InvalidToken, UnicodeDecodeError, UnicodeEncodeError) as error:
            raise ImproperlyConfigured(
                'KTP data could not be decrypted. Verify the configured C2C_KTP_ENCRYPTION_KEY.'
            ) from error
