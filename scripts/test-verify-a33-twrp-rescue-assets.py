#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import io
from pathlib import Path, PurePosixPath
import sys
import tarfile
import tempfile

HERE = Path(__file__).resolve().parent
MODULE = HERE / "verify-a33-twrp-rescue-assets.py"
spec = importlib.util.spec_from_file_location("a33_twrp_rescue_verify_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

assert module.normalized_member_name("./recovery.img") == PurePosixPath("recovery.img")
for unsafe in ("../recovery.img", "/recovery.img", "dir/../../recovery.img"):
    try:
        module.normalized_member_name(unsafe)
    except module.RescueError:
        pass
    else:
        raise AssertionError(f"unsafe rescue member was accepted: {unsafe}")

with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    odin = root / "tools/odin4"
    odin.parent.mkdir(parents=True)
    odin.write_bytes(b"fixture-odin")
    odin.chmod(0o755)
    rescue = root / "build/rescue/twrp-a33x-restore.img.tar"
    rescue.parent.mkdir(parents=True)
    payload = b"fixture-twrp"
    with tarfile.open(rescue, "w") as archive:
        info = tarfile.TarInfo("recovery.img")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))

    old_odin = module.EXPECTED_ODIN_SHA256
    old_twrp = module.EXPECTED_TWRP_SHA256
    old_size = module.EXPECTED_TWRP_SIZE
    module.EXPECTED_ODIN_SHA256 = module.sha_file(odin)
    module.EXPECTED_TWRP_SHA256 = module.hashlib.sha256(payload).hexdigest()
    module.EXPECTED_TWRP_SIZE = len(payload)
    try:
        assets = module.verify_assets(root=root, odin=odin, rescue_tar=rescue)
        assert assets.twrp_size == len(payload)
        assert assets.twrp_sha256 == module.EXPECTED_TWRP_SHA256
        assert assets.report.is_file()
        report = assets.report.read_text(encoding="utf-8")
        assert "phone_partition_writes=no" in report
        assert "verification_status=passed" in report

        with tarfile.open(rescue, "w") as archive:
            for name in ("recovery.img", "extra.img"):
                info = tarfile.TarInfo(name)
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))
        try:
            module.verify_assets(root=root, odin=odin, rescue_tar=rescue)
        except module.RescueError:
            pass
        else:
            raise AssertionError("multi-member rescue archive was accepted")
    finally:
        module.EXPECTED_ODIN_SHA256 = old_odin
        module.EXPECTED_TWRP_SHA256 = old_twrp
        module.EXPECTED_TWRP_SIZE = old_size

print("a33_twrp_rescue_asset_verifier_self_test=passed")
print("safe_tar_member_validation=passed")
print("exact_odin_and_twrp_identity=passed")
print("host_only_report_contract=passed")
print("multi_member_archive_refusal=passed")
