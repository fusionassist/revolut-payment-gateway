# Part of payment_revolut. See LICENSE file for full copyright and licensing details.

import hashlib
import hmac
import pprint
import time

from werkzeug.exceptions import Forbidden

from odoo import http
from odoo.exceptions import ValidationError
from odoo.http import request

from odoo.addons.payment import utils as payment_utils
from odoo.addons.payment.const import CURRENCY_MINOR_UNITS
from odoo.addons.payment.logging import get_payment_logger
from odoo.addons.payment_revolut import const


_logger = get_payment_logger(__name__, const.SENSITIVE_KEYS)


class RevolutController(http.Controller):

    _return_url = '/payment/revolut/return'
    _webhook_url = '/payment/revolut/webhook'

    @http.route(_return_url, type='http', methods=['GET'], auth='public')
    def revolut_return(self, reference=None, access_token=None, **kwargs):
        """ Process the payment after the customer was redirected back from Revolut.

        The redirect data are never trusted: the authoritative order state is fetched from the
        API before processing. The webhook remains the source of truth for the final state if
        the customer never lands back on this route.

        :param str reference: The transaction reference embedded in the redirect URL.
        :param str access_token: The access token used to verify the reference.
        :param dict kwargs: Extra query parameters, ignored.
        """
        tx_sudo = request.env['payment.transaction'].sudo()._search_by_reference(
            'revolut', {'reference': reference}
        )
        if (
            tx_sudo
            and tx_sudo.provider_reference  # The order creation may have failed.
            and payment_utils.check_access_token(access_token, reference)
        ):
            try:
                order_data = tx_sudo._send_api_request(
                    'GET', f'orders/{tx_sudo.provider_reference}'
                )
            except ValidationError:
                _logger.error("Failed to fetch the order while processing the return from Revolut.")
            else:
                tx_sudo._process('revolut', {'reference': reference, 'order': order_data})
        return request.redirect('/payment/status')

    @http.route(_webhook_url, type='http', methods=['POST'], auth='public', csrf=False)
    def revolut_webhook(self):
        """ Process the payment data sent by Revolut to the webhook.

        The webhook payload is thin (event, order_id, merchant reference); the full order is
        fetched from the API after the signature is verified, which makes the processing
        idempotent and resilient to out-of-order events.

        :return: An empty string to acknowledge the notification.
        :rtype: str
        """
        data = request.get_json_data()
        _logger.info("Notification received from Revolut with data:\n%s", pprint.pformat(data))

        event_type = data.get('event')
        if event_type in const.HANDLED_WEBHOOK_EVENTS and data.get('order_id'):
            payment_data = {
                'event_type': event_type,
                'order_id': data.get('order_id'),
                'reference': data.get('merchant_order_ext_ref'),
            }
            try:
                tx_sudo = request.env['payment.transaction'].sudo()._search_by_reference(
                    'revolut', payment_data
                )
                if tx_sudo:
                    self._verify_signature(tx_sudo)

                    # Fetch the authoritative order state from the API.
                    order_data = tx_sudo._send_api_request(
                        'GET', f'orders/{data["order_id"]}'
                    )
                    payment_data['order'] = order_data

                    # Refund orders reference the source order's transaction when they were not
                    # initiated from Odoo (e.g., a refund issued from the Revolut dashboard); the
                    # payment data must then be processed on a dedicated refund transaction.
                    if (
                        order_data.get('type') == 'refund'
                        and tx_sudo.operation != 'refund'
                    ):
                        tx_sudo = self._resolve_refund_tx(tx_sudo, order_data)
                    tx_sudo._process('revolut', payment_data)
            except ValidationError:  # Acknowledge the notification to avoid getting spammed.
                _logger.exception("Unable to process the payment data; skipping to acknowledge")
        return request.make_json_response('')

    @staticmethod
    def _resolve_refund_tx(source_tx_sudo, order_data):
        """ Find or create the refund transaction matching a Revolut refund order.

        :param payment.transaction source_tx_sudo: The sudoed transaction of the source order.
        :param dict order_data: The Revolut order object of type 'refund'.
        :return: The sudoed refund transaction.
        :rtype: payment.transaction
        """
        refund_tx_sudo = source_tx_sudo.child_transaction_ids.filtered(
            lambda tx: tx.operation == 'refund'
            and tx.provider_reference == order_data.get('id')
        )
        if not refund_tx_sudo:
            amount_to_refund = payment_utils.to_major_currency_units(
                order_data.get('amount', 0),
                source_tx_sudo.currency_id,
                arbitrary_decimal_number=CURRENCY_MINOR_UNITS.get(
                    source_tx_sudo.currency_id.name
                ),
            )
            refund_tx_sudo = source_tx_sudo._create_child_transaction(
                amount_to_refund, is_refund=True
            )
        return refund_tx_sudo

    def _verify_signature(self, tx_sudo):
        """ Check that the received signature matches the expected one.

        The signed payload is `v1.{timestamp}.{raw_body}`, signed with HMAC-SHA256 using the
        webhook signing secret. During a signing secret rotation, the `Revolut-Signature` header
        contains multiple comma-separated signatures; the notification is valid if any of them
        matches. See
        https://developer.revolut.com/docs/guides/merchant/monitor-and-observe/webhooks/verify-the-payload-signature

        :param payment.transaction tx_sudo: The sudoed transaction referenced by the payment data.
        :return: None
        :raise Forbidden: If the timestamp is too old or if no signature matches.
        """
        signing_secret = tx_sudo.provider_id.revolut_webhook_secret
        if not signing_secret:
            _logger.warning("Received webhook event but no signing secret is configured.")
            raise Forbidden()

        # Check that the timestamp is not too old. It is expressed in milliseconds.
        timestamp = request.httprequest.headers.get('Revolut-Request-Timestamp')
        try:
            event_timestamp = int(timestamp) / 1000
        except (TypeError, ValueError):
            _logger.warning("Received payment data with missing or malformed timestamp.")
            raise Forbidden()
        if abs(time.time() - event_timestamp) > const.WEBHOOK_AGE_TOLERANCE:
            _logger.warning("Received payment data with outdated timestamp: %s.", timestamp)
            raise Forbidden()

        # Compare the received signatures with the expected signature computed from the payload.
        signature_header = request.httprequest.headers.get('Revolut-Signature')
        if not signature_header:
            _logger.warning("Received payment data with missing signature.")
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
            _logger.warning("Received payment data with invalid signature.")
            raise Forbidden()
