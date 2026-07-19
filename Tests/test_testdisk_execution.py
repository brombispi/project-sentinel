import errno
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


def _ino_for(path):
    # A deterministic, stable fake inode per path so lstat and fstat agree for
    # the same object (exercising the fd dev/ino identity check).
    return abs(hash(path)) % 1_000_000 + 1

SOURCE_ROOT = Path(__file__).resolve().parent.parent / "Source"
sys.path.insert(0, str(SOURCE_ROOT))

# ARCHIVE-internal helpers and the two module-public execution entry points.
import modules.archive as archive  # noqa: E402
from modules.archive import (  # noqa: E402
    _TESTDISK_LOG_MODE,
    _TESTDISK_RECOVERED_DIR_MODE,
    _TESTDISK_WORKING_COPY_MODE,
    _build_testdisk_root_command,
    execute_testdisk_recovery,
    prepare_testdisk_execution,
)

RECOVERY_UID = 999
RECOVERY_GID = 991


def _valid_config(**overrides):
    config = {
        "recovery_account": "sentinel-recovery",
        "forbidden_groups": ["disk", "sudo"],
        "privilege_drop_mechanism": "setpriv",
        "execution_mode": "root",
        "working_copy_safety_margin_bytes": 0,
    }
    config.update(overrides)
    return config


def _identity(**overrides):
    identity = {
        "account": "sentinel-recovery",
        "uid": RECOVERY_UID,
        "gid": RECOVERY_GID,
        "groups": ["sentinel-recovery"],
        "group_gids": [RECOVERY_GID],
    }
    identity.update(overrides)
    return lambda name: dict(identity)


