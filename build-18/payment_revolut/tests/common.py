# Part of payment_revolut. See LICENSE file for full copyright and licensing details.

from odoo.addons.payment.tests.common import PaymentCommon


class RevolutCommon(PaymentCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.revolut = cls._prepare_provider('revolut', update_values={
            'revolut_secret_key': 'sk_dummy_secret_key',
            'revolut_webhook_secret': 'wsk_dummy_signing_secret',
            'revolut_webhook_id': 'dummy-webhook-id',
        })
        cls.provider = cls.revolut
        cls.currency = cls.currency_euro
        cls.amount = 70.34

        cls.order_id = '0adc0e3c-ab44-4f33-bcc0-534ded7354ce'
        cls.webhook_payload = {
            'event': 'ORDER_COMPLETED',
            'order_id': cls.order_id,
            'merchant_order_ext_ref': cls.reference,
        }

    @classmethod
    def _get_order_data(cls, state='completed', **kwargs):
        """ Return a Revolut order object in the given state. """
        return {
            'id': cls.order_id,
            'token': 'dummy-order-token',
            'type': 'payment',
            'state': state,
            'amount': 7034,
            'currency': 'EUR',
            'capture_mode': 'automatic',
            'checkout_url': 'https://checkout.revolut.com/payment-link/dummy-order-token',
            'merchant_order_data': {'reference': cls.reference},
            'payments': [{
                'id': '6645aaa3-f00d-a600-17cd-c66b00000000',
                'state': 'captured' if state == 'completed' else 'pending',
                'amount': 7034,
                'currency': 'EUR',
                'payment_method': {'type': 'card'},
            }],
            **kwargs,
        }
