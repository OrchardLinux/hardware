"""The installed system's password and lock-screen PIN, asked on the host.

They are asked before anything is written, so an install never waits for
input halfway. The CLI uploads them as a root-only file for one run (see
install_device.py); they never go on a command line or into the plan.
"""
import getpass

MIN_PASSWORD = 8


def password_problem(password):
    if len(password) < MIN_PASSWORD:
        return f'Use at least {MIN_PASSWORD} characters.'
    if '\n' in password or '\0' in password:
        return 'The password cannot contain a line break.'
    return None


def pin_problem(pin):
    if len(pin) != 6 or not (pin.isascii() and pin.isdigit()):
        return 'The PIN is exactly 6 digits.'
    return None


def _ask_twice(label, again, problem, ask, say):
    while True:
        value = ask(f'  {label}: ')
        error = problem(value)
        if error:
            say('  ' + error)
        elif ask(f'  {again}: ') != value:
            say('  They do not match.')
        else:
            return value


def ask_secrets(account, ask=getpass.getpass, say=print):
    """The password and PIN ACCOUNT (the release's install.account) asks for."""
    say('')
    say(f"Setting up the user '{account['user']}'")
    secrets = {}
    if account['password']:
        secrets['password'] = _ask_twice(f'Password (for sudo, at least {MIN_PASSWORD} characters)',
                                         'Repeat the password', password_problem, ask, say)
    if account['pin']:
        secrets['pin'] = _ask_twice('Lock-screen PIN (6 digits)', 'Repeat the PIN', pin_problem, ask, say)
    say('')
    return secrets
