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


def _ask_twice(label, problem, ask, say):
    while True:
        value = ask(f'{label}: ')
        error = problem(value)
        if error:
            say(error)
        elif ask(f'{label} again: ') != value:
            say('They do not match.')
        else:
            return value


def ask_secrets(ask=getpass.getpass, say=print):
    say('The password is for sudo and system changes; the PIN only unlocks the screen.')
    password = _ask_twice('Password for the new system', password_problem, ask, say)
    pin = _ask_twice('6-digit lock-screen PIN', pin_problem, ask, say)
    return {'password': password, 'pin': pin}
