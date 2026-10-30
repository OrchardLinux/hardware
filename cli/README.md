# Orchard CLI

Host-side tools for installing, booting, and debugging Linux on supported Apple devices.

## Install or update

Requires Linux or macOS, Python 3.11+ with venv/pip, libusb, and OpenSSH. Linux also requires sudo and udev. Device firmware is read straight from Apple's restore image by byte range: about 40 MB instead of a 7 GB download, with no extraction tools.

```sh
python3 -m venv ~/.local/share/orchard/venv
~/.local/share/orchard/venv/bin/python -m pip install --upgrade --only-binary=:all: -r \
  https://github.com/OrchardLinux/cli/releases/latest/download/requirements.txt
mkdir -p ~/.local/bin
ln -sfn ~/.local/share/orchard/venv/bin/orchard ~/.local/bin/orchard
export PATH="$HOME/.local/bin:$PATH"
orchard setup-host
```

The release requirements select compatible CLI and hardware wheels from GitHub, with SHA-256 hashes. Other Python dependencies come from PyPI; the project packages do not require PyPI publication. Rerun these commands to update both Orchard and its paired hardware package. If an installation says “Upgrade Orchard to at least …”, rerun the commands above, then retry the installation.

`setup-host` installs missing palera1n and ipsw executables into `~/.local/bin`, leaving existing executables untouched. Linux USB rules are installed with sudo when needed; reconnect the device afterward. It never boots or modifies a device.

## Select a release

```sh
orchard distros omarchy-a https://github.com/OrchardLinux/omarchy-a/releases/latest/download/index.toml
orchard install omarchy-a
```

The registered name stores a URL. Each resolution fetches the current index; verified images are cached by SHA-256. Publishing a new distro index updates future installations, not an already installed operating system.

## Launch an installed system

Connect the device in DFU mode, then run:

```sh
orchard launch
```

The host sends only Pongo and the bootloader (m1n1), which ship with Orchard;
m1n1 reads the boot entries from the device's boot partition and starts the one
you choose (`--entry ID` picks one without asking). Any computer with Orchard
can launch any installed system: nothing is downloaded, and nothing from an
earlier install on that computer is needed. Nothing is formatted or reinstalled.

## Documentation

- [Release publishing and updates](docs/releases.md)
- [Device operation protocol](docs/device-protocol.md)
- [Host adapter contract](docs/adapter-contract.md)
- [SSH lifecycle](docs/ssh-lifecycle.md)
