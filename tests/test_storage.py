import json
import threading

from crowdcount.cli import EXIT_OK, EXIT_USAGE, main
from crowdcount.server.auth import UserStore
from crowdcount.server.storage import JsonStore


def test_missing_file_uses_default(tmp_path):
    assert JsonStore(tmp_path / "x.json", lambda: {"items": []}).read() == {"items": []}


def test_update_is_atomic_and_leaves_no_temp_files(tmp_path):
    store = JsonStore(tmp_path / "x.json", lambda: {"n": 0})
    store.update(lambda d: d.__setitem__("n", d["n"] + 1))
    assert json.loads((tmp_path / "x.json").read_text()) == {"n": 1}
    assert [p.name for p in tmp_path.iterdir()] == ["x.json"]


def test_concurrent_updates_do_not_lose_writes(tmp_path):
    store = JsonStore(tmp_path / "x.json", lambda: {"items": []})

    def worker(i):
        for j in range(25):
            store.update(lambda d, item=(i, j): d["items"].append(item))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(store.read()["items"]) == 100


def test_corrupt_file_is_moved_aside(tmp_path):
    path = tmp_path / "users.json"
    path.write_text('{"users": [')
    store = JsonStore(path, lambda: {"users": []})
    assert store.read() == {"users": []}
    backups = list(tmp_path.glob("users.corrupt-*.json"))
    assert len(backups) == 1 and backups[0].read_text() == '{"users": ['


def test_private_files_are_owner_only(tmp_path):
    store = JsonStore(tmp_path / "secret.json", dict, private=True)
    store.write({"a": 1})
    assert (tmp_path / "secret.json").stat().st_mode & 0o077 == 0


class TestResetPasswordCommand:
    def users(self, tmp_path):
        return UserStore(JsonStore(tmp_path / "data" / "users.json", lambda: {"users": []}))

    def test_creates_admin_when_no_users(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert main(["admin", "reset-password", "--password", "brand-new-pass"]) == EXIT_OK
        assert self.users(tmp_path).authenticate("admin", "brand-new-pass") is not None

    def test_changes_existing_password(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        main(["admin", "reset-password", "--password", "first-password"])
        assert main(["admin", "reset-password", "--password", "second-password"]) == EXIT_OK
        users = self.users(tmp_path)
        assert users.authenticate("admin", "second-password") is not None
        assert users.authenticate("admin", "first-password") is None

    def test_rejects_short_password_and_unknown_user(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert main(["admin", "reset-password", "--password", "short"]) == EXIT_USAGE
        main(["admin", "reset-password", "--password", "first-password"])
        assert (
            main(["admin", "reset-password", "--username", "ghost", "--password", "whatever-1"]) == EXIT_USAGE
        )
