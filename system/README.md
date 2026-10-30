# System defaults

What orchard puts into every installed system, next to the kernel, DTBs and
modules of its platform (the platform layer, `orchard.image.platform`):

- `regulatory/`: the public wireless regulatory database and its upstream
  signature, which cfg80211 reads before Wi-Fi comes up;
- `defaults/`: hardware defaults under `/usr/lib`, such as NetworkManager
  leaving orchard's USB gadget link alone and the battery gauge's poll
  interval.

Defaults live only under `/usr/lib`. A distro changes them the usual way, with
a file of the same name under `/etc`. Everything else in a root image is the
distro's: packages, users, services, power and desktop policy.