class FakeExecFs:
    """
    A single in-memory filesystem model backing every injected provider used by
    prepare_testdisk_execution: the working-copy fs_ops, the output/log fs_ops,
    and the stat_provider / statvfs_provider. Operations mutate the model so the
    final validation pass observes the freshly-prepared state, exactly as a real
    run would. Optional per-op failure injection.
    """

    BASE = "/case"
    CANONICAL = "/case/images/source.img"
    WORKING_DIR = "/case/working"
    WORKING_IMAGE = "/case/working/testdisk.img"
    RECOVERED_TESTDISK = "/case/recovered/testdisk"
    EVIDENCE_DIR = "/case/evidence"
    LOG = "/case/evidence/testdisk.log"

    def __init__(self, *, canonical_mode=0o400, canonical_kind="file",
                 canonical_is_symlink=False, source_size=100,
                 bavail=10 ** 9, fail_on=None):
        self.fail_on = set(fail_on or [])
        self.bavail = bavail
        self.open_fds = {}
        self.closed_fds = []
        self._next_fd = 21
        self.objects = {}
        for directory in ("/case", "/case/images", "/case/working",
                          "/case/recovered", "/case/evidence"):
            self.objects[directory] = {
                "kind": "dir", "uid": 0, "gid": 0, "mode": 0o755, "size": 0,
                "is_symlink": False,
            }
        self.objects[self.CANONICAL] = {
            "kind": canonical_kind, "uid": 0, "gid": 0, "mode": canonical_mode,
            "size": source_size, "is_symlink": canonical_is_symlink,
        }
        # Resolved absolute executables (regular, executable, not symlinks).
        for exe in (SETPRIV_PATH, TESTDISK_PATH):
            self.objects[exe] = {
                "kind": "file", "uid": 0, "gid": 0, "mode": 0o755, "size": 0,
                "is_symlink": False,
            }

    def resolver(self, missing=()):
        mapping = {"setpriv": SETPRIV_PATH, "testdisk": TESTDISK_PATH}

        def _resolve(name):
            if name in missing:
                return None
            return mapping.get(name)

        return _resolve

    def _maybe_fail(self, op):
        if op in self.fail_on:
            raise OSError(f"simulated {op} failure")

    def _mode_with_type(self, entry):
        if entry["is_symlink"]:
            return stat.S_IFLNK | entry["mode"]
        if entry["kind"] == "dir":
            return stat.S_IFDIR | entry["mode"]
        return stat.S_IFREG | entry["mode"]

    # ---- working-copy fs_ops -------------------------------------------------
    def exists(self, path):
        return path in self.objects

    def unlink(self, path):
        self._maybe_fail("unlink")
        self.objects.pop(path, None)

    def create_secure_file(self, path, mode):
        self._maybe_fail("create_secure_file")
        self.objects[path] = {
            "kind": "file", "uid": 0, "gid": 0, "mode": mode, "size": 0,
            "is_symlink": False,
        }

    def open_canonical(self, path):
        self._maybe_fail("open_canonical")
        if path not in self.objects:
            raise FileNotFoundError(path)
        if self.objects[path]["is_symlink"]:
            raise OSError(errno.ELOOP, "symlink refused by O_NOFOLLOW")
        fd = self._next_fd
        self._next_fd += 1
        self.open_fds[fd] = path
        return fd

    def fstat(self, fd):
        path = self.open_fds[fd]
        entry = self.objects[path]
        return SimpleNamespace(
            st_mode=self._mode_with_type(entry),
            st_uid=entry["uid"], st_gid=entry["gid"], st_size=entry["size"],
            st_dev=1, st_ino=_ino_for(path),
        )

    def copy_fd_to_path(self, source_fd, destination):
        self._maybe_fail("copy")
        source_path = self.open_fds[source_fd]
        self.objects[destination]["size"] = self.objects[source_path]["size"]

    def close(self, fd):
        self.closed_fds.append(fd)
        self.open_fds.pop(fd, None)

    def size(self, path):
        if path not in self.objects:
            raise FileNotFoundError(path)
        return self.objects[path]["size"]

    def fsync_file(self, path):
        self._maybe_fail("fsync_file")

    def fsync_dir(self, path):
        self._maybe_fail("fsync_dir")

    def rename(self, source, destination):
        self._maybe_fail("rename")
        self.objects[destination] = self.objects.pop(source)

    def chown(self, path, uid, gid):
        self._maybe_fail("chown")
        self.objects[path]["uid"] = uid
        self.objects[path]["gid"] = gid

    def chmod(self, path, mode):
        self._maybe_fail("chmod")
        self.objects[path]["mode"] = mode

    # ---- output/log fs_ops ---------------------------------------------------
    def lstat(self, path):
        self._maybe_fail("lstat")
        if path not in self.objects:
            raise FileNotFoundError(path)
        entry = self.objects[path]
        return SimpleNamespace(
            st_mode=self._mode_with_type(entry),
            st_uid=entry["uid"], st_gid=entry["gid"],
            st_dev=1, st_ino=_ino_for(path),
        )

    def mkdir(self, path, mode):
        self._maybe_fail("mkdir")
        if path in self.objects:
            raise FileExistsError(path)
        self.objects[path] = {
            "kind": "dir", "uid": 0, "gid": 0, "mode": mode, "size": 0,
            "is_symlink": False,
        }

    def create_regular_file(self, path, mode):
        self._maybe_fail("create_regular_file")
        if path in self.objects:
            raise FileExistsError(path)
        self.objects[path] = {
            "kind": "file", "uid": 0, "gid": 0, "mode": mode, "size": 0,
            "is_symlink": False,
        }

    def rmdir(self, path):
        self.objects.pop(path, None)

    # ---- stat_provider / statvfs_provider ------------------------------------
    def stat(self, path):
        if path not in self.objects:
            raise FileNotFoundError(path)
        entry = self.objects[path]
        return SimpleNamespace(
            st_mode=self._mode_with_type(entry),
            st_uid=entry["uid"], st_gid=entry["gid"], st_size=entry["size"],
            st_dev=1, st_ino=_ino_for(path),
        )

    def statvfs(self, path):
        return SimpleNamespace(f_bavail=self.bavail, f_frsize=1)


def _make_session():
    return SimpleNamespace(recovery_path="/case", source_device=None,
                           status="RECOVERING")


def _prepare(fs, *, config=None, geteuid=lambda: 0, command_resolver=None,
             identity_resolver=None, source_environ=None,
             acquisition_gate_passes=True):
    acquisition_patcher = None
    if acquisition_gate_passes:
        acquisition_patcher = mock.patch.object(
            archive,
            "classify_acquisition_state",
            return_value={
                "state": "completed_canonical",
                "code": "ACQUISITION_COMPLETED_CANONICAL",
                "message": (
                    "Canonical acquisition is complete and fingerprint exists."
                ),
                "image_exists": True,
                "map_exists": True,
                "sha256_exists": True,
            },
        )
        acquisition_patcher.start()

    try:
        return prepare_testdisk_execution(
            _make_session(),
            config if config is not None else _valid_config(),
            identity_resolver=identity_resolver or _identity(),
            command_resolver=command_resolver or fs.resolver(),
            geteuid=geteuid,
            stat_provider=fs.stat,
            statvfs_provider=fs.statvfs,
            lstat_provider=fs.lstat,
            source_environ=source_environ if source_environ is not None
            else {"TERM": "xterm-256color"},
            fs_ops=fs,
        )
    finally:
        if acquisition_patcher is not None:
            acquisition_patcher.stop()


