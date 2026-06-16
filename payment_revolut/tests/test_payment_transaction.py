# Part of payment_revolut. See LICENSE file for full copyright and licensing details.

from unittest.mock import patch

from odoo.tests import tagged
from odoo.tools import mute_logger

from odoo.addons.payment import utils as payment_utils
from odoo.addons.payment_revolut.tests.common import RevolutCommon


@tagged('post_install', '-at_install')
class TestPaymentTransaction(RevolutCommon):

    def test_order_payload_values(self):
        """ Test that the order creation payload is built with the correct values. """
        tx = self._create_transaction('redirect')
        with patch.object(
            payment_utils, 'generate_access_token', self._generate_test_access_token
        ):
            payload = tx._revolut_prepare_order_payload()
        self.assertEqual(payload['amount'], 7034)  # €70.34 in minor units.
        self.assertEqual(payload['currency'], 'EUR')
        self.assertEqual(payload['capture_mode'], 'automatic')
        self.assertEqual(payload['merchant_order_data']['reference'], tx.reference)
        self.assertIn('/payment/revolut/return', payload['redirect_url'])
        self.assertIn('access_token=', payload['redirect_url'])

    def test_order_payload_manual_capture(self):
        """ Test that the manual capture mode is requested when enabled on the provider. """
        self.provider.capture_manually = True
        tx = self._create_transaction('redirect')
        with patch.object(
            payment_utils, 'generate_access_token', self._generate_test_access_token
        ):
            payload = tx._revolut_prepare_order_payload()
        self.assertEqual(payload['capture_mode'], 'manual')

    def test_rendering_values_redirect_to_checkout_url(self):
        """ Test that the rendering values redirect to the hosted checkout page and that the
        order id is stored as the provider reference. """
        tx = self._create_transaction('redirect')
        order_data = self._get_order_data(state='pending')
        with patch(
            'odoo.addons.payment.models.payment_transaction.PaymentTransaction'
            '._send_api_request', return_value=order_data,
        ), patch.object(
            payment_utils, 'generate_access_token', self._generate_test_access_token
        ):
            rendering_values = tx._get_specific_rendering_values(None)
        self.assertEqual(rendering_values['api_url'], order_data['checkout_url'])
        self.assertEqual(tx.provider_reference, self.order_id)

    def test_apply_updates_pending(self):
        tx = self._create_transaction('redirect')
        tx._process('revolut', {'reference': tx.reference, 'order': self._get_order_data('pending')})
        self.assertEqual(tx.state, 'pending')

    def test_apply_updates_processing_is_pending(self):
        tx = self._create_transaction('redirect')
        tx._process(
            'revolut', {'reference': tx.reference, 'order': self._get_order_data('processing')}
        )
        self.assertEqual(tx.state, 'pending')

    def test_apply_updates_completed(self):
        tx = self._create_transaction('redirect')
        tx._process(
            'revolut', {'reference': tx.reference, 'order': self._get_order_data('completed')}
        )
        self.assertEqual(tx.state, 'done')

    def test_apply_updates_authorised_with_manual_capture(self):
        self.provider.capture_manually = True
        tx = self._create_transaction('redirect')
        tx._process(
            'revolut', {'reference': tx.reference, 'order': self._get_order_data('authorised')}
        )
        self.assertEqual(tx.state, 'authorized')

    def test_apply_updates_authorised_without_manual_capture(self):
        """ Test that the transient 'authorised' state is ignored in automatic capture mode. """
        tx = self._create_transaction('redirect')
        tx._process(
            'revolut', {'reference': tx.reference, 'order': self._get_order_data('authorised')}
        )
        self.assertEqual(tx.state, 'draft')

    def test_apply_updates_cancelled(self):
        tx = self._create_transaction('redirect')
        tx._process(
            'revolut', {'reference': tx.reference, 'order': self._get_order_data('cancelled')}
        )
        self.assertEqual(tx.state, 'cancel')

    def test_apply_updates_failed_with_decline_reason(self):
        tx = self._create_transaction('redirect')
        order_data = self._get_order_data('failed')
        order_data['payments'][-1]['decline_reason'] = 'insufficient_funds'
        tx._process('revolut', {'reference': tx.reference, 'order': order_data})
        self.assertEqual(tx.state, 'error')
        self.assertIn('insufficient_funds', tx.state_message)

    @mute_logger('odoo.addons.payment_revolut.models.payment_transaction')
    def test_apply_updates_unknown_state_is_error(self):
        tx = self._create_transaction('redirect')
        tx._process(
            'revolut', {'reference': tx.reference, 'order': self._get_order_data('whatever')}
        )
        self.assertEqual(tx.state, 'error')

    def test_amount_mismatch_sets_error(self):
        """ Test that a mismatch between the order amount and the transaction amount is caught
        by the generic amount validation. """
        tx = self._create_transaction('redirect')
        order_data = self._get_order_data('completed', amount=9999)
        tx._process('revolut', {'reference': tx.reference, 'order': order_data})
        self.assertEqual(tx.state, 'error')

    def test_search_by_reference_prefers_order_id(self):
        tx = self._create_transaction('redirect')
        tx.provider_reference = self.order_id
        found = self.env['payment.transaction']._search_by_reference(
            'revolut', {'order_id': self.order_id, 'reference': 'wrong-ref'}
        )
        self.assertEqual(found, tx)

    def test_search_by_reference_falls_back_to_reference(self):
        tx = self._create_transaction('redirect')
        found = self.env['payment.transaction']._search_by_reference(
            'revolut', {'order_id': 'unknown-order-id', 'reference': tx.reference}
        )
        self.assertEqual(found, tx)

    def test_refund_request_payload(self):
        """ Test that the refund request is sent with the correct payload and that the response
        is processed on the refund transaction. """
        source_tx = self._create_transaction('redirect', state='done')
        source_tx.provider_reference = self.order_id
        refund_order_data = self._get_order_data(
            'completed', id='refund-order-id', type='refund', amount=1000
        )
        with patch(
            'odoo.addons.payment.models.payment_transaction.PaymentTransaction'
            '._send_api_request', return_value=refund_order_data,
        ) as send_request_mock:
            source_tx._refund(amount_to_refund=10.0)
        call_args = send_request_mock.call_args
        self.assertEqual(call_args.args[0], 'POST')
        self.assertEqual(call_args.args[1], f'orders/{self.order_id}/refund')
        self.assertEqual(call_args.kwargs['json']['amount'], 1000)
        self.assertEqual(call_args.kwargs['json']['currency'], 'EUR')
        refund_tx = source_tx.child_transaction_ids.filtered(lambda t: t.operation == 'refund')
        self.assertTrue(refund_tx)
        self.assertEqual(refund_tx.state, 'done')
        self.assertEqual(refund_tx.provider_reference, 'refund-order-id')
