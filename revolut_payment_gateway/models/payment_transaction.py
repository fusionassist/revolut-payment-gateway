# Part of revolut_payment_gateway. See LICENSE file for full copyright and licensing details.

from urllib.parse import urljoin

from werkzeug import urls

from odoo import _, api, models
from odoo.exceptions import ValidationError

from odoo.addons.payment import utils as payment_utils
from odoo.addons.payment.const import CURRENCY_MINOR_UNITS
from odoo.addons.payment.logging import get_payment_logger
from odoo.addons.revolut_payment_gateway import const
from odoo.addons.revolut_payment_gateway.controllers.main import RevolutController


_logger = get_payment_logger(__name__, const.SENSITIVE_KEYS)


class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    def _get_specific_rendering_values(self, processing_values):
        """ Override of `payment` to return Revolut-specific rendering values.

        A Revolut order is created server-side and the customer is redirected to the returned
        hosted checkout page.

        Note: self.ensure_one() from `_get_processing_values`

        :param dict processing_values: The generic and specific processing values of the
                                       transaction.
        :return: The dict of provider-specific rendering values.
        :rtype: dict
        """
        res = super()._get_specific_rendering_values(processing_values)
        if self.provider_code != 'revolut':
            return res

        payload = self._revolut_prepare_order_payload()
        try:
            order_data = self._send_api_request(
                'POST', 'orders', json=payload, idempotency_key=self.reference
            )
        except ValidationError as error:
            self._set_error(str(error))
            return {}

        # The order id is permanent; store it right away so that the return controller and the
        # webhook handler can fetch the authoritative order state from the API.
        self.provider_reference = order_data.get('id')

        return {'api_url': order_data.get('checkout_url')}

    def _revolut_prepare_order_payload(self):
        """ Prepare the payload for the order creation request.

        :return: The request payload.
        :rtype: dict
        """
        base_url = self.provider_id._revolut_get_base_url()
        return_url_params = urls.url_encode({
            'reference': self.reference,
            'access_token': payment_utils.generate_access_token(self.reference),
        })
        payload = {
            'amount': payment_utils.to_minor_currency_units(
                self.amount,
                self.currency_id,
                arbitrary_decimal_number=CURRENCY_MINOR_UNITS.get(self.currency_id.name),
            ),
            'currency': self.currency_id.name,
            'description': self.reference,
            'capture_mode': 'manual' if self.provider_id.capture_manually else 'automatic',
            'redirect_url': f'{urljoin(base_url, RevolutController._return_url)}'
                            f'?{return_url_params}',
            'merchant_order_data': {
                'reference': self.reference,
            },
        }
        customer = {}
        if self.partner_name:
            customer['full_name'] = self.partner_name
        if self.partner_email:
            customer['email'] = self.partner_email
        if customer:
            payload['customer'] = customer
        return payload

    def _send_refund_request(self):
        """Override of `payment` to send a refund request to Revolut."""
        if self.provider_code != 'revolut':
            return super()._send_refund_request()

        order_data = self._send_api_request(
            'POST',
            f'orders/{self.source_transaction_id.provider_reference}/refund',
            json={
                'amount': payment_utils.to_minor_currency_units(
                    -self.amount,  # Refund transactions' amount is negative, inverse it.
                    self.currency_id,
                    arbitrary_decimal_number=CURRENCY_MINOR_UNITS.get(self.currency_id.name),
                ),
                'currency': self.currency_id.name,
                'description': _("Refund of %s", self.source_transaction_id.reference),
                'merchant_order_data': {
                    'reference': self.reference,  # Sent back as the webhook's ext ref.
                },
            },
            idempotency_key=self.reference,
        )

        # The response is a new order object of type 'refund'.
        self._process('revolut', {'reference': self.reference, 'order': order_data})

    def _send_capture_request(self):
        """Override of `payment` to send a capture request to Revolut."""
        if self.provider_code != 'revolut':
            return super()._send_capture_request()

        order_data = self._send_api_request(
            'POST',
            f'orders/{self.source_transaction_id.provider_reference}/capture',
            json={
                'amount': payment_utils.to_minor_currency_units(
                    self.amount,
                    self.currency_id,
                    arbitrary_decimal_number=CURRENCY_MINOR_UNITS.get(self.currency_id.name),
                ),
            },
            idempotency_key=self.reference,
        )
        self._process('revolut', {'reference': self.reference, 'order': order_data})

    def _send_void_request(self):
        """Override of `payment` to send a void request to Revolut."""
        if self.provider_code != 'revolut':
            return super()._send_void_request()

        order_data = self._send_api_request(
            'POST', f'orders/{self.source_transaction_id.provider_reference}/cancel'
        )
        self._process('revolut', {'reference': self.reference, 'order': order_data})

    @api.model
    def _search_by_reference(self, provider_code, payment_data):
        """ Override of `payment` to find the transaction based on Revolut data.

        Webhook notifications carry the Revolut order id and, for payment orders, the merchant
        reference. The order id takes precedence as it unambiguously identifies the transaction,
        including refund transactions whose related refund orders don't carry the merchant
        reference of the source transaction.

        :param str provider_code: The code of the provider handling the transaction.
        :param dict payment_data: The payment data sent by the provider.
        :return: The transaction, if found.
        :rtype: payment.transaction
        """
        if provider_code != 'revolut':
            return super()._search_by_reference(provider_code, payment_data)

        tx = self
        order_id = payment_data.get('order_id') or payment_data.get('order', {}).get('id')
        reference = payment_data.get('reference')
        if order_id:
            tx = self.search([
                ('provider_reference', '=', order_id), ('provider_code', '=', 'revolut'),
            ])
        if not tx and reference:
            tx = self.search([
                ('reference', '=', reference), ('provider_code', '=', 'revolut'),
            ])
        if not tx:
            _logger.warning(
                "No transaction found matching order id %(order_id)s (reference %(ref)s).",
                {'order_id': order_id, 'ref': reference},
            )
        return tx

    def _extract_amount_data(self, payment_data):
        """Override of `payment` to extract the amount and currency from the payment data."""
        if self.provider_code != 'revolut':
            return super()._extract_amount_data(payment_data)

        order_data = payment_data.get('order', {})
        currency_code = order_data.get('currency', '')
        amount = payment_utils.to_major_currency_units(
            order_data.get('amount', 0),
            self.currency_id,
            arbitrary_decimal_number=CURRENCY_MINOR_UNITS.get(currency_code),
        )
        return {
            'amount': amount,
            'currency_code': currency_code,
            'precision_digits': CURRENCY_MINOR_UNITS.get(currency_code),
        }

    def _apply_updates(self, payment_data):
        """Override of `payment` to update the transaction based on the payment data."""
        if self.provider_code != 'revolut':
            return super()._apply_updates(payment_data)

        order_data = payment_data.get('order', {})

        # Update the provider reference. For refund transactions, the reference is the id of the
        # refund order created by Revolut, not the id of the source order. Capture and void child
        # transactions must not copy the source order id: the provider reference must stay unique
        # per order for webhook notifications to resolve to a single transaction.
        if order_data.get('id') and (
            not self.provider_reference or order_data.get('type') == 'refund'
        ):
            self.provider_reference = order_data['id']

        # Update the payment method based on the last payment made towards the order.
        payments = order_data.get('payments') or []
        last_payment = payments[-1] if payments else {}
        payment_method_type = (last_payment.get('payment_method') or {}).get('type')
        if payment_method_type:
            if payment_method_type in const.REVOLUT_PAY_METHOD_TYPES:
                payment_method_type = 'revolut_pay'
            payment_method = self.env['payment.method']._get_from_code(payment_method_type)
            self.payment_method_id = payment_method or self.payment_method_id

        # Update the payment state. The order state is the source of truth: payment-level events
        # such as a declined attempt don't fail the order, as the customer can retry the payment
        # from the hosted checkout page until the order expires.
        state = order_data.get('state')
        if not state:
            self._set_error(_("Received data with missing order state."))
        elif state in const.PAYMENT_STATUS_MAPPING['pending']:
            self._set_pending()
        elif state in const.PAYMENT_STATUS_MAPPING['authorized']:
            if self.provider_id.capture_manually:
                self._set_authorized()
            # In automatic capture mode, 'authorised' is a transient state; the order completes
            # without further action and the transaction is updated when it does.
        elif state in const.PAYMENT_STATUS_MAPPING['done']:
            self._set_done()

            # Immediately post-process the transaction if it is a refund, as the post-processing
            # will not be triggered by a customer browsing the transaction from the portal.
            if self.operation == 'refund':
                self.env.ref('payment.cron_post_process_payment_tx')._trigger()
        elif state in const.PAYMENT_STATUS_MAPPING['cancel']:
            self._set_canceled()
        elif state in const.PAYMENT_STATUS_MAPPING['error']:
            decline_reason = last_payment.get('decline_reason')
            if decline_reason:
                self._set_error(_(
                    "An error occurred during the processing of your payment (%s). Please try"
                    " again.", decline_reason,
                ))
            else:
                self._set_error(
                    _("An error occurred during the processing of your payment. Please try again.")
                )
        else:
            _logger.warning(
                "Received invalid order state (%s) for transaction %s.", state, self.reference
            )
            self._set_error(_("Received data with invalid order state: %s.", state))
