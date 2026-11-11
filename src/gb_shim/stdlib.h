/* SPDX-License-Identifier: WTFPL */
/* Freestanding shim for Peanut-GB (camera build has no libc). */
#ifndef GB_SHIM_STDLIB_H
#define GB_SHIM_STDLIB_H
#include <stddef.h>
__attribute__((noreturn)) void abort(void);
#endif
