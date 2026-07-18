import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

SOURCE_ROOT = Path(__file__).resolve().parent.parent / "Source"
sys.path.insert(0, str(SOURCE_ROOT))

# These are ARCHIVE-internal helpers (underscore-prefixed): not a public API.
# Tests import the private names explicitly, which is permitted.
from modules.archive import (  # noqa: E402
    _TESTDISK_WORKING_COPY_FILENAME,
    _TESTDISK_WORKING_COPY_MODE,
    _check_working_free_space,
    _default_identity_resolver,
    _prepare_testdisk_working_copy,
    _reject_unsafe_recovery_identity,
    _resolve_recovery_identity,
    _validate_ancestors_traversable,
    _validate_canonical_protection,
    _validate_execution_mode,
    _validate_execution_target,
    _validate_privilege_drop_mechanism,
    _validate_recovery_target,
)

RECOVERY_IDENTITY = {
    "account": "sentinel-recovery",
    "uid": 999,
    "gid": 991,
    "groups": ["sentinel-recovery"],
    "group_gids": [991],
}


def _stat(*, uid=0, gid=0, mode=0o400, size=0):
    # st_mode is stored with permission bits only in these tests; the helpers
    # mask with 0o777 / 0o077 / 0o001 so file-type bits are irrelevant here.
    return SimpleNamespace(st_uid=uid, st_gid=gid, st_mode=mode, st_size=size)


def _statvfs(*, bavail, frsize=4096):
    return SimpleNamespace(f_bavail=bavail, f_frsize=frsize)


class FakeFsOps:
    """
    In-memory filesystem operations for working-copy preparation tests. Records
    the ordered sequence of calls and can be told to raise OSError at a named
    step. No real files, no privileged operations.
    """

    def __init__(self, *, source_path, source_size=100, copied_size=None,
                 fail_on=None):
        self.calls = []
        self.fail_on = set(fail_on or [])
        self.source_size = source_size
        self.copied_size = source_size if copied_size is None else copied_size
        self.files = {str(source_path): source_size}
        self.owners = {}
        self.modes = {}

    def _maybe_fail(self, step):
        if step in self.fail_on:
            raise OSError(f"simulated {step} failure")

    def exists(self, path):
        self.calls.append(("exists", path))
        return path in self.files

    def unlink(self, path):
        self.calls.append(("unlink", path))
        self._maybe_fail("unlink")
        self.files.pop(path, None)
        self.modes.pop(path, None)

    def create_secure_file(self, path, mode):
        self.calls.append(("create_secure_file", path, mode))
        self._maybe_fail("create_secure_file")
        self.files[path] = 0
        self.modes[path] = mode

    def copy(self, source, destination):
        self.calls.append(("copy", source, destination))
        self._maybe_fail("copy")
        self.files[destination] = self.copied_size

    def size(self, path):
        self.calls.append(("size", path))
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    def fsync_file(self, path):
        self.calls.append(("fsync_file", path))
        self._maybe_fail("fsync_file")

    def fsync_dir(self, path):
        self.calls.append(("fsync_dir", path))
        self._maybe_fail("fsync_dir")

    def rename(self, source, destination):
        self.calls.append(("rename", source, destination))
        self._maybe_fail("rename")
        self.files[destination] = self.files.pop(source)
        if source in self.modes:
            self.modes[destination] = self.modes.pop(source)

    def chown(self, path, uid, gid):
        self.calls.append(("chown", path, uid, gid))
        self._maybe_fail("chown")
        self.owners[path] = (uid, gid)

    def chmod(self, path, mode):
        self.calls.append(("chmod", path, mode))
        self._maybe_fail("chmod")
        self.modes[path] = mode

    def call_names(self):
        return [call[0] for call in self.calls]