SETPRIV_PATH = "/usr/bin/setpriv"
TESTDISK_PATH = "/usr/bin/testdisk"


class CommandBuilderTests(unittest.TestCase):
    def test_exact_argv_uses_absolute_paths(self):
        argv = _build_testdisk_root_command(
            SETPRIV_PATH, TESTDISK_PATH, 999, 991,
            "/case/working/testdisk.img",
        )
        self.assertEqual(
            argv,
            [
                "/usr/bin/setpriv",
                "--reuid=999",
                "--regid=991",
                "--clear-groups",
                "--",
                "/usr/bin/testdisk",
                "/log",
                "/case/working/testdisk.img",
            ],
        )

    def test_bare_executable_names_never_appear(self):
        argv = _build_testdisk_root_command(
            SETPRIV_PATH, TESTDISK_PATH, 999, 991, "/w/img"
        )
        self.assertEqual(argv[0], SETPRIV_PATH)
        self.assertEqual(argv[5], TESTDISK_PATH)
        self.assertNotIn("setpriv", argv)
        self.assertNotIn("testdisk", argv)

    def test_integer_uid_gid_rendering(self):
        argv = _build_testdisk_root_command(
            SETPRIV_PATH, TESTDISK_PATH, 1234, 5678, "/w/img"
        )
        self.assertEqual(argv[1], "--reuid=1234")
        self.assertEqual(argv[2], "--regid=5678")

    def test_root_uid_refused(self):
        with self.assertRaises(ValueError):
            _build_testdisk_root_command(SETPRIV_PATH, TESTDISK_PATH, 0, 991,
                                         "/w/img")

    def test_root_gid_refused(self):
        with self.assertRaises(ValueError):
            _build_testdisk_root_command(SETPRIV_PATH, TESTDISK_PATH, 999, 0,
                                         "/w/img")

    def test_non_integer_uid_refused(self):
        with self.assertRaises(ValueError):
            _build_testdisk_root_command(SETPRIV_PATH, TESTDISK_PATH, "999",
                                         991, "/w/img")

    def test_bool_uid_refused(self):
        with self.assertRaises(ValueError):
            _build_testdisk_root_command(SETPRIV_PATH, TESTDISK_PATH, True, 991,
                                         "/w/img")

    def test_relative_setpriv_path_refused(self):
        with self.assertRaises(ValueError):
            _build_testdisk_root_command("bin/setpriv", TESTDISK_PATH, 999, 991,
                                         "/w/img")

    def test_relative_testdisk_path_refused(self):
        with self.assertRaises(ValueError):
            _build_testdisk_root_command(SETPRIV_PATH, "bin/testdisk", 999, 991,
                                         "/w/img")


