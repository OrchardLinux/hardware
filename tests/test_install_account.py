"""Asking for the installed system's password and PIN."""
import unittest

from orchard import install_account as A

TABLET = {'user': 'tablet', 'password': True, 'pin': True, 'passwordless_sudo': False}


def answers(*values):
    queue = list(values)
    return lambda prompt: queue.pop(0)


class InstallAccount(unittest.TestCase):
    def test_asks_each_twice(self):
        said = []
        secrets = A.ask_secrets(TABLET, answers('long enough', 'long enough', '123456', '123456'), said.append)
        self.assertEqual(secrets, {'password': 'long enough', 'pin': '123456'})

    def test_retries_until_valid_and_matching(self):
        said = []
        secrets = A.ask_secrets(TABLET, answers('short', 'long enough', 'different', 'long enough', 'long enough',
                                        '12345', '１２３４５６', '654321', '654321'), said.append)
        self.assertEqual(secrets['pin'], '654321')
        self.assertEqual([line.strip() for line in said if line.strip()],
                         ["Setting up the user 'tablet'", 'Use at least 8 characters.', 'They do not match.',
                          'The PIN is exactly 6 digits.', 'The PIN is exactly 6 digits.'])


    def test_only_what_the_release_declares_is_asked(self):
        secrets = A.ask_secrets({**TABLET, 'pin': False}, answers('long enough', 'long enough'), lambda _: None)
        self.assertEqual(secrets, {'password': 'long enough'})


if __name__ == '__main__':
    unittest.main()
