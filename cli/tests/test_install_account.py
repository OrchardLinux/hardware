"""Asking for the installed system's password and PIN."""
import unittest

from orchard_cli import install_account as A


def answers(*values):
    queue = list(values)
    return lambda prompt: queue.pop(0)


class InstallAccount(unittest.TestCase):
    def test_asks_each_twice(self):
        said = []
        secrets = A.ask_secrets(answers('long enough', 'long enough', '123456', '123456'), said.append)
        self.assertEqual(secrets, {'password': 'long enough', 'pin': '123456'})

    def test_retries_until_valid_and_matching(self):
        said = []
        secrets = A.ask_secrets(answers('short', 'long enough', 'different', 'long enough', 'long enough',
                                        '12345', '１２３４５６', '654321', '654321'), said.append)
        self.assertEqual(secrets['pin'], '654321')
        self.assertEqual(said[1:], ['Use at least 8 characters.', 'They do not match.',
                                    'The PIN is exactly 6 digits.', 'The PIN is exactly 6 digits.'])


if __name__ == '__main__':
    unittest.main()
