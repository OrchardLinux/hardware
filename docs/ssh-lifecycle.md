# Installer SSH lifecycle

The installer creates a fresh, passphrase-free Ed25519 client key for each
session and provisions its public key through the physical USB serial
connection (`orchard-device bootstrap`). Recovery creates an Ed25519 host key in
RAM; the installer pins the returned public key with strict host-key checking
and disables connection multiplexing.

Keys live only for one recovery boot: the client key in a temporary directory
on the host, the authorization and host key in the recovery's RAM. Nothing is
written to the installed system. The SSH server listens on the USB address
only, with passwords and forwarding disabled, and uses a 4 MiB receive window
so the install stream keeps the USB link full.

Discovery reads ECID and boot identity from USB; the pinned SSH `status` must
match before the session is used. One host-wide lock permits one device
operation at a time; multiple connected targets are rejected.
