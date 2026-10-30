# iPad simulator — VM prototype

Run on an Apple Silicon Mac:

```sh
./omarchy-next/ipad-simulator/run
```

This builds and opens the native AppKit viewer. If a VM disk is prepared, it starts the VM (or reconnects to it). Drag the frame or its corners to move or resize it; click the display to focus guest input. By default, a mouse press, drag and release on the display become single-finger events on a virtual touchscreen; keyboard input still works. Choose **iPad Simulator → Mouse Input** for ordinary mouse movement, right-click and wheel scrolling, or **Touch Input** to return to touch. Touch mode has no hover, wheel or multi-finger gestures. Cmd-Q closes the viewer but **does not stop the VM**. The touchscreen device requires a VM restart after upgrading an already-running VM.

The silver/white Wi-Fi frame uses the [iPad 6 dimensions](https://www.dimensions.com/element/apple-ipad-6th-gen-2018): 169.5 × 240 mm, with a 9.7-inch 4:3 display. Smaller details are visual approximations, not measured CAD geometry; it is not physical-size calibrated.

## Controls and shutdown

The frame buttons send QEMU virtual keyboard events to the running Linux guest:

- **Power** sends `KEY_POWER`. In the desktop session it blanks the display immediately while requesting the session lock; another press wakes the display without unlocking once the lock is confirmed. If locking fails, the display is restored and the error is reported. A black screen alone is not proof that the lock is secure. At the greeter Power blanks/restores the display.
- **Home** sends `KEY_HOMEPAGE`. It wakes a blank display without unlocking; while the desktop is unlocked and lit it opens or closes the app launcher.
- **Volume + / −** follow the existing bindings for display brightness.

Drag downward on the on-screen keyboard to dismiss it; a short drag returns it to its original position. Use the keyboard button in the shell to show it again. The virtual touchscreen is not the physical iPad touchscreen; physical behavior still requires device testing.

After five minutes of inactivity the session locks and the display blanks; the lock screen blanks again after 15 seconds without activity. The idle timeout is configurable in the tablet user's `~/.config/omarchy-ipad/shell.json` (`idle.lock`, in seconds; 0 disables it), and the existing Stay Awake toggle inhibits idle locking. There is not yet a settings screen for this policy. The simulator does **not** suspend; actual sleep and hardware wake sources remain future work.

The VM has no J71b panel. A **VM-only kernel module** registers a virtual backlight with the same sysfs interface used by the actual screen-toggle helper. A guest bridge reads the resulting brightness and sends it over virtio-serial; the host viewer dims or blanks the live VM display accordingly. This tests the actual key binding and helper, but **not the iPad backlight driver or physical power hardware**. The VM starts its virtual backlight at a fixed brightness on boot instead of restoring a saved zero.

For the *development VM*, not the product device:

```sh
./omarchy-next/ipad-simulator/vm status
./omarchy-next/ipad-simulator/vm stop
./omarchy-next/ipad-simulator/vm start
```

The app menu also offers **Shut Down Development VM**. `stop` sends a narrowly scoped shutdown request over a separate VM-only virtio channel, invoking the guest's `systemctl poweroff`; it waits and does not force shutdown if that fails. This management path does not imply the tethered iPad system has a working shutdown feature. The guest bridge accepts only the shutdown command, and host control sockets are owner-only.

## VM preparation

This is an experimental, disposable development installation, **not the iPad's installed system**. It boots the public `out/rootfs/0.1.0-next.1/rootfs.tar.zst` under QEMU ARM64/HVF with **1 vCPU and 2 GiB RAM**, using a separate VM kernel and modules copied from the local Podman Linux VM. It does not copy private iPad firmware. The guest runs systemd and the image's existing tty1 autologin and desktop session. Its J71b hardware-readiness gate and charge/diagnostics units are disabled in the VM copy, and `/etc/fstab` is bound to the VM disk. The product archive is untouched.

Install QEMU using `glu install qemu`, and start the local Podman machine. From the repository root, run (substitute the absolute project path if different):

```sh
podman machine ssh 'sudo /Users/henrikklee/projects/OrchardLinux/omarchy-next/ipad-simulator/prepare-vm-linux.sh'
./omarchy-next/ipad-simulator/build-vm-backlight
# Only install while the VM is stopped; never mount a running VM disk.
podman machine ssh 'sudo /Users/henrikklee/projects/OrchardLinux/omarchy-next/ipad-simulator/vm-support/install-linux.sh'
./omarchy-next/ipad-simulator/install-vm-greeter
./omarchy-next/ipad-simulator/run
```

Preparation refuses to overwrite an existing disk and uses a file-backed ext4 filesystem only. The sparse disk has a 6 GiB logical size and initially consumes around 3.2 GiB. Check free space first; it can grow. A failed preparation may leave a partial disk; inspect it before removing and retrying. The matching kernel-devel RPM is fetched and verified against the Podman VM's Fedora RPM keyring; the module build uses the existing Debian toolchain image with a GCC compatibility wrapper, so compiler-version warnings are expected. The VM is tied to the copied kernel snapshot; rebuilding the module currently requires the Podman machine still to be running that same kernel. Intermediate kernel build files are removed after the build.

Logs, the kernel and disk are under `out/ipad-simulator/vm/`. No physical iPad is accessed.

## Greeter prototype

The simulator boots into a Labwc/Quickshell greeter backed by real greetd and PAM. The greeter and session lock use the same system-owned lock-screen UI, while their authentication remains separate. On first boot the greeter uses the packaged default theme; after the first login it uses an atomic snapshot of the tablet user's theme and wallpaper. Theme and wallpaper changes publish a new snapshot for the next greeter. The snapshot in `/var/lib/omarchy-greeter-theme` is user-owned and publicly readable by the greeter; select only wallpapers suitable for display before login. Unsupported video posters fall back to the packaged wallpaper. Enter **123456** on the keypad or keyboard; the sixth digit submits automatically at both sign-in and unlock. This is a disposable, deliberately public **VM test PIN**, not the account password; it does not change `sudo` credentials. A VM-only root service verifies the PIN for both greetd and the session lock through PAM; the PIN hash is not readable by the tablet user. Five failures across either flow trigger a shared 60-second lockout. Its state is in memory and resets on reboot or service restart, so this is not a production security policy. Login launches the existing tablet session; exiting Labwc returns to the greeter.

Greeter source lives in `omarchy-a/desktop/greeter/` and is staged by `omarchy-a/desktop/stage.py`. To update the VM copy, stop the VM, run `install-vm-greeter`, then start it again. This installs signed Arch Linux ARM greetd plus VM-only PAM/test-PIN configuration. The installer preserves an existing test credential. **The published product rootfs has not yet been rebuilt or switched to greetd**, and the VM test PIN implementation must not be shipped as a product credential. Production PIN enrollment, password fallback/recovery, persistent throttling, and a reviewed privileged authentication service remain to be designed.

## Current limitations

- The guest has a VM-only virtual Ethernet adapter using QEMU user-mode NAT and the Mac's existing network connection. Outbound network access works without a separate guest Wi-Fi connection; inbound connections and LAN discovery are not provided by default. This does not test iPad networking hardware.
- There is no live source sync or QEMU guest agent. Greeter changes currently require reinstalling them into the stopped VM.
- Index 4.0.2 is installed in the current disposable VM for evaluation only; it is not part of the desktop or rootfs build.
- **Files (Flea touch preview)** runs the full upstream Flea 0.3.12 application with the version-pinned patch in `omarchy-a/desktop/files/flea-0.3.12-touch.patch`. Install or refresh it with the VM stopped using `./ipad-simulator/install-vm-flea-preview`; the VM greeter must already be installed. It is not the system default or file chooser. The patch enlarges common controls, adds long-press menus in list and grid views, and lets touch drags scroll instead of starting a file drag. Mouse drag-and-drop remains available in Mouse Input mode; touch file moves currently use the cut/paste menu instead. List and grid touch scrolling have been exercised in the simulator; the remaining views, multi-selection, all features, physical touch, and optional runtime dependencies have not yet been fully adapted or verified.
- QEMU's software display is sent over an unauthenticated **loopback-only** VNC socket at `127.0.0.1:5908`; the app uses a raw-RFB client, not a screenshot polling viewer. This is local development infrastructure, not a security boundary. `vm start` refuses to start if the port is already occupied.
- Resolution is 768×1024. Virtual backlight intensity is approximated by dimming the viewer; display color/brightness and iPad physical hardware are not validated. Performance and input mappings need more testing.

For a rendering check, set `IPAD_SIMULATOR_RENDER_CHECK` to a writable PNG path when launching the compiled executable; it renders its view after three seconds and exits. Set `ORCHARD_VM_VNC=1` and `ORCHARD_VM_DIR` to include a running VM display and backlight bridge. `IPAD_SIMULATOR_SELF_TEST` can instead name a result file for a power-button click/restore test; only one viewer can use the backlight bridge at a time.