class DefaultIdentityResolverTests(unittest.TestCase):
    """
    Correction #1: supplementary groups come from os.getgrouplist() (host
    identity service), not grp.getgrall().
    """

    def _patched(self, *, getgrouplist, passwd=None, gid_names=None):
        passwd = passwd or SimpleNamespace(pw_uid=999, pw_gid=991)
        gid_names = gid_names or {991: "sentinel-recovery", 6: "disk"}

        def _getgrgid(gid):
            if gid not in gid_names:
                raise KeyError(gid)
            return SimpleNamespace(gr_name=gid_names[gid])

        return (
            mock.patch("modules.archive.os.getgrouplist", getgrouplist),
            mock.patch("pwd.getpwnam", lambda name: passwd),
            mock.patch("grp.getgrgid", _getgrgid),
        )

    def test_supplementary_membership_via_getgrouplist(self):
        gl = mock.Mock(return_value=[991, 6])
        p1, p2, p3 = self._patched(getgrouplist=gl)
        with p1, p2, p3:
            identity = _default_identity_resolver("sentinel-recovery")
        gl.assert_called_once_with("sentinel-recovery", 991)
        self.assertEqual(identity["uid"], 999)
        self.assertEqual(identity["gid"], 991)
        self.assertEqual(identity["group_gids"], [991, 6])
        self.assertIn("disk", identity["groups"])

    def test_primary_group_is_included(self):
        gl = mock.Mock(return_value=[991])
        p1, p2, p3 = self._patched(getgrouplist=gl)
        with p1, p2, p3:
            identity = _default_identity_resolver("sentinel-recovery")
        self.assertIn("sentinel-recovery", identity["groups"])
        self.assertEqual(identity["group_gids"], [991])

    def test_group_enumeration_failure_fails_closed(self):
        def _raise(name, gid):
            raise OSError("nss group enumeration failed")

        p1, p2, p3 = self._patched(getgrouplist=_raise)
        with p1, p2, p3:
            result = _resolve_recovery_identity(
                "sentinel-recovery",
                identity_resolver=_default_identity_resolver,
            )
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_IDENTITY_LOOKUP_FAILED")

    def test_missing_account_fails_closed(self):
        def _raise(name):
            raise KeyError(name)

        with mock.patch("pwd.getpwnam", _raise):
            result = _resolve_recovery_identity(
                "ghost", identity_resolver=_default_identity_resolver
            )
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_IDENTITY_MISSING")


