/* SPDX-License-Identifier: GPL-2.0-only OR MIT */
#ifndef J71B_SIO_QUIESCE_API_H
#define J71B_SIO_QUIESCE_API_H
#include <linux/soc/apple/sio.h>
/* May sleep indefinitely on unresolved hardware state. No release before drain. */
void apple_sio_quiesce26(struct apple_sio *sio);
/* Read-only load-time opt-in: rejection diagnostics must require false. */
bool apple_sio_payload_allowed(void);
unsigned int apple_sio_payload_submits(void);
#endif
