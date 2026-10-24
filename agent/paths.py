"""Path allowlist. Run `python -m agent.paths` for the self-check."""
from pathlib import Path


def resolve_allowed(path, roots=None):
    """Return the real path if it lies under an allowed root, else raise PermissionError.

    resolve(strict=True) follows symlinks/junctions and collapses `..` before the check,
    so a link inside a root that points outside it is rejected.
    """
    if roots is None:
        from . import config
        roots = config.load()["roots"]
    p = Path(path)
    if not p.is_absolute():  # also rejects drive-relative "C:foo" and "\foo"
        raise PermissionError("absolute path required")
    try:
        real = p.resolve(strict=True)  # FileNotFoundError if missing
    except (OSError, ValueError) as e:  # null byte, "a<b" and other names Windows can't have: same as missing
        raise FileNotFoundError(f"Not found: {path}") from e
    for root in roots:
        try:
            root_real = Path(root).resolve(strict=True)
        except OSError:  # a configured root that doesn't exist (yet) grants nothing
            continue
        if real.is_relative_to(root_real):
            return real
    raise PermissionError("path outside allowed roots")


def _self_check():
    import _winapi
    import os
    import string
    import tempfile

    def denied(p, roots):
        try:
            resolve_allowed(p, roots)
        except PermissionError:
            return True
        return False

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        root, outside, evil = tmp / "root", tmp / "outside", tmp / "root_evil"
        for d in (root, outside, evil):
            d.mkdir()
        (root / "ok.mp4").write_bytes(b"x")
        (outside / "secret.txt").write_bytes(b"x")
        (evil / "x.txt").write_bytes(b"x")
        roots = [str(root)]

        assert resolve_allowed(root / "ok.mp4", roots) == (root / "ok.mp4").resolve()
        assert denied(root / ".." / "outside" / "secret.txt", roots), ".. traversal"
        assert denied(evil / "x.txt", roots), "sibling with root as string prefix"
        assert denied("root/ok.mp4", roots), "relative path"

        # Junction (no admin needed) and, if permitted, a symlink pointing outside the root.
        _winapi.CreateJunction(str(outside), str(root / "junc"))
        assert denied(root / "junc" / "secret.txt", roots), "junction escape"
        try:
            os.symlink(outside / "secret.txt", root / "link.txt")
            assert denied(root / "link.txt", roots), "symlink escape"
        except OSError:
            print("symlink test skipped (needs Developer Mode or admin)")

        here = root.drive.upper()
        drive = next((f"{c}:" for c in string.ascii_uppercase
                      if f"{c}:" != here and Path(f"{c}:\\").exists()), None)
        if drive:
            assert denied(f"{drive}\\", roots), "other drive"
            assert denied(f"{drive}\\..\\..", roots), "other drive with .."
        assert denied(f"{here}\\", roots), "drive root"
        assert denied(f"{here}ok.mp4", roots), "drive-relative path"
    print("paths self-check OK")


if __name__ == "__main__":
    _self_check()
