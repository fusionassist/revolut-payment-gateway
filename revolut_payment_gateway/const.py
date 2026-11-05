# Part of revolut_payment_gateway. See LICENSE file for full copyright and licensing details.

# The Merchant API version against which the module was built and tested.
# See https://developer.revolut.com/docs/guides/merchant/reference/versioning/api-versions
API_VERSION = '2026-03-12'

# The base URLs of the Merchant API, keyed by provider state.
API_URLS = {
    'enabled': 'https://merchant.revolut.com/api/',
    'test': 'https://sandbox-merchant.revolut.com/api/',
    'disabled': 'https://sandbox-merchant.revolut.com/api/',
}

# The currencies supported for card payments on the hosted checkout page, in ISO 4217 format.
SUPPORTED_CURRENCIES = [
    'AUD',
    'CAD',
    'CHF',
    'CZK',
    'DKK',
    'EUR',
    'GBP',
    'HKD',
    'HUF',
    'ILS',
    'JPY',
    'MXN',
    'NOK',
    'NZD',
    'PLN',
    'RON',
    'SEK',
    'SGD',
    'TRY',
    'USD',
    'ZAR',
]

# The codes of the payment methods to activate when Revolut is activated.
DEFAULT_PAYMENT_METHOD_CODES = {
    'card',
    'revolut_pay',
}

# The `payment_method.type` values of the Revolut order's payments that map to the generic
# `revolut_pay` payment method code in Odoo.
REVOLUT_PAY_METHOD_TYPES = {
    'revolut_pay_card',
    'revolut_pay_account',
}

# Mapping of transaction states to Revolut order states.
# See https://developer.revolut.com/docs/guides/merchant/reference/order-lifecycle
PAYMENT_STATUS_MAPPING = {
    'pending': ('pending', 'processing'),
    'authorized': ('authorised',),
    'done': ('completed',),
    'cancel': ('cancelled',),
    'error': ('failed',),
}

# The webhook events that are registered with Revolut and processed by the webhook controller.
HANDLED_WEBHOOK_EVENTS = [
    'ORDER_COMPLETED',
    'ORDER_AUTHORISED',
    'ORDER_CANCELLED',
    'ORDER_FAILED',
    'ORDER_PAYMENT_DECLINED',
    'ORDER_PAYMENT_FAILED',
]

# The maximum age of an incoming webhook notification, in seconds, as recommended by Revolut.
# The `Revolut-Request-Timestamp` header is expressed in milliseconds.
WEBHOOK_AGE_TOLERANCE = 5 * 60
