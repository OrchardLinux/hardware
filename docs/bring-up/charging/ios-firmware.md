# Reading iPadOS: firmware analysis and runtime observation

## Kernelcache

J71b analysis used iPadOS 17.7.11, build 21H461, iPad7,5/7,6 kernelcache
(SHA-256 `5c0822a78a10191b56edd468f100b331b0e86bcafa3fdcc5d974dadc427dbec9`).
All addresses in this directory are unslid VAs in that file. Other builds
will differ. Do not publish the kernelcache or full disassembly.

How it was read:

1. The A10 kernelcache is a legacy `MH_EXECUTE`. It has no `MH_FILESET`, no
   symbol table and no `LC_FUNCTION_STARTS`, so `ipsw kernel extract` and
   symbol-based disassembly do not work.
2. A script (`ipadlinux/experiments/power-001/analyze-kext.py`, local) finds
   the embedded kext Mach-O headers and rebuilds `ADRP+ADD` references to
   strings. Strings such as `brick id`, `inputLimitTargetMA=%d`, `sdp`/`cdp`
   and class names then label the code.
3. File offset of a kext VA: `0xa50000 + (VA − 0xfffffff005364000)`.
4. Disassemble each kext separately (`disassemble-kext.sh`). Check the
   section base. The first D2365 listing put the ELF header at the code VA
   and shifted every address by +0x80.
5. Follow C++ dispatch through the **instance** vtable installed by the
   concrete class's constructor, not the metaclass and not the base class.
   For CBTL1610:
   - constructor `…61545a4` calls the THS7383 base constructor;
   - it then installs vtable `…6e58c18` (class name string `…5533b6f`).

   Base-class slots that CBTL1610 overrides gave a wrong response layout.
6. Pin every finding with a firmware test: exact instruction bytes at the
   address. When a later analysis moves an address, the test fails instead
   of silently drifting.

Useful kexts:

- `IOAccessoryManager` (detector, BrickID, port type, Type-C, PD);
- `AppleTriStar`/`AppleCBTL1610` (routing, monitor, response);
- `AppleD2365PMU` and parent `AppleDialogPMU` (registers, ADC, events);
- `AppleARMPMU` and charger (adapter selection, backoff).

Debug boot arguments in the detector (not runtime inputs):

- `crnd` forces a charging current (`1` means 1000 mA, other values are used
  directly);
- `ioaupsd_debug` enables verbose logging.

### Anchor index (21H461)

| Address | Meaning |
|---|---|
| `…5e69b38` | D+/D− band classifier |
| `…549bf00` | BrickID current table |
| `…549bec0` / `…549bec8` | Port-type jump table / subtype table |
| `…5e68a90` | Port-type source item |
| `…5e68a50` | Mode-4 classification |
| `…5e69d7c`, `…5e69b78` | BrickID/D+/D− publication and fill order |
| `…5e6da50`–`…5e6db84` | Upstream-class gating of BrickID reads |
| `…5e6df78` | `determineUSBCharger` constructs the Type-C current service |
| `…5ebbc80` | Type-C sampling task |
| `…5ebbd4c`–`…5ebbd80` | Type-C prior-port cap |
| `…5ebc110`, `…5ebc258`–`…5ebc2b0` | Type-C eligibility, CK profile compare |
| `…5e69690` | Type-C voltage thresholds |
| `…5ef32c4`, `…5ef2acc` | Capability → route tags, legacy/special helper |
| `…5ef2940` | Orientation-adjusted route tags |
| `…5ef713c` / `…614c2d8` / `…5531990` | Gated analog wrapper / BuiltIn `0x860` / selector table |
| `…6152e64` | Tristar `readBrickID` |
| `…615c57c` | CBTL1610 route handler |
| `…615d57c` | Guarded monitor (modes 1/2/4/5) |
| `…615da44` | Direct monitor (Type-C CC) |
| `…6154e68` | CBTL1610 response reader (8 bytes at `0x20`) |
| `…615c498`, `…615c440` | Orientation, presence |
| `…6b866a8`, `…6b81d84` | D2365 voltage conversion, scale/offset helper |
| `…6b85e0c`, `…6b85e40` | ADC start (`0x4000`), result (`0x4001`) |
| `…6b7c3f4`, `…6b7c3b4` | ADC serialization, completion callback |
| `…6b7e414`–`…6b7e428` | Event `0x1601` (176) registration |
| `0xfffffff006fc18f0` + `0x590` → `…6b7f3a0` | Event → PMIC GPIO mapping |
| `…6b84f40`, `…6b84f94` | `0xa03` read, write |
| `…6b84970`, `…6b84980`, `…6b84fb0`, `…6b8501c` | Input-limit calibration and floor |
| `…5e78aa4`, `…5e791e4`, `…5e78908` | IDBus PD event, PDO parsing, RDO |

`…` stands for `0xfffffff00`. For example, `…5e69b38` is
`0xfffffff005e69b38`.

PR #8 cites further J71b anchors that Orchard has not checked:

- BC secondary `…5e68c48`;
- name serializer `…5e68de8` and table `…6dc3750`;
- GetID engine `…6155f70`;
- D2365 input-limiting callback `…6b85348` (constant false);
- measured input current `…6b85230` (unsupported);
- calibration branches `…6b857a0`.

## Runtime observation on iPadOS

Under a jailbroken iPadOS 17.7 (palera1n), a small read-only program
enumerated selected IOService classes and printed only numeric and boolean
properties. The source is local at
`ipadlinux/experiments/power-001/ios-source-registry-readonly.c`.

Classes and keys of interest:

| Class | Keys |
|---|---|
| `AppleTriStarBuiltIn` | `IOAccessoryUSBCurrentLimit`, `IOAccessoryUSBCurrentLimitBase`, `IOAccessoryUSBChargingVoltage`, `PortType`, `IOAccessoryDetect` |
| `AppleARMPMUCharger` | `AdapterInfo`, `BestAdapterIndex` |
| `AppleCBTL1610`, `AppleD2365PMU`, `AppleARMPMU` | presence |
| `IOAccessoryIDBusPD`, `IOAccessoryPowerSourceItemPD`, `IOAccessoryIDBusTransport`, `IOPortTransportAIDBus`, `IOAccessoryDock9Pin`, `IOAccessoryManager`, `IOAccessoryPort`, `IOAccessoryPowerSource` | presence and power-related numbers |

The program dropped any key containing `serial`, `address`, `ecid`,
`unique`, `productid`, `vendorid`, `deviceid`, `chipid` or `accessoryid`, as
well as dictionaries and data blobs. Do the same: these are per-device
identifiers.

J71b values per source are in [README.md §2.5](README.md#25-what-ipados-chose-reference).

Notes:

- Keep the battery well below 100 %. At 100 % the pack current says nothing
  about available input.
- The Mac-side allocation (500 mA on a hub port, 2400 mA override on a
  direct USB-C port) is a host accounting value, not a measured current.
- `AdapterInfo` was 16384 and `BestAdapterIndex` 0 in every J71b case.
