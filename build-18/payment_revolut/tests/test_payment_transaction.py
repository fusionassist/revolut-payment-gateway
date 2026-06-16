# Part of payment_revolut. See LICENSE file for full copyright and licensing details.

from unittest.mock import patch

from odoo.tests import tagged
from odoo.tools import mute_logger

from odoo.addons.payment import utils as payment_utils
from odoo.addons.payment_revolut.tests.common import RevolutCommon

_REQUEST = (
    'odoo.addons.payment_revolut.models.payment_provider.PaymentProvider._revolut_make_request'
)


@tagged('post_install', '-at_install')
class TestPaymentTransaction(RevolutCommon):

    def test_order_payload_values(self):
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

    def test_order_payload_manual_capture(self):
        self.provider.capture_manually = True
        tx = self._create_transaction('redirect')
        with patch.object(
            payment_utils, 'generate_access_token', self._generate_test_access_token
        ):
            payload = tx._revolut_prepare_order_payload()
        self.assertEqual(payload['capture_mode'], 'manual')

    def test_rendering_values_redirect_to_checkout_url(self):
        tx = self._create_transaction('redirect')
        order_data = self._get_order_data(state='pending')
        with patch(_REQUEST, return_value=order_data), patch.object(
            payment_utils, 'generate_access_token', self._generate_test_access_token
        ):
            rendering_values = tx._get_specific_rendering_values(None)
        self.assertEqual(rendering_values['api_url'], order_data['checkout_url'])
        self.assertEqual(tx.provider_reference, self.order_id)

    def test_process_pending(self):
        tx = self._create_transaction('redirect')
        tx._process_notification_data({'reference': tx.reference, 'order': self._get_order_data('pending')})
        self.assertEqual(tx.state, 'pending')

    def test_process_processing_is_pending(self):
        tx = self._create_transaction('redirect')
        tx._process_notification_data(
            {'reference': tx.reference, 'order': self._get_order_data('processing')}
        )
        self.assertEqual(tx.state, 'pending')

    def test_process_completed(self):
        tx = self._create_transaction('redirect')
        tx._process_notification_data(
            {'reference': tx.reference, 'order': self._get_order_data('completed')}
        )
        self.assertEqual(tx.state, 'done')

    def test_process_authorised_with_manual_capture(self):
        self.provider.capture_manually = True
        tx = self._create_transaction('redirect')
        tx._process_notification_data(
            {'reference': tx.reference, 'order': self._get_order_data('authorised')}
        )
        self.assertEqual(tx.state, 'authorized')

    def test_process_authorised_without_manual_capture_stays_draft(self):
        tx = self._create_transaction('redirect')
        tx._process_notification_data(
            {'reference': tx.reference, 'order': self._get_order_data('authorised')}
        )
        self.assertEqual(tx.state, 'draft')

    def test_process_cancelled(self):
        tx = self._create_transaction('redirect')
        tx._process_notification_data(
            {'reference': tx.reference, 'order': self._get_order_data('cancelled')}
        )
        self.assertEqual(tx.state, 'cancel')

    def test_process_failed_with_decline_reason(self):
        tx = self._create_transaction('redirect')
        order_data = self._get_order_data('failed')
        order_data['payments'][-1]['decline_reason'] = 'insufficient_funds'
        tx._process_notification_data({'reference': tx.reference, 'order': order_data})
        self.assertEqual(tx.state, 'error')
        self.assertIn('insufficient_funds', tx.state_message)

    @mute_logger('odoo.addons.payment_revolut.models.payment_transaction')
    def test_process_unknown_state_is_error(self):
        tx = self._create_transaction('redirect')
        tx._process_notification_data(
            {'reference': tx.reference, 'order': self._get_order_data('whatever')}
        )
        self.assertEqual(tx.state, 'error')

    def test_get_tx_prefers_order_id(self):
        tx = self._create_transaction('redirect')
        tx.provider_reference = self.order_id
        found = self.env['payment.transaction']._get_tx_from_notification_data(
            'revolut', {'order_id': self.order_id, 'reference': 'wrong-ref'}
        )
        self.assertEqual(found, tx)

    def test_get_tx_falls_back_to_reference(self):
        tx = self._create_transaction('redirect')
        found = self.env['payment.transaction']._get_tx_from_notification_data(
            'revolut', {'order_id': 'unknown-order-id', 'reference': tx.reference}
        )
        self.assertEqual(found, tx)

    def test_refund_request(self):
        source_tx = self._create_transaction('redirect', state='done')
        source_tx.provider_reference = self.order_id
        refund_order_data = self._get_order_data(
            'completed', id='refund-order-id', type='refund', amount=1000
        )
        with patch(_REQUEST, return_value=refund_order_data) as request_mock:
            source_tx._send_refund_request(amount_to_refund=10.0)
        self.assertIn(f'orders/{self.order_id}/refund', request_mock.call_args.args[0])
        refund_tx = source_tx.child_transaction_ids.filtered(lambda t: t.operation == 'refund')
        self.assertTrue(refund_tx)
        self.assertEqual(refund_tx.state, 'done')
        self.assertEqual(refund_tx.provider_reference, 'refund-order-id')
