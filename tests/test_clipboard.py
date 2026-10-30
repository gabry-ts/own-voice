import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import clipboard  # noqa: E402


def which_of(*names):
    return lambda n: "/bin/" + n if n in names else None


class PickTests(unittest.TestCase):
    def test_macos(self):
        self.assertEqual(clipboard.pick_command("darwin", "", {}, which_of("pbcopy")), (["pbcopy"], "utf-8"))

    def test_windows(self):
        self.assertEqual(clipboard.pick_command("win32", "", {}, which_of("clip"))[1], "utf-16-le")

    def test_wsl(self):
        got = clipboard.pick_command("linux", "5.15-microsoft-standard", {}, which_of("clip.exe", "xclip"))
        self.assertEqual(got[0], ["clip.exe"])

    def test_wayland_then_xclip_then_xsel(self):
        env = {"WAYLAND_DISPLAY": "wayland-0"}
        self.assertEqual(clipboard.pick_command("linux", "6.1", env, which_of("wl-copy", "xclip"))[0], ["wl-copy"])
        self.assertEqual(clipboard.pick_command("linux", "6.1", env, which_of("xclip"))[0][0], "xclip")
        self.assertEqual(clipboard.pick_command("linux", "6.1", {}, which_of("wl-copy", "xsel"))[0][0], "xsel")

    def test_none(self):
        self.assertIsNone(clipboard.pick_command("linux", "6.1", {}, which_of()))


class CopyTests(unittest.TestCase):
    def test_encoding(self):
        self.assertEqual(clipboard.encode("a\n", "utf-16-le"), b"\xff\xfea\x00\n\x00")
        self.assertEqual(clipboard.encode("è\n\n", "utf-8"), "è\n\n".encode())

    def test_copy_passes_exact_bytes(self):
        with mock.patch.object(clipboard, "pick_command", return_value=(["pbcopy"], "utf-8")), \
                mock.patch.object(clipboard.subprocess, "run") as run:
            run.return_value.returncode = 0
            self.assertEqual(clipboard.copy("ciao\n\n"), "pbcopy")
            self.assertEqual(run.call_args.kwargs["input"], b"ciao\n\n")
            self.assertEqual(run.call_args.args[0], ["pbcopy"])

    def test_copy_no_tool(self):
        with mock.patch.object(clipboard, "pick_command", return_value=None):
            with self.assertRaises(RuntimeError) as cm:
                clipboard.copy("x")
            self.assertIn("xclip", str(cm.exception))

    def test_cli_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "m.txt"
            p.write_bytes("riga\n\n".encode())
            with mock.patch.object(clipboard, "copy", return_value="pbcopy") as cp:
                self.assertEqual(clipboard.main(["--file", str(p)]), 0)
                cp.assert_called_once_with("riga\n")
                cp.reset_mock()
                self.assertEqual(clipboard.main(["--file", str(p), "--keep-final-newline"]), 0)
                cp.assert_called_once_with("riga\n\n")

    def test_strip_final_newline(self):
        f = clipboard.strip_final_newline
        self.assertEqual(f(b"a\nb\n"), b"a\nb")
        self.assertEqual(f(b"a\r\n"), b"a")
        self.assertEqual(f(b"a\n\n"), b"a\n")
        self.assertEqual(f(b"a"), b"a")
        self.assertEqual(f(b""), b"")

    def test_cli_stdin(self):
        stdin = mock.Mock()
        stdin.buffer.read.return_value = "x\ny\n".encode()
        with mock.patch.object(clipboard.sys, "stdin", stdin), \
                mock.patch.object(clipboard, "copy", return_value="pbcopy") as cp:
            self.assertEqual(clipboard.main([]), 0)
            cp.assert_called_once_with("x\ny")

    def test_cli_error(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "m.txt"
            p.write_text("x")
            with mock.patch.object(clipboard, "copy", side_effect=RuntimeError("boom")):
                self.assertEqual(clipboard.main(["--file", str(p)]), 1)


if __name__ == "__main__":
    unittest.main()