class PrepareExecutionTests(unittest.TestCase):
    def test_root_success_returns_normalized_data(self):
        fs = FakeExecFs()
        result = _prepare(fs)
        self.assertTrue(result["success"], result)
        self.assertEqual(result["code"], "TESTDISK_PREPARED")
        self.assertEqual(result["status"], "prepared")
        self.assertEqual(result["recovery_uid"], RECOVERY_UID)
        self.assertEqual(result["recovery_gid"], RECOVERY_GID)
        self.assertEqual(result["cwd"], "/case/evidence")
        self.assertEqual(result["working_image_path"],
                         "/case/working/testdisk.img")
        self.assertEqual(result["recovered_directory"],
                         "/case/recovered/testdisk")
        self.assertEqual(result["log_path"], "/case/evidence/testdisk.log")
        self.assertEqual(result["setpriv_path"], SETPRIV_PATH)
        self.assertEqual(result["testdisk_path"], TESTDISK_PATH)
        self.assertEqual(
            result["argv"],
            [
                SETPRIV_PATH,
                "--reuid=999",
                "--regid=991",
                "--clear-groups",
                "--",
                TESTDISK_PATH,
                "/log",
                "/case/working/testdisk.img",
            ],
        )
        # Bare names never appear; the child env is present and minimal.
        self.assertNotIn("setpriv", result["argv"])
        self.assertNotIn("testdisk", result["argv"])
        self.assertEqual(result["env"]["PATH"], "/usr/sbin:/usr/bin:/sbin:/bin")
        self.assertEqual(result["env"]["TERM"], "xterm-256color")

    def test_dangerous_env_dropped_and_source_not_mutated(self):
        fs = FakeExecFs()
        source = {
            "TERM": "xterm", "LD_PRELOAD": "/tmp/evil.so",
            "PYTHONPATH": "/tmp/py", "PATH": "/attacker/bin",
        }
        snapshot = dict(source)
        result = _prepare(fs, source_environ=source)
        self.assertTrue(result["success"], result)
        self.assertEqual(result["env"]["PATH"], "/usr/sbin:/usr/bin:/sbin:/bin")
        self.assertNotIn("LD_PRELOAD", result["env"])
        self.assertNotIn("PYTHONPATH", result["env"])
        self.assertEqual(source, snapshot)

    def test_nul_in_env_fails_closed(self):
        fs = FakeExecFs()
        result = _prepare(fs, source_environ={"TERM": "xterm\x00evil"})
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_ENV_INVALID")

    def test_symlinked_executable_refused(self):
        fs = FakeExecFs()
        fs.objects[SETPRIV_PATH]["is_symlink"] = True
        result = _prepare(fs)
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_EXECUTABLE_IS_SYMLINK")

    def test_relative_resolver_result_refused(self):
        fs = FakeExecFs()
        result = _prepare(fs, command_resolver=lambda name: "bin/" + name)
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_EXECUTABLE_NOT_ABSOLUTE")

    def test_canonical_symlink_refused(self):
        fs = FakeExecFs(canonical_is_symlink=True)
        result = _prepare(fs)
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_CANONICAL_IS_SYMLINK")

    def test_canonical_directory_refused(self):
        fs = FakeExecFs(canonical_kind="dir")
        result = _prepare(fs)
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_CANONICAL_NOT_REGULAR")

    def test_prepared_artifacts_have_required_ownership_and_modes(self):
        fs = FakeExecFs()
        result = _prepare(fs)
        self.assertTrue(result["success"], result)
        # Prepared artifacts have the required ownership and exact modes.
        self.assertEqual(fs.objects[fs.WORKING_IMAGE]["mode"],
                         _TESTDISK_WORKING_COPY_MODE)
        self.assertEqual(fs.objects[fs.RECOVERED_TESTDISK]["mode"],
                         _TESTDISK_RECOVERED_DIR_MODE)
        self.assertEqual(fs.objects[fs.LOG]["mode"], _TESTDISK_LOG_MODE)
        for path in (fs.WORKING_IMAGE, fs.RECOVERED_TESTDISK, fs.LOG):
            self.assertEqual(
                (fs.objects[path]["uid"], fs.objects[path]["gid"]),
                (RECOVERY_UID, RECOVERY_GID),
            )

    def test_no_lifecycle_status_or_persistence_side_effects(self):
        fs = FakeExecFs()
        session = _make_session()
        with mock.patch.object(
            archive,
            "classify_acquisition_state",
            return_value={
                "state": "completed_canonical",
                "code": "ACQUISITION_COMPLETED_CANONICAL",
                "message": (
                    "Canonical acquisition is complete and fingerprint exists."
                ),
            },
        ):
            prepare_testdisk_execution(
                session,
                _valid_config(),
                identity_resolver=_identity(),
                command_resolver=fs.resolver(),
                geteuid=lambda: 0,
                stat_provider=fs.stat,
                statvfs_provider=fs.statvfs,
                lstat_provider=fs.lstat,
                source_environ={"TERM": "xterm"},
                fs_ops=fs,
            )
        # Status is untouched and no recovery-operations record was attached.
        self.assertEqual(session.status, "RECOVERING")
        self.assertFalse(hasattr(session, "recovery_operations"))

    def test_non_root_refused(self):
        fs = FakeExecFs()
        result = _prepare(fs, geteuid=lambda: 1000)
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_REQUIRES_ROOT")

    def test_sudo_mode_refused_not_executable_yet(self):
        fs = FakeExecFs()
        result = _prepare(fs, config=_valid_config(execution_mode="sudo"))
        self.assertFalse(result["success"])
        self.assertEqual(
            result["code"], "TESTDISK_EXECUTION_MODE_SUDO_NOT_EXECUTABLE_YET"
        )

    def test_external_mode_refused_not_executable_yet(self):
        fs = FakeExecFs()
        result = _prepare(fs, config=_valid_config(execution_mode="external"))
        self.assertFalse(result["success"])
        self.assertEqual(
            result["code"],
            "TESTDISK_EXECUTION_MODE_EXTERNAL_NOT_EXECUTABLE_YET",
        )

    def test_testdisk_missing_refused(self):
        fs = FakeExecFs()
        result = _prepare(fs, command_resolver=fs.resolver(missing={"testdisk"}))
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_EXECUTABLE_NOT_FOUND")

    def test_setpriv_missing_refused(self):
        fs = FakeExecFs()
        result = _prepare(fs, command_resolver=fs.resolver(missing={"setpriv"}))
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_EXECUTABLE_NOT_FOUND")

    def test_unsafe_identity_refused(self):
        fs = FakeExecFs()
        result = _prepare(
            fs,
            identity_resolver=_identity(
                groups=["sentinel-recovery", "disk"], group_gids=[991, 6]
            ),
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_IDENTITY_PRIVILEGED_GROUP")

    def test_canonical_failure_refused(self):
        fs = FakeExecFs(canonical_mode=0o440)
        result = _prepare(fs)
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_CANONICAL_PERMISSIVE")

    def test_free_space_failure_refused(self):
        fs = FakeExecFs(source_size=100, bavail=10)
        result = _prepare(fs)
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_INSUFFICIENT_FREE_SPACE")

    def test_working_copy_failure_refused(self):
        fs = FakeExecFs(fail_on={"copy"})
        result = _prepare(fs)
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_WORKING_COPY_COPY_FAILED")

    def test_canonical_descriptor_opened_once_and_closed_on_success(self):
        fs = FakeExecFs()
        result = _prepare(fs)
        self.assertTrue(result["success"], result)
        # Exactly one canonical descriptor was opened and it was closed; the
        # copy read from that descriptor, never a reopened path.
        self.assertEqual(len(fs.closed_fds), 1)
        self.assertEqual(fs.open_fds, {})

    def test_canonical_descriptor_closed_on_working_copy_failure(self):
        fs = FakeExecFs(fail_on={"copy"})
        result = _prepare(fs)
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_WORKING_COPY_COPY_FAILED")
        # The descriptor is closed via the caller's finally even when the copy
        # fails, so no descriptor leaks.
        self.assertEqual(len(fs.closed_fds), 1)
        self.assertEqual(fs.open_fds, {})

    def test_output_preparation_failure_refused(self):
        fs = FakeExecFs(fail_on={"mkdir"})
        result = _prepare(fs)
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_OUTPUT_CREATE_FAILED")

    def test_malformed_config_refused(self):
        fs = FakeExecFs()
        result = _prepare(fs, config={"recovery_account": "x"})
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_CONFIG_STRUCTURE_INVALID")


class RecordingRunner:
    def __init__(self, *, returncode=0, raise_error=None):
        self.calls = []
        self.returncode = returncode
        self.raise_error = raise_error

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.raise_error is not None:
            raise self.raise_error
        return SimpleNamespace(returncode=self.returncode)


class ExecuteTestdiskRecoveryTests(unittest.TestCase):
    ARGV = [
        SETPRIV_PATH, "--reuid=999", "--regid=991", "--clear-groups", "--",
        TESTDISK_PATH, "/log", "/case/working/testdisk.img",
    ]
    CHILD_ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "TERM": "xterm"}

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.recovered = Path(self._tmp.name) / "recovered"
        self.testdisk_dir = self.recovered / "testdisk"

    def _preparation(self):
        return {
            "success": True,
            "status": "prepared",
            "code": "TESTDISK_PREPARED",
            "message": "ok",
            "setpriv_path": SETPRIV_PATH,
            "testdisk_path": TESTDISK_PATH,
            "argv": list(self.ARGV),
            "env": dict(self.CHILD_ENV),
            "cwd": str(Path(self._tmp.name) / "evidence"),
            "working_image_path": "/case/working/testdisk.img",
            "recovered_directory": str(self.testdisk_dir),
            "log_path": "/case/evidence/testdisk.log",
        }

    def test_runner_called_exactly_once_without_shell_or_capture(self):
        self.testdisk_dir.mkdir(parents=True)
        runner = RecordingRunner(returncode=0)
        prep = self._preparation()
        execute_testdisk_recovery(prep, runner=runner)
        self.assertEqual(len(runner.calls), 1)
        args, kwargs = runner.calls[0]
        # Called exactly as runner(argv, cwd=cwd, env=child_env); no shell,
        # capture_output, check, text, or redirected stdio.
        self.assertEqual(args, (self.ARGV,))
        self.assertEqual(kwargs, {"cwd": prep["cwd"], "env": self.CHILD_ENV})

    def test_runner_receives_stored_env_not_live_environment(self):
        self.testdisk_dir.mkdir(parents=True)
        runner = RecordingRunner(returncode=0)
        prep = self._preparation()
        # Mutating the ambient environment after preparation must not change
        # what execution passes: execution never rebuilds the env or looks up
        # PATH again.
        with mock.patch.dict(os.environ, {"PATH": "/attacker/bin",
                                          "LD_PRELOAD": "/tmp/evil.so"}):
            execute_testdisk_recovery(prep, runner=runner)
        args, kwargs = runner.calls[0]
        self.assertEqual(kwargs["env"], self.CHILD_ENV)
        self.assertNotIn("LD_PRELOAD", kwargs["env"])
        self.assertEqual(args[0], self.ARGV)

    def test_missing_env_is_rejected(self):
        runner = mock.Mock()
        bad = self._preparation()
        del bad["env"]
        result = execute_testdisk_recovery(bad, runner=runner)
        runner.assert_not_called()
        self.assertEqual(result["code"], "TESTDISK_PREPARATION_INVALID")

    def test_zero_exit_is_success_ended(self):
        self.testdisk_dir.mkdir(parents=True)
        runner = RecordingRunner(returncode=0)
        result = execute_testdisk_recovery(self._preparation(), runner=runner)
        self.assertTrue(result["success"])
        self.assertEqual(result["status"], "ended")
        self.assertEqual(result["code"], "TESTDISK_ENDED_NORMALLY")

    def test_non_zero_exit_is_failed(self):
        self.testdisk_dir.mkdir(parents=True)
        runner = RecordingRunner(returncode=3)
        result = execute_testdisk_recovery(self._preparation(), runner=runner)
        self.assertFalse(result["success"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["code"], "TESTDISK_EXIT_CODE")
        self.assertEqual(result["display_args"]["exit_code"], 3)

    def test_launch_oserror_is_distinct_failure(self):
        runner = RecordingRunner(raise_error=OSError("no such binary"))
        result = execute_testdisk_recovery(self._preparation(), runner=runner)
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_LAUNCH_FAILED")
        # The runner was invoked (launch attempt) exactly once.
        self.assertEqual(len(runner.calls), 1)

    def test_artifact_counts_from_recovered_testdisk(self):
        self.testdisk_dir.mkdir(parents=True)
        (self.testdisk_dir / "recovered_1.jpg").write_bytes(b"abcde")
        (self.testdisk_dir / "recovered_2.jpg").write_bytes(b"fg")
        runner = RecordingRunner(returncode=0)
        result = execute_testdisk_recovery(self._preparation(), runner=runner)
        self.assertEqual(result["recovered_directory_count"], 1)
        self.assertEqual(result["recovered_file_count"], 2)
        self.assertEqual(result["recovered_total_bytes"], 7)
        self.assertEqual(result["artifacts"], [str(self.testdisk_dir)])

    def test_runner_not_called_with_failed_preparation(self):
        runner = mock.Mock()
        bad = self._preparation()
        bad["success"] = False
        result = execute_testdisk_recovery(bad, runner=runner)
        runner.assert_not_called()
        self.assertFalse(result["success"])
        self.assertEqual(result["code"], "TESTDISK_PREPARATION_INVALID")

    def test_runner_not_called_with_malformed_preparation(self):
        runner = mock.Mock()
        result = execute_testdisk_recovery({"success": True}, runner=runner)
        runner.assert_not_called()
        self.assertEqual(result["code"], "TESTDISK_PREPARATION_INVALID")


if __name__ == "__main__":
    unittest.main()
