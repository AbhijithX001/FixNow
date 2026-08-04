import requests
import json
import logging
from django.core.mail.backends.base import BaseEmailBackend
from django.conf import settings
from django.core.mail.message import EmailMultiAlternatives

logger = logging.getLogger(__name__)

class BrevoHTTPBackend(BaseEmailBackend):
    """
    A custom Django EmailBackend that sends emails using the Brevo HTTP API (v3)
    instead of SMTP.
    """

    def __init__(self, fail_silently=False, **kwargs):
        super().__init__(fail_silently=fail_silently)
        self.api_url = "https://api.brevo.com/v3/smtp/email"
        self.api_key = getattr(settings, 'BREVO_API_KEY', None)

    def send_messages(self, email_messages):
        if not email_messages:
            return 0
            
        if not self.api_key:
            if not self.fail_silently:
                raise ValueError("BREVO_API_KEY is not set in settings.")
            return 0

        headers = {
            "accept": "application/json",
            "api-key": self.api_key,
            "content-type": "application/json"
        }

        sent_count = 0

        for message in email_messages:
            try:
                # Prepare payload
                payload = {
                    "sender": {"email": message.from_email},
                    "to": [{"email": to_email} for to_email in message.to],
                    "subject": message.subject,
                    "textContent": message.body,
                }
                
                # Check if there is an HTML alternative
                if isinstance(message, EmailMultiAlternatives) and message.alternatives:
                    for alt in message.alternatives:
                        if alt[1] == 'text/html':
                            payload['htmlContent'] = alt[0]
                            break

                response = requests.post(self.api_url, headers=headers, json=payload)
                response.raise_for_status()
                sent_count += 1
                
            except requests.exceptions.RequestException as e:
                logger.error(f"Failed to send email via Brevo HTTP API: {e}")
                if hasattr(e, 'response') and e.response is not None:
                    logger.error(f"Brevo API response: {e.response.text}")
                
                if not self.fail_silently:
                    raise

        return sent_count
