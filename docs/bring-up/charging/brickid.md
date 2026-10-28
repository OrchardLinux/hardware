# Apple BrickID and port-type detection (USB-A)

## BrickID table

Firmware: classifier `…5e69b38`, current table `…549bf00`, publication
`…5e69d7c` (`brick id`, `d+ voltage`, `d- voltage`). iPadOS 17.7.11 (21H461),
iPad7,5/7,6. Primary detect voltage = D+, secondary = D−.

| D+ \ D− | 1500–2183 mV | 2184–2879 mV | ≥ 2880 mV |
|---|---|---|---|
| 1500–2183 mV | ID1 500 mA | ID2 1000 mA | ID3 1000 mA |
| 2184–2879 mV | ID4 2100 mA | ID5 2400 mA | ID6 3000 mA |
| ≥ 2880 mV | ID7 1000 mA | ID8 1000 mA | ID9 1000 mA |

- `ID = 1 + 3 × row(D+) + col(D−)`.
- Either voltage < 1500 mV gives ID0, no signature (the compare against
  `0x5dd` = 1501 is the floor check).
- IDs outside 1–9 give 0 mA.
- The table is not a permission to draw 3 A. Cap at the board maximum.

Boundaries were host-tested at 1499/1500, 2183/2184 and 2879/2880 on both
axes. An early reading of the floor alone (1501 mV) was taken as "the
detector". It was only the floor check.

linux-a9/PR #8 uses 2199/2200 instead of 2183/2184. J71b firmware says
2184. The difference only matters for a source within 16 mV of the
boundary. Orchard's detector adds a 50 mV exclusion band around every
boundary and reports unknown there.

ID numbers do not map to BC1.2 types. "ID1 = SDP-like" is an
interpretation, not a fact.

## Measured signatures (J71b, same Lightning to USB-A cable)

| Source | Orientation | Bank 0 D+/D− mV | Bank 1 D+/D− mV | Result |
|---|---|---|---|---|
| Apple A1357 (10 W, label 5.1 V 2.1 A) | A | 2652/1951 | 39/39 | ID4 2100 mA |
| Apple A1357 | B | 39/39 | 2647/1949 | ID4 2100 mA |
| Apple A1357, route `0x11` only | — | 2644/1941 (also 2644/1944) | — | ID4 |
| Apple A1357, route `0x01` (`0x0c`/`0x0d`) | — | 2725/2015 | — | ID4 |
| 20 W power bank USB-A (label 5 V 2.4 A total) | A | 39/39 | 2657/2657 | ID5 2400 mA |
| 20 W power bank USB-A | B | 2651/2652 | 39/39 | ID5 2400 mA |
| Mac through USB-A hub | A | 39/39 | 39/39 | none |

Every successful run had four ADC completions (event 176), no epoch
change, and `0x01`/`0x03` restored to 0. The inactive bank always read
raw 0.

Rule for two banks:

- Exactly one bank valid: use it.
- Both valid and equal: use it.
- Both valid but different: the prototype takes the lower. A stricter
  choice is to report unknown.
- Never pick the higher of two conflicting values.

The prototype reads D− before D+ in each bank. The order does not matter
electrically as far as we know.

Untested IDs: 1, 2, 3, 6, 7, 8, 9. Untested Apple bricks: 5 W, 12 W. PR #8
claims they commonly read 1000 and 2400 mA respectively ([CH-11](experiments.md#ch-11)).

## Port-type table

Firmware `…5e68a90` builds a second kind of source item. Its limit comes
from a jump table `…549bec0` (`id − 1 ≤ 6`) and a subtype table `…549bec8`.
Strings `sdp`, `dcp`, `cdp`, `cp` are at `…549d650`–`…549d65c`.

| Port-type ID | Limit |
|---|---|
| 1 or 7 | subtype table `[1000, 1500, 1000, 1500, 1500]` mA |
| 2, 5, 6 | 1500 mA |
| 3 | 500 mA |
| 4 | 1000 mA |
| other | 0 |

The mapping of IDs to SDP/CDP/DCP labels is not established.

Mode-4 classification (`…5e68a50`) depends on an upstream class, not
voltage alone:

- It uses thresholds 257/258 mV or a 326–899 mV window, depending on the
  class.
- It may request mode 5 for port type 7.
- Upstream classes 2–4 skip the mode 1/2 BrickID reads entirely
  (`…5e6da50`–`…5e6db84`).
- The upstream class comes from Dock9Pin state, on some paths DigitalID,
  and a USB-port virtual method (slot 688, `…5e6da30`). That method is not
  necessarily "UDC configured".

PR #8 claims (from iPad 5 captures, with BC secondary/name serializer at
`…5e68c48`/`…5e68de8`, table `…6dc3750` cited as J71b anchors; unverified):

- primary D− 326–899 mV → run secondary detection;
- secondary D+ < 326 mV → CDP;
- secondary D+ ≥ 326 mV → DCP;
- both CDP and DCP give 1500 mA;
- iPad 5 samples: Mac USB-C 594/39 mV (CDP), USB-C wall charger
  667/668 mV (DCP).

Orchard's mode 4/5 data on J71b, from forced modes that bypassed the route
guard (negative controls only):

| Connection | Mode 4/5 mV |
|---|---|
| Apple 140 W, USB-C cable | 39/1537 |
| Direct Mac, same cable | 39/791 |
| Mac dock / A1357 on USB-A cable | 39/39 |

## What iPadOS reported for USB-A sources

| Source | `IOAccessoryUSBCurrentLimit` |
|---|---|
| A1357 | 2100 mA (matches ID4) |
| Mac dock via USB-A (Mac allocation 500 mA) | 1500 mA (port-type path; CDP not confirmed) |

After a detach, `IOAccessoryUSBCurrentLimitBase` read 0. After reconnect it
was 2100 again, with `IOAccessoryDetect` = 1.
