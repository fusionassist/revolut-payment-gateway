# Part of payment_revolut. See LICENSE file for full copyright and licensing details.

import logging
import pprint
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests

from odoo import _, fields, models
from odoo.exceptions import ValidationError

from odoo.addons.payment_revolut import const
from odoo.addons.payment_revolut.controllers.main import RevolutController


_logger = logging.getLogger(__name__)


class PaymentProvider(models.Model):
    _inherit = 'payment.provider'

    code = fields.Selection(
        selection_add=[('revolut', "Revolut")], ondelete={'revolut': 'set default'}
    )
    revolut_secret_key = fields.Char(
        string="Revolut Secret API Key",
        help="The Merchant API secret key of the matching environment (sandbox in test mode).",
        required_if_provider='revolut',
        groups='base.group_system',
    )
    revolut_webhook_secret = fields.Char(
        string="Revolut Webhook Signing Secret",
        help="Set automatically when the webhook is registered.",
        groups='base.group_system',
    )
    revolut_webhook_id = fields.Char(
        string="Revolut Webhook ID",
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

    # === BUSINESS METHODS - PAYMENT FLOW === #

    def _get_supported_currencies(self):
        """ Override of `payment` to return the supported currencies. """
        supported_currencies = super()._get_supported_currencies()
        if self.code == 'revolut':
            supported_currencies = supported_currencies.filtered(
                lambda c: c.name in const.SUPPORTED_CURRENCIES
            )
        return supported_currencies

    def _get_default_payment_method_codes(self):
        """ Override of `payment` to return the default payment method codes. """
        default_codes = super()._get_default_payment_method_codes()
        if self.code != 'revolut':
            return default_codes
        return const.DEFAULT_PAYMENT_METHOD_CODES

    def _revolut_get_base_url(self):
        """ Return the instance base URL, forcing HTTPS for public hosts.

        Revolut requires a public HTTPS endpoint to deliver webhooks (it does not follow redirects
        on POST). Behind a reverse proxy, `get_base_url` can report an `http` scheme even when the
        public site is HTTPS, so the scheme is upgraded for non-local hosts.
        """
        self.ensure_one()
        base_url = self.get_base_url()
        parts = urlsplit(base_url)
        if parts.scheme == 'http' and (parts.hostname or '') not in ('localhost', '127.0.0.1'):
            base_url = urlunsplit(parts._replace(scheme='https'))
        return base_url

    def _revolut_make_request(self, endpoint, payload=None, method='POST', idempotency_key=None):
        """ Make a request to the Revolut Merchant API and return the JSON content of the response.

        Note: self.ensure_one()

        :param str endpoint: The endpoint to be reached by the request.
        :param dict payload: The JSON payload of the request.
        :param str method: The HTTP method of the request.
        :param str idempotency_key: The idempotency key to send with a mutating request.
        :return: The JSON-formatted content of the response (empty dict for 204 responses).
        :rtype: dict
        :raise ValidationError: If an HTTP error occurs.
        """
        self.ensure_one()

        url = urljoin(const.API_URLS[self.state], endpoint)
        headers = {
            'Authorization': f'Bearer {self.revolut_secret_key}',
            'Revolut-Api-Version': const.API_VERSION,
            'Accept': 'application/json',
        }
        if idempotency_key:
            headers['Idempotency-Key'] = idempotency_key
        try:
            response = requests.request(method, url, json=payload, headers=headers, timeout=10)
            response.raise_for_status()
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            _logger.exception("Unable to reach Revolut endpoint at %s", url)
            raise ValidationError(
                "Revolut: " + _("Could not establish the connection to the API.")
            )
        except requests.exceptions.HTTPError as err:
            try:
                error_message = err.response.json().get('message', err.response.text)
            except Exception:  # noqa: BLE001 - error body may not be JSON.
                error_message = err.response.text
            _logger.exception("Invalid API request at %s with data:\n%s", url, pprint.pformat(payload))
            raise ValidationError(
                "Revolut: " + _(
                    "The communication with the API failed. Revolut gave us the following"
                    " information: '%s'", error_message
                )
            )
        return response.json() if response.content else {}

    # === ACTION METHODS === #

    def action_revolut_register_webhook(self):
        """ Create or update the webhook on Revolut and store its id and signing secret. """
        self.ensure_one()

        webhook_url = urljoin(self._revolut_get_base_url(), RevolutController._webhook_url)
        payload = {'url': webhook_url, 'events': const.HANDLED_WEBHOOK_EVENTS}
        message = None
        if self.revolut_webhook_id:
            try:
                self._revolut_make_request(
                    f'webhooks/{self.revolut_webhook_id}', payload=payload, method='PATCH'
                )
                message = _("The webhook was successfully updated to %s.", webhook_url)
            except ValidationError:
                _logger.warning(
                    "Failed to update webhook %s on Revolut; registering a new one.",
                    self.revolut_webhook_id,
                )
                self.revolut_webhook_id = False
        if not self.revolut_webhook_id:
            webhook_data = self._revolut_make_request('webhooks', payload=payload)
            self.revolut_webhook_id = webhook_data.get('id')
            if webhook_data.get('signing_secret'):
                self.revolut_webhook_secret = webhook_data['signing_secret']
            message = _("The webhook was successfully registered at %s.", webhook_url)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {'message': message, 'sticky': False, 'type': 'success'},
        }

    def action_revolut_unregister_webhook(self):
        """ Delete the webhook on Revolut and clear the related fields. """
        self.ensure_one()

        if self.revolut_webhook_id:
            try:
                self._revolut_make_request(
                    f'webhooks/{self.revolut_webhook_id}', method='DELETE'
                )
            except ValidationError:
                _logger.warning(
                    "Failed to delete webhook %s on Revolut; clearing local fields anyway.",
                    self.revolut_webhook_id,
                )
        self.revolut_webhook_id = False
        self.revolut_webhook_secret = False
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {'message': _("The webhook was deleted."), 'sticky': False, 'type': 'success'},
        }
