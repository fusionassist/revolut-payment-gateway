# Part of payment_revolut. See LICENSE file for full copyright and licensing details.

from urllib.parse import urljoin, urlsplit, urlunsplit

from odoo import _, fields, models
from odoo.exceptions import ValidationError

from odoo.addons.payment.logging import get_payment_logger
from odoo.addons.payment_revolut import const
from odoo.addons.payment_revolut.controllers.main import RevolutController


_logger = get_payment_logger(__name__, const.SENSITIVE_KEYS)


class PaymentProvider(models.Model):
    _inherit = 'payment.provider'

    code = fields.Selection(
        selection_add=[('revolut', "Revolut")], ondelete={'revolut': 'set default'}
    )
    revolut_secret_key = fields.Char(
        string="Revolut Secret API Key",
        help="The Merchant API secret key of the matching environment (sandbox in test mode).",
        required_if_provider='revolut',
        copy=False,
        groups='base.group_system',
    )
    revolut_webhook_secret = fields.Char(
        string="Revolut Webhook Signing Secret",
        help="Set automatically when the webhook is registered. Only edit it manually if the"
             " webhook was created outside of Odoo.",
        copy=False,
        groups='base.group_system',
    )
    revolut_webhook_id = fields.Char(
        string="Revolut Webhook ID",
        copy=False,
        groups='base.group_system',
        readonly=True,
    )

    # === COMPUTE METHODS === #

    def _compute_feature_support_fields(self):
        """ Override of `payment` to enable additional features. """
        super()._compute_feature_support_fields()
        self.filtered(lambda p: p.code == 'revolut').update({
            'support_manual_capture': 'full_only',
            'support_refund': 'partial',
        })

    def _get_supported_currencies(self):
        """ Override of `payment` to return the supported currencies. """
        supported_currencies = super()._get_supported_currencies()
        if self.code == 'revolut':
            supported_currencies = supported_currencies.filtered(
                lambda c: c.name in const.SUPPORTED_CURRENCIES
            )
        return supported_currencies

    # === CRUD METHODS === #

    def _get_default_payment_method_codes(self):
        """ Override of `payment` to return the default payment method codes. """
        self.ensure_one()
        if self.code != 'revolut':
            return super()._get_default_payment_method_codes()
        return const.DEFAULT_PAYMENT_METHOD_CODES

    # === ACTION METHODS === #

    def action_revolut_register_webhook(self):
        """ Create or update the webhook on Revolut and store its id and signing secret.

        :return: A client action displaying the result of the registration.
        :rtype: dict
        """
        self.ensure_one()

        webhook_url = urljoin(self._revolut_get_base_url(), RevolutController._webhook_url)
        payload = {
            'url': webhook_url,
            'events': const.HANDLED_WEBHOOK_EVENTS,
        }
        message = None
        if self.revolut_webhook_id:  # A webhook was already registered; update it in place.
            try:
                self._send_api_request(
                    'PATCH', f'webhooks/{self.revolut_webhook_id}', json=payload
                )
                message = _("The webhook was successfully updated to %s.", webhook_url)
            except ValidationError:
                # The webhook might have been deleted on Revolut's side; discard the stale id
                # and fall through to register a fresh webhook.
                _logger.warning(
                    "Failed to update webhook %s on Revolut; registering a new one.",
                    self.revolut_webhook_id,
                )
                self.revolut_webhook_id = False
        if not self.revolut_webhook_id:
            webhook_data = self._send_api_request('POST', 'webhooks', json=payload)
            self.revolut_webhook_id = webhook_data.get('id')
            if webhook_data.get('signing_secret'):
                self.revolut_webhook_secret = webhook_data['signing_secret']
            message = _("The webhook was successfully registered at %s.", webhook_url)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'message': message,
                'sticky': False,
                'type': 'success',
            },
        }

    def action_revolut_unregister_webhook(self):
        """ Delete the webhook on Revolut and clear the related fields.

        :return: A client action displaying the result of the deletion.
        :rtype: dict
        """
        self.ensure_one()

        if self.revolut_webhook_id:
            try:
                self._send_api_request('DELETE', f'webhooks/{self.revolut_webhook_id}')
            except ValidationError:
                _logger.warning(
                    "Failed to delete webhook %s on Revolut; clearing the local fields anyway.",
                    self.revolut_webhook_id,
                )
        self.revolut_webhook_id = False
        self.revolut_webhook_secret = False
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'message': _("The webhook was deleted."),
                'sticky': False,
                'type': 'success',
            },
        }

    # === BUSINESS METHODS === #

    def _revolut_get_base_url(self):
        """ Return the instance base URL, forcing HTTPS for public hosts.

        Revolut requires a public HTTPS endpoint to deliver webhooks — it does not follow
        redirects on POST, so a webhook registered over http:// that the reverse proxy
        301-redirects to https:// is silently dropped. The hosted checkout likewise only
        redirects back to a public host. Behind a reverse proxy, `get_base_url` can report
        an `http` scheme even when the public site is served over HTTPS, so the scheme is
        upgraded for non-local hosts.

        :return: The base URL with an https scheme for public hosts.
        :rtype: str
        """
        self.ensure_one()
        base_url = self.get_base_url()
        parts = urlsplit(base_url)
        if parts.scheme == 'http' and (parts.hostname or '') not in ('localhost', '127.0.0.1'):
            base_url = urlunsplit(parts._replace(scheme='https'))
        return base_url

    # === REQUEST HELPERS === #

    def _build_request_url(self, endpoint, **kwargs):
        """Override of `payment` to build the request URL."""
        if self.code != 'revolut':
            return super()._build_request_url(endpoint, **kwargs)
        return urljoin(const.API_URLS[self.state], endpoint)

    def _build_request_headers(self, method, endpoint, payload, *, idempotency_key=None, **kwargs):
        """Override of `payment` to build the request headers."""
        if self.code != 'revolut':
            return super()._build_request_headers(
                method, endpoint, payload, idempotency_key=idempotency_key, **kwargs
            )
        headers = {
            'Authorization': f'Bearer {self.revolut_secret_key}',
            'Revolut-Api-Version': const.API_VERSION,
            'Accept': 'application/json',
        }
        if idempotency_key:
            headers['Idempotency-Key'] = idempotency_key
        return headers

    def _parse_response_content(self, response, **kwargs):
        """Override of `payment` to handle empty responses, e.g. of webhook deletions."""
        if self.code != 'revolut':
            return super()._parse_response_content(response, **kwargs)
        return response.json() if response.content else {}

    def _parse_response_error(self, response):
        """Override of `payment` to parse the error message."""
        if self.code != 'revolut':
            return super()._parse_response_error(response)
        try:
            return response.json().get('message', response.text)
        except ValueError:
            return response.text
