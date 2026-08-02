#!/bin/sh

krel="5.10.66-Gabriel260BR-TWRP-ga0103aac9499"
moddir="/usr/lib/modules/$krel"

[ -d "$moddir" ] || exit 0

echo "Generating module indexes for $krel"
/usr/sbin/depmod -a "$krel"
