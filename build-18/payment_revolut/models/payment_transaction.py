# Part of payment_revolut. See LICENSE file for full copyright and licensing details.

import logging
from urllib.parse import urljoin

from werkzeug import urls

from odoo import _, models
from odoo.exceptions import ValidationError

from odoo.addons.payment import utils as payment_utils
from odoo.addons.payment.const import CURRENCY_MINOR_UNITS
from odoo.addons.payment_revolut import const
from odoo.addons.payment_revolut.controllers.main import RevolutController


_logger = logging.getLogger(__name__)


class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    def _get_specific_rendering_values(self, processing_values):
        """ Override of `payment` to return Revolut-specific rendering values.

        Note: self.ensure_one() from `_get_processing_values`
        """
        res = super()._get_specific_rendering_values(processing_values)
        if self.provider_code != 'revolut':
            return res

        payload = self._revolut_prepare_order_payload()
        order_data = self.provider_id._revolut_make_request(
            'orders', payload=payload, idempotency_key=self.reference
        )
        self.provider_reference = order_data.get('id')
        return {'api_url': order_data.get('checkout_url')}

    def _revolut_prepare_order_payload(self):
        """ Prepare the payload for the order creation request. """
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
            'redirect_url': f'{urljoin(base_url, RevolutController._return_url)}?{return_url_params}',
            'merchant_order_data': {'reference': self.reference},
        }
        customer = {}
        if self.partner_name:
            customer['full_name'] = self.partner_name
        if self.partner_email:
            customer['email'] = self.partner_email
        if customer:
            payload['customer'] = customer
        return payload

    def _send_refund_request(self, amount_to_refund=None):
        """ Override of `payment` to send a refund request to Revolut. """
        refund_tx = super()._send_refund_request(amount_to_refund=amount_to_refund)
        if self.provider_code != 'revolut':
            return refund_tx

        order_data = self.provider_id._revolut_make_request(
            f'orders/{self.provider_reference}/refund',
            payload={
                'amount': payment_utils.to_minor_currency_units(
                    -refund_tx.amount,  # Refund transactions' amount is negative, inverse it.
                    refund_tx.currency_id,
                    arbitrary_decimal_number=CURRENCY_MINOR_UNITS.get(refund_tx.currency_id.name),
                ),
                'currency': refund_tx.currency_id.name,
                'description': _("Refund of %s", self.reference),
                'merchant_order_data': {'reference': refund_tx.reference},
            },
            idempotency_key=refund_tx.reference,
        )
        refund_tx._handle_notification_data(
            'revolut', {'reference': refund_tx.reference, 'order': order_data}
        )
        return refund_tx

    def _send_capture_request(self, amount_to_capture=None):
        """ Override of `payment` to send a capture request to Revolut. """
        child_capture_tx = super()._send_capture_request(amount_to_capture=amount_to_capture)
        if self.provider_code != 'revolut':
            return child_capture_tx

        order_data = self.provider_id._revolut_make_request(
            f'orders/{self.provider_reference}/capture',
            payload={
                'amount': payment_utils.to_minor_currency_units(
                    amount_to_capture or self.amount,
                    self.currency_id,
                    arbitrary_decimal_number=CURRENCY_MINOR_UNITS.get(self.currency_id.name),
                ),
            },
            idempotency_key=self.reference,
        )
        self._handle_notification_data('revolut', {'reference': self.reference, 'order': order_data})
        return child_capture_tx

    def _send_void_request(self, amount_to_void=None):
        """ Override of `payment` to send a void request to Revolut. """
        child_void_tx = super()._send_void_request(amount_to_void=amount_to_void)
        if self.provider_code != 'revolut':
            return child_void_tx

        order_data = self.provider_id._revolut_make_request(
            f'orders/{self.provider_reference}/cancel'
        )
        self._handle_notification_data('revolut', {'reference': self.reference, 'order': order_data})
        return child_void_tx

    def _get_tx_from_notification_data(self, provider_code, notification_data):
        """ Override of `payment` to find the transaction based on Revolut data. """
        tx = super()._get_tx_from_notification_data(provider_code, notification_data)
        if provider_code != 'revolut' or len(tx) == 1:
            return tx

        order_id = notification_data.get('order_id') or notification_data.get('order', {}).get('id')
        reference = notification_data.get('reference')
        if order_id:
            tx = self.search([
                ('provider_reference', '=', order_id), ('provider_code', '=', 'revolut'),
            ])
        if not tx and reference:
            tx = self.search([
                ('reference', '=', reference), ('provider_code', '=', 'revolut'),
            ])
        if not tx:
            raise ValidationError("Revolut: " + _(
                "No transaction found matching order %(order)s / reference %(ref)s.",
                order=order_id, ref=reference,
            ))
        return tx

    def _process_notification_data(self, notification_data):
        """ Override of `payment` to process the transaction based on Revolut data.

        Note: self.ensure_one()
        """
        self.ensure_one()

        super()._process_notification_data(notification_data)
        if self.provider_code != 'revolut':
            return

        order_data = notification_data.get('order', {})

        # Update the provider reference. Capture/void child transactions must not copy the source
        # order id, so that webhook notifications resolve to a single transaction.
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

        # Update the payment state from the authoritative order state.
        state = order_data.get('state')
        if not state:
            self._set_error(_("Received data with missing order state."))
        elif state in const.PAYMENT_STATUS_MAPPING['pending']:
            self._set_pending()
        elif state in const.PAYMENT_STATUS_MAPPING['authorized']:
            if self.provider_id.capture_manually:
                self._set_authorized()
            # In automatic capture mode, 'authorised' is transient; the order completes on its own.
        elif state in const.PAYMENT_STATUS_MAPPING['done']:
            self._set_done()
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
