# Part of revolut_payment_gateway. See LICENSE file for full copyright and licensing details.

import hashlib
import hmac
import json
import time
from unittest.mock import patch

from odoo.tests import tagged
from odoo.tools import mute_logger

from odoo.addons.payment.tests.http_common import PaymentHttpCommon
from odoo.addons.revolut_payment_gateway.controllers.main import RevolutController
from odoo.addons.revolut_payment_gateway.tests.common import RevolutCommon

_REQUEST = (
    'odoo.addons.revolut_payment_gateway.models.payment_provider.PaymentProvider._revolut_make_request'
)


@tagged('post_install', '-at_install')
class TestProcessingFlows(RevolutCommon, PaymentHttpCommon):

    def _build_signature_headers(self, payload, secret=None, timestamp=None, extra_signatures=()):
        timestamp = timestamp if timestamp is not None else int(time.time() * 1000)
        secret = secret or self.provider.revolut_webhook_secret
        signed_payload = f'v1.{timestamp}.{payload}'
        signature = 'v1=' + hmac.new(
            secret.encode(), signed_payload.encode(), hashlib.sha256
        ).hexdigest()
        signatures = ','.join((*extra_signatures, signature))
        return {
            'Content-Type': 'application/json',
            'Revolut-Request-Timestamp': str(timestamp),
            'Revolut-Signature': signatures,
        }

    def _post_webhook(self, headers):
        payload = json.dumps(self.webhook_payload)
        url = self._build_url(RevolutController._webhook_url)
        with patch(_REQUEST, return_value=self._get_order_data('completed')):
            return self.url_open(url, data=payload, headers=headers)

    def test_webhook_with_valid_signature_processes_payment(self):
        tx = self._create_transaction('redirect')
        tx.provider_reference = self.order_id
        response = self._post_webhook(self._build_signature_headers(json.dumps(self.webhook_payload)))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(tx.state, 'done')

    def test_webhook_replay_is_idempotent(self):
        tx = self._create_transaction('redirect')
        tx.provider_reference = self.order_id
        headers = self._build_signature_headers(json.dumps(self.webhook_payload))
        self._post_webhook(headers)
        response = self._post_webhook(headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(tx.state, 'done')

    @mute_logger('odoo.addons.revolut_payment_gateway.controllers.main')
    def test_webhook_with_invalid_signature_is_rejected(self):
        tx = self._create_transaction('redirect')
        tx.provider_reference = self.order_id
        headers = self._build_signature_headers(
            json.dumps(self.webhook_payload), secret='wsk_wrong_secret'
        )
        response = self._post_webhook(headers)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(tx.state, 'draft')

    @mute_logger('odoo.addons.revolut_payment_gateway.controllers.main')
    def test_webhook_with_outdated_timestamp_is_rejected(self):
        tx = self._create_transaction('redirect')
        tx.provider_reference = self.order_id
        outdated = int((time.time() - 600) * 1000)
        headers = self._build_signature_headers(json.dumps(self.webhook_payload), timestamp=outdated)
        response = self._post_webhook(headers)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(tx.state, 'draft')

    def test_webhook_with_rotated_secret_accepts_any_valid_signature(self):
        tx = self._create_transaction('redirect')
        tx.provider_reference = self.order_id
        stale_signature = 'v1=' + '0' * 64
        headers = self._build_signature_headers(
            json.dumps(self.webhook_payload), extra_signatures=(stale_signature,)
        )
        response = self._post_webhook(headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(tx.state, 'done')

    @mute_logger('odoo.addons.revolut_payment_gateway.controllers.main')
    def test_webhook_without_signing_secret_is_rejected(self):
        self.provider.revolut_webhook_secret = False
        tx = self._create_transaction('redirect')
        tx.provider_reference = self.order_id
        headers = self._build_signature_headers(
            json.dumps(self.webhook_payload), secret='wsk_anything'
        )
        response = self._post_webhook(headers)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(tx.state, 'draft')

    def test_webhook_ignores_unhandled_events(self):
        self._create_transaction('redirect')
        self.webhook_payload = dict(self.webhook_payload, event='PAYOUT_INITIATED')
        headers = self._build_signature_headers(json.dumps(self.webhook_payload))
        with patch(
            'odoo.addons.payment.models.payment_transaction.PaymentTransaction'
            '._process_notification_data'
        ) as process_mock:
            response = self._post_webhook(headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(process_mock.call_count, 0)

    def test_return_flow_fetches_order_and_processes(self):
        tx = self._create_transaction('redirect')
        tx.provider_reference = self.order_id
        access_token = self._generate_test_access_token(tx.reference)
        url = self._build_url(
            f'{RevolutController._return_url}?reference={tx.reference}&access_token={access_token}'
        )
        with patch(_REQUEST, return_value=self._get_order_data('completed')):
            self._make_http_get_request(url)
        self.assertEqual(tx.state, 'done')

    def test_return_flow_with_bad_access_token_does_nothing(self):
        tx = self._create_transaction('redirect')
        tx.provider_reference = self.order_id
        url = self._build_url(
            f'{RevolutController._return_url}?reference={tx.reference}&access_token=forged'
        )
        with patch(_REQUEST, return_value=self._get_order_data('completed')):
            self._make_http_get_request(url)
        self.assertEqual(tx.state, 'draft')
