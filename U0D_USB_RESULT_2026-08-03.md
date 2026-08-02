# U0d USB result — 2026-08-03

## Candidate

- Candidate: `U0d-typec-muic-none`
- Recovery SHA256: `e6a26e68ad52d55cd99efd26ec413365e0f071f7290524578ba22f972e051b84`
- Functional delta: `usb_typec_manager` accepted-cable mask `0x16 -> 0x17`
- `pdic_notifier_module` restored to the original binary
- Embedded module set unchanged at 66 modules

## Proven runtime result

U0d fixed the Type-C manager rejection. The real PDIC attachment was accepted rather than skipped:

```text
[    2.417998] usb:pdic_event_notifier, dest=USB, id=ID_USB, drp=USB_ATTACH_UFP
[    2.418027] TCM: manager_usb_event_send(USB_ATTACH_UFP)
[    2.418074] usb_notifier: ccic_usb_handle_notification: Turn On Device(UFP)
```

No `Skip event (muic_none)` and no factory-mode duplicate event occurred.

The VBUS event was initially reserved during boot and replayed when the OTG notifier became ready:

```text
[   13.543390] usb_notify: reserve_state_check booting delay finished
[   13.543414] usb_notify: reserve_state_check event=vbus(1) enable=1
[   13.549909] usb_notifier: exynos_set_peripheral usb attached
[   13.551587] exynos-dwc3 13200000.usb: dwc3_exynos_rsw_work
[   13.551612] dwc3 13200000.dwc3: Turn on gadget dwc3-gadget
[   13.555088] usb: dwc3_gadget_run_stop : is_on = 1
```

Therefore the remaining failure is below Type-C classification and notifier delivery.

## Missing host-side transition

After DWC3 started, the log contained only:

```text
[   13.561805] usb: dwc3_gadget_vbus_draw: suspend
```

It did **not** contain the reset/connect-done sequence seen during a working Samsung/TWRP USB boot:

```text
dwc3_gadget_reset_interrupt
dwc3_gadget_conndone_interrupt
USB_STATE=CONNECTED
USB_STATE=CONFIGURED
```

The host likewise saw no new USB device after the old TWRP device disconnected.

## Strongest remaining hypothesis: S2MU106 physical data switch

The full Samsung path programs the MUIC USB switch before USB works:

```text
s2mu106_set_usb_vbus_out: MUIC: CONTROL: 0x13
s2mu106_set_usb_vbus_out: MUIC: MANUAL_SW1: [0x24]
s2mu106_set_usb_vbus_out: MUIC: CONTROL: 0x17
```

Samsung's S2MU106 definitions identify:

- MUIC I2C address: `0x3e`
- `MUIC_CTRL1`: register `0x6d`
- `MANUAL_SW_CTRL`: register `0x70`
- USB D-/D+ route value: `0x24`

U0d did not load the full MUIC driver and no equivalent switch programming appears in its boot log. The next isolated diagnostic should program only this proven switch sequence, while retaining the successful U0d Type-C mask patch and unchanged 66-module set.

## Next experiment requirements

1. First verify whether `/dev/i2c-6` and an `i2cget`/`i2cset` implementation are available in the recovery/initramfs environment.
2. Read and log MUIC registers `0x6d` and `0x70` before modification.
3. Apply the exact sequence to slave `0x3e`:
   - `0x6d <- 0x13`
   - `0x70 <- 0x24`
   - `0x6d <- 0x17`
4. Read back both registers and fail closed on any mismatch.
5. Keep the U0d notifier patch unchanged and add no full MUIC/CPIF/BTS closure for this diagnostic.
6. If USB enumerates, replace the diagnostic raw I2C sequence with a proper minimal recovery MUIC implementation or a correctly scoped driver solution.
