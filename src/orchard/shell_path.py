"""~/.local/bin on PATH for new terminals, as rustup or uv set it up.

The installer's `orchard` and the development one from ./setup both live
there. One marked line goes into the shell startup files, only where it is
missing; ORCHARD_NO_MODIFY_PATH=1 leaves them alone.
"""
import os
from pathlib import Path

MARK = '# orchard: ~/.local/bin on PATH'
POSIX = (f'{MARK}\ncase ":$PATH:" in *":$HOME/.local/bin:"*) ;; '
         '*) export PATH="$HOME/.local/bin:$PATH" ;; esac\n')
FISH = f'{MARK}\nfish_add_path -g "$HOME/.local/bin"\n'


def binary():
    return Path.home() / '.local/bin'


def on_path(environ=os.environ):
    return str(binary()) in environ.get('PATH', '').split(os.pathsep)


def startup_files(environ=os.environ):
    """The files a new terminal of the user's shell reads, and the line for each."""
    home = Path.home()
    shell = Path(environ.get('SHELL', '')).name
    files = {home / '.profile': POSIX}          # login shells of every POSIX shell
    if shell == 'zsh':
        files[home / '.zshenv'] = POSIX          # every zsh, login or not
    elif shell == 'bash':
        files[home / '.bashrc'] = POSIX          # interactive bash (Linux terminals)
        if (home / '.bash_profile').exists():    # bash login shells read it instead of .profile
            files[home / '.bash_profile'] = POSIX
    elif shell == 'fish':
        files[home / '.config/fish/conf.d/orchard.fish'] = FISH
    return files


def ensure(environ=os.environ):
    """Add the line where it is missing; returns the files changed (empty: nothing to do)."""
    if environ.get('ORCHARD_NO_MODIFY_PATH') == '1':
        return []
    changed = []
    for path, text in startup_files(environ).items():
        try:
            current = path.read_text()
        except FileNotFoundError:
            current = ''
        if MARK in current:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a') as stream:
            stream.write(('\n' if current and not current.endswith('\n') else '') + text)
        changed.append(path)
    return changed