class ResolveRecoveryIdentityTests(unittest.TestCase):
    def test_success(self):
        result = _resolve_recovery_identity(
            "sentinel-recovery",
            identity_resolver=lambda name: dict(RECOVERY_IDENTITY),
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["code"], "TESTDISK_IDENTITY_RESOLVED")
        self.assertEqual(result["identity"], RECOVERY_IDENTITY)

    def test_unconfigured_is_refused(self):
        result = _resolve_recovery_identity(
            "  ",
            identity_resolver=lambda name: dict(RECOVERY_IDENTITY),
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_IDENTITY_UNCONFIGURED")

    def test_missing_account_is_refused(self):
        def _raise(name):
            raise KeyError(name)

        result = _resolve_recovery_identity("ghost", identity_resolver=_raise)
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_IDENTITY_MISSING")

    def test_lookup_error_is_refused(self):
        def _raise(name):
            raise OSError("nss failure")

        result = _resolve_recovery_identity(
            "sentinel-recovery", identity_resolver=_raise
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_IDENTITY_LOOKUP_FAILED")


class UnsafeRecoveryIdentityTests(unittest.TestCase):
    """Correction #2: reject root uid/gid and privileged groups, distinct codes."""

    def test_safe_identity_passes(self):
        result = _reject_unsafe_recovery_identity(
            RECOVERY_IDENTITY, ["disk", "sudo"]
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["code"], "TESTDISK_IDENTITY_SAFE")

    def test_root_uid_is_refused(self):
        identity = dict(RECOVERY_IDENTITY, uid=0)
        result = _reject_unsafe_recovery_identity(identity, ["disk"])
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_IDENTITY_ROOT_UID")

    def test_root_primary_gid_is_refused(self):
        identity = dict(RECOVERY_IDENTITY, gid=0, group_gids=[0])
        result = _reject_unsafe_recovery_identity(identity, ["disk"])
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_IDENTITY_ROOT_GID")

    def test_supplementary_root_gid_is_refused(self):
        identity = dict(RECOVERY_IDENTITY, gid=991, group_gids=[991, 0])
        result = _reject_unsafe_recovery_identity(identity, ["disk"])
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_IDENTITY_ROOT_GID")

    def test_privileged_group_is_refused(self):
        identity = dict(
            RECOVERY_IDENTITY,
            groups=["sentinel-recovery", "disk"],
            group_gids=[991, 6],
        )
        result = _reject_unsafe_recovery_identity(identity, ["disk", "sudo"])
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_IDENTITY_PRIVILEGED_GROUP")
        self.assertIn("disk", result["display_args"]["groups"])


class PrivilegeDropMechanismTests(unittest.TestCase):
    def test_available_mechanism_passes(self):
        result = _validate_privilege_drop_mechanism(
            "setpriv", command_exists=lambda name: True
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["code"], "TESTDISK_DROP_MECHANISM_AVAILABLE")

    def test_unconfigured_mechanism_is_refused(self):
        result = _validate_privilege_drop_mechanism(
            "", command_exists=lambda name: True
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_DROP_MECHANISM_UNCONFIGURED")

    def test_missing_mechanism_is_refused(self):
        result = _validate_privilege_drop_mechanism(
            "setpriv", command_exists=lambda name: False
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_DROP_MECHANISM_MISSING")


class ExecutionModeTests(unittest.TestCase):
    """Correction #3: root / sudo / external, no arbitrary mode accepted."""

    def test_root_mode_usable_when_euid_zero(self):
        result = _validate_execution_mode("root", geteuid=lambda: 0)
        self.assertTrue(result["success"])
        self.assertEqual(result["code"], "TESTDISK_EXECUTION_MODE_USABLE")

    def test_root_mode_unusable_when_not_root(self):
        result = _validate_execution_mode("root", geteuid=lambda: 1000)
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_EXECUTION_MODE_UNUSABLE")

    def test_sudo_mode_usable_when_sudo_present(self):
        result = _validate_execution_mode(
            "sudo", geteuid=lambda: 1000, command_exists=lambda name: True
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["code"], "TESTDISK_EXECUTION_MODE_USABLE")

    def test_sudo_mode_unusable_without_sudo(self):
        result = _validate_execution_mode(
            "sudo", geteuid=lambda: 1000, command_exists=lambda name: False
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_EXECUTION_MODE_UNUSABLE")

    def test_external_mode_usable_with_available_mechanism(self):
        result = _validate_execution_mode(
            "external",
            drop_mechanism="setpriv",
            command_exists=lambda name: True,
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["code"], "TESTDISK_EXECUTION_MODE_USABLE")
        self.assertEqual(result["mode"], "external")

    def test_external_mode_unusable_without_configured_mechanism(self):
        result = _validate_execution_mode(
            "external", drop_mechanism="", command_exists=lambda name: True
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_EXECUTION_MODE_UNUSABLE")

    def test_external_mode_unusable_when_mechanism_missing_on_path(self):
        result = _validate_execution_mode(
            "external",
            drop_mechanism="setpriv",
            command_exists=lambda name: False,
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_EXECUTION_MODE_UNUSABLE")

    def test_arbitrary_mode_is_rejected(self):
        result = _validate_execution_mode("wizardry", geteuid=lambda: 0)
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_EXECUTION_MODE_INVALID")


class CanonicalProtectionTests(unittest.TestCase):
    def test_protected_canonical_passes(self):
        result = _validate_canonical_protection(
            "/case/images/source.img",
            RECOVERY_IDENTITY,
            stat_provider=lambda path: _stat(uid=0, gid=0, mode=0o400),
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["code"], "TESTDISK_CANONICAL_PROTECTED")

    def test_missing_canonical_is_refused(self):
        def _raise(path):
            raise FileNotFoundError(path)

        result = _validate_canonical_protection(
            "/case/images/source.img", RECOVERY_IDENTITY, stat_provider=_raise
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_CANONICAL_MISSING")

    def test_canonical_owned_by_recovery_uid_is_refused(self):
        result = _validate_canonical_protection(
            "/case/images/source.img",
            RECOVERY_IDENTITY,
            stat_provider=lambda path: _stat(uid=999, gid=0, mode=0o400),
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_CANONICAL_OWNED_BY_RECOVERY")

    def test_canonical_owned_by_recovery_gid_is_refused(self):
        result = _validate_canonical_protection(
            "/case/images/source.img",
            RECOVERY_IDENTITY,
            stat_provider=lambda path: _stat(uid=0, gid=991, mode=0o400),
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_CANONICAL_OWNED_BY_RECOVERY")

    def test_permissive_canonical_is_refused(self):
        result = _validate_canonical_protection(
            "/case/images/source.img",
            RECOVERY_IDENTITY,
            stat_provider=lambda path: _stat(uid=0, gid=0, mode=0o440),
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_CANONICAL_PERMISSIVE")

    def test_stat_error_is_refused(self):
        def _raise(path):
            raise OSError("permission denied")

        result = _validate_canonical_protection(
            "/case/images/source.img", RECOVERY_IDENTITY, stat_provider=_raise
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_CANONICAL_STAT_FAILED")


class RecoveryTargetTests(unittest.TestCase):
    def test_owned_and_correct_mode_passes(self):
        result = _validate_recovery_target(
            "/case/recovered/testdisk",
            RECOVERY_IDENTITY,
            0o700,
            stat_provider=lambda path: _stat(uid=999, gid=991, mode=0o700),
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["code"], "TESTDISK_TARGET_OK")

    def test_wrong_owner_is_refused(self):
        result = _validate_recovery_target(
            "/case/recovered/testdisk",
            RECOVERY_IDENTITY,
            0o700,
            stat_provider=lambda path: _stat(uid=0, gid=0, mode=0o700),
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_TARGET_WRONG_OWNER")

    def test_wrong_mode_is_refused(self):
        result = _validate_recovery_target(
            "/case/recovered/testdisk",
            RECOVERY_IDENTITY,
            0o700,
            stat_provider=lambda path: _stat(uid=999, gid=991, mode=0o755),
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_TARGET_WRONG_MODE")

    def test_missing_target_is_refused(self):
        def _raise(path):
            raise FileNotFoundError(path)

        result = _validate_recovery_target(
            "/case/recovered/testdisk", RECOVERY_IDENTITY, 0o700,
            stat_provider=_raise,
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_TARGET_MISSING")

    def test_stat_error_is_refused(self):
        def _raise(path):
            raise OSError("io error")

        result = _validate_recovery_target(
            "/case/recovered/testdisk", RECOVERY_IDENTITY, 0o700,
            stat_provider=_raise,
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_TARGET_STAT_FAILED")


class AncestorTraversalTests(unittest.TestCase):
    def test_all_traversable_passes(self):
        modes = {"/case/recovered": 0o755, "/case": 0o755}
        result = _validate_ancestors_traversable(
            "/case/recovered/testdisk",
            "/case",
            stat_provider=lambda path: _stat(mode=modes[path]),
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["code"], "TESTDISK_TRAVERSAL_OK")

    def test_non_traversable_parent_is_refused(self):
        modes = {"/case/recovered": 0o755, "/case": 0o700}
        result = _validate_ancestors_traversable(
            "/case/recovered/testdisk",
            "/case",
            stat_provider=lambda path: _stat(mode=modes[path]),
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_PARENT_NOT_TRAVERSABLE")
        self.assertIn("/case", result["display_args"]["paths"])

    def test_stat_error_on_parent_is_refused(self):
        def _raise(path):
            raise OSError("denied")

        result = _validate_ancestors_traversable(
            "/case/recovered/testdisk", "/case", stat_provider=_raise
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_PARENT_NOT_TRAVERSABLE")

    def test_boundary_not_ancestor_is_refused(self):
        result = _validate_ancestors_traversable(
            "/case/recovered/testdisk",
            "/somewhere/else",
            stat_provider=lambda path: _stat(mode=0o755),
        )
        self.assertFalse(result["success"])
        self.assertEqual(
            result["code"], "TESTDISK_TRAVERSAL_BOUNDARY_NOT_ANCESTOR"
        )


class FreeSpaceTests(unittest.TestCase):
    def test_sufficient_space_passes(self):
        result = _check_working_free_space(
            "/case/images/source.img",
            "/case/working",
            safety_margin_bytes=0,
            stat_provider=lambda path: _stat(size=1000),
            statvfs_provider=lambda path: _statvfs(bavail=2000, frsize=1),
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["code"], "TESTDISK_FREE_SPACE_OK")
        self.assertEqual(result["required_bytes"], 1000)
        self.assertEqual(result["available_bytes"], 2000)

    def test_zero_margin_exact_boundary_passes(self):
        # available == required (source + 0) must pass.
        result = _check_working_free_space(
            "/case/images/source.img",
            "/case/working",
            safety_margin_bytes=0,
            stat_provider=lambda path: _stat(size=1000),
            statvfs_provider=lambda path: _statvfs(bavail=1000, frsize=1),
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["code"], "TESTDISK_FREE_SPACE_OK")

    def test_exact_boundary_with_margin_passes(self):
        # available == source + margin must pass.
        result = _check_working_free_space(
            "/case/images/source.img",
            "/case/working",
            safety_margin_bytes=100,
            stat_provider=lambda path: _stat(size=900),
            statvfs_provider=lambda path: _statvfs(bavail=1000, frsize=1),
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["code"], "TESTDISK_FREE_SPACE_OK")

    def test_one_byte_short_is_refused(self):
        result = _check_working_free_space(
            "/case/images/source.img",
            "/case/working",
            safety_margin_bytes=100,
            stat_provider=lambda path: _stat(size=901),
            statvfs_provider=lambda path: _statvfs(bavail=1000, frsize=1),
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_INSUFFICIENT_FREE_SPACE")

    def test_negative_margin_is_refused(self):
        result = _check_working_free_space(
            "/case/images/source.img",
            "/case/working",
            safety_margin_bytes=-1,
            stat_provider=lambda path: _stat(size=10),
            statvfs_provider=lambda path: _statvfs(bavail=10 ** 9, frsize=1),
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_FREE_SPACE_INVALID_MARGIN")

    def test_missing_source_is_refused(self):
        def _raise(path):
            raise FileNotFoundError(path)

        result = _check_working_free_space(
            "/case/images/source.img", "/case/working",
            stat_provider=_raise,
            statvfs_provider=lambda path: _statvfs(bavail=10 ** 9),
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_SOURCE_IMAGE_MISSING")

    def test_statvfs_error_is_refused(self):
        def _raise(path):
            raise OSError("no such fs")

        result = _check_working_free_space(
            "/case/images/source.img", "/case/working",
            stat_provider=lambda path: _stat(size=10),
            statvfs_provider=_raise,
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_FREE_SPACE_UNDETERMINED")


class PrepareWorkingCopyTests(unittest.TestCase):
    SOURCE = "/case/images/source.img"
    WORKING = "/case/working"
    FINAL = f"/case/working/{_TESTDISK_WORKING_COPY_FILENAME}"
    TMP = f"/case/working/{_TESTDISK_WORKING_COPY_FILENAME}.tmp"

    def test_success_sequence_and_ownership(self):
        fs = FakeFsOps(source_path=self.SOURCE, source_size=100)
        result = _prepare_testdisk_working_copy(
            self.SOURCE, self.WORKING,
            owner_uid=999, owner_gid=991, fs_ops=fs,
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["code"], "TESTDISK_WORKING_COPY_PREPARED")
        self.assertEqual(result["path"], self.FINAL)
        self.assertEqual(result["size_bytes"], 100)
        names = fs.call_names()
        for step in ("create_secure_file", "copy", "fsync_file", "rename",
                     "fsync_dir", "chown", "chmod"):
            self.assertIn(step, names)
        # Restricted create happens BEFORE any copy; file fsync before rename;
        # directory fsync after rename; ownership last.
        self.assertLess(names.index("create_secure_file"), names.index("copy"))
        self.assertLess(names.index("copy"), names.index("fsync_file"))
        self.assertLess(names.index("fsync_file"), names.index("rename"))
        self.assertLess(names.index("rename"), names.index("fsync_dir"))
        self.assertLess(names.index("fsync_dir"), names.index("chown"))
        self.assertLess(names.index("chown"), names.index("chmod"))
        self.assertIn(self.FINAL, fs.files)
        self.assertNotIn(self.TMP, fs.files)
        self.assertEqual(fs.owners[self.FINAL], (999, 991))
        self.assertEqual(fs.modes[self.FINAL], _TESTDISK_WORKING_COPY_MODE)

    def test_tmp_is_restricted_to_0600_before_copy(self):
        fs = FakeFsOps(source_path=self.SOURCE, source_size=100)
        _prepare_testdisk_working_copy(
            self.SOURCE, self.WORKING,
            owner_uid=999, owner_gid=991, fs_ops=fs,
        )
        # The create_secure_file call must carry 0600 and precede the copy call.
        create_call = next(c for c in fs.calls if c[0] == "create_secure_file")
        copy_index = fs.call_names().index("copy")
        create_index = fs.call_names().index("create_secure_file")
        self.assertEqual(create_call[2], _TESTDISK_WORKING_COPY_MODE)
        self.assertLess(create_index, copy_index)

    def test_create_failure_cleans_up(self):
        fs = FakeFsOps(source_path=self.SOURCE, source_size=100,
                       fail_on={"create_secure_file"})
        result = _prepare_testdisk_working_copy(
            self.SOURCE, self.WORKING,
            owner_uid=999, owner_gid=991, fs_ops=fs,
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_WORKING_COPY_CREATE_FAILED")
        self.assertNotIn(self.TMP, fs.files)
        self.assertNotIn(self.FINAL, fs.files)
        self.assertNotIn("copy", fs.call_names())

    def test_stale_tmp_is_removed_before_create(self):
        fs = FakeFsOps(source_path=self.SOURCE, source_size=100)
        fs.files[self.TMP] = 42  # stale temp present
        result = _prepare_testdisk_working_copy(
            self.SOURCE, self.WORKING,
            owner_uid=999, owner_gid=991, fs_ops=fs,
        )
        self.assertTrue(result["success"])
        names = fs.call_names()
        self.assertLess(names.index("unlink"), names.index("create_secure_file"))

    def test_missing_source_is_refused(self):
        fs = FakeFsOps(source_path="/other", source_size=100)
        result = _prepare_testdisk_working_copy(
            self.SOURCE, self.WORKING,
            owner_uid=999, owner_gid=991, fs_ops=fs,
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_SOURCE_IMAGE_MISSING")

    def test_stale_tmp_cleanup_failure_is_reported(self):
        fs = FakeFsOps(source_path=self.SOURCE, source_size=100,
                       fail_on={"unlink"})
        fs.files[self.TMP] = 42
        result = _prepare_testdisk_working_copy(
            self.SOURCE, self.WORKING,
            owner_uid=999, owner_gid=991, fs_ops=fs,
        )
        self.assertFalse(result["success"])
        self.assertEqual(
            result["code"], "TESTDISK_WORKING_COPY_STALE_TMP_CLEANUP_FAILED"
        )

    def test_copy_failure_cleans_tmp(self):
        fs = FakeFsOps(source_path=self.SOURCE, source_size=100,
                       fail_on={"copy"})
        result = _prepare_testdisk_working_copy(
            self.SOURCE, self.WORKING,
            owner_uid=999, owner_gid=991, fs_ops=fs,
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_WORKING_COPY_COPY_FAILED")
        self.assertNotIn(self.TMP, fs.files)
        self.assertNotIn(self.FINAL, fs.files)

    def test_size_mismatch_cleans_tmp(self):
        fs = FakeFsOps(source_path=self.SOURCE, source_size=100, copied_size=99)
        result = _prepare_testdisk_working_copy(
            self.SOURCE, self.WORKING,
            owner_uid=999, owner_gid=991, fs_ops=fs,
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_WORKING_COPY_SIZE_MISMATCH")
        self.assertNotIn(self.TMP, fs.files)
        self.assertNotIn(self.FINAL, fs.files)

    def test_fsync_file_failure_cleans_tmp(self):
        fs = FakeFsOps(source_path=self.SOURCE, source_size=100,
                       fail_on={"fsync_file"})
        result = _prepare_testdisk_working_copy(
            self.SOURCE, self.WORKING,
            owner_uid=999, owner_gid=991, fs_ops=fs,
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_WORKING_COPY_FSYNC_FAILED")
        self.assertNotIn(self.TMP, fs.files)
        self.assertNotIn(self.FINAL, fs.files)

    def test_rename_failure_cleans_tmp(self):
        fs = FakeFsOps(source_path=self.SOURCE, source_size=100,
                       fail_on={"rename"})
        result = _prepare_testdisk_working_copy(
            self.SOURCE, self.WORKING,
            owner_uid=999, owner_gid=991, fs_ops=fs,
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_WORKING_COPY_RENAME_FAILED")
        self.assertNotIn(self.TMP, fs.files)
        self.assertNotIn(self.FINAL, fs.files)

    def test_dir_fsync_failure_after_rename_removes_final(self):
        fs = FakeFsOps(source_path=self.SOURCE, source_size=100,
                       fail_on={"fsync_dir"})
        result = _prepare_testdisk_working_copy(
            self.SOURCE, self.WORKING,
            owner_uid=999, owner_gid=991, fs_ops=fs,
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_WORKING_COPY_FSYNC_FAILED")
        self.assertNotIn(self.FINAL, fs.files)
        self.assertNotIn(self.TMP, fs.files)

    def test_ownership_failure_after_rename_removes_final(self):
        fs = FakeFsOps(source_path=self.SOURCE, source_size=100,
                       fail_on={"chown"})
        result = _prepare_testdisk_working_copy(
            self.SOURCE, self.WORKING,
            owner_uid=999, owner_gid=991, fs_ops=fs,
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_WORKING_COPY_OWNERSHIP_FAILED")
        self.assertNotIn(self.FINAL, fs.files)
        self.assertNotIn(self.TMP, fs.files)


class ExecutionTargetGuardTests(unittest.TestCase):
    def test_safe_target_passes(self):
        result = _validate_execution_target(
            "/case/working/testdisk.img",
            canonical_image_path="/case/images/source.img",
            source_device_path="/dev/sdb",
            path_resolver=lambda p: p,
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["code"], "TESTDISK_TARGET_SAFE")

    def test_canonical_target_is_refused(self):
        result = _validate_execution_target(
            "/case/images/source.img",
            canonical_image_path="/case/images/source.img",
            source_device_path="/dev/sdb",
            path_resolver=lambda p: p,
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_TARGET_IS_CANONICAL")

    def test_original_device_target_is_refused(self):
        result = _validate_execution_target(
            "/dev/sdb",
            canonical_image_path="/case/images/source.img",
            source_device_path="/dev/sdb",
            path_resolver=lambda p: p,
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_TARGET_IS_ORIGINAL_DEVICE")

    def test_symlinked_target_to_canonical_is_refused_via_resolver(self):
        resolved = {
            "/case/working/link.img": "/case/images/source.img",
            "/case/images/source.img": "/case/images/source.img",
        }
        result = _validate_execution_target(
            "/case/working/link.img",
            canonical_image_path="/case/images/source.img",
            path_resolver=lambda p: resolved.get(p, p),
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_TARGET_IS_CANONICAL")


if __name__ == "__main__":
    unittest.main()
