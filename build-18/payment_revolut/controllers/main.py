# Part of payment_revolut. See LICENSE file for full copyright and licensing details.

import hashlib
import hmac
import logging
import pprint
import time

from werkzeug.exceptions import Forbidden

from odoo import http
from odoo.exceptions import ValidationError
from odoo.http import request

from odoo.addons.payment import utils as payment_utils
from odoo.addons.payment.const import CURRENCY_MINOR_UNITS
from odoo.addons.payment_revolut import const


_logger = logging.getLogger(__name__)


class RevolutController(http.Controller):

    _return_url = '/payment/revolut/return'
    _webhook_url = '/payment/revolut/webhook'

    @http.route(_return_url, type='http', methods=['GET'], auth='public')
    def revolut_return(self, reference=None, access_token=None, **kwargs):
        """ Process the payment after the customer is redirected back from Revolut.

        The redirect is never trusted: the authoritative order state is fetched from the API
        before processing.
        """
        tx_sudo = request.env['payment.transaction'].sudo().search(
            [('reference', '=', reference), ('provider_code', '=', 'revolut')], limit=1
        )
        if (
            tx_sudo
            and tx_sudo.provider_reference
            and payment_utils.check_access_token(access_token, reference)
        ):
            try:
                order_data = tx_sudo.provider_id._revolut_make_request(
                    f'orders/{tx_sudo.provider_reference}', method='GET'
                )
                tx_sudo._handle_notification_data(
                    'revolut', {'reference': reference, 'order': order_data}
                )
            except ValidationError:
                _logger.exception("Failed to process the return from Revolut.")
        return request.redirect('/payment/status')

    @http.route(_webhook_url, type='http', methods=['POST'], auth='public', csrf=False)
    def revolut_webhook(self):
        """ Process the notification data sent by Revolut to the webhook.

        The webhook payload is thin; the full order is fetched from the API after the signature is
        verified, which makes processing idempotent and resilient to out-of-order events.
        """
        data = request.get_json_data()
        _logger.info("Notification received from Revolut with data:\n%s", pprint.pformat(data))

        event_type = data.get('event')
        if event_type in const.HANDLED_WEBHOOK_EVENTS and data.get('order_id'):
            notification_data = {
                'event_type': event_type,
                'order_id': data.get('order_id'),
                'reference': data.get('merchant_order_ext_ref'),
            }
            try:
                tx_sudo = request.env['payment.transaction'].sudo()._get_tx_from_notification_data(
                    'revolut', notification_data
                )
                self._verify_signature(tx_sudo)

                order_data = tx_sudo.provider_id._revolut_make_request(
                    f'orders/{data["order_id"]}', method='GET'
                )
                notification_data['order'] = order_data

                # A refund order initiated outside Odoo references the source order's transaction;
                # process it on a dedicated refund transaction.
                if order_data.get('type') == 'refund' and tx_sudo.operation != 'refund':
                    tx_sudo = self._resolve_refund_tx(tx_sudo, order_data)
                tx_sudo._handle_notification_data('revolut', notification_data)
            except ValidationError:
                _logger.exception("Unable to handle notification data; skipping to acknowledge.")
        return request.make_json_response('', status=200)

    @staticmethod
    def _resolve_refund_tx(source_tx_sudo, order_data):
        """ Find or create the refund transaction matching a Revolut refund order. """
        refund_tx_sudo = source_tx_sudo.child_transaction_ids.filtered(
            lambda tx: tx.operation == 'refund'
            and tx.provider_reference == order_data.get('id')
        )
        if not refund_tx_sudo:
            amount_to_refund = payment_utils.to_major_currency_units(
                order_data.get('amount', 0),
                source_tx_sudo.currency_id,
                arbitrary_decimal_number=CURRENCY_MINOR_UNITS.get(source_tx_sudo.currency_id.name),
            )
            refund_tx_sudo = source_tx_sudo._create_child_transaction(
                amount_to_refund, is_refund=True
            )
        return refund_tx_sudo

    def _verify_signature(self, tx_sudo):
        """ Check that the received signature matches the expected one.

        The signed payload is `v1.{timestamp}.{raw_body}`, HMAC-SHA256 with the webhook signing
        secret. During a rotation, the `Revolut-Signature` header carries multiple comma-separated
        signatures; the notification is valid if any of them matches.

        :raise Forbidden: If the timestamp is too old or no signature matches.
        """
        signing_secret = tx_sudo.provider_id.revolut_webhook_secret
        if not signing_secret:
            _logger.warning("Received webhook event but no signing secret is configured.")
            raise Forbidden()

        timestamp = request.httprequest.headers.get('Revolut-Request-Timestamp')
        try:
            event_timestamp = int(timestamp) / 1000
        except (TypeError, ValueError):
            _logger.warning("Received notification with missing or malformed timestamp.")
            raise Forbidden()
        if abs(time.time() - event_timestamp) > const.WEBHOOK_AGE_TOLERANCE:
            _logger.warning("Received notification with outdated timestamp: %s.", timestamp)
            raise Forbidden()

        signature_header = request.httprequest.headers.get('Revolut-Signature')
        if not signature_header:
            _logger.warning("Received notification with missing signature.")
            raise Forbidden()
        received_signatures = [entry.strip() for entry in signature_header.split(',')]
        signed_payload = f'v1.{timestamp}.{request.httprequest.data.decode("utf-8")}'
        expected_signature = 'v1=' + hmac.new(
            signing_secret.encode('utf-8'), signed_payload.encode('utf-8'), hashlib.sha256
        ).hexdigest()
        if not any(
            hmac.compare_digest(expected_signature, received_signature)
            for received_signature in received_signatures
        ):
            _logger.warning("Received notification with invalid signature.")
            raise Forbidden()
