# Samsung A33 reproducibility

This project currently has two different reproducibility levels. They must not be conflated.

## 1. Exact binary deployment

The existing pipeline can reproduce deployment to another compatible Samsung Galaxy A33 from preserved binary artifacts. The required inputs are:

- the exact known-good TWRP recovery image;
- the extracted TWRP kernel, DTB and recovery-DTBO;
- the exact original kernel module tree for `5.10.66-Gabriel260BR-TWRP-ga0103aac9499`;
- the rootfs deployment image and its deployment report;
- the Linuxa33 repository commit and local pmaports packages;
- the pinned AOSP `mkbootimg` and `avbtool` revisions;
- the local AVB signing key and the fixed salt used by the recovery builder;
- candidate manifests, patch reports and SHA256 values.

The generated recovery must continue to validate the following invariants:

- kernel, DTB and recovery-DTBO are unchanged unless a candidate explicitly declares a kernel-only experiment;
- the Samsung trailer is preserved;
- the kernel command line is unchanged unless a manifest declares a specific addition;
- the AVB footer verifies;
- the recovery partition readback equals the candidate;
- userdata, cache, super, boot and GPT remain untouched by recovery-only experiments.

A second phone must still be verified as the same supported model and partition layout. Bootloader state and firmware compatibility are external prerequisites; an identical marketing name alone is not sufficient.

Run the read-only audit with:

```bash
python3 scripts/audit-a33-reproducibility.py
```

Important summary fields are:

```text
same_phone_exact_deployment=passed
binary_recovery_rebuild=passed|incomplete
kernel_source_rebuild=passed|missing
overall_status=...
```

The default audit succeeds when exact deployment artifacts are intact even if kernel source provenance is still missing. Use `--strict-source` when a source-built kernel is required.

## 2. Kernel source reproducibility

The TWRP device tree used by this port references a prebuilt kernel `Image`. The Linuxa33 downstream kernel package also packages that extracted binary rather than compiling a kernel source tree. Therefore the original source commit, complete kernel configuration and compiler provenance are not currently available from the existing build tree.

A similar public S5E8825 kernel tree is useful for understanding and repairing Samsung EMS, but it must not be described as the exact source of the current `5.10.66-Gabriel260BR-TWRP-ga0103aac9499` binary until an unpatched build is shown to be compatible and its provenance is recorded.

Kernel source reproducibility becomes complete only when this file exists:

```text
~/a33-port/build/a33-kernel-source.lock
```

It must contain all of these non-empty fields:

```text
source_repository=
source_commit=
source_tree_sha256=
kernel_config_sha256=
toolchain_identity=
toolchain_sha256=
unpatched_kernel_sha256=
patched_kernel_sha256=
```

The source commit must be a full 40-character Git commit and every SHA256 field must contain 64 lowercase hexadecimal characters.

Before a source-built kernel is flashed, its validation must establish:

1. the source repository and commit are pinned and fetchable;
2. the complete build configuration and toolchain are pinned;
3. an unpatched build boots with the existing U0k initramfs or any differences are explicitly explained;
4. kernel modules are rebuilt from the same source/config and match the kernel ABI;
5. the EMS patch is minimal and tested against root, Android-style and non-Android CPU-cgroup IDs;
6. the resulting recovery differs from U0k only in declared kernel/module payloads;
7. exact TWRP restoration remains available.

Until those conditions pass, the project is reproducible at the preserved-binary deployment level but not at the kernel-source level.
