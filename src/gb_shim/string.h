/* SPDX-License-Identifier: WTFPL */
#ifndef GB_SHIM_STRING_H
#define GB_SHIM_STRING_H
#include <stddef.h>
void *memset(void *dst, int value, size_t count);
void *memcpy(void *dst, const void *src, size_t count);
#endif
