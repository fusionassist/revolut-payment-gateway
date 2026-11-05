# Part of revolut_payment_gateway. See LICENSE file for full copyright and licensing details.

{
    'name': "Revolut Payment Gateway",
    'version': '18.0.1.0.0',
    'category': 'Accounting/Payment Providers',
    'sequence': 350,
    'summary': "Accept payments via Revolut hosted checkout (cards and Revolut Pay).",
    'description': " ",  # Non-empty string to avoid loading the README file.
    'author': "Fusion Technologies",
    'website': "https://fusiontechnologies.ie",
    'depends': ['payment'],
    'price': 59.99,
    'currency': 'EUR',
    'images': ['static/description/banner.png'],
    'support': 'hello@fusiontechnologies.ie',
    'data': [
        'views/payment_provider_views.xml',
        'views/revolut_payment_gateway_templates.xml',

        'data/payment_provider_data.xml',  # Depends on revolut_payment_gateway_templates.xml
    ],
    'post_init_hook': 'post_init_hook',
    'uninstall_hook': 'uninstall_hook',
    'license': 'OPL-1',
}
